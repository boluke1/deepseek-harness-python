# ============================================================================
# mycordis/core/inject.py
# @Inject / @Provide 装饰器：声明式依赖注入。
#
# 对标 DSH registry.ts 的 @Inject 装饰器：
#   · 类级别：@Inject('llm', 'sessions') 声明插件依赖
#   · 方法级别：@Inject('llm') 延迟方法调用直到服务可用
# ============================================================================

from typing import Callable


def Inject(*deps: str):
    """
    类装饰器：声明插件的 inject 依赖。

    @Inject('llm', 'sessions')
    class MyPlugin(Plugin):
        provide = ['myService']
        async def apply(self, ctx):
            llm = ctx.llm  # 已保证可用
    """
    def decorator(cls):
        existing = list(getattr(cls, 'inject', []))
        for dep in deps:
            if dep not in existing:
                existing.append(dep)
        cls.inject = existing
        return cls
    return decorator


def Provide(*services: str):
    """
    类装饰器：声明插件的 provide 服务。

    @Provide('myService')
    class MyPlugin(Plugin):
        inject = ['llm']
        async def apply(self, ctx):
            ctx.provide('myService', ...)
    """
    def decorator(cls):
        existing = list(getattr(cls, 'provide', []))
        for svc in services:
            if svc not in existing:
                existing.append(svc)
        cls.provide = existing
        return cls
    return decorator
