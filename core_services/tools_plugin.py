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
#   ★ ToolRegistry 继承 Service 基类，自动注册 + init 钩子。
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

from mycordis import Context, Plugin, Service

# 本模块的日志记录器（模块级备用）。
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
        self.name = name
        self.fn = fn
        self.description = description
        self.parameters = parameters or {}
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


class ToolRegistry(Service):
    """
    作用域工具注册表 + 守卫执行管道（对标 DSH Service 基类）。

    ★ 增强版（90%+）：
      · scope-aware 工具过滤：注册时指定 scope，查询时按 scope 过滤
      · 执行事件发射：执行前后发射 tools/execute-start、tools/execute-end
      · 结果截断：post-guard 自动截断超长结果
    """

    def __init__(self, ctx: Context, name: str = "tools"):
        self._tools: Dict[str, Tool] = {}
        self._tool_scopes: Dict[str, str] = {}
        self._pre_guards: List[Callable] = []
        self._post_guards: List[Callable] = []
        self._max_result_length: int = 10000
        super().__init__(ctx, name)

    def init(self):
        """★ init 钩子。"""
        self.ctx.logger.info("工具注册表已初始化 (init hook)")

    # ------------------------------------------------------------------
    # 注册 / 查询
    # ------------------------------------------------------------------
    def register(self, tool: Tool, scope: str = "default") -> None:
        """
        注册一个工具。

        ★ 增强：记录工具所属 scope，用于 scope-aware 过滤。
        """
        if tool.name in self._tools:
            raise ValueError(f"工具 {tool.name} 已注册")
        self._tools[tool.name] = tool
        self._tool_scopes[tool.name] = scope
        self.ctx.logger.info(f"[Tools] 注册工具: {tool.name} (scope={scope})")

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

    def list_tools(self, scope: str = None) -> List[Tool]:
        """
        列出工具。

        ★ 增强：如果指定 scope，只返回该 scope 内的工具 + global 工具。
        """
        if scope is None:
            return list(self._tools.values())
        return [
            tool for name, tool in self._tools.items()
            if self._tool_scopes.get(name) in (scope, "default", "global")
        ]

    def list_schemas(self, scope: str = None) -> List[Dict]:
        """
        列出工具的 JSON Schema。

        ★ 增强：支持 scope 过滤。
        """
        return [tool.schema() for tool in self.list_tools(scope)]

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
        守卫执行管道。

        ★ 增强：
          · 执行前发射 tools/execute-start 事件
          · 执行后发射 tools/execute-end 事件
          · 自动截断超长结果
        """
        tool = self.get(name)

        # ★ 发射执行开始事件。
        try:
            self.ctx.emit("tools/execute-start", {"tool": name, "args": args})
        except Exception:
            pass

        # 前置守卫。
        for guard in self._pre_guards:
            result = guard(name, args)
            if asyncio.iscoroutine(result):
                result = await result
            if result is not None:
                name, args = result

        # 执行工具（带超时）。
        self.ctx.logger.info(f"[Tools] 执行工具: {name}({args})")
        try:
            if inspect.iscoroutinefunction(tool.fn):
                result = await asyncio.wait_for(tool.fn(**args), timeout=tool.timeout)
            else:
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, functools.partial(tool.fn, **args)),
                    timeout=tool.timeout,
                )
        except asyncio.TimeoutError:
            raise TimeoutError(f"工具 {name} 执行超时（>{tool.timeout}s）")

        # 后置守卫。
        for guard in self._post_guards:
            gres = guard(name, result)
            if asyncio.iscoroutine(gres):
                gres = await gres
            if gres is not None:
                result = gres

        # ★ 自动截断超长结果。
        if isinstance(result, str) and len(result) > self._max_result_length:
            result = result[:self._max_result_length] + f"\n... [truncated, {len(result)} chars]"

        # ★ 发射执行结束事件。
        try:
            self.ctx.emit("tools/execute-end", {"tool": name, "result_len": len(str(result))})
        except Exception:
            pass

        self.ctx.logger.info(f"[Tools] 工具 {name} 完成")
        return result

    def set_max_result_length(self, max_len: int) -> None:
        """★ 设置结果最大长度。"""
        self._max_result_length = max_len

    def get_tool_scopes(self) -> Dict[str, str]:
        """★ 获取所有工具的 scope 映射。"""
        return dict(self._tool_scopes)


class ToolsPlugin(Plugin):
    """
    提供工具注册表与守卫执行管道服务（服务键 'tools'）。
    """

    # --- 插件声明 ---
    inject = []
    provide = ['tools']

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        # ★ ToolRegistry 继承 Service，构造时自动注册为 ctx.tools。
        ToolRegistry(ctx)
        ctx.logger.info("[ToolsPlugin] 工具服务已就绪")
