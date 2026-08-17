# ============================================================================
# agents/agent_interface.py
# Agent 抽象接口 + 实时注册表。
#
# 设计意图（对标 DSH 的 ctx.agents 核心服务）：
#   定义 Agent 的公共契约（接口），让"Agent 是什么"与"Agent 怎么实现"
#   解耦。任何 Agent 实现（如 ReAct 循环、研究型循环）只要满足本接口，
#   就能被注册到 AgentRegistry，并可在运行期替换。
#
# 服务键：
#   'agents' —— 对外暴露一个 AgentRegistry 实例。
# ============================================================================

import logging
from typing import Dict, List, Optional

from mycordis import Context, Plugin

# 本模块的日志记录器。
logger = logging.getLogger(__name__)


class Agent:
    """
    Agent 抽象接口：所有 Agent 实现的公共契约。

    子类必须实现 run() 方法。id 与 status 用于注册表跟踪。
    """

    # Agent 的唯一标识（由实现类设置，如 'react-loop'）。
    id: str = ""

    # Agent 状态：'idle' | 'running' | 'finished' | 'error'。
    status: str = "idle"

    async def run(self, user_input: str) -> str:
        """
        处理一轮用户输入，返回最终回复文本。

        :param user_input: 用户输入。
        :return:           Agent 的最终回复。
        :raises NotImplementedError: 抽象方法，子类必须实现。
        """
        raise NotImplementedError("Subclasses must implement run()")


class AgentRegistry:
    """
    Agent 实时注册表：管理所有已注册的 Agent 实例。
    """

    def __init__(self):
        """初始化 Agent 注册表。"""
        # Agent 表：key 为 agent id，value 为 Agent 实例。
        self._agents: Dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        """
        注册一个 Agent。

        :param agent: Agent 实例。
        :raises ValueError: 当 agent.id 为空或已存在时抛出。
        """
        if not agent.id:
            raise ValueError("Agent 必须设置非空的 id")
        if agent.id in self._agents:
            raise ValueError(f"Agent {agent.id} 已注册")
        self._agents[agent.id] = agent
        logger.info(f"[Agents] 注册 Agent: {agent.id}")

    def get(self, agent_id: str) -> Agent:
        """
        获取一个 Agent。

        :param agent_id: Agent id。
        :return:         Agent 实例。
        :raises KeyError: 当不存在时抛出。
        """
        if agent_id not in self._agents:
            raise KeyError(f"Agent {agent_id} 不存在")
        return self._agents[agent_id]

    def list(self) -> List[str]:
        """
        列出所有已注册的 Agent id。

        :return: Agent id 列表。
        """
        return list(self._agents.keys())


class AgentsPlugin(Plugin):
    """
    提供 Agent 注册表服务（服务键 'agents'）。
    """

    # --- 插件声明 ---

    # inject：本插件不依赖其他服务（仅提供注册表容器）。
    inject = []

    # provide：对外提供 'agents' 服务。
    provide = ['agents']

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        # 创建 Agent 注册表。
        registry = AgentRegistry()

        # 注册为 'agents' 服务。
        ctx.provide('agents', registry, self.name or 'AgentsPlugin')
        logger.info("[AgentsPlugin] Agent 注册表已就绪")
