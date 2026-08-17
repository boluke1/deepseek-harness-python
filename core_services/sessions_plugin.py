# ============================================================================
# core_services/sessions_plugin.py
# SessionsPlugin：事件溯源（Event-sourced）的会话日志服务。
#
# 设计意图：
#   对标 DeepSeek Harness 的 ctx.sessions 核心服务，实现"会话即单一真实来源"。
#   会话中的每条记录都是一个"不可变事件"（只追加，不修改、不删除）。
#
#   ★ 接入"不变式校验器"（对标 DSH session/invariant.ts）。
#   ★ get_messages 过滤元事件/中间态，避免污染模型上下文。
# ============================================================================

import logging
from typing import Dict, List, Optional

from mycordis import Context, Plugin
from .invariant import SessionInvariantValidator, InvariantError

# 本模块的日志记录器。
logger = logging.getLogger(__name__)


class Session:
    """
    单个会话的历史容器（事件日志）。
    """

    def __init__(self, session_id: str):
        """
        初始化一个会话。

        :param session_id: 会话的唯一标识。
        """
        self.id = session_id
        self.events: List[Dict] = []
        self._validator = SessionInvariantValidator()

    def append(self, event_type: str, content: str, meta: Optional[Dict] = None) -> Dict:
        """
        追加一个不可变事件到本会话，并自动做不变式校验。

        :param event_type: 事件类型。
        :param content:    事件内容。
        :param meta:       可选元数据。
        :return:           刚追加的事件字典。
        :raises InvariantError: 违反不变式时抛出（事件不会被记录）。
        """
        event = {
            "type": event_type,
            "content": content,
            "meta": meta or {},
        }
        # 先校验，再追加。
        self._validator.validate(event)
        self.events.append(event)
        logger.debug(f"[Session:{self.id}] 追加事件 {event_type}")
        return event

    def finalize(self) -> None:
        """
        流程结束时校验。
        """
        self._validator.finalize()

    def validate(self) -> None:
        """
        全量重校验当前事件列表。
        """
        self._validator.validate_all(self.events)

    def get_messages(self) -> List[Dict]:
        """
        把本会话的事件历史转换为 OpenAI 格式的消息列表。

        过滤规则：
          · 元事件（turn/start、step/start、turn/end）跳过。
          · 中间态（带 tool_calls 的 assistant_message、assistant/chunk、tool_call）跳过。
          · tool_result 以 Observation 形式注入。

        :return: 形如 [{"role": "user", "content": "..."}, ...] 的消息列表。
        """
        # 元事件与中间态：不注入模型上下文。
        SKIP_EVENTS = {"turn/start", "step/start", "turn/end", "assistant/chunk", "tool_call"}

        messages: List[Dict] = []
        for event in self.events:
            event_type = event["type"]
            content = event["content"]

            # 跳过元事件与中间态。
            if event_type in SKIP_EVENTS:
                continue

            if event_type == "user_message":
                messages.append({"role": "user", "content": content})
            elif event_type == "assistant_message":
                meta = event.get("meta", {})
                if meta.get("tool_calls"):
                    continue   # 中间态，不注入
                messages.append({"role": "assistant", "content": content})
            elif event_type == "tool_result":
                messages.append({"role": "user", "content": f"Observation: {content}"})
            else:
                messages.append({"role": "user", "content": str(content)})

        return messages


class SessionManager:
    """
    会话仓库：管理多个会话的创建、查询、列出与删除。
    """

    def __init__(self):
        """初始化会话仓库。"""
        self._sessions: Dict[str, Session] = {}

    def create(self, session_id: Optional[str] = None) -> Session:
        """
        新建一个会话并返回它。

        :param session_id: 可选的自定义会话 id。
        :return:           新建的 Session 实例。
        """
        if session_id is None:
            session_id = f"session_{len(self._sessions) + 1}"
        if session_id in self._sessions:
            logger.warning(f"会话 {session_id} 已存在，复用之。")
            return self._sessions[session_id]
        session = Session(session_id)
        self._sessions[session_id] = session
        logger.info(f"[Sessions] 创建会话: {session_id}")
        return session

    def get(self, session_id: str) -> Session:
        """
        获取一个已存在的会话。

        :param session_id: 会话 id。
        :return:           Session 实例。
        :raises KeyError:  当该会话不存在时抛出。
        """
        if session_id not in self._sessions:
            raise KeyError(f"会话 {session_id} 不存在")
        return self._sessions[session_id]

    def list(self) -> List[str]:
        """
        列出所有会话 id。

        :return: 会话 id 列表。
        """
        return list(self._sessions.keys())

    def delete(self, session_id: str) -> None:
        """
        删除一个会话。

        :param session_id: 会话 id。
        """
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info(f"[Sessions] 删除会话: {session_id}")
        else:
            logger.warning(f"[Sessions] 尝试删除不存在的会话: {session_id}")


class SessionsPlugin(Plugin):
    """
    提供事件溯源会话日志服务（服务键 'sessions'）。
    """

    # --- 插件声明 ---
    inject = []
    provide = ['sessions']

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        manager = SessionManager()
        ctx.provide('sessions', manager, self.name or 'SessionsPlugin')
        logger.info("[SessionsPlugin] 会话日志服务已就绪")
