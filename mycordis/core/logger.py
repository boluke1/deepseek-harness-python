# ============================================================================
# mycordis/core/logger.py
# Logger 服务（对标 DSH logger.ts）。
#
# 提供结构化的日志服务，自动注册为 ctx.logger。
#   · 支持 info/warn/error/debug 四个级别
#   · 支持子 logger（带前缀）
#   · 支持日志级别控制
#   · 作为内置服务自动注册到上下文
# ============================================================================

import logging
from typing import Optional

from .context import Context
from .service import Service


class LoggerService(Service):
    """
    日志服务（对标 DSH LoggerService）。

    自动注册为 ctx.logger，提供结构化日志能力。
    每个 fiber 上下文可获得独立的子 logger（带前缀）。

    使用示例：
        ctx.logger.info('plugin activated')
        child = ctx.logger.child('MyPlugin')
        child.info('hello')  # → [MyPlugin] hello
    """

    def __init__(self, ctx: Context, name: str = "logger",
                 inner: Optional[logging.Logger] = None,
                 prefix: str = ""):
        self._inner = inner or logging.getLogger('cordis')
        self._prefix = prefix
        super().__init__(ctx, name)

    def info(self, *args, **kwargs) -> None:
        """记录 INFO 级别日志。"""
        self._inner.info(self._fmt(*args), **kwargs)

    def warn(self, *args, **kwargs) -> None:
        """记录 WARNING 级别日志。"""
        self._inner.warning(self._fmt(*args), **kwargs)

    def error(self, *args, **kwargs) -> None:
        """记录 ERROR 级别日志。"""
        self._inner.error(self._fmt(*args), **kwargs)

    def debug(self, *args, **kwargs) -> None:
        """记录 DEBUG 级别日志。"""
        self._inner.debug(self._fmt(*args), **kwargs)

    def child(self, prefix: str) -> 'LoggerService':
        """
        创建带前缀的子 logger（对标 DSH LoggerService.child）。

        :param prefix: 前缀字符串。
        :return:       新的 LoggerService 实例（不自动注册到 ctx）。
        """
        new_prefix = f"{self._prefix}{prefix}" if self._prefix else prefix
        # 创建子 logger 但不重复注册到 ctx
        child = object.__new__(LoggerService)
        child.ctx = self.ctx
        child.name = self.name
        child._inner = self._inner
        child._prefix = new_prefix
        return child

    def set_level(self, level: int) -> None:
        """设置日志级别。"""
        self._inner.setLevel(level)

    def _fmt(self, *args) -> str:
        """格式化日志消息，添加前缀。"""
        msg = " ".join(str(a) for a in args)
        if self._prefix:
            return f"[{self._prefix}] {msg}"
        return msg
