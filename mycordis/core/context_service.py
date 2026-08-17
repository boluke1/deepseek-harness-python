# ============================================================================
# mycordis/core/context_service.py
# Context 管理服务：上下文生命周期管理 + 诊断。
#
# 对标 DSH core/context：
#   · 管理 Context 树的生命周期。
#   · 提供上下文树诊断（dump/inspect）。
#   · 跟踪活跃上下文数量。
#   · 提供上下文查找与遍历。
#
# 使用示例：
#   ctx_mgmt = ctx.contextService
#   tree = ctx_mgmt.get_context_tree()     # 获取上下文树结构
#   info = ctx_mgmt.inspect(some_ctx)       # 诊断某上下文
#   ctx_mgmt.dump()                         # 打印完整上下文树
# ============================================================================

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Context

logger = logging.getLogger(__name__)


class ContextInfo:
    """上下文诊断信息。"""

    def __init__(self, ctx: 'Context'):
        self.ctx = ctx
        self.services: List[str] = []
        self.providers: Dict[str, str] = {}
        self.fiber_name: Optional[str] = None
        self.parent_ref: Optional['Context'] = None
        self.children: List['ContextInfo'] = []
        self.isolate_keys: List[str] = []
        self.mixin_sources: List[str] = []
        self.accessor_names: List[str] = []
        self.disposer_count: int = 0

        self._collect()

    def _collect(self):
        """从 Context 收集诊断信息。"""
        try:
            store = self.ctx._store
            self.services = list(store.keys())
        except Exception:
            pass

        try:
            providers = self.ctx._providers
            self.providers = dict(providers)
        except Exception:
            pass

        try:
            fiber = self.ctx.fiber
            if fiber is not None:
                self.fiber_name = fiber.name
        except Exception:
            pass

        try:
            self.parent_ref = self.ctx.parent
        except Exception:
            pass

        try:
            isolate_map = self.ctx._isolate_map
            self.isolate_keys = list(isolate_map.keys())
        except Exception:
            pass

        try:
            mixins = self.ctx._mixins
            self.mixin_sources = list(mixins.keys())
        except Exception:
            pass

        try:
            accessors = self.ctx._accessors
            self.accessor_names = list(accessors.keys())
        except Exception:
            pass

        try:
            self.disposer_count = len(self.ctx._disposers)
        except Exception:
            pass

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。"""
        return {
            "services": self.services,
            "providers": self.providers,
            "fiber_name": self.fiber_name,
            "isolate_keys": self.isolate_keys,
            "mixin_sources": self.mixin_sources,
            "accessor_names": self.accessor_names,
            "disposer_count": self.disposer_count,
            "children_count": len(self.children),
        }


class ContextService:
    """
    上下文管理服务（对标 DSH core/context）。

    ★ 继承 Service 基类：构造时自动注册为 ctx.contextService。
    提供上下文树诊断、生命周期管理。
    """

    def __init__(self, ctx: 'Context', name: str = "contextService"):
        from .service import Service
        self._root_ctx = ctx
        self._tracked_contexts: List['Context'] = [ctx]
        # ★ 通过 Service 基类自动注册。
        Service.__init__(self, ctx, name)

    def init(self):
        """★ init 钩子。"""
        pass

    def track(self, ctx: 'Context') -> None:
        """
        跟踪一个新创建的上下文。

        :param ctx: 要跟踪的上下文。
        """
        if ctx not in self._tracked_contexts:
            self._tracked_contexts.append(ctx)

    def untrack(self, ctx: 'Context') -> None:
        """
        取消跟踪一个上下文。

        :param ctx: 要取消跟踪的上下文。
        """
        if ctx in self._tracked_contexts:
            self._tracked_contexts.remove(ctx)

    @property
    def active_count(self) -> int:
        """活跃上下文数量。"""
        return len(self._tracked_contexts)

    def inspect(self, ctx: 'Context' = None) -> ContextInfo:
        """
        诊断一个上下文。

        :param ctx: 要诊断的上下文（默认为根上下文）。
        :return:    ContextInfo 实例。
        """
        if ctx is None:
            ctx = self._root_ctx
        return ContextInfo(ctx)

    def get_context_tree(self) -> Dict[str, Any]:
        """
        获取完整上下文树结构。

        :return: 嵌套字典表示的上下文树。
        """
        return self._build_tree(self._root_ctx)

    def _build_tree(self, ctx: 'Context') -> Dict[str, Any]:
        """递归构建上下文树。"""
        info = ContextInfo(ctx)
        node = {
            "fiber": info.fiber_name or "root",
            "services": info.services,
            "children": [],
        }

        # 查找子上下文。
        for tracked_ctx in self._tracked_contexts:
            try:
                if tracked_ctx.parent is ctx:
                    child_node = self._build_tree(tracked_ctx)
                    node["children"].append(child_node)
            except Exception:
                pass

        return node

    def dump(self, ctx: 'Context' = None, indent: int = 0) -> str:
        """
        打印完整上下文树（诊断用）。

        :param ctx:    起始上下文（默认根）。
        :param indent: 缩进级别。
        :return:       格式化字符串。
        """
        if ctx is None:
            ctx = self._root_ctx

        info = ContextInfo(ctx)
        prefix = "  " * indent
        lines = []

        fiber_name = info.fiber_name or "root"
        lines.append(f"{prefix}[{fiber_name}] services={info.services}")

        if info.isolate_keys:
            lines.append(f"{prefix}  isolate: {info.isolate_keys}")
        if info.mixin_sources:
            lines.append(f"{prefix}  mixins: {info.mixin_sources}")
        if info.accessor_names:
            lines.append(f"{prefix}  accessors: {info.accessor_names}")
        if info.disposer_count > 0:
            lines.append(f"{prefix}  disposers: {info.disposer_count}")

        # 递归子上下文。
        for tracked_ctx in self._tracked_contexts:
            try:
                if tracked_ctx.parent is ctx:
                    child_dump = self.dump(tracked_ctx, indent + 1)
                    lines.append(child_dump)
            except Exception:
                pass

        return "\n".join(lines)

    def find_by_service(self, service_name: str) -> List['Context']:
        """
        查找拥有某服务的所有上下文。

        :param service_name: 服务名。
        :return:             拥有该服务的上下文列表。
        """
        result = []
        for ctx in self._tracked_contexts:
            try:
                if service_name in ctx._store:
                    result.append(ctx)
            except Exception:
                pass
        return result

    def find_by_fiber(self, fiber_name: str) -> Optional['Context']:
        """
        查找属于某 fiber 的上下文。

        :param fiber_name: Fiber 名称。
        :return:           匹配的上下文或 None。
        """
        for ctx in self._tracked_contexts:
            try:
                fiber = ctx.fiber
                if fiber is not None and fiber.name == fiber_name:
                    return ctx
            except Exception:
                pass
        return None
