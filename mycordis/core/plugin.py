from typing import List, Optional
from .context import Context


class Plugin:
    """
    插件基类。
    所有自定义插件必须继承此类，并设置 inject/provide 属性，实现 apply 方法。
    """
    # inject: 声明该插件依赖哪些服务（字符串列表）。
    # 如果依赖不满足，注册表不会激活此插件。
    inject: List[str] = []

    # provide: 声明该插件将会提供哪些服务（字符串列表）。
    # 插件在 apply 中调用 ctx.provide 时，必须只提供这里声明的 key。
    provide: List[str] = []

    # name: 插件名称（可选，子类可覆盖）
    name: Optional[str] = None

    async def apply(self, ctx: Context) -> None:
        """
        插件的初始化/激活函数。
        框架会在依赖满足后调用此方法。
        在此方法中，插件应：
        1. 通过 ctx.get() 获取依赖的服务。
        2. 通过 ctx.provide() 注册自己的服务。
        3. 通过 ctx.effect() 注册清理逻辑。
        """
        raise NotImplementedError("Subclasses must implement apply")