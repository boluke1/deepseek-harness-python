# plugins/llm_plugin.py
import os
import logging
from openai import AsyncOpenAI
from mycordis import Context, Plugin

logger = logging.getLogger(__name__)

class LLMPlugin(Plugin):
    """
    提供真实的 DeepSeek LLM 服务。
    从环境变量读取 API 密钥和模型配置。
    """
    inject = []
    provide = ['llm']

    async def apply(self, ctx: Context):
        # 从环境变量读取配置
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("环境变量 DEEPSEEK_API_KEY 未设置！")

        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

        # 初始化 AsyncOpenAI 客户端
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

        class LLMClient:
            async def chat(self, messages: list) -> str:
                """
                调用 DeepSeek Chat API，返回回复内容。
                """
                try:
                    response = await client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.7,
                        max_tokens=1024,
                    )
                    content = response.choices[0].message.content
                    logger.info(f"[LLM] 收到回复: {content[:50]}...")
                    return content
                except Exception as e:
                    logger.error(f"[LLM] API 调用失败: {e}")
                    return f"抱歉，我遇到了技术问题：{e}"

        ctx.provide('llm', LLMClient(), self.name or 'LLMPlugin')
        logger.info(f"[LLMPlugin] DeepSeek LLM 已就绪 (模型: {model})")