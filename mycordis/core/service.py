# ============================================================================
# mycordis/core/service.py
# Service 基类：自动注册 + 可调用服务 + 配置合并 + init 钩子 + filter + extend。
#
# 对标 DSH cordis/src/service.ts 的全部能力：
#   · 构造函数自动调用 ctx.provide() 注册服务。
#   · 随 fiber 卸载自动反注册（通过 provide 的可逆副作用）。
#   · resolve_config()：沿祖先链遍历 intercept 原型链合并配置。
#   · ★ init 钩子：构造后自动调用（对标 DSH [Service.init]）。
#   · ★ filter：检查 isolate 标签匹配（对标 DSH [Service.filter]）。
#   · ★ extend：创建派生服务实例（对标 DSH [Service.extend]）。
#   · ★ Callable 服务：子类实现 __call__ 即可作为可调用服务（对标 DSH [Service.invoke]）。
# ============================================================================

import asyncio
import copy
import logging
from typing import Any, Optional

from .context import Context
from .symbols import symbols

logger = logging.getLogger(__name__)


class Service:
    """
    服务基类。子类构造函数调用 super().__init__(ctx, name) 后，
    自动注册为 ctx 上的服务，并随 fiber 卸载自动反注册。

    使用示例：
        class MyService(Service):
            def __init__(self, ctx):
                super().__init__(ctx, 'myService')

            def do_something(self):
                return 42

        # ctx.myService.do_something() → 42

    可调用服务示例（对标 DSH [Service.invoke]）：
        class LoggerService(Service):
            def __init__(self, ctx):
                super().__init__(ctx, 'logger')

            def __call__(self, msg):
                self.info(msg)

        # ctx.logger('hello')  ← 可调用
        # ctx.logger.info('hello')  ← 也可作为对象
    """

    provide_name: str = ""

    def __init__(self, ctx: Context, name: str = ""):
        """
        初始化并自动注册服务。

        :param ctx:  归属的上下文。
        :param name: 服务名。若为空，则使用 provide_name 或类名驼峰化。
        """
        if not name:
            name = self.provide_name or (
                type(self).__name__[0].lower() + type(self).__name__[1:]
            )
        self.ctx = ctx
        self.name = name

        # ★ 获取插件名（从 fiber 引用），用于正确的提供者溯源。
        plugin_name = name
        try:
            fiber = object.__getattribute__(ctx, '_fiber')
            if fiber is not None and hasattr(fiber, 'name'):
                plugin_name = fiber.name
        except AttributeError:
            pass

        # ★ 注册服务（对标 DSH ctx.reflect.provide）。
        ctx.provide(name, self, plugin_name)
        logger.debug(f"[Service] '{name}' auto-registered by {type(self).__name__}")

        # ★ init 钩子（对标 DSH [Service.init]）。
        # 构造后自动调用 init() 方法（如果子类定义了的话）。
        if hasattr(self, 'init') and callable(self.init):
            try:
                result = self.init()
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception as e:
                logger.error(f"[Service] '{name}' init hook failed: {e}")

    def _make_check(self):
        """
        创建可用性检查函数（对标 DSH [Service.filter]）。

        检查当前上下文的 isolate 标签是否与此服务的提供上下文匹配。
        """
        def check():
            try:
                isolate_map = object.__getattribute__(self.ctx, '_isolate_map')
                ctx_label = isolate_map.get(self.name)
                svc_label = self.ctx._scopes.get(self.name)
                if ctx_label is not None and svc_label is not None:
                    return ctx_label is svc_label
            except (AttributeError, KeyError):
                pass
            return True
        return check

    def resolve_config(self, base: dict = None, head: dict = None) -> dict:
        """
        沿祖先链遍历 intercept 原型链合并配置（对标 DSH [Service.resolveConfig]）。

        从最远的祖先到最近的上下文，依次收集本服务的 intercept 配置，
        然后合并 base → 祖先配置 → head。

        :param base: 最低优先级配置。
        :param head: 最高优先级配置。
        :return:     合并后的配置。
        """
        configs = []

        # ★ 沿祖先链遍历 intercept 配置（对标 DSH while 循环）。
        ctx = self.ctx
        visited = set()
        while ctx is not None:
            ctx_id = id(ctx)
            if ctx_id in visited:
                break
            visited.add(ctx_id)
            try:
                intercept_map = object.__getattribute__(ctx, '_intercept_map')
                if self.name in intercept_map:
                    configs.insert(0, intercept_map[self.name])
            except AttributeError:
                pass
            ctx = object.__getattribute__(ctx, 'parent') if hasattr(ctx, 'parent') else None

        if base:
            configs.insert(0, base)
        if head:
            configs.append(head)

        if not configs:
            return {}

        merged = {}
        for c in configs:
            if isinstance(c, dict):
                merged.update(c)
        return merged

    def extend(self, props: dict = None) -> 'Service':
        """
        创建派生服务实例（对标 DSH [Service.extend]）。

        基于当前实例创建新实例，可选地覆盖属性。
        如果服务是可调用的，新实例也是可调用的。

        :param props: 要覆盖的属性字典。
        :return:      新的服务实例。
        """
        new_self = copy.copy(self)
        if props:
            for key, value in props.items():
                setattr(new_self, key, value)
        return new_self

    def __repr__(self):
        return f"<{type(self).__name__} name={self.name!r}>"
