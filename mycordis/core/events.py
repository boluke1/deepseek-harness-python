# ============================================================================
# mycordis/core/events.py
# 类型化事件系统（Typed Events）：插件间协作的核心通信机制。
#
# ★ 对标 DSH events.ts 的完整能力：
#   · 7 种分发模式：on/once/emit/waterfall/parallel/serial/bail
#   · 可逆副作用：监听器通过 ctx.effect() 自动清理
#   · filter：上下文级监听器过滤（对标 DSH Context[symbols.filter]）
#   · waterfall 错误传播：带 error 载体的 waterfall（对标 DSH）
#   · mixin 集成：事件方法可通过 ctx.mixin() 暴露到 ctx 上
# ============================================================================

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventEmitter:
    """
    事件发射器：管理某上下文上的所有事件监听器，提供七种分发模式。

    一个 Context 对应一个 EventEmitter。监听器通过 on() 注册，
    并通过 ctx.effect() 登记为可逆副作用，保证插件卸载时自动移除。
    """

    def __init__(self, ctx: 'Context'):
        self.ctx = ctx
        self._listeners: Dict[str, List[Callable]] = {}
        self._filter: Optional[Callable] = None

    # ------------------------------------------------------------------
    # Filter（对标 DSH Context[symbols.filter]）
    # ------------------------------------------------------------------
    def set_filter(self, filter_fn: Callable) -> None:
        """
        设置监听器过滤器。dispatch 时只触发通过过滤的监听器。

        :param filter_fn: (listener, event_name) -> bool
        """
        self._filter = filter_fn

    # ------------------------------------------------------------------
    # 监听器注册 / 移除
    # ------------------------------------------------------------------
    def on(self, name: str, listener: Callable) -> None:
        """
        注册一个事件监听器（自动通过 ctx.effect 登记可逆副作用）。

        :param name:     事件名，如 'agent/step'。
        :param listener: 监听器函数（可为同步或异步函数）。
        """
        if name not in self._listeners:
            self._listeners[name] = []
        self._listeners[name].append(listener)

        async def _remove():
            if name in self._listeners and listener in self._listeners[name]:
                self._listeners[name].remove(listener)
                if not self._listeners[name]:
                    del self._listeners[name]

        self.ctx.effect(_remove)

    def once(self, name: str, listener: Callable) -> None:
        """
        注册一个"只触发一次"的监听器。
        触发一次后自动移除。
        """
        def _once_wrapper(payload):
            self.off(name, _once_wrapper)
            return listener(payload)
        self.on(name, _once_wrapper)

    def off(self, name: str, listener: Callable) -> None:
        """手动移除一个监听器。"""
        if name in self._listeners and listener in self._listeners[name]:
            self._listeners[name].remove(listener)
            if not self._listeners[name]:
                del self._listeners[name]

    def _get_listeners(self, name: str) -> List[Callable]:
        """获取某事件的所有监听器（应用 filter）。"""
        listeners = self._listeners.get(name, [])
        if self._filter is not None:
            listeners = [l for l in listeners if self._filter(l, name)]
        return listeners

    # ------------------------------------------------------------------
    # 分发模式
    # ------------------------------------------------------------------
    def emit(self, name: str, payload: Any = None) -> None:
        """
        观察者模式：按注册顺序触发所有监听器，不等待返回值。

        支持多参数传递（对标 DSH events.emit 的 ...args）：
          emit(name)              — 无参数
          emit(name, payload)     — 单参数
          emit(name, (a, b, c))   — 内部事件以 tuple 形式传递多参数

        :param name:    事件名。
        :param payload: 传给监听器的数据。
        """
        for listener in self._get_listeners(name):
            try:
                # ★ 内部事件支持多参数传递。
                if isinstance(payload, tuple) and name.startswith('internal/'):
                    result = listener(*payload)
                else:
                    result = listener(payload)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                logger.exception(f"Event '{name}' listener raised: {e}")

    async def waterfall(self, name: str, value: Any, *extra_args) -> Any:
        """
        中间件模式：顺序执行，每个监听器可改写 value 传给下一个。

        支持额外参数（对标 DSH waterfall 的 error 载体等）：
          waterfall(name, value)              — 基础用法
          waterfall(name, ctx, prop, error, fallback) — 带错误传播

        :param name:       事件名。
        :param value:      初始值。
        :param extra_args: 额外参数（传递给监听器）。
        :return:           所有监听器处理后的最终值。
        """
        result = value
        for listener in self._get_listeners(name):
            try:
                if extra_args:
                    res = listener(result, *extra_args)
                else:
                    res = listener(result)
            except TypeError:
                # 监听器不接受额外参数，回退到单参数调用。
                res = listener(result)

            if asyncio.iscoroutine(res):
                res = await res
            if res is not None:
                result = res
        return result

    async def parallel(self, name: str, payload: Any = None) -> None:
        """
        并行扇出：用 asyncio.gather 同时执行所有监听器。

        :param name:    事件名。
        :param payload: 传给所有监听器的数据。
        """
        listeners = self._get_listeners(name)
        coros = []

        async def _wrap(fn):
            res = fn(payload)
            if asyncio.iscoroutine(res):
                await res
            return res

        for listener in listeners:
            coros.append(_wrap(listener))

        await asyncio.gather(*coros, return_exceptions=True)

    async def serial(self, name: str, payload: Any = None) -> List[Any]:
        """
        串行执行：顺序等待每个监听器完成，并收集所有返回值。

        :param name:    事件名。
        :param payload: 传给所有监听器的数据。
        :return:        所有监听器返回值的列表（按注册顺序）。
        """
        results: List[Any] = []
        for listener in self._get_listeners(name):
            res = listener(payload)
            if asyncio.iscoroutine(res):
                res = await res
            results.append(res)
        return results

    async def bail(self, name: str, value: Any = None) -> Any:
        """
        短路求值模式：依次调用监听器，遇真值（truthy）即停止并返回该值。

        :param name:  事件名。
        :param value: 初始值（可选）。
        :return:      第一个返回真值的监听器的返回值。
        """
        result = value
        for listener in self._get_listeners(name):
            res = listener(result)
            if asyncio.iscoroutine(res):
                res = await res
            if res:
                return res
            result = res
        return result


def ensure_events(ctx: 'Context') -> EventEmitter:
    """
    确保某上下文已绑定 EventEmitter，返回之（懒加载）。

    :param ctx: 上下文对象。
    :return:    该上下文的 EventEmitter 实例。
    """
    if ctx._events is None:
        ctx._events = EventEmitter(ctx)
    return ctx._events
