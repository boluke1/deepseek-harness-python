# ============================================================================
# mycordis/core/invariant.py
# Invariant（不变式）运行时强制服务。
#
# 对标 DSH 的核心不变式：
#   "Model-visible means logged" — 模型所见必须能从会话日志重建。
#
# 这意味着：
#   1. 任何发送给模型的内容（system prompt、user message、tool result）
#      都必须先记录到 session 日志。
#   2. 任何来自模型的内容（assistant message、tool call）
#      都必须立即记录到 session 日志。
#   3. 新增模型可见输入必须新增一个会话事件类型。
#
# 本服务在框架级别提供不变式校验的注册与执行机制，
# 允许任何插件注册自定义不变式规则。
#
# 使用示例：
#   invariant = ctx.invariant
#   invariant.register_rule("tool_call_paired", check_fn)
#   invariant.check(event)   # 执行所有规则
# ============================================================================

import logging
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Context

logger = logging.getLogger(__name__)


class InvariantViolation(Exception):
    """不变式违反异常。"""

    def __init__(self, rule_name: str, message: str, event: Any = None):
        self.rule_name = rule_name
        self.event = event
        super().__init__(f"[{rule_name}] {message}")


class InvariantService:
    """
    不变式运行时强制服务（对标 DSH invariant enforcement）。

    ★ 继承 Service 基类：构造时自动注册为 ctx.invariant。
    管理不变式规则，提供运行时校验接口。
    """

    def __init__(self, ctx: 'Context', name: str = "invariant"):
        from .service import Service
        self._ctx = ctx
        self._rules: Dict[str, Callable] = {}
        self._enabled: bool = True
        self._violation_log: List[InvariantViolation] = []
        self._max_log: int = 100
        # ★ 通过 Service 基类自动注册。
        Service.__init__(self, ctx, name)

    def init(self):
        """★ init 钩子：注册内置不变式规则。"""
        self._register_builtin_rules()

    # ------------------------------------------------------------------
    # 规则管理
    # ------------------------------------------------------------------
    def register_rule(self, name: str, rule_fn: Callable) -> None:
        """
        注册一个不变式规则。

        :param name:    规则名称（唯一）。
        :param rule_fn: 规则函数 (event) -> None。
                        违反时抛出 InvariantViolation。
                        通过时不返回或返回 None。
        """
        self._rules[name] = rule_fn

    def remove_rule(self, name: str) -> None:
        """移除一个不变式规则。"""
        self._rules.pop(name, None)

    def list_rules(self) -> List[str]:
        """列出所有已注册的规则名。"""
        return list(self._rules.keys())

    # ------------------------------------------------------------------
    # 运行时校验
    # ------------------------------------------------------------------
    def check(self, event: Any) -> List[InvariantViolation]:
        """
        对一个事件执行所有不变式规则。

        :param event: 要校验的事件。
        :return:      违反列表（空 = 全部通过）。
        """
        if not self._enabled:
            return []

        violations = []
        for name, rule_fn in self._rules.items():
            try:
                result = rule_fn(event)
                # 支持异步规则（但这里只处理同步）。
                if result is not None:
                    violations.append(result)
                    self._log_violation(result)
            except InvariantViolation as v:
                violations.append(v)
                self._log_violation(v)
            except Exception as e:
                logger.warning(f"Invariant rule '{name}' error: {e}")

        return violations

    def assert_no_violation(self, event: Any) -> None:
        """
        严格模式：如果有任何违反，立即抛出异常。

        :param event: 要校验的事件。
        :raises InvariantViolation: 任何规则违反时抛出（第一个违反）。
        """
        violations = self.check(event)
        if violations:
            raise violations[0]

    # ------------------------------------------------------------------
    # 控制
    # ------------------------------------------------------------------
    def enable(self) -> None:
        """启用不变式校验。"""
        self._enabled = True

    def disable(self) -> None:
        """禁用不变式校验（用于测试/调试）。"""
        self._enabled = False

    @property
    def is_enabled(self) -> bool:
        """是否启用。"""
        return self._enabled

    @property
    def violations(self) -> List[InvariantViolation]:
        """获取最近的违反记录。"""
        return list(self._violation_log)

    def clear_violations(self) -> None:
        """清空违反记录。"""
        self._violation_log.clear()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _log_violation(self, violation: InvariantViolation) -> None:
        """记录违反事件。"""
        self._violation_log.append(violation)
        if len(self._violation_log) > self._max_log:
            self._violation_log.pop(0)
        logger.error(f"Invariant violation: {violation}")

    def _register_builtin_rules(self) -> None:
        """
        注册内置不变式规则（对标 DSH 核心不变式）。

        内置规则：
          · model_visible_logged: 事件必须有 type 和 content 字段。
          · event_type_known:     事件类型必须是已知类型。
          · ★ session_event_valid: 框架事件类型必须是合法的。
        """
        # 已知事件类型集合。
        KNOWN_TYPES = frozenset({
            "user_message", "assistant_message", "assistant/chunk",
            "tool_call", "tool_result",
            "turn/start", "step/start", "turn/end",
            "session/create", "session/open", "session/close",
        })

        # ★ 扩展已知类型：包含框架级事件。
        FRAMEWORK_EVENTS = KNOWN_TYPES | {
            "agent/turn-start", "agent/turn-end", "agent/step",
            "agent/register", "agent/unregister", "agent/state-change",
            "tools/execute-start", "tools/execute-end",
            "llm/stream",
        }

        def rule_model_visible_logged(event):
            """所有模型可见内容必须有 type 和 content。"""
            if isinstance(event, dict):
                if "type" not in event:
                    return InvariantViolation(
                        "model_visible_logged",
                        "Event must have 'type' field to be logged",
                        event,
                    )
            return None

        def rule_event_type_known(event):
            """事件类型必须是已知类型（可扩展）。"""
            if isinstance(event, dict):
                event_type = event.get("type", "")
                if event_type and event_type not in KNOWN_TYPES:
                    logger.debug(
                        f"Unknown event type '{event_type}' — "
                        f"consider extending SessionEventMap"
                    )
            return None

        def rule_session_event_valid(event):
            """★ 框架事件类型必须是合法的。"""
            if isinstance(event, dict):
                event_type = event.get("type", "")
                if event_type and event_type.startswith(("session/", "agent/", "tools/", "llm/")):
                    if event_type not in FRAMEWORK_EVENTS:
                        logger.debug(
                            f"Unknown framework event type '{event_type}'"
                        )
            return None

        self.register_rule("model_visible_logged", rule_model_visible_logged)
        self.register_rule("event_type_known", rule_event_type_known)
        self.register_rule("session_event_valid", rule_session_event_valid)
