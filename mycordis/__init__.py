# mycordis/__init__.py
# 暴露核心类，方便外部导入

from .core.context import Context
from .core.plugin import Plugin
from .core.registry import Registry
from .core.events import EventEmitter, ensure_events

__all__ = ['Context', 'Plugin', 'Registry', 'EventEmitter', 'ensure_events']
