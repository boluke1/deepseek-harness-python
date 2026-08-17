# ============================================================================
# core_services/tools_plugin.py
# ToolsPlugin：作用域工具注册表 + 守卫执行管道。
#
# 设计意图：
#   对标 DeepSeek Harness 的 ctx.tools 核心服务。
#   工具是 Agent 与外部世界交互的"能力接口"。本服务提供：
#     1. 工具注册表：register/list/get，支持按作用域（scope）隔离。
#     2. 守卫执行管道：执行工具前经过 pre-execute 守卫（校验/过滤），
#        执行后经过 post-execute 守卫（净化/截断），保证安全与可控。
#     3. 工具 Schema 生成：为 systemPrompt 提供标准 JSON Schema。
#
# 服务键：
#   'tools' —— 对外暴露一个 ToolRegistry 实例。
# ============================================================================

import asyncio
import functools   # 用于 partial 绑定同步工具函数的参数（fix: run_in_executor 不支持 **args）
import inspect
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from mycordis import Context, Plugin

# 本模块的日志记录器。
logger = logging.getLogger(__name__)


class Tool:
    """
    工具定义：封装一个可被 Agent 调用的函数及其元信息。
    """

    def __init__(self,
                 name: str,
                 fn: Callable,
                 description: str = "",
                 parameters: Optional[Dict] = None,
                 timeout: float = 10.0):
        """
        初始化一个工具。

        :param name:        工具名（Agent 调用时使用的标识）。
        :param fn:          工具的实际执行函数（可为同步或异步）。
        :param description: 工具描述（返回导向，说明返回什么）。
        :param parameters:  JSON Schema 形式的参数定义（可选）。
        :param timeout:     执行超时（秒）。
        """
        # 工具名。
        self.name = name
        # 执行函数。
        self.fn = fn
        # 工具描述。
        self.description = description
        # 参数 Schema。
        self.parameters = parameters or {}
        # 超时时间。
        self.timeout = timeout

    def schema(self) -> Dict:
        """
        生成该工具的 JSON Schema（供 systemPrompt 组装，也符合 function calling 格式）。

        :return: 形如 {"name":..., "description":..., "parameters":...} 的字典。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """
    作用域工具注册表 + 守卫执行管道。
    """

    def __init__(self):
        """初始化工具注册表。"""
        # 工具表：key 为工具名，value 为 Tool 实例。
        # 本实现将工具统一注册到全局（scope 可后续扩展为按 Agent 隔离）。
        self._tools: Dict[str, Tool] = {}

        # 执行守卫：前置守卫（pre）与后置守卫（post）列表。
        # pre-guard : (name, args) -> (name, args)，可校验/修改参数。
        # post-guard: (name, result) -> result，可净化/修改结果。
        self._pre_guards: List[Callable] = []
        self._post_guards: List[Callable] = []

    # ------------------------------------------------------------------
    # 注册 / 查询
    # ------------------------------------------------------------------
    def register(self, tool: Tool, scope: str = "default") -> None:
        """
        注册一个工具。

        :param tool:  Tool 实例。
        :param scope: 作用域（预留，当前统一注册；可用于按 Agent 隔离）。
        """
        # 防重复：同名工具已存在则报错，避免歧义。
        if tool.name in self._tools:
            raise ValueError(f"工具 {tool.name} 已注册")
        self._tools[tool.name] = tool
        logger.info(f"[Tools] 注册工具: {tool.name} (scope={scope})")

    def register_fn(self,
                    name: str,
                    fn: Callable,
                    description: str = "",
                    parameters: Optional[Dict] = None,
                    scope: str = "default") -> None:
        """
        便捷方法：直接用函数注册工具（自动包装成 Tool）。

        :param name:        工具名。
        :param fn:          执行函数。
        :param description: 描述。
        :param parameters:  参数 Schema。
        :param scope:       作用域。
        """
        self.register(Tool(name, fn, description, parameters), scope)

    def get(self, name: str) -> Tool:
        """
        获取一个工具。

        :param name: 工具名。
        :return:     Tool 实例。
        :raises KeyError: 当工具不存在时抛出。
        """
        if name not in self._tools:
            raise KeyError(f"工具 {name} 不存在")
        return self._tools[name]

    def list_tools(self, scope: str = "default") -> List[Tool]:
        """
        列出所有工具（当前不区分 scope）。

        :param scope: 作用域（预留）。
        :return:      Tool 列表。
        """
        return list(self._tools.values())

    def list_schemas(self, scope: str = "default") -> List[Dict]:
        """
        列出所有工具的 JSON Schema（供 systemPrompt 组装）。

        :param scope: 作用域（预留）。
        :return:      Schema 字典列表。
        """
        return [tool.schema() for tool in self._tools.values()]

    # ------------------------------------------------------------------
    # 守卫
    # ------------------------------------------------------------------
    def add_pre_guard(self, guard: Callable) -> None:
        """
        添加前置守卫：执行工具前调用，可校验/修改参数。

        :param guard: 函数 (name, args) -> (name, args)。
        """
        self._pre_guards.append(guard)

    def add_post_guard(self, guard: Callable) -> None:
        """
        添加后置守卫：执行工具后调用，可净化/修改结果。

        :param guard: 函数 (name, result) -> result。
        """
        self._post_guards.append(guard)

    # ------------------------------------------------------------------
    # 执行管道
    # ------------------------------------------------------------------
    async def execute(self, name: str, args: Dict, scope: str = "default") -> Any:
        """
        守卫执行管道：执行一个工具，经过前置守卫 -> 执行 -> 后置守卫。

        :param name:  工具名。
        :param args:  工具参数（字典）。
        :param scope: 作用域（预留）。
        :return:      工具执行结果。
        :raises KeyError: 工具不存在时抛出。
        :raises Exception: 执行超时或工具内部错误时抛出。
        """
        # 1) 查找工具。
        tool = self.get(name)

        # 2) 前置守卫：校验/修改参数。
        for guard in self._pre_guards:
            result = guard(name, args)
            if asyncio.iscoroutine(result):
                result = await result
            if result is not None:
                name, args = result

        # 3) 执行工具（带超时）。
        logger.info(f"[Tools] 执行工具: {name}({args})")
        try:
            # 判断工具函数是同步还是异步。
            if inspect.iscoroutinefunction(tool.fn):
                # 异步函数：用 asyncio.wait_for 包超时。
                result = await asyncio.wait_for(tool.fn(**args), timeout=tool.timeout)
            else:
                # 同步函数：在线程池中运行，避免阻塞事件循环。
                # ★ 用 functools.partial 把 (fn, **args) 打包成无参调用，
                #   再交给 run_in_executor（它不支持直接接收关键字参数）。
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, functools.partial(tool.fn, **args)),
                    timeout=tool.timeout,
                )
        except asyncio.TimeoutError:
            raise TimeoutError(f"工具 {name} 执行超时（>{tool.timeout}s）")

        # 4) 后置守卫：净化/修改结果。
        for guard in self._post_guards:
            gres = guard(name, result)
            if asyncio.iscoroutine(gres):
                gres = await gres
            if gres is not None:
                result = gres

        logger.info(f"[Tools] 工具 {name} 完成")
        return result


class ToolsPlugin(Plugin):
    """
    提供工具注册表与守卫执行管道服务（服务键 'tools'）。
    """

    # --- 插件声明 ---

    # inject：本插件不依赖任何其他服务。
    inject = []

    # provide：本插件对外提供 'tools' 服务。
    provide = ['tools']

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        # 创建工具注册表。
        registry = ToolRegistry()

        # 注册为 'tools' 服务。
        ctx.provide('tools', registry, self.name or 'ToolsPlugin')
        logger.info("[ToolsPlugin] 工具服务已就绪")
