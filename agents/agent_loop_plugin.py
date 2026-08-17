# ============================================================================
# agents/agent_loop_plugin.py
# AgentLoopPlugin：基于 Function Calling 的 Agent 循环（ReAct 的实现）。
#
# 设计意图（对标 DSH 的 ctx.agentLoop + agent.ts）：
#   ★ Turn/Step 双层状态机 + waterfall 拦截 + 流式事件广播。
#   · Turn：一次用户交互，turn/start → turn/end。
#   · Step：一次模型请求 + 工具调用，嵌套在 Turn 内。
#   · waterfall 拦截点：agent/pre-step（改写输入）、agent/request（改写请求配置）。
#   · llm/stream 事件：最终答复按 chunk 广播（对标 DSH llm/stream → assistant/chunk*）。
#
#   ★ 升级版：全面使用 ctx.logger / ctx.emit / ctx.waterfall / ctx.effect()。
#
# 死循环防护：
#   仅当"动作 + 观察结果"组合连续重复 ≥3 次才判定为死循环。
# ============================================================================

import json
import logging
from typing import Optional

from mycordis import Context, Plugin
from .agent_interface import Agent

# 本模块的日志记录器（模块级备用）。
logger = logging.getLogger(__name__)


class ReActAgent(Agent):
    """
    基于 Function Calling 的 Turn/Step 双层 Agent 循环实现。
    """

    def __init__(self,
                 ctx: Context,
                 agent_id: str = "react-loop",
                 max_steps: int = 5,
                 default_session_id: Optional[str] = None):
        """
        初始化 ReAct Agent。

        ★ 接收 ctx 引用，通过 ctx.xxx 访问所有服务（对标 DSH 反射层）。
        """
        self.ctx = ctx
        self.id = agent_id
        self.status = "idle"
        self.max_steps = max_steps
        self.default_session_id = default_session_id
        self._turn_counter = 0

    async def run(self, user_input: str, session_id: Optional[str] = None) -> str:
        """
        处理一轮用户输入，返回最终回复（一个 Turn）。

        ★ 增强：
          · 与 AgentRegistry 活跃跟踪集成
          · step 级不变式校验
          · 完整 agent/* 事件协议
        """
        self.status = "running"

        # ★ 标记为活跃 Agent。
        try:
            agents_reg = self.ctx.agents
            agents_reg.mark_active(self.id)
        except (KeyError, AttributeError):
            pass

        # ★ 通过 ctx 反射层访问服务。
        sessions = self.ctx.sessions
        system_prompt = self.ctx.systemPrompt
        tools = self.ctx.tools

        # ---- 1. 获取会话 ----
        if session_id is None:
            session_id = self.default_session_id
            if session_id is None:
                session = sessions.create()
                self.default_session_id = session.id
            else:
                session = sessions.get(session_id)
        else:
            try:
                session = sessions.get(session_id)
            except KeyError:
                session = sessions.create(session_id)

        # 记录用户消息。
        session.append("user_message", user_input)

        # ---- 2. 开启 Turn ----
        self._turn_counter += 1
        turn_no = self._turn_counter
        session.append("turn/start", "", {"turn": turn_no})
        # ★ 使用 ctx.emit() 直接发射事件（对标 DSH auto-mixin）。
        self.ctx.emit("agent/turn-start", {"turn": turn_no, "session": session.id, "agent_id": self.id})
        self.ctx.logger.info(f"[Agent] Turn {turn_no} start")

        # ---- 3. 组装系统提示 + 工具 Schema ----
        system_prompt_text = system_prompt.build()
        tool_schemas = tools.list_schemas()

        # ---- 4. Step 循环 ----
        messages = [{"role": "system", "content": system_prompt_text}]
        messages.extend(session.get_messages())

        recent_obs_key = None
        repeat_count = 0

        try:
            for step_no in range(1, self.max_steps + 1):
                session.append("step/start", "", {"step": step_no, "turn": turn_no})
                self.ctx.emit("agent/step", {"step": step_no, "turn": turn_no, "agent_id": self.id})
                self.ctx.logger.info(f"[Agent] Step {step_no}/{self.max_steps}")

                # ---- agent/pre-step 拦截（waterfall）----
                # ★ 使用 ctx.waterfall() 直接调用（对标 DSH auto-mixin）。
                intercepted = await self.ctx.waterfall("agent/pre-step", messages)
                if intercepted is not None:
                    messages = intercepted

                # ---- 组装模型请求配置 ----
                request_config = {"messages": messages, "tools": tool_schemas or None}

                # ---- agent/request 拦截（waterfall）----
                request_config = await self.ctx.waterfall("agent/request", request_config)

                # 调用 LLM。
                llm = self.ctx.llm
                result = await llm.chat(
                    request_config.get("messages", messages),
                    tools=request_config.get("tools", tool_schemas or None),
                )
                content = result["content"]
                tool_calls = result["tool_calls"]

                # ★ 不变式校验：LLM 回复必须先记录。
                try:
                    self.ctx.invariant.check({
                        "type": "assistant_message",
                        "content": content,
                    })
                except (KeyError, AttributeError):
                    pass

                # 无工具调用 → 最终答复 → 流式广播 + 结束 Turn。
                if not tool_calls:
                    final_answer = content or "已完成。"
                    self._stream_final_answer(session, final_answer, turn_no, step_no)
                    session.append("assistant_message", final_answer)
                    self.status = "finished"
                    self._end_turn(session, turn_no, final_answer)
                    self._mark_inactive("finished")
                    return final_answer

                # 有工具调用：记录 assistant 中间态。
                session.append("assistant_message", content or "[tool call]",
                               {"tool_calls": tool_calls})

                observation_parts = []
                for tc in tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["arguments"]
                    action_key = f"{tool_name}({json.dumps(tool_args, sort_keys=True)})"

                    session.append(
                        "tool_call",
                        json.dumps({"name": tool_name, "arguments": tool_args}, ensure_ascii=False),
                        {"tool": tool_name, "callId": action_key},
                    )

                    # 执行工具。
                    try:
                        obs = await tools.execute(tool_name, tool_args)
                    except Exception as e:
                        obs = f"工具 {tool_name} 执行失败: {e}"
                    self.ctx.logger.info(f"[Agent] 工具结果: {obs}")

                    # 死循环防护。
                    obs_key = f"{action_key} => {obs}"
                    if obs_key == recent_obs_key:
                        repeat_count += 1
                    else:
                        recent_obs_key = obs_key
                        repeat_count = 1

                    if repeat_count >= 3:
                        msg = "检测到连续重复动作且结果无变化，可能陷入循环，已停止。请换一种表述。"
                        session.append("assistant_message", msg)
                        self.status = "error"
                        self._end_turn(session, turn_no, msg)
                        self._mark_inactive("error")
                        return msg

                    session.append("tool_result", str(obs), {"tool": tool_name})
                    observation_parts.append(f"工具 {tool_name} 的结果: {obs}")

                # 回灌观察。
                observation_text = "\n".join(observation_parts)
                messages.append({"role": "user", "content": f"Observation: {observation_text}"})

            # 达到 max_steps。
            msg = f"已达到最大步数（{self.max_steps}），任务未完成。"
            session.append("assistant_message", msg)
            self.status = "error"
            self._end_turn(session, turn_no, msg)
            self._mark_inactive("error")
            return msg

        except Exception as e:
            self.ctx.logger.error(f"[Agent] Turn {turn_no} 异常: {e}")
            self.status = "error"
            self._end_turn(session, turn_no, f"执行出错: {e}")
            self._mark_inactive("error")
            return f"执行出错: {e}"

    def _stream_final_answer(self, session, final_answer: str, turn_no: int, step_no: int) -> None:
        """
        把最终答复按 chunk 广播 llm/stream 事件并写 assistant/chunk 日志。
        """
        chunk_size = 20
        for i in range(0, len(final_answer), chunk_size):
            chunk = final_answer[i:i + chunk_size]
            self.ctx.emit("llm/stream", {"chunk": chunk, "turn": turn_no, "step": step_no})
            session.append("assistant/chunk", chunk, {"turn": turn_no, "step": step_no})

    def _mark_inactive(self, final_status: str) -> None:
        """★ 标记 Agent 为非活跃。"""
        try:
            agents_reg = self.ctx.agents
            agents_reg.mark_inactive(self.id, final_status)
        except (KeyError, AttributeError):
            pass

    def _end_turn(self, session, turn_no: int, result: str) -> None:
        """
        结束当前 Turn：记录 turn/end 事件并广播。
        """
        session.append("turn/end", "", {"turn": turn_no})
        self.ctx.emit("agent/turn-end", {"turn": turn_no, "result": result, "agent_id": self.id})
        self.ctx.logger.info(f"[Agent] Turn {turn_no} end")


class AgentLoopPlugin(Plugin):
    """
    提供默认的 Function-Calling Agent 循环（服务键 'agentLoop'）。

    ★ 升级版：使用 ctx.logger + ctx.effect() + ctx.on()。
    """

    # --- 插件声明 ---
    inject = ['llm', 'sessions', 'systemPrompt', 'tools']
    provide = ['agentLoop']

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        # ★ ReActAgent 接收 ctx 引用，通过反射层访问所有服务。
        agent = ReActAgent(ctx)

        ctx.provide('agentLoop', agent, self.name or 'AgentLoopPlugin')

        # ★ 尝试注册到 Agent 注册表（如果可用）。
        try:
            agents_registry = ctx.agents
            agents_registry.register(agent)
        except (KeyError, AttributeError):
            ctx.logger.warn("[AgentLoop] 'agents' 服务未注册，跳过注册表登记。")
        except ValueError as e:
            ctx.logger.warn(f"[AgentLoop] 注册表登记失败: {e}")

        # ★ 使用 ctx.effect() 注册清理（对标 DSH 可逆副作用）。
        async def _cleanup():
            agent.status = "idle"
            ctx.logger.info("[AgentLoop] Agent 状态已重置")

        ctx.effect(_cleanup)

        # ★ 使用 ctx.on() 监听事件（对标 DSH auto-mixin）。
        ctx.on('agent/turn-end', lambda payload: ctx.logger.info(
            f"[AgentLoop] Turn 结束: turn={payload.get('turn') if payload else '?'}"
        ))

        ctx.logger.info("[AgentLoopPlugin] ReAct Agent 循环已启动")
