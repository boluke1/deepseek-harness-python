# ============================================================================
# mycordis/core/context.py
# 上下文（Context）：整个插件系统的"服务容器"与"可逆副作用栈"。
#
# 设计意图（对标 DeepSeek Harness / Cordis 的 context.ts + reflect.ts）：
#   Context 是插件之间进行解耦通信的核心载体，负责：
#     1. 存储服务（provide）：插件把自身提供的能力注册进来。
#     2. 解析依赖：ctx.xxx 直接以"属性访问"方式拿到服务（反射层）。
#     3. 管理清理（effect / revert）：把撤销副作用登记到 LIFO 栈，卸载时逆序执行。
#
#   ★ 对标 DSH 的核心增强：
#     · 服务即属性：provide 后，ctx.llm / ctx.sessions 可直接访问服务。
#     · internal/get 走事件：服务解析可被监听器拦截。
#     · get() 同步（支持同步监听器）、aget() 异步（支持异步 waterfall）。
#     · Symbol 标签作用域：isolate(name, label) 用 object() 做"标签墙"。
#     · provide 是可逆副作用：revert() 时服务自动移除。
# ============================================================================

from typing import Dict, Any, Optional, Callable, Awaitable, List


class Context:
    """
    上下文是服务的容器，并充当"反射层"——ctx.xxx 即服务。
    """

    def __init__(self, parent: Optional['Context'] = None):
        """
        初始化一个上下文实例。

        :param parent: 父上下文。若为 None，则为"根上下文"。
        """
        self.parent = parent
        self._services: Dict[str, Any] = {}
        self._providers: Dict[str, str] = {}
        self._scopes: Dict[str, object] = {}
        self.__expected_scope: Dict[str, object] = {}
        self._disposers: List[Callable[[], Awaitable[None]]] = []
        self._events = None

    # ------------------------------------------------------------------
    # 服务注册（可逆副作用）
    # ------------------------------------------------------------------
    def provide(self, key: str, value: Any, plugin_name: str = "") -> None:
        """
        在当前上下文注册一个服务（自动登记可逆反注册副作用）。

        :param key:         服务名。
        :param value:       服务实例。
        :param plugin_name: 提供该服务的插件名。
        :raises ValueError: key 已存在时抛出。
        """
        if key in self._services:
            raise ValueError(f"Service '{key}' is already provided by {self._providers.get(key)}")

        self._services[key] = value
        self._providers[key] = plugin_name
        if key not in self._scopes:
            self._scopes[key] = object()

        async def _dispose():
            self._services.pop(key, None)
            self._providers.pop(key, None)

        self._disposers.append(_dispose)

    # ------------------------------------------------------------------
    # 服务获取
    # ------------------------------------------------------------------
    def get(self, key: str) -> Any:
        """
        同步获取服务（支持同步 internal/get 监听器拦截）。

        :param key: 服务名。
        :return:    服务实例。
        :raises KeyError: 找不到时抛出。
        """
        resolved = self._resolve_service_via_events(key)
        if resolved is not None:
            return resolved
        return self._lookup(key)

    async def aget(self, key: str) -> Any:
        """
        异步获取服务（对标 DSH 的异步服务解析）。

        与 get() 不同，aget() 支持异步 internal/get 监听器（可 await），
        因为它是 async 方法，可以真正执行 waterfall。

        :param key: 服务名。
        :return:    服务实例。
        :raises KeyError: 找不到时抛出。
        """
        # 若事件系统已初始化，走真正的异步 waterfall。
        if self._events is not None:
            result = await self._events.waterfall("internal/get", key)
            if result is not None and result != key:
                return result
        # 普通查找。
        return self._lookup(key)

    def _resolve_service_via_events(self, key: str):
        """
        尝试通过 internal/get 事件监听器解析服务（仅同步监听器）。

        :param key: 服务名。
        :return:    拦截结果或 None。
        """
        try:
            if self._events is None:
                return None
            listeners = self._events._get_listeners('internal/get')
            if not listeners:
                return None
            for listener in listeners:
                res = listener(key)
                import asyncio
                if asyncio.iscoroutine(res):
                    continue
                if res is not None:
                    return res
            return None
        except Exception:
            return None

    def _lookup(self, key: str) -> Any:
        """
        普通服务查找：沿上下文链向上，并遵守作用域标签墙。

        :param key: 服务名。
        :return:    服务实例。
        :raises KeyError: 找不到或标签墙阻断时抛出。
        """
        if key in self._services:
            return self._services[key]

        expected = self.__expected_scope.get(key)

        if self.parent:
            if expected is not None:
                parent_has = key in self.parent._services
                parent_label = self.parent._scopes.get(key)
                if not parent_has or parent_label is not expected:
                    raise KeyError(
                        f"Service '{key}' is isolated by scope tag, "
                        f"cannot access from parent"
                    )
                return self.parent._services[key]
            else:
                return self.parent._lookup(key)

        raise KeyError(f"Service '{key}' not found in context chain")

    # ------------------------------------------------------------------
    # 服务即属性（反射层）
    # ------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        """
        让 ctx.xxx 直接返回服务。

        :param name: 属性名（同时也是服务名）。
        :return:     服务实例。
        :raises AttributeError: 既非属性也非服务时抛出。
        """
        if name.startswith('_'):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        try:
            return self.get(name)
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    # ------------------------------------------------------------------
    # 隔离（Symbol 标签作用域）
    # ------------------------------------------------------------------
    def isolate(self, name: str = None, label: object = None) -> 'Context':
        """
        创建当前上下文的隔离子上下文。

        :param name:  可选，要隔离的服务名。
        :param label: 可选，隔离标签（object 实例，作为 Symbol 使用）。
        :return:      一个新的 Context 实例。
        """
        child = Context(parent=self)
        if name is not None:
            child.__expected_scope[name] = label if label is not None else object()
        return child

    # ------------------------------------------------------------------
    # 可逆副作用管理
    # ------------------------------------------------------------------
    def effect(self, disposer: Callable[[], Awaitable[None]]) -> None:
        """
        注册一个可逆副作用（清理函数），压入 LIFO 栈。

        :param disposer: 一个 async 清理函数。
        """
        self._disposers.append(disposer)

    async def revert(self) -> None:
        """
        按 LIFO 顺序执行并清空 _disposers 中的所有清理函数。

        因为 provide() 会自动登记反注册副作用，所以 revert() 后
        本上下文注册的所有服务都会被自动移除。
        """
        while self._disposers:
            disposer = self._disposers.pop()
            await disposer()

    # ------------------------------------------------------------------
    # 提供者溯源
    # ------------------------------------------------------------------
    def get_provider_name(self, key: str) -> Optional[str]:
        """
        查询某个服务是由哪个插件提供的（沿 parent 向上递归查找）。

        :param key: 服务名。
        :return:    提供该服务的插件名；找不到返回 None。
        """
        if key in self._providers:
            return self._providers[key]
        if self.parent:
            return self.parent.get_provider_name(key)
        return None


def ensure_events(ctx: 'Context'):
    """
    确保某上下文已绑定 EventEmitter，返回之（懒加载）。

    :param ctx: 上下文对象。
    :return:    该上下文的 EventEmitter 实例。
    """
    if ctx._events is None:
        from .events import EventEmitter
        ctx._events = EventEmitter(ctx)
    return ctx._events
