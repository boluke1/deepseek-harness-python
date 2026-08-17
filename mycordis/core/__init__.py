# mycordis/core/__init__.py
# 标记为子包

from .events import EventEmitter, ensure_events
from .service import Service
from .fiber import Fiber, FiberState
from .inject import Inject, Provide
from .symbols import symbols
from .errors import CordisError, Validation
from .disposable import DisposalList, EffectMeta
from .logger import LoggerService
# ★ 核心子系统服务
from .seam import SeamDefinition, SeamProvider, SeamConsumer, SeamRegistry
from .scope import Scope, ScopeService
from .identity import AgentIdentity, IdentityService
from .invariant import InvariantService, InvariantViolation
from .llm import LLMProvider, LLMAdapterService
from .context_service import ContextService, ContextInfo
