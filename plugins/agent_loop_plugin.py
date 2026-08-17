# ============================================================================
# plugins/agent_loop_plugin.py
# AgentLoopPlugin：依赖 LLM 的多轮对话 Agent 服务插件。
#
# 设计意图：
#   在 LLM 能力之上，封装一个"带历史记忆的多轮对话 Agent"（服务键 'agent'）。
#   它依赖 'llm' 服务（inject = ['llm']），并对外提供 'agent' 服务（provide = ['agent']）。
#
# 在框架中的协作：
#   - 当 'llm' 服务已被提升到根上下文后，此插件的依赖才满足，Registry 才会激活它。
#   - apply 中通过 ctx.get('llm') 获取依赖，再注册自己的 'agent' 服务。
#   - 若 'llm' 被卸载，协调循环会检测到本插件依赖丢失，自动将其停用；
#     当 'llm' 恢复后又会自动重新激活（反应式自愈）。
# ============================================================================

import logging
from typing import List, Dict
from mycordis import Context, Plugin

# 本模块的日志记录器，用于输出 Agent 调用日志。
logger = logging.getLogger(__name__)


class AgentLoopPlugin(Plugin):
    """
    提供带历史记忆的多轮对话 Agent 服务。
    """

    # --- 插件声明 ---

    # inject：本插件依赖 'llm' 服务。
    #         只有 'llm' 在根上下文中存在时，本插件才会被激活。
    inject = ['llm']

    # provide：本插件对外提供一个服务，键名为 'agent'（即"多轮对话 Agent"）。
    provide = ['agent']

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        # ---- 第 1 步：获取依赖 ----
        # 从上下文获取 'llm' 服务（此时它已由 LLMPlugin 提升到根上下文，可沿链找到）。
        llm = ctx.get('llm')

        # ---- 第 2 步：定义对外的 Agent 服务对象 ----
        # 用闭包把 llm 客户端捕获进来，并维护对话历史，实现多轮上下文记忆。
        class AgentRunner:
            def __init__(self, llm_client):
                # 底层 LLM 客户端（用于实际调用对话 API）。
                self.llm = llm_client
                # 对话历史：按 OpenAI 格式保存每轮消息（role: user/assistant）。
                # 每轮对话都会追加到此处，从而让模型"记得"之前聊过什么。
                self.history: List[Dict[str, str]] = []

            async def run(self, user_input: str) -> str:
                """
                处理一轮用户输入：维护历史并调用 LLM，返回模型回复。

                :param user_input: 用户本轮输入文本。
                :return:           模型回复文本。
                """
                logger.info(f"[Agent] 用户输入: {user_input}")

                # 1) 把用户输入追加进历史。
                self.history.append({"role": "user", "content": user_input})

                # 2) 把完整历史交给 LLM，得到回复（模型可参考之前所有对话）。
                response = await self.llm.chat(self.history)

                # 3) 把模型回复也追加进历史，保持上下文连续。
                self.history.append({"role": "assistant", "content": response})
                logger.info(f"[Agent] 回复: {response[:50]}...")
                return response

        # ---- 第 3 步：把 Agent 服务注册到上下文 ----
        # 注册键 'agent'，值为 AgentRunner 实例；提供者为 self.name 或默认 'AgentLoopPlugin'。
        # Registry 会将其"提升"到根上下文，main.py 通过根上下文 get('agent') 使用。
        ctx.provide('agent', AgentRunner(llm), self.name or 'AgentLoopPlugin')
        logger.info("[AgentLoopPlugin] Agent 服务已启动")
