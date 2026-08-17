# ============================================================================
# core_services/sessions_plugin.py
# SessionsPlugin：事件溯源（Event-sourced）的会话日志服务。
#
# 设计意图：
#   对标 DeepSeek Harness 的 ctx.sessions 核心服务，实现"会话即单一真实来源"。
#   会话中的每条记录都是一个"不可变事件"（只追加，不修改、不删除），
#   因此整个对话历史可以随时被完整回放 / 重建，作为模型上下文的基础。
#
# 服务键：
#   'sessions' —— 对外暴露一个 SessionManager 实例。
#
# 在框架中的协作：
#   - inject = []：本插件不依赖任何服务，注册即激活。
#   - provide = ['sessions']：提供会话管理能力，供后续的 Agent Loop 等插件使用。
# ============================================================================

import logging
from typing import Dict, List, Optional

from mycordis import Context, Plugin

# 本模块的日志记录器。
logger = logging.getLogger(__name__)


class Session:
    """
    单个会话的历史容器（事件日志）。

    采用"事件溯源"思想：所有交互都以不可变事件的形式追加到 events 列表。
    """

    def __init__(self, session_id: str):
        """
        初始化一个会话。

        :param session_id: 会话的唯一标识。
        """
        # 会话唯一标识。
        self.id = session_id

        # 事件列表：按时间顺序保存本会话的所有交互事件（只追加，不可变）。
        # 每个事件形如：
        #   {
        #       "type": "user_message" | "assistant_message" | "tool_call" | "tool_result",
        #       "content": "...",      # 事件内容
        #       "meta": {...},         # 可选元数据（如工具名、参数、时间戳等）
        #   }
        self.events: List[Dict] = []

    def append(self, event_type: str, content: str, meta: Optional[Dict] = None) -> Dict:
        """
        追加一个不可变事件到本会话。

        :param event_type: 事件类型，见上方 events 注释中的取值。
        :param content:    事件内容（通常是文本）。
        :param meta:       可选元数据字典（如工具名、参数、时间戳等）。
        :return:           刚追加的事件字典。
        """
        # 构造一个事件记录。
        event = {
            "type": event_type,
            "content": content,
            "meta": meta or {},
        }
        # 只追加到事件列表（append-only，不修改历史）。
        self.events.append(event)
        logger.debug(f"[Session:{self.id}] 追加事件 {event_type}")
        return event

    def get_messages(self) -> List[Dict]:
        """
        把本会话的事件历史转换为 OpenAI 格式的消息列表。

        LLM 可直接使用该结果作为对话上下文（messages）。

        关键规则（★ 修复）：
          对于"附带工具调用元信息（meta.tool_calls）"的 assistant 事件，
          它们是 Agent 循环中的"中间态"（表示模型决定调用工具），
          其占位 content 不应注入模型历史——否则会在后续轮次中污染模型上下文
          （模型可能错误引用这类占位文本，如把 "(调用工具中)" 当成回复内容）。

        :return: 形如 [{"role": "user", "content": "..."}, ...] 的消息列表。
        """
        messages: List[Dict] = []
        for event in self.events:
            event_type = event["type"]
            content = event["content"]

            # 根据事件类型映射为 OpenAI 的 role。
            if event_type == "user_message":
                messages.append({"role": "user", "content": content})
            elif event_type == "assistant_message":
                # ★ 修复：若该 assistant 事件附带工具调用元信息，则跳过。
                # 工具调用的意图已通过后续的 tool_result(Observation) 事件体现，
                # 无需在此注入占位文本，避免污染模型上下文。
                meta = event.get("meta", {})
                if meta.get("tool_calls"):
                    continue
                messages.append({"role": "assistant", "content": content})
            elif event_type == "tool_call":
                # 工具调用：把"调用意图"以 user 角色注入（简化处理）。
                messages.append({"role": "user", "content": f"Tool call: {content}"})
            elif event_type == "tool_result":
                # 工具结果：作为 user 角色的观察（Observation）注入。
                messages.append({"role": "user", "content": f"Observation: {content}"})
            else:
                # 未知类型：以 user 角色兜底，保证不丢数据。
                messages.append({"role": "user", "content": str(content)})

        return messages


class SessionManager:
    """
    会话仓库：管理多个会话的创建、查询、列出与删除。
    """

    def __init__(self):
        """初始化会话仓库，准备一个空的会话字典。"""
        # 会话表：key 为会话 id，value 为 Session 实例。
        self._sessions: Dict[str, Session] = {}

    def create(self, session_id: Optional[str] = None) -> Session:
        """
        新建一个会话并返回它。

        :param session_id: 可选的自定义会话 id；若为 None 则自动生成。
        :return:           新建的 Session 实例。
        """
        # 若未提供 id，则生成一个自增/时间戳 id（保证不冲突）。
        if session_id is None:
            session_id = f"session_{len(self._sessions) + 1}"

        # 若 id 已存在，则复用已有会话（幂等）。
        if session_id in self._sessions:
            logger.warning(f"会话 {session_id} 已存在，复用之。")
            return self._sessions[session_id]

        # 创建会话并登记。
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

    # inject：本插件不依赖任何其他服务。
    inject = []

    # provide：本插件对外提供 'sessions' 服务（会话管理能力）。
    provide = ['sessions']

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        # 创建会话管理器实例。
        manager = SessionManager()

        # 把会话管理能力注册为 'sessions' 服务，供其他插件（如 Agent Loop）使用。
        ctx.provide('sessions', manager, self.name or 'SessionsPlugin')
        logger.info("[SessionsPlugin] 会话日志服务已就绪")
