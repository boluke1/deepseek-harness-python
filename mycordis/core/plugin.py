# ============================================================================
# mycordis/core/plugin.py
# 插件基类（Plugin）+ 多形态支持 + Config Schema。
#
# 对标 DSH registry.ts 的 Plugin 类型：
#   · 类插件：继承 Plugin，实现 apply()
#   · 函数插件：async def my_plugin(ctx, config): ...
#   · 对象插件：{"apply": async fn}
#   · Config Schema：插件可声明配置验证器
# ============================================================================

from typing import Any, Dict, List, Optional
from .context import Context


class Plugin:
    """
    插件基类。所有自定义插件必须继承此类。

    使用示例：
        class MyPlugin(Plugin):
            inject  = ['llm']           # 依赖 'llm' 服务
            provide = ['my_service']    # 提供 'my_service' 服务

            async def apply(self, ctx):
                llm = ctx.llm           # 1. 获取依赖
                ctx.provide('my_service', ...)  # 2. 注册服务
                ctx.effect(cleanup)       # 3. 注册清理逻辑
    """

    # inject：声明该插件依赖哪些服务。
    inject: List[str] = []

    # provide：声明该插件将会提供哪些服务。
    provide: List[str] = []

    # name：插件名称（可选）。
    name: Optional[str] = None

    # Config：可选的配置验证 Schema 类。
    Config: Optional[Any] = None

    async def apply(self, ctx: Context) -> None:
        """
        插件的初始化 / 激活函数（子类必须实现）。

        :param ctx: 插件专属的隔离子上下文。
        """
        raise NotImplementedError("Subclasses must implement apply")

    def validate_config(self, config: dict) -> dict:
        """
        用 Config Schema 验证配置。

        :param config: 原始配置。
        :return:       验证后的配置。
        """
        if self.Config and hasattr(self.Config, 'validate'):
            return self.Config.validate(config)
        return config


class FunctionPlugin:
    """
    函数插件包装器（对标 DSH Plugin.Function）。

    把普通 async 函数包装成插件接口：
        async def my_plugin(ctx, config):
            ctx.provide('myService', ...)

        plugin = FunctionPlugin(my_plugin, name='myPlugin')
    """

    def __init__(self, callback, name: str = "", inject: List[str] = None, provide: List[str] = None):
        self._callback = callback
        self.name = name or getattr(callback, '__name__', 'function_plugin')
        self.inject = inject or []
        self.provide = provide or []

    async def apply(self, ctx: Context) -> None:
        await self._callback(ctx, {})


def resolve_plugin(plugin: Any) -> Optional[Plugin]:
    """
    解析插件形态，返回统一的 Plugin 接口（对标 DSH RegistryService.resolve）。

    支持三种形态：
      1. Plugin 子类实例 → 直接返回
      2. async 函数 → 包装为 FunctionPlugin
      3. 带 apply 方法的对象 → 适配为 Plugin

    :param plugin: 任意形态的插件。
    :return:       统一的 Plugin 接口，或 None（无效插件）。
    """
    if isinstance(plugin, Plugin):
        return plugin

    if callable(plugin) and not isinstance(plugin, type):
        return FunctionPlugin(plugin)

    if hasattr(plugin, 'apply') and callable(plugin.apply):
        class _ObjectPlugin(Plugin):
            pass
        op = _ObjectPlugin()
        op.apply = plugin.apply
        op.name = getattr(plugin, 'name', None) or 'object_plugin'
        op.inject = getattr(plugin, 'inject', [])
        op.provide = getattr(plugin, 'provide', [])
        return op

    return None
