# ============================================================================
# mycordis/core/scope.py
# Scope（作用域）服务：按 Agent 划分的注册空间。
#
# 对标 DSH core/scope：
#   · Scope — 按 agent 划分的注册空间（agent.ctx）：
#     一个 agent 注册的内容只对该 agent 可见。
#   · 每个 scope 是一个隔离子上下文，通过 ctx.isolate() 实现。
#   · 工具、事件、服务都可以按 scope 隔离。
#
# 使用示例：
#   scope_svc = ctx.scope
#   agent_scope = scope_svc.create_scope("agent-1")
#   agent_scope.register_tool(tool)
#   # 只有 agent-1 能看到这个工具
# ============================================================================

from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Context


class Scope:
    """
    单个作用域：一个隔离的注册空间。

    对标 DSH 的 per-agent scope（agent.ctx）。
    每个 scope 有独立的标签，通过 ctx.isolate() 实现隔离。
    """

    def __init__(self, scope_id: str, label: object, parent_ctx: 'Context'):
        """
        :param scope_id:   作用域唯一标识（通常是 agent id）。
        :param label:      隔离标签（object 实例，作为 Symbol）。
        :param parent_ctx: 父上下文。
        """
        self.id = scope_id
        self.label = label
        self._ctx = parent_ctx.isolate(scope_id, label)
        self._services: Dict[str, Any] = {}

    @property
    def ctx(self) -> 'Context':
        """获取该 scope 的隔离子上下文。"""
        return self._ctx

    def register(self, key: str, value: Any) -> Callable:
        """
        在该 scope 内注册一个服务。

        :param key:   服务名。
        :param value: 服务实例。
        :return:      反注册函数。
        """
        self._services[key] = value
        # 注册到隔离上下文。
        self._ctx.provide(key, value, f"scope:{self.id}")

        def _dispose():
            self._services.pop(key, None)

        return _dispose

    def get(self, key: str) -> Any:
        """从该 scope 获取服务。"""
        return self._ctx.get(key)

    def has(self, key: str) -> bool:
        """检查该 scope 是否包含某服务。"""
        try:
            self._ctx.get(key)
            return True
        except (KeyError, AttributeError):
            return False

    def list_services(self) -> List[str]:
        """列出该 scope 内的所有服务名。"""
        return list(self._services.keys())

    def __repr__(self):
        return f"Scope({self.id!r}, services={len(self._services)})"


class ScopeService:
    """
    作用域管理服务（对标 DSH core/scope）。

    ★ 继承 Service 基类：构造时自动注册为 ctx.scope。
    管理所有 scope 的生命周期，提供 scope 的创建/查询/销毁。
    """

    def __init__(self, ctx: 'Context', name: str = "scope"):
        from .service import Service
        self._ctx = ctx
        self._scopes: Dict[str, Scope] = {}
        self._default_scope: Optional[Scope] = None
        # ★ 通过 Service 基类自动注册。
        Service.__init__(self, ctx, name)

    def init(self):
        """★ init 钩子：创建默认 scope。"""
        self._default_scope = self.create_scope("__default__")

    def create_scope(self, scope_id: str) -> Scope:
        """
        创建一个新的作用域。

        :param scope_id: 作用域唯一标识。
        :return:         新建的 Scope 实例。
        """
        if scope_id in self._scopes:
            return self._scopes[scope_id]

        label = object()
        scope = Scope(scope_id, label, self._ctx)
        self._scopes[scope_id] = scope
        return scope

    def get_scope(self, scope_id: str) -> Scope:
        """
        获取一个作用域。

        :param scope_id: 作用域标识。
        :return:         Scope 实例。
        :raises KeyError: 不存在时抛出。
        """
        if scope_id not in self._scopes:
            raise KeyError(f"Scope '{scope_id}' not found")
        return self._scopes[scope_id]

    def get_or_create(self, scope_id: str) -> Scope:
        """获取或创建作用域。"""
        if scope_id in self._scopes:
            return self._scopes[scope_id]
        return self.create_scope(scope_id)

    def destroy_scope(self, scope_id: str) -> None:
        """
        销毁一个作用域。

        ★ 增强：清理 scope 内的所有服务 + 发射事件。
        """
        if scope_id in self._scopes:
            scope = self._scopes[scope_id]
            scope._services.clear()
            del self._scopes[scope_id]
            try:
                self._ctx.emit("scope/destroyed", {"scope_id": scope_id})
            except Exception:
                pass

    def list_scopes(self) -> List[str]:
        """列出所有作用域标识。"""
        return list(self._scopes.keys())

    @property
    def default(self) -> Optional[Scope]:
        """获取默认作用域。"""
        return self._default_scope
