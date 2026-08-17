# ============================================================================
# agents/agent_loop_plugin.py
# AgentLoopPlugin：基于 Function Calling 的 Agent 循环（ReAct 的实现）。
#
# 设计意图（对标 DSH 的 ctx.agentLoop 核心服务）：
#   agent-loop 只是 Agent 接口的"默认实现"，本身也是一个插件。
#   它依赖四个核心服务，通过"思考-调用工具-观察"循环完成任务：
#     1. 组装系统提示（ctx.systemPrompt）
#     2. 从会话日志渲染模型历史（ctx.sessions）
#     3. 调用 LLM，若返回 tool_calls 则执行工具（ctx.tools），
#        并把工具结果回灌给模型，直到模型给出最终答复。
#
# 会话记忆：
#   ReActAgent 内部维护一个"默认会话 id"（default_session_id），
#   首次 run 时自动创建一次，后续轮次复用同一会话，从而实现跨轮对话记忆。
#
# 死循环防护（★ 修复）：
#   动作去重仅当"完全相同的工具+参数连续重复 ≥2 次"才判定为死循环。
#   这样既防止模型无限重复调用，又不会误伤"Observation 之后再次调用同一工具"
#   这类合法的多步推理行为。
#
# 服务键：
#   'agentLoop' —— 对外暴露当前 Agent（本实现为 ReAct 循环）实例。
#
# 依赖：
#   inject = ['llm', 'sessions', 'systemPrompt', 'tools']
# ============================================================================

import json
import logging
from typing import Dict, List, Optional

from mycordis import Context, Plugin
from mycordis.core.events import ensure_events
from .agent_interface import Agent

# 本模块的日志记录器。
logger = logging.getLogger(__name__)


class ReActAgent(Agent):
    """
    基于 Function Calling 的 Agent 循环实现。

    通过"调用 LLM → 若请求工具则执行 → 把观察回灌"的循环，
    逐步逼近最终答案。具备 max_steps 上限与动作去重防死循环，
    并维护一个默认会话以实现跨轮记忆。
    """

    def __init__(self,
                 llm,
                 sessions,
                 system_prompt,
                 tools,
                 events,
                 agent_id: str = "react-loop",
                 max_steps: int = 5,
                 default_session_id: Optional[str] = None):
        """
        初始化 ReAct Agent。

        :param llm:           'llm' 服务（LLMClient，支持 chat(messages, tools)）。
        :param sessions:      'sessions' 服务（SessionManager）。
        :param system_prompt: 'systemPrompt' 服务（SystemPromptBuilder）。
        :param tools:         'tools' 服务（ToolRegistry）。
        :param events:        事件发射器（EventEmitter）。
        :param agent_id:      Agent 唯一标识。
        :param max_steps:     最大循环步数（防死循环）。
        :param default_session_id: 默认会话 id；若为 None，首次 run 时自动创建一次并复用。
        """
        self.llm = llm
        self.sessions = sessions
        self.system_prompt = system_prompt
        self.tools = tools
        self.events = events
        self.id = agent_id
        self.status = "idle"
        self.max_steps = max_steps
        # 默认会话 id：用于跨轮对话记忆。首次 run 时若为 None 则创建并保存。
        self.default_session_id = default_session_id

    async def run(self, user_input: str, session_id: Optional[str] = None) -> str:
        """
        处理一轮用户输入，返回最终回复。

        :param user_input: 用户输入。
        :param session_id: 可选会话 id；若为 None，则复用默认会话（实现跨轮记忆）。
        :return:           Agent 最终回复文本。
        """
        self.status = "running"

        # ---- 1. 获取会话（复用默认会话，实现跨轮记忆）----
        if session_id is None:
            # 未显式指定：复用默认会话。若默认会话尚未创建，则首次创建并保存。
            session_id = self.default_session_id
            if session_id is None:
                session = self.sessions.create()
                self.default_session_id = session.id   # 记住，后续轮次复用
            else:
                # 默认会话已存在，直接获取。
                session = self.sessions.get(session_id)
        else:
            # 显式指定了会话 id：直接获取（不存在则创建）。
            try:
                session = self.sessions.get(session_id)
            except KeyError:
                session = self.sessions.create(session_id)

        # 记录用户消息事件。
        session.append("user_message", user_input)
        # 广播事件：agent/start。
        self.events.emit("agent/start", {"agent": self.id, "session": session.id})

        # ---- 2. 组装系统提示 + 工具 Schema ----
        system_prompt = self.system_prompt.build()
        tool_schemas = self.tools.list_schemas()
        tools_param = tool_schemas or None   # 无工具时不传 tools

        # ---- 3. 构建消息历史（系统提示 + 会话渲染）----
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(session.get_messages())

        # ---- 4. 循环执行（最多 max_steps 步）----
        # ★ 死循环检测：记录"本轮（本次 run）内最近一次动作"。
        #   每次 run 都重置，只针对当前这轮用户输入内的连续重复做检测。
        recent_action = None

        for step in range(1, self.max_steps + 1):
            # 广播事件：agent/step。
            self.events.emit("agent/step", {"step": step, "session": session.id})
            logger.info(f"[Agent] Step {step}/{self.max_steps}")

            # a. 调用 LLM（支持工具调用）。
            result = await self.llm.chat(messages, tools=tools_param)
            content = result["content"]
            tool_calls = result["tool_calls"]

            # b. 若模型给出最终答复（无工具调用）。
            if not tool_calls:
                final_answer = content or "已完成。"
                # 记录最终 assistant 回复事件（仅在此时追加一次，避免重复）。
                session.append("assistant_message", final_answer)
                self.status = "finished"
                self.events.emit("agent/end", {"agent": self.id, "result": final_answer})
                return final_answer

            # c. 模型请求调用工具：记录工具调用元信息 + 逐条执行。
            # 占位 content 用中性的 "[tool call]"（不会被注入模型历史，见 Session.get_messages）。
            session.append("assistant_message", content or "[tool call]",
                           {"tool_calls": tool_calls})

            observation_parts = []
            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = tc["arguments"]

                # ★ 死循环防护：仅当"与最近一次动作完全相同"才判定为死循环。
                #   放宽判定，避免误伤"Observation 后再次调用同一工具"的合法推理。
                action_key = f"{tool_name}({json.dumps(tool_args, sort_keys=True)})"
                if action_key == recent_action:
                    self.status = "error"
                    msg = "检测到连续重复动作，可能陷入循环，已停止。请换一种表述。"
                    session.append("assistant_message", msg)
                    self.events.emit("agent/end", {"agent": self.id, "result": msg})
                    return msg
                recent_action = action_key

                # 执行工具（经过守卫管道）。
                try:
                    obs = await self.tools.execute(tool_name, tool_args)
                except Exception as e:
                    obs = f"工具 {tool_name} 执行失败: {e}"
                logger.info(f"[Agent] 工具结果: {obs}")

                # 记录工具结果事件。
                session.append("tool_result", str(obs), {"tool": tool_name})
                observation_parts.append(f"工具 {tool_name} 的结果: {obs}")

            # d. 把工具结果作为"观察"回灌给模型。
            observation_text = "\n".join(observation_parts)
            messages.append({"role": "user", "content": f"Observation: {observation_text}"})

        # ---- 5. 达到 max_steps 仍未出最终答复 ----
        self.status = "error"
        msg = f"已达到最大步数（{self.max_steps}），任务未完成。"
        session.append("assistant_message", msg)
        self.events.emit("agent/end", {"agent": self.id, "result": msg})
        return msg


class AgentLoopPlugin(Plugin):
    """
    提供默认的 Function-Calling Agent 循环（服务键 'agentLoop'）。
    该循环实现 Agent 接口，可被其他 Agent 实现替换。
    """

    # --- 插件声明 ---

    # inject：依赖四个核心服务。
    inject = ['llm', 'sessions', 'systemPrompt', 'tools']

    # provide：本插件对外提供 'agentLoop' 服务（默认 Agent 实现）。
    # 注意：'agents' 由独立的 AgentsPlugin 提供，此处不重复声明。
    provide = ['agentLoop']

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        # 获取四个核心服务依赖。
        llm = ctx.get('llm')
        sessions = ctx.get('sessions')
        system_prompt = ctx.get('systemPrompt')
        tools = ctx.get('tools')

        # 创建事件发射器（懒加载绑定到本插件上下文）。
        events = ensure_events(ctx)

        # 创建 ReAct Agent 实例。
        agent = ReActAgent(llm, sessions, system_prompt, tools, events)

        # 注册为 'agentLoop' 服务。
        ctx.provide('agentLoop', agent, self.name or 'AgentLoopPlugin')

        # 尝试把 Agent 注册到 'agents' 注册表（若已存在）。
        try:
            agents_registry = ctx.get('agents')
            agents_registry.register(agent)
        except KeyError:
            # 若 'agents' 服务未注册，则忽略（注册表非必需）。
            logger.warning("[AgentLoop] 'agents' 服务未注册，跳过注册表登记。")
        except ValueError as e:
            logger.warning(f"[AgentLoop] 注册表登记失败: {e}")

        logger.info("[AgentLoopPlugin] ReAct Agent 循环已启动")
