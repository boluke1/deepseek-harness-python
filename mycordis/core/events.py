# ============================================================================
# mycordis/core/events.py
# 类型化事件系统（Typed Events）：插件间协作的核心通信机制。
#
# 设计意图：
#   对标 DeepSeek Harness / Cordis 的事件分发模式，为插件提供
#   解耦的"事件总线"，并复用 Context 的 effect 机制实现"可逆副作用"——
#   插件卸载（revert）时，其注册的事件监听器会被自动移除，不留残留。
#
#   ★ 六种模式（对齐 DSH events.ts）：
#     · on / once  监听注册：常规监听 + 只触发一次
#     · emit       观察者模式：顺序触发，不等待返回值（广播通知）。
#     · waterfall  中间件模式：顺序执行，每个监听器可改写传给下一个的值。
#     · parallel   并行扇出：所有监听器同时执行。
#     · serial     顺序执行，等待每个完成，并收集所有返回值。
#     · bail       短路求值：遇真值立即停止并返回该值。
#
# 与 Context 的关系：
#   每个 Context 持有一个 EventEmitter（懒加载绑定到 ctx._events），
#   通过 ctx.effect() 把"移除监听器"登记为可逆副作用。
# ============================================================================

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

# 本模块的日志记录器。
logger = logging.getLogger(__name__)


class EventEmitter:
    """
    事件发射器：管理某上下文上的所有事件监听器，提供六种分发模式。

    一个 Context 对应一个 EventEmitter。监听器通过 on() 注册，
    并通过 ctx.effect() 登记为可逆副作用，保证插件卸载时自动移除。
    """

    def __init__(self, ctx: 'Context'):
        """
        初始化事件发射器。

        :param ctx: 归属的上下文（Context），用于把"移除监听器"登记为可逆副作用。
        """
        self.ctx = ctx

        # 监听器表：key 为事件名，value 为监听器函数列表（按注册顺序保存）。
        self._listeners: Dict[str, List[Callable]] = {}

    # ------------------------------------------------------------------
    # 监听器注册 / 移除
    # ------------------------------------------------------------------
    def on(self, name: str, listener: Callable) -> None:
        """
        注册一个事件监听器（观察者）。

        同时调用 ctx.effect() 登记"移除该监听器"的可逆副作用，
        从而在插件卸载（ctx.revert()）时自动注销，避免残留。

        :param name:     事件名，如 'agent/step'。
        :param listener: 监听器函数（可为同步或异步函数）。
        """
        # 懒初始化事件名对应的监听器列表。
        if name not in self._listeners:
            self._listeners[name] = []

        # 追加监听器。
        self._listeners[name].append(listener)

        # ★ 可逆副作用：登记一个清理函数，revert 时从监听器表中移除它。
        async def _remove():
            # 从该事件名的监听器列表中移除指定监听器。
            self._listeners[name].remove(listener)
            # 若列表已空，删除该事件名的键，保持表整洁。
            if not self._listeners[name]:
                del self._listeners[name]

        # 把清理函数交给上下文，纳入副作用栈（LIFO）。
        self.ctx.effect(_remove)

    def once(self, name: str, listener: Callable) -> None:
        """
        注册一个"只触发一次"的监听器。

        触发一次后，该监听器会被自动移除，不再响应后续事件。
        同样通过 ctx.effect() 登记可逆副作用，插件卸载时自动清理。

        :param name:     事件名。
        :param listener: 监听器函数（可为同步或异步）。
        """
        # 内部包装：触发时调用原监听器，然后自动移除自身。
        def _once_wrapper(payload):
            # 先移除自己，避免在异步执行期间被再次触发。
            self.off(name, _once_wrapper)
            return listener(payload)

        # 用普通 on() 注册包装器（也会登记可逆副作用）。
        self.on(name, _once_wrapper)

    def off(self, name: str, listener: Callable) -> None:
        """
        手动移除一个监听器（非必要，因为 on() 已通过 effect 自动管理）。

        :param name:     事件名。
        :param listener: 要移除的监听器函数。
        """
        if name in self._listeners and listener in self._listeners[name]:
            self._listeners[name].remove(listener)
            if not self._listeners[name]:
                del self._listeners[name]

    def _get_listeners(self, name: str) -> List[Callable]:
        """获取某事件的所有监听器（不存在则返回空列表）。"""
        return self._listeners.get(name, [])

    # ------------------------------------------------------------------
    # 分发模式
    # ------------------------------------------------------------------
    def emit(self, name: str, payload: Any = None) -> None:
        """
        观察者模式：按注册顺序触发所有监听器，不等待返回值。

        :param name:    事件名。
        :param payload: 传给监听器的数据。
        """
        for listener in self._get_listeners(name):
            try:
                # 调用监听器（同步或异步均可，此处不 await，即 fire-and-forget）。
                result = listener(payload)
                # 若是异步监听器（返回协程），用 create_task 调度执行，
                # 避免阻塞当前流程，也不因未 await 而产生警告。
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                # 单个监听器异常不应阻断其他监听器。
                logger.exception(f"Event '{name}' listener raised: {e}")

    async def waterfall(self, name: str, value: Any) -> Any:
        """
        中间件模式：顺序执行，每个监听器可改写 value 传给下一个。

        常用于数据流转 / 拦截（如 agent/request 修改请求、tools/* 修改参数）。
        监听器可返回新值（None 表示不修改），最终返回处理后的 value。

        :param name:  事件名。
        :param value: 初始值，会依次经过所有监听器加工。
        :return:      所有监听器处理后的最终值。
        """
        result = value
        for listener in self._get_listeners(name):
            # 同步或异步监听器统一 await 处理（同步函数返回值可直接 await 兼容）。
            res = listener(result)
            if asyncio.iscoroutine(res):
                res = await res
            # 只有返回非 None 才覆盖 value，否则保留上一值。
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
        # 收集所有监听器的协程（同步函数会被直接调用并包装）。
        coros = []

        async def _wrap(fn):
            res = fn(payload)
            if asyncio.iscoroutine(res):
                await res
            return res

        for listener in listeners:
            coros.append(_wrap(listener))

        # 并行执行；用 return_exceptions=True 防止单个失败中断其余。
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

        对标 DSH 的 bail 语义——常用于"谁先能处理就交给谁"的场景，
        如权限校验、服务解析短路、多候选处理器竞争。

        :param name:  事件名。
        :param value: 初始值（可选），会作为第一个监听器的输入。
        :return:      第一个返回真值的监听器的返回值；
                     若所有监听器都返回假值，则返回最后一个值（或初始 value）。
        """
        result = value
        for listener in self._get_listeners(name):
            res = listener(result)
            if asyncio.iscoroutine(res):
                res = await res
            # 遇真值立即短路返回。
            if res:
                return res
            # 否则更新 result，继续下一个监听器。
            result = res
        return result


def ensure_events(ctx: 'Context') -> EventEmitter:
    """
    确保某上下文已绑定 EventEmitter，返回之（懒加载）。

    首次调用时创建实例并绑定到 ctx._events；后续直接复用。

    :param ctx: 上下文对象。
    :return:    该上下文的 EventEmitter 实例。
    """
    if ctx._events is None:
        ctx._events = EventEmitter(ctx)
    return ctx._events
