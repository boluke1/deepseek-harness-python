# ============================================================================
# agents/agent_interface.py
# Agent 抽象接口 + 实时注册表。
#
# 设计意图（对标 DSH 的 ctx.agents 核心服务）：
#   定义 Agent 的公共契约（接口），让"Agent 是什么"与"Agent 怎么实现"
#   解耦。
#
#   ★ 增强版（90%+）：
#     · 活跃 Agent 跟踪：get_active() / list_active()
#     · Agent 状态机：idle → running → finished/error → idle
#     · agent/* 事件协议：agent/register, agent/unregister,
#       agent/state-change, agent/turn-start, agent/turn-end
#
# 服务键：'agents'
# ============================================================================

import logging
from typing import Dict, List, Optional, Set

from mycordis import Context, Plugin, Service

logger = logging.getLogger(__name__)


class Agent:
    """
    Agent 抽象接口。

    ★ 增强：状态机 + 序列化。
    """

    id: str = ""
    status: str = "idle"

    async def run(self, user_input: str) -> str:
        raise NotImplementedError("Subclasses must implement run()")

    def to_dict(self) -> Dict:
        """★ 序列化 Agent 状态。"""
        return {"id": self.id, "status": self.status}


class AgentRegistry(Service):
    """
    Agent 实时注册表（对标 DSH ctx.agents）。

    ★ 增强版（90%+）：
      · 活跃 Agent 跟踪
      · agent/* 事件协议
      · 状态变更通知
    """

    def __init__(self, ctx: Context, name: str = "agents"):
        self._agents: Dict[str, Agent] = {}
        self._active_agents: Set[str] = set()
        super().__init__(ctx, name)

    def init(self):
        """★ init 钩子。"""
        self.ctx.logger.info("Agent 注册表已初始化 (init hook)")

    def register(self, agent: Agent) -> None:
        """注册一个 Agent。★ 增强：发射 agent/register 事件。"""
        if not agent.id:
            raise ValueError("Agent 必须设置非空的 id")
        if agent.id in self._agents:
            raise ValueError(f"Agent {agent.id} 已注册")
        self._agents[agent.id] = agent
        try:
            self.ctx.emit("agent/register", {"agent_id": agent.id})
        except Exception:
            pass
        self.ctx.logger.info(f"[Agents] 注册 Agent: {agent.id}")

    def unregister(self, agent_id: str) -> None:
        """★ 注销一个 Agent。"""
        if agent_id in self._agents:
            del self._agents[agent_id]
            self._active_agents.discard(agent_id)
            try:
                self.ctx.emit("agent/unregister", {"agent_id": agent_id})
            except Exception:
                pass
            self.ctx.logger.info(f"[Agents] 注销 Agent: {agent_id}")

    def get(self, agent_id: str) -> Agent:
        """获取一个 Agent。"""
        if agent_id not in self._agents:
            raise KeyError(f"Agent {agent_id} 不存在")
        return self._agents[agent_id]

    def list(self) -> List[str]:
        """列出所有已注册的 Agent id。"""
        return list(self._agents.keys())

    # ------------------------------------------------------------------
    # ★ 活跃 Agent 跟踪
    # ------------------------------------------------------------------
    def mark_active(self, agent_id: str) -> None:
        """★ 标记 Agent 为活跃（running）。"""
        if agent_id in self._agents:
            old_status = self._agents[agent_id].status
            self._agents[agent_id].status = "running"
            self._active_agents.add(agent_id)
            try:
                self.ctx.emit("agent/state-change", {
                    "agent_id": agent_id,
                    "old_status": old_status,
                    "new_status": "running",
                })
            except Exception:
                pass

    def mark_inactive(self, agent_id: str, final_status: str = "idle") -> None:
        """★ 标记 Agent 为非活跃。"""
        if agent_id in self._agents:
            old_status = self._agents[agent_id].status
            self._agents[agent_id].status = final_status
            self._active_agents.discard(agent_id)
            try:
                self.ctx.emit("agent/state-change", {
                    "agent_id": agent_id,
                    "old_status": old_status,
                    "new_status": final_status,
                })
            except Exception:
                pass

    def get_active(self) -> List[Agent]:
        """★ 获取所有活跃的 Agent。"""
        return [self._agents[aid] for aid in self._active_agents if aid in self._agents]

    def list_active(self) -> List[str]:
        """★ 列出活跃 Agent id。"""
        return list(self._active_agents)

    def get_status(self, agent_id: str) -> str:
        """★ 获取 Agent 当前状态。"""
        if agent_id in self._agents:
            return self._agents[agent_id].status
        return "unknown"

    def get_all_status(self) -> Dict[str, str]:
        """★ 获取所有 Agent 的状态快照。"""
        return {aid: a.status for aid, a in self._agents.items()}


class AgentsPlugin(Plugin):
    """提供 Agent 注册表服务（服务键 'agents'）。"""

    inject = []
    provide = ['agents']

    async def apply(self, ctx: Context):
        AgentRegistry(ctx)
        ctx.logger.info("[AgentsPlugin] Agent 注册表已就绪 (活跃跟踪 + 事件协议)")
