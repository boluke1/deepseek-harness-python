# ============================================================================
# plugins/llm_plugin.py
# LLMPlugin：提供真实 DeepSeek LLM 服务的插件。
#
# 设计意图：
#   把"大模型对话能力"封装成一个可被其他插件注入依赖的服务（key 为 'llm'）。
#   它自身不依赖任何服务（inject = []），只对外提供能力（provide = ['llm']）。
#
# 在框架中的协作：
#   - Registry 在注册该插件后，因 inject 为空（无条件），立即满足依赖并调用 apply。
#   - apply 中调用 ctx.provide('llm', client, name) 注册服务；
#     之后 Registry 会把它"提升"到根上下文，供如 AgentLoopPlugin 通过 ctx.get('llm') 获取。
# ============================================================================

import os                      # 读取环境变量（API 密钥、模型、Base URL）
import logging                 # 日志记录
from openai import AsyncOpenAI # OpenAI 异步客户端（DeepSeek 兼容 OpenAI 协议）
from mycordis import Context, Plugin   # 引入框架的上下文与插件基类

# 本模块的日志记录器，用于输出 LLM 调用日志。
logger = logging.getLogger(__name__)


class LLMPlugin(Plugin):
    """
    提供真实的 DeepSeek LLM 服务。
    从环境变量读取 API 密钥和模型配置。
    """

    # --- 插件声明 ---

    # inject：本插件不依赖任何其他服务，因此为空列表。
    #         这意味着只要被注册，就立刻满足激活条件。
    inject = []

    # provide：本插件对外提供一个服务，键名为 'llm'（即"大模型对话能力"）。
    #          apply 中必须调用 ctx.provide('llm', ...) 实际注册。
    provide = ['llm']

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        # ---- 第 1 步：从环境变量读取配置 ----

        # API 密钥是必填项，缺失则直接抛异常（Registry 会捕获并标记激活失败）。
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("环境变量 DEEPSEEK_API_KEY 未设置！")

        # 模型名与 Base URL 均可选，提供合理的默认值。
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

        # ---- 第 2 步：初始化异步 OpenAI 客户端 ----
        # DeepSeek 的 API 与 OpenAI 协议兼容，因此可直接复用 AsyncOpenAI。
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )

        # ---- 第 3 步：定义对外的 LLM 服务对象 ----
        # 用闭包把 client/model 捕获进来，对外只暴露简洁的 chat 接口。
        class LLMClient:
            async def chat(self, messages: list) -> str:
                """
                调用 DeepSeek Chat API，返回回复内容。

                :param messages: OpenAI 格式的消息列表（含 role 与 content），
                                 例如 [{"role":"user","content":"你好"}]。
                :return:         模型回复的文本内容。
                """
                try:
                    # 发起 Chat Completion 请求。
                    response = await client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.7,       # 控制回答的随机性
                        max_tokens=1024,       # 单次回答的最大 token 数
                    )
                    # 提取第一条回复的文本内容。
                    content = response.choices[0].message.content
                    logger.info(f"[LLM] 收到回复: {content[:50]}...")
                    return content
                except Exception as e:
                    # 网络异常、额度不足等一律捕获，返回友好的错误信息（不让主流程崩溃）。
                    logger.error(f"[LLM] API 调用失败: {e}")
                    return f"抱歉，我遇到了技术问题：{e}"

        # ---- 第 4 步：把服务注册到上下文 ----
        # 注册键 'llm'，值为 LLMClient 实例；提供者名字用 self.name 或默认 'LLMPlugin'。
        # 此后 Registry 会将其"提升"到根上下文，其他插件可 ctx.get('llm') 获得。
        ctx.provide('llm', LLMClient(), self.name or 'LLMPlugin')
        logger.info(f"[LLMPlugin] DeepSeek LLM 已就绪 (模型: {model})")
