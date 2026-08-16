# plugins/agent_loop_plugin.py
import logging
from typing import List, Dict
from mycordis import Context, Plugin

logger = logging.getLogger(__name__)

class AgentLoopPlugin(Plugin):
    inject = ['llm']
    provide = ['agent']

    async def apply(self, ctx: Context):
        llm = ctx.get('llm')

        class AgentRunner:
            def __init__(self, llm_client):
                self.llm = llm_client
                self.history: List[Dict[str, str]] = []

            async def run(self, user_input: str) -> str:
                logger.info(f"[Agent] 用户输入: {user_input}")
                self.history.append({"role": "user", "content": user_input})
                response = await self.llm.chat(self.history)
                self.history.append({"role": "assistant", "content": response})
                logger.info(f"[Agent] 回复: {response[:50]}...")
                return response

        ctx.provide('agent', AgentRunner(llm), self.name or 'AgentLoopPlugin')
        logger.info("[AgentLoopPlugin] Agent 服务已启动")