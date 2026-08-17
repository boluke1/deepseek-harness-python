# ============================================================================
# core_services/sessions_plugin.py
# SessionsPlugin：事件溯源（Event-sourced）的会话日志服务。
#
# 设计意图：
#   对标 DeepSeek Harness 的 ctx.sessions 核心服务，实现"会话即单一真实来源"。
#   会话中的每条记录都是一个"不可变事件"（只追加，不修改、不删除）。
#
#   ★ 增强版（对标 90%+）：
#     · SessionEvent 类型系统：带序列号、时间戳、类型安全的事件对象
#     · Session 生命周期事件：create / open / close
#     · 与核心 InvariantService 桥接：每次 append 自动触发 ctx.invariant.check()
#     · 事件投影（projection）：按类型过滤、按时间范围查询
#     · get_messages 过滤元事件/中间态，避免污染模型上下文
#
#   ★ SessionManager 继承 Service 基类，自动注册 + init 钩子。
# ============================================================================

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

from mycordis import Context, Plugin, Service
from .invariant import SessionInvariantValidator, InvariantError

logger = logging.getLogger(__name__)


# ============================================================================
# ★ SessionEvent 类型系统（对标 DSH SessionEventMap）
# ============================================================================

class SessionEvent:
    """
    ★ 不可变会话事件对象（对标 DSH SessionEvent）。

    每个事件带有序列号（单调递增）、时间戳、类型、内容、元数据。
    序列号用于精确重建事件流顺序。
    """

    __slots__ = ('seq', 'type', 'content', 'meta', 'timestamp')

    def __init__(self, seq: int, event_type: str, content: str,
                 meta: Optional[Dict] = None, timestamp: float = None):
        self.seq = seq
        self.type = event_type
        self.content = content
        self.meta = meta or {}
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> Dict:
        """转换为字典（兼容旧接口）。"""
        return {
            "type": self.type,
            "content": self.content,
            "meta": self.meta,
            "seq": self.seq,
            "timestamp": self.timestamp,
        }

    def __repr__(self):
        return f"SessionEvent(seq={self.seq}, type={self.type!r}, content={self.content[:30]!r})"


# ★ 已知事件类型集合（对标 DSH SessionEventMap）。
SESSION_EVENT_TYPES: Set[str] = {
    "user_message", "assistant_message", "assistant/chunk",
    "tool_call", "tool_result",
    "turn/start", "step/start", "turn/end",
    "session/create", "session/open", "session/close",
}


class Session:
    """
    单个会话的历史容器（事件日志）。

    ★ 增强版：SessionEvent 类型系统 + 序列号 + 生命周期 + 投影查询。
    """

    def __init__(self, session_id: str, ctx: Optional[Context] = None):
        self.id = session_id
        self.events: List[SessionEvent] = []
        self._seq_counter: int = 0
        self._validator = SessionInvariantValidator()
        self._ctx = ctx
        self._created_at: float = time.time()
        self._closed: bool = False

    def append(self, event_type: str, content: str, meta: Optional[Dict] = None) -> Dict:
        """
        追加一个不可变事件到本会话，并自动做不变式校验。

        ★ 增强：
          · 创建 SessionEvent 对象（带序列号 + 时间戳）
          · 与核心 InvariantService 桥接（如果 ctx.invariant 可用）
          · 先校验，再追加

        :param event_type: 事件类型。
        :param content:    事件内容。
        :param meta:       可选元数据。
        :return:           刚追加的事件字典。
        :raises InvariantError: 违反不变式时抛出。
        """
        self._seq_counter += 1
        event = SessionEvent(self._seq_counter, event_type, content, meta)

        event_dict = event.to_dict()

        # ★ 会话级不变式校验（状态机校验）。
        self._validator.validate(event_dict)

        # ★ 与核心 InvariantService 桥接（model-visible = logged）。
        if self._ctx is not None:
            try:
                inv = self._ctx.get("invariant")
                inv.check(event_dict)
            except (KeyError, AttributeError):
                pass

        self.events.append(event)
        logger.debug(f"[Session:{self.id}] seq={event.seq} {event_type}")
        return event_dict

    def close(self) -> None:
        """
        ★ 关闭会话：执行 finalize 校验 + 发射 session/close 事件。
        """
        if not self._closed:
            self._validator.finalize()
            self._closed = True
            self.append("session/close", "", {"session_id": self.id})

    def validate(self) -> None:
        """全量重校验当前事件列表。"""
        self._validator.validate_all([e.to_dict() for e in self.events])

    def get_messages(self) -> List[Dict]:
        """
        把本会话的事件历史转换为 OpenAI 格式的消息列表。

        过滤规则：
          · 元事件（turn/start、step/start、turn/end）跳过。
          · 中间态（带 tool_calls 的 assistant_message、assistant/chunk、tool_call）跳过。
          · 生命周期事件（session/*）跳过。
          · tool_result 以 Observation 形式注入。
        """
        SKIP_EVENTS = {
            "turn/start", "step/start", "turn/end",
            "assistant/chunk", "tool_call",
            "session/create", "session/open", "session/close",
        }

        messages: List[Dict] = []
        for event in self.events:
            if event.type in SKIP_EVENTS:
                continue

            if event.type == "user_message":
                messages.append({"role": "user", "content": event.content})
            elif event.type == "assistant_message":
                if event.meta.get("tool_calls"):
                    continue
                messages.append({"role": "assistant", "content": event.content})
            elif event.type == "tool_result":
                messages.append({"role": "user", "content": f"Observation: {event.content}"})
            else:
                messages.append({"role": "user", "content": str(event.content)})

        return messages

    # ------------------------------------------------------------------
    # ★ 投影查询（对标 DSH session-query）
    # ------------------------------------------------------------------
    def filter_by_type(self, *event_types: str) -> List[SessionEvent]:
        """按类型过滤事件。"""
        type_set = set(event_types)
        return [e for e in self.events if e.type in type_set]

    def filter_by_seq_range(self, start_seq: int, end_seq: int) -> List[SessionEvent]:
        """按序列号范围过滤（含两端）。"""
        return [e for e in self.events if start_seq <= e.seq <= end_seq]

    def filter_by_time_range(self, start_ts: float, end_ts: float) -> List[SessionEvent]:
        """按时间戳范围过滤（含两端）。"""
        return [e for e in self.events if start_ts <= e.timestamp <= end_ts]

    @property
    def event_count(self) -> int:
        """事件总数。"""
        return len(self.events)

    @property
    def last_seq(self) -> int:
        """最新序列号。"""
        return self._seq_counter

    @property
    def is_closed(self) -> bool:
        """会话是否已关闭。"""
        return self._closed

    def get_summary(self) -> Dict:
        """★ 获取会话摘要（对标 DSH session summary）。"""
        type_counts: Dict[str, int] = {}
        for e in self.events:
            type_counts[e.type] = type_counts.get(e.type, 0) + 1
        return {
            "id": self.id,
            "event_count": self.event_count,
            "last_seq": self.last_seq,
            "is_closed": self._closed,
            "created_at": self._created_at,
            "type_counts": type_counts,
        }


class SessionManager(Service):
    """
    会话仓库（对标 DSH Service 基类自动注册）。

    ★ 增强版：
      · 创建/获取会话时自动发射生命周期事件
      · 提供全局查询接口（按 ID、按状态）
    """

    def __init__(self, ctx: Context, name: str = "sessions"):
        self._sessions: Dict[str, Session] = {}
        super().__init__(ctx, name)

    def init(self):
        """★ init 钩子。"""
        self.ctx.logger.info("会话日志服务已初始化 (init hook)")

    def create(self, session_id: Optional[str] = None) -> Session:
        """新建一个会话并返回它。自动发射 session/create 事件。"""
        if session_id is None:
            session_id = f"session_{len(self._sessions) + 1}"
        if session_id in self._sessions:
            self.ctx.logger.warn(f"会话 {session_id} 已存在，复用之。")
            return self._sessions[session_id]
        session = Session(session_id, ctx=self.ctx)
        self._sessions[session_id] = session
        # ★ 发射生命周期事件。
        session.append("session/create", "", {"session_id": session_id})
        self.ctx.logger.info(f"[Sessions] 创建会话: {session_id}")
        return session

    def get(self, session_id: str) -> Session:
        """获取一个已存在的会话。"""
        if session_id not in self._sessions:
            raise KeyError(f"会话 {session_id} 不存在")
        return self._sessions[session_id]

    def list(self) -> List[str]:
        """列出所有会话 id。"""
        return list(self._sessions.keys())

    def delete(self, session_id: str) -> None:
        """删除一个会话（先关闭再删除）。"""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            if not session.is_closed:
                try:
                    session.close()
                except InvariantError:
                    pass
            del self._sessions[session_id]
            self.ctx.logger.info(f"[Sessions] 删除会话: {session_id}")

    def get_all_summaries(self) -> List[Dict]:
        """★ 获取所有会话的摘要。"""
        return [s.get_summary() for s in self._sessions.values()]


class SessionsPlugin(Plugin):
    """
    提供事件溯源会话日志服务（服务键 'sessions'）。
    """

    inject = []
    provide = ['sessions']

    async def apply(self, ctx: Context):
        SessionManager(ctx)
        ctx.logger.info("[SessionsPlugin] 会话日志服务已就绪 (SessionEvent + 生命周期 + 投影)")
