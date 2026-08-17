# ============================================================================
# adapters/llm_plugin.py
# LLMPlugin：提供真实 DeepSeek LLM 服务的插件。
#
# 设计意图：
#   把"大模型对话能力"封装成一个可被其他插件注入依赖的服务（key 为 'llm'）。
#   它自身不依赖任何服务（inject = []），只对外提供能力（provide = ['llm']）。
#
#   ★ 本版本支持 Function Calling 与流式输出：
#     · chat(messages, tools)：非流式，返回结构化结果。
#     · chat_stream(messages, tools)：流式，逐 chunk 产出（对标 DSH llm/stream）。
#
# 返回约定：
#   正常文本回复：{"content": str, "tool_calls": []}
#   请求调用工具：{"content": str, "tool_calls": [{"name", "arguments"}]}
# ============================================================================

import os                      # 读取环境变量
import json                    # JSON 解析
import logging                 # 日志
from openai import AsyncOpenAI # OpenAI 异步客户端（DeepSeek 兼容）
from mycordis import Context, Plugin

# 本模块的日志记录器。
logger = logging.getLogger(__name__)


class LLMClient:
    """
    封装 DeepSeek LLM 的调用，对外提供 chat 接口（支持工具调用与流式）。
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
        调用 Chat Completion API（非流式），支持工具调用。

        :param messages: OpenAI 格式的消息列表。
        :param tools:    可选的工具 Schema 列表。
        :return: 统一返回字典：
                 - 无工具调用: {"content": str, "tool_calls": []}
                 - 有工具调用: {"content": str, "tool_calls": [{"name", "arguments"}]}
        """
        # 构造请求参数。
        kwargs = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await self._client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            message = choice.message

            # 1) 若模型请求调用工具。
            if getattr(message, "tool_calls", None):
                tool_calls = []
                for tc in message.tool_calls:
                    try:
                        arguments = json.loads(tc.function.arguments)
                    except Exception:
                        arguments = {}
                    tool_calls.append({
                        "name": tc.function.name,
                        "arguments": arguments,
                    })
                logger.info(f"[LLM] 请求调用工具: {[tc['name'] for tc in tool_calls]}")
                return {"content": message.content or "", "tool_calls": tool_calls}

            # 2) 否则为普通文本回复。
            content = message.content or ""
            logger.info(f"[LLM] 收到文本回复: {content[:50]}...")
            return {"content": content, "tool_calls": []}

        except Exception as e:
            logger.error(f"[LLM] API 调用失败: {e}")
            return {"content": f"抱歉，我遇到了技术问题：{e}", "tool_calls": []}

    async def chat_stream(self, messages: list, tools: list = None):
        """
        流式调用 Chat Completion API（对标 DSH 的 llm/stream → assistant/chunk*）。

        作为异步生成器，逐 chunk 产出内容文本。若最终发现模型请求工具调用，
        会在流结束产出 ("__TOOL_CALLS__", tool_calls) 标记。

        :param messages: OpenAI 格式的消息列表。
        :param tools:    可选的工具 Schema 列表。
        :yield:          文本增量 chunk（str）或 ("__TOOL_CALLS__", tool_calls)。
        """
        # 构造请求参数（stream=True 开启流式）。
        kwargs = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            # 流式响应。
            stream = await self._client.chat.completions.create(**kwargs)

            # 收集完整的 tool_calls 参数（若模型请求工具）。
            tool_calls_accum = {}   # index -> {name, arguments_str}

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # 文本增量。
                if getattr(delta, "content", None):
                    yield delta.content

                # 工具调用增量（流式工具调用会分段到达）。
                if getattr(delta, "tool_calls", None):
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_accum:
                            tool_calls_accum[idx] = {
                                "name": tc.function.name or "",
                                "arguments_str": tc.function.arguments or "",
                            }
                        else:
                            if tc.function.name:
                                tool_calls_accum[idx]["name"] = tc.function.name
                            tool_calls_accum[idx]["arguments_str"] += tc.function.arguments or ""

            # 若检测到工具调用，返回结构（作为生成器结束时的标记）。
            if tool_calls_accum:
                tool_calls = []
                for idx in sorted(tool_calls_accum):
                    try:
                        arguments = json.loads(tool_calls_accum[idx]["arguments_str"])
                    except Exception:
                        arguments = {}
                    tool_calls.append({
                        "name": tool_calls_accum[idx]["name"],
                        "arguments": arguments,
                    })
                logger.info(f"[LLM] 流式请求调用工具: {[tc['name'] for tc in tool_calls]}")
                yield ("__TOOL_CALLS__", tool_calls)

        except Exception as e:
            logger.error(f"[LLM] 流式 API 调用失败: {e}")
            yield f"抱歉，我遇到了技术问题：{e}"


class LLMPlugin(Plugin):
    """
    提供真实的 DeepSeek LLM 服务（支持 Function Calling 与流式）。
    """

    # --- 插件声明 ---
    inject = []
    provide = ['llm']

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("环境变量 DEEPSEEK_API_KEY 未设置！")

        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)

        llm_client = LLMClient(client, model)
        ctx.provide('llm', llm_client, self.name or 'LLMPlugin')
        logger.info(f"[LLMPlugin] DeepSeek LLM 已就绪 (模型: {model}, 支持工具调用/流式)")
