# ============================================================================
# mycordis/core/plugin.py
# 插件基类（Plugin）：所有具体插件的"标准模板"。
#
# 设计意图：
#   Plugin 定义了插件对外暴露的"契约"：
#     · inject：声明本插件依赖哪些服务（依赖注入的"入"）。
#     · provide：声明本插件将会提供哪些服务（能力输出的"出"）。
#     · apply：插件被激活时由框架调用的初始化逻辑。
#
#   Registry 依据 inject 判断"依赖是否已满足"，决定是否激活该插件；
#   依据 provide 在 apply 成功后把对应服务提升到根上下文，供其他插件发现。
#   因此，子类应遵循：provide 中声明的服务，必须在 apply 里调用 ctx.provide 实际注册。
# ============================================================================

from typing import List, Optional
from .context import Context


class Plugin:
    """
    插件基类。

    所有自定义插件必须继承此类，并设置 inject/provide 属性，实现 apply 方法。

    使用示例：
        class MyPlugin(Plugin):
            inject  = ['llm']           # 依赖 'llm' 服务
            provide = ['my_service']    # 提供 'my_service' 服务

            async def apply(self, ctx):
                llm = ctx.get('llm')    # 1. 获取依赖
                ctx.provide('my_service', ...)  # 2. 注册服务
                ctx.effect(cleanup)     # 3. 注册清理逻辑
    """

    # ------------------------------------------------------------------
    # 类变量：插件声明（子类应覆盖）
    # ------------------------------------------------------------------

    # inject：声明该插件依赖哪些服务（服务键名的字符串列表）。
    # Registry 会依据此列表检查依赖是否全部满足；
    # 若任一依赖在当前根上下文中缺失，则该插件保持 inactive，不会执行 apply。
    inject: List[str] = []

    # provide：声明该插件将会提供哪些服务（服务键名的字符串列表）。
    # 插件在 apply 中调用 ctx.provide 注册服务时，只能注册这里声明的 key；
    # Registry 会在 apply 成功后，把这里声明的服务提升到根上下文供全局使用。
    provide: List[str] = []

    # name：插件名称（可选）。
    # 通常由子类覆盖，或在实例化时由外部指定；用于日志、服务溯源等场景。
    name: Optional[str] = None

    # ------------------------------------------------------------------
    # 抽象方法：插件的激活逻辑
    # ------------------------------------------------------------------
    async def apply(self, ctx: Context) -> None:
        """
        插件的初始化 / 激活函数（抽象方法，子类必须实现）。

        框架（Registry）会在该插件的所有 inject 依赖均满足后，自动调用此方法，
        并传入该插件专属的隔离子上下文 ctx。

        在此方法中，插件应依次完成：
            1. 通过 ctx.get()          获取依赖的服务（如 ctx.get('llm')）。
            2. 通过 ctx.provide()      注册自己的服务（须与 provide 声明一致）。
            3. 通过 ctx.effect()       登记卸载时要执行的清理逻辑。

        :param ctx: 插件专属的隔离子上下文，提供 get/provide/effect 等能力。
        :raises NotImplementedError: 基类默认实现，子类必须重写。
        """
        raise NotImplementedError("Subclasses must implement apply")
