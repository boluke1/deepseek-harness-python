# mycordis/__init__.py
# 暴露核心类，方便外部导入

from .core.context import Context
from .core.plugin import Plugin, FunctionPlugin, resolve_plugin
from .core.registry import Registry
from .core.events import EventEmitter, ensure_events
from .core.service import Service
from .core.fiber import Fiber, FiberState
from .core.inject import Inject, Provide
from .core.errors import CordisError, Validation
from .core.disposable import DisposalList, EffectMeta
from .core.logger import LoggerService
# ★ 核心子系统服务
from .core.seam import SeamDefinition, SeamProvider, SeamConsumer, SeamRegistry
from .core.scope import Scope, ScopeService
from .core.identity import AgentIdentity, IdentityService
from .core.invariant import InvariantService, InvariantViolation
from .core.llm import LLMProvider, LLMAdapterService
from .core.context_service import ContextService, ContextInfo

__all__ = [
    'Context', 'Plugin', 'FunctionPlugin', 'resolve_plugin',
    'Registry', 'EventEmitter', 'ensure_events',
    'Service', 'Fiber', 'FiberState',
    'Inject', 'Provide',
    'CordisError', 'Validation',
    'DisposalList', 'EffectMeta',
    'LoggerService',
    # ★ 核心子系统
    'SeamDefinition', 'SeamProvider', 'SeamConsumer', 'SeamRegistry',
    'Scope', 'ScopeService',
    'AgentIdentity', 'IdentityService',
    'InvariantService', 'InvariantViolation',
    'LLMProvider', 'LLMAdapterService',
    'ContextService', 'ContextInfo',
]
