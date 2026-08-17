# ============================================================================
# mycordis/core/context.py
# 上下文（Context）：整个插件系统的"服务容器"与"可逆副作用栈"。
#
# 设计意图：
#   Context 是插件之间进行解耦通信的核心载体，主要承担三件事：
#     1. 存储服务（provide）：插件把自身提供的能力（如 'llm'、'agent'）注册进来。
#     2. 解析依赖（get）：插件通过它向上查找自己需要的服务（如 'llm'）。
#     3. 管理清理（effect / revert）：插件把"撤销副作用"的函数登记到 LIFO 栈，
#        当插件被停用/卸载时统一按逆序执行，保证资源可回收、状态可还原。
#
#   它还通过 "父指针（parent）" 建立起一棵上下文树：
#   - 根上下文（_root_ctx）持有所有全局可见的服务；
#   - 每个插件拥有一棵独立的隔离子上下文，互不污染；
#   - 子上下文找不到服务时，会沿着 parent 一路向上递归查找（继承）。
#
# 命名约定：
#   - 下划线开头的成员（_services/_providers/_disposers）均为"内部实现细节"，
#     外部不应直接访问，而应通过 provide/get/effect/revert 等公开方法操作。
# ============================================================================

from typing import Dict, Any, Optional, Callable, Awaitable, List


class Context:
    """
    上下文是服务的容器。

    负责存储插件提供的服务（provide），以及向上查找依赖（get）。
    同时维护一个"可逆副作用栈"（disposers），用于卸载时清理。

    --- 角色定位 ---
    · 服务仓库：_services 以 key->value 形式保存服务实例。
    · 提供者索引：_providers 记录每个服务是由哪个插件提供的，方便调试与精确回收。
    · 副作用栈：_disposers 用 LIFO 顺序保存清理函数，revert() 时按逆序执行。
    · 继承链：parent 让子上下文可以"借用"父级（乃至根级）的服务，实现依赖向上查找。
    """

    def __init__(self, parent: Optional['Context'] = None):
        """
        初始化一个上下文实例。

        :param parent: 父上下文。若为 None，则此上下文即为"根上下文"；
                       若不为 None，则当前上下文是 parent 的一个子上下文，
                       找不到服务时会向 parent 递归查找。
        """
        # 父上下文指针，用于构建"上下文树"，实现服务的向上继承查找。
        self.parent = parent

        # 服务仓库：key 为服务名（如 'llm'），value 为任意对象（如 LLM 客户端实例）。
        # 当前层级注册的所有服务都存放于此；子上下文各自拥有独立的 _services，互不干扰。
        self._services: Dict[str, Any] = {}

        # 提供者索引：key 为服务名，value 为该服务是由哪个插件名提供的。
        # 用于调试定位服务来源，以及在卸载插件时精确移除其提供的服务。
        self._providers: Dict[str, str] = {}

        # 可逆副作用栈（LIFO / 后进先出）。
        # 元素是"清理函数"（disposer），形如 async () -> None。
        # 插件在 apply 中调用 effect() 登记，revert() 时按相反顺序逐个执行，
        # 从而保证"先注册的副作用后清理"，还原到初始状态。
        self._disposers: List[Callable[[], Awaitable[None]]] = []

    # ------------------------------------------------------------------
    # 服务注册与获取
    # ------------------------------------------------------------------
    def provide(self, key: str, value: Any, plugin_name: str) -> None:
        """
        在当前上下文注册一个服务。

        若 key 已存在，则抛出 ValueError，避免同名服务被覆盖而产生歧义。

        :param key:         服务名（服务键），如 'llm'。
        :param value:       服务实例，可以是任意对象。
        :param plugin_name: 提供该服务的插件名，写入 _providers 用于溯源。
        :raises ValueError: 当 key 在当前层级已存在时抛出。
        """
        # 防重复：同一层级的服务名必须唯一。
        if key in self._services:
            raise ValueError(f"Service '{key}' is already provided by {self._providers.get(key)}")

        # 写入服务仓库，并登记提供者。
        self._services[key] = value
        self._providers[key] = plugin_name

    def get(self, key: str) -> Any:
        """
        获取服务：优先在当前上下文查找，找不到则向父级递归查找。

        :param key: 服务名（服务键），如 'llm'。
        :return:    服务实例。
        :raises KeyError: 当在整个上下文链上都找不到该服务时抛出。
        """
        # 1) 先在当前层级查找。
        if key in self._services:
            return self._services[key]

        # 2) 当前层级没有，则向上交给父上下文递归查找（实现"继承"）。
        if self.parent:
            return self.parent.get(key)

        # 3) 已到根上下文仍找不到，说明该依赖缺失。
        raise KeyError(f"Service '{key}' not found in context chain")

    # ------------------------------------------------------------------
    # 隔离
    # ------------------------------------------------------------------
    def isolate(self) -> 'Context':
        """
        创建当前上下文的隔离子上下文（parent 指向 self）。

        子上下文拥有独立的 _services / _providers / _disposers，
        因此向子上下文注入的服务不会污染父级；但它可以向上查找到父级的服务。
        每个插件都会通过 _root_ctx.isolate() 获得这样一个专属子上下文。

        :return: 一个新的 Context 实例，其 parent 为当前上下文。
        """
        return Context(parent=self)

    # ------------------------------------------------------------------
    # 可逆副作用管理
    # ------------------------------------------------------------------
    def effect(self, disposer: Callable[[], Awaitable[None]]) -> None:
        """
        注册一个可逆副作用（清理函数），压入 LIFO 栈 _disposers。

        插件在 apply 中调用此方法登记"卸载时要做的事"，
        例如关闭连接、释放资源、移除全局监听器等。

        :param disposer: 一个 async 清理函数，形如 async def dispose(): ...
        """
        self._disposers.append(disposer)

    async def revert(self) -> None:
        """
        按 LIFO（后进先出）顺序执行并清空 _disposers 中的所有清理函数。

        之所以逆序执行，是因为副作用往往存在依赖关系：
        后注册的副作用可能建立在前一个副作用之上，因此应先撤销后注册的。
        当插件被停用或卸载时，Registry 会调用它来触发清理。

        注意：单个 disposer 抛出的异常会向外传播，调用方需自行兜底。
        """
        # 反复从栈顶弹出并执行，直到栈空，从而保证 LIFO 顺序。
        while self._disposers:
            disposer = self._disposers.pop()
            await disposer()

    # ------------------------------------------------------------------
    # 提供者溯源
    # ------------------------------------------------------------------
    def get_provider_name(self, key: str) -> Optional[str]:
        """
        查询某个服务是由哪个插件提供的（沿 parent 向上递归查找）。

        用于调试定位服务来源，或在卸载时判断某服务是否属于某插件。

        :param key: 服务名。
        :return:    提供该服务的插件名；若整个上下文链上都未登记，返回 None。
        """
        # 1) 先在当前层级查找提供者索引。
        if key in self._providers:
            return self._providers[key]

        # 2) 当前层级没有，则向父级递归查找。
        if self.parent:
            return self.parent.get_provider_name(key)

        # 3) 已到根仍未找到，返回 None 表示"无人提供"。
        return None
