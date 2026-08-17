# ============================================================================
# adapters/llm_plugin.py
# LLMPlugin：提供真实 DeepSeek LLM 服务的插件。
#
# 设计意图：
#   把"大模型对话能力"封装成一个可被其他插件注入依赖的服务（key 为 'llm'）。
#   它自身不依赖任何服务（inject = []），只对外提供能力（provide = ['llm']）。
#
#   ★ 本版本升级为支持 Function Calling：
#     · chat(messages, tools=None) 可传入工具 Schema。
#     · 当模型决定调用工具时，返回结构化的 tool_calls；
#       否则返回纯文本消息。两者通过返回值统一标识。
#
# 返回约定（重要，后续 Agent Loop 依赖此结构）：
#   正常文本回复：
#       {"content": "模型回复文本", "tool_calls": []}
#   模型请求调用工具：
#       {"content": "可选说明文本", "tool_calls": [{"name": "工具名", "arguments": {...}}]}
# ============================================================================

import os                      # 读取环境变量（API 密钥、模型、Base URL）
import logging                 # 日志记录
from openai import AsyncOpenAI # OpenAI 异步客户端（DeepSeek 兼容 OpenAI 协议）
from mycordis import Context, Plugin   # 引入框架的上下文与插件基类

# 本模块的日志记录器，用于输出 LLM 调用日志。
logger = logging.getLogger(__name__)


class LLMClient:
    """
    封装 DeepSeek LLM 的调用，对外提供简洁的 chat 接口（支持工具调用）。
    """

    def __init__(self, client, model: str):
        """
        :param client: AsyncOpenAI 客户端实例。
        :param model:  使用的模型名（如 'deepseek-chat'）。
        """
        self._client = client
        self._model = model

    async def chat(self, messages: list, tools: list = None) -> dict:
        """
        调用 Chat Completion API，支持工具调用。

        :param messages: OpenAI 格式的消息列表。
        :param tools:    可选的工具 Schema 列表（Function Calling 声明）。
        :return: 统一返回字典：
                 - 无工具调用: {"content": str, "tool_calls": []}
                 - 有工具调用: {"content": str, "tool_calls": [{"name", "arguments"}]}
        """
        # 构造请求参数（tools 为空则不传，避免 API 歧义）。
        kwargs = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"   # 允许模型自主决定是否调用工具

        try:
            # 发起请求。
            response = await self._client.chat.completions.create(**kwargs)
            # 取第一条回复。
            choice = response.choices[0]
            message = choice.message

            # ---- 1) 若模型请求调用工具 ----
            if getattr(message, "tool_calls", None):
                tool_calls = []
                for tc in message.tool_calls:
                    # arguments 是 JSON 字符串，需要解析成字典。
                    import json
                    try:
                        arguments = json.loads(tc.function.arguments)
                    except Exception:
                        arguments = {}
                    tool_calls.append({
                        "name": tc.function.name,
                        "arguments": arguments,
                    })
                logger.info(f"[LLM] 请求调用工具: {[tc['name'] for tc in tool_calls]}")
                return {
                    "content": message.content or "",
                    "tool_calls": tool_calls,
                }

            # ---- 2) 否则为普通文本回复 ----
            content = message.content or ""
            logger.info(f"[LLM] 收到文本回复: {content[:50]}...")
            return {"content": content, "tool_calls": []}

        except Exception as e:
            # 网络异常、额度不足等一律捕获，返回友好错误（不让主流程崩溃）。
            logger.error(f"[LLM] API 调用失败: {e}")
            return {"content": f"抱歉，我遇到了技术问题：{e}", "tool_calls": []}


class LLMPlugin(Plugin):
    """
    提供真实的 DeepSeek LLM 服务（支持 Function Calling）。
    从环境变量读取 API 密钥和模型配置。
    """

    # --- 插件声明 ---

    # inject：本插件不依赖任何其他服务。
    inject = []

    # provide：本插件对外提供一个服务，键名为 'llm'。
    provide = ['llm']

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        # ---- 第 1 步：从环境变量读取配置 ----
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("环境变量 DEEPSEEK_API_KEY 未设置！")

        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

        # ---- 第 2 步：初始化异步 OpenAI 客户端 ----
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)

        # ---- 第 3 步：创建 LLM 客户端服务并注册 ----
        llm_client = LLMClient(client, model)
        ctx.provide('llm', llm_client, self.name or 'LLMPlugin')
        logger.info(f"[LLMPlugin] DeepSeek LLM 已就绪 (模型: {model}, 支持工具调用)")
