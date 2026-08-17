# ============================================================================
# core_services/invariant.py
# 会话日志不变式校验器（对标 DeepSeek Harness 的 session/invariant.ts）。
#
# 设计意图：
#   DSH 用"状态机 + 序列号"在线校验事件流，强制保证"模型可见即已记录"。
#
#   支持的事件类型：
#     user_message / assistant_message / assistant/chunk / tool_call / tool_result
#     turn/start / step/start / turn/end     （Turn/Step 状态机，元事件）
#
#   不变式：
#     I1：tool_result 必须对应一个未闭合的 tool_call。
#     I2：tool_call 必须最终被 tool_result 闭合（流程结束不允许残留）。
#     I3：tool_result 之后应跟随 assistant_message。
#     I4：user_message 之后应跟 assistant_message 或 tool_call。
#     I5：turn/start 后必须被 turn/end 闭合（turn 序号递增）。
#     I6：step 必须嵌套在打开的 turn 内；turn 内 step 序号递增。
#
#   ★ 元事件（turn/start、step/start、turn/end、assistant/chunk）不参与 I3/I4。
# ============================================================================

from typing import Dict, List, Optional


class InvariantError(Exception):
    """会话日志违反不变式时抛出的异常。"""
    pass


class SessionInvariantValidator:
    """
    会话日志不变式校验器：维护一个状态机，增量校验每次追加的事件。
    """

    # 元事件：流程标记或中间态，不参与 I3/I4 消息顺序约束。
    META_EVENTS = {"turn/start", "step/start", "turn/end", "assistant/chunk"}

    def __init__(self):
        """初始化校验器状态。"""
        self._pending_calls: Dict[str, Dict] = {}
        self._last_type: str = None
        self._open_turn: Optional[int] = None
        self._next_turn: int = 1
        self._next_step: int = 1

    def validate(self, event: Dict) -> None:
        """
        校验一个新追加的事件（增量）。

        :param event: 事件字典。
        :raises InvariantError: 违反任一不变式时抛出。
        """
        event_type = event["type"]
        meta = event.get("meta", {}) or {}

        # ---- Turn/Step 状态机校验（I5/I6）----
        if event_type == "turn/start":
            turn = meta.get("turn")
            if self._open_turn is not None:
                raise InvariantError(
                    f"[I5] turn/start 时已有打开的 turn={self._open_turn}（未闭合）"
                )
            if turn is not None and turn != self._next_turn:
                raise InvariantError(
                    f"[I5] turn/start 期望 turn={self._next_turn}，实际得到 {turn}"
                )
            self._open_turn = turn if turn is not None else self._next_turn
            self._next_step = 1

        elif event_type == "step/start":
            if self._open_turn is None:
                raise InvariantError("[I6] step/start 但当前没有打开的 turn")
            step = meta.get("step")
            if step is not None and step != self._next_step:
                raise InvariantError(
                    f"[I6] step/start 期望 step={self._next_step}，实际得到 {step}"
                )
            self._next_step += 1

        elif event_type == "turn/end":
            if self._open_turn is None:
                raise InvariantError("[I5] turn/end 但当前没有打开的 turn")
            end_turn = meta.get("turn")
            if end_turn is not None and end_turn != self._open_turn:
                raise InvariantError(
                    f"[I5] turn/end 期望关闭 turn={self._open_turn}，实际得到 {end_turn}"
                )
            if self._pending_calls:
                raise InvariantError(
                    f"[I2] turn/end 但仍有未闭合的 tool_call: {list(self._pending_calls.keys())}"
                )
            self._open_turn = None
            self._next_turn += 1

        # ---- I1：tool_result 必须对应一个未闭合的 tool_call ----
        if event_type == "tool_result":
            call_id = meta.get("callId")
            if call_id is not None:
                if call_id not in self._pending_calls:
                    raise InvariantError(
                        f"[I1] tool_result 没有对应的 tool_call (callId={call_id})"
                    )
                del self._pending_calls[call_id]
            else:
                if not self._pending_calls:
                    raise InvariantError(
                        "[I1] tool_result 没有对应的前置 tool_call（无未闭合调用）"
                    )
                oldest_key = next(iter(self._pending_calls))
                del self._pending_calls[oldest_key]

        # ---- I2：tool_call 登记为未闭合 ----
        elif event_type == "tool_call":
            call_id = meta.get("callId", f"call_{len(self._pending_calls)}_{id(event)}")
            if call_id in self._pending_calls:
                raise InvariantError(
                    f"[I2] tool_call 重复（callId={call_id} 已存在未闭合调用）"
                )
            self._pending_calls[call_id] = event

        # ---- I3/I4：消息顺序约束（跳过元事件）----
        if event_type not in self.META_EVENTS:
            if self._last_type == "tool_result" and event_type not in (
                "assistant_message", "tool_call"
            ):
                raise InvariantError(
                    f"[I3] tool_result 之后不应直接出现 {event_type}（应跟 assistant_message）"
                )
            if self._last_type == "user_message" and event_type not in (
                "assistant_message", "tool_call"
            ):
                raise InvariantError(
                    f"[I4] user_message 之后不应直接出现 {event_type}（应跟 assistant_message 或 tool_call）"
                )

        # 更新最近事件类型。
        self._last_type = event_type

    def finalize(self) -> None:
        """
        流程结束时校验。

        :raises InvariantError: 存在未闭合的 tool_call 或未关闭的 turn 时抛出。
        """
        if self._pending_calls:
            raise InvariantError(
                f"[I2] 流程结束但仍有未闭合的 tool_call: {list(self._pending_calls.keys())}"
            )
        if self._open_turn is not None:
            raise InvariantError(
                f"[I5] 流程结束时仍有未关闭的 turn={self._open_turn}"
            )

    def validate_all(self, events: List[Dict]) -> None:
        """
        全量校验一组事件（从头开始重建状态机）。

        :param events: 事件列表（按时间顺序）。
        :raises InvariantError: 违反任一不变式时抛出。
        """
        self._pending_calls = {}
        self._last_type = None
        self._open_turn = None
        self._next_turn = 1
        self._next_step = 1
        for event in events:
            self.validate(event)
