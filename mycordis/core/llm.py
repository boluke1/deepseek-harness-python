# ============================================================================
# mycordis/core/llm.py
# LLM 适配器抽象层：多 Provider 支持。
#
# 对标 DSH core/llm 的 Seam 模式：
#   · LLM 是一个 Seam，Service Definition 声明 chat/stream 接口。
#   · 每个模型提供商（DeepSeek、OpenAI、Anthropic 等）是一个 Provider。
#   · 消费者通过 ctx.llm 访问，不关心底层实现。
#   · 换一个 Provider 即改变整个产品行为。
#
# 使用示例：
#   # 注册 Provider
#   adapter = LLMAdapterService(ctx)
#   adapter.register_provider("deepseek", DeepSeekProvider(client, model))
#   adapter.register_provider("openai", OpenAIProvider(client, model))
#   adapter.switch_provider("openai")   # 切换
#
#   # 使用（消费者通过 ctx 反射层）
#   llm = ctx.llm
#   result = await llm.chat(messages, tools)
# ============================================================================

import logging
from typing import Any, AsyncIterator, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Context

logger = logging.getLogger(__name__)


class LLMProvider:
    """
    LLM Provider 接口（对标 DSH LLM Seam 的 Provider 角色）。

    所有 LLM 提供商必须实现此接口。
    """

    name: str = ""

    async def chat(self, messages: list, tools: list = None) -> dict:
        """
        非流式对话。

        :param messages: OpenAI 格式消息列表。
        :param tools:    可选工具 Schema 列表。
        :return: {"content": str, "tool_calls": list}
        """
        raise NotImplementedError

    async def chat_stream(self, messages: list, tools: list = None) -> AsyncIterator:
        """
        流式对话。

        :param messages: OpenAI 格式消息列表。
        :param tools:    可选工具 Schema 列表。
        :yield: 文本增量 chunk 或 ("__TOOL_CALLS__", tool_calls)。
        """
        raise NotImplementedError
        yield  # Make this an async generator

    async def close(self) -> None:
        """关闭客户端连接。"""
        pass

    def get_model_info(self) -> Dict[str, str]:
        """获取模型信息。"""
        return {"name": self.name, "type": "unknown"}


class LLMAdapterService:
    """
    LLM 适配器服务（对标 DSH core/llm Seam）。

    ★ 继承 Service 基类：构造时自动注册为 ctx.llm。
    管理多个 LLM Provider，支持热切换。
    """

    def __init__(self, ctx: 'Context', name: str = "llm"):
        from .service import Service
        self._ctx = ctx
        self._providers: Dict[str, LLMProvider] = {}
        self._active_provider: Optional[str] = None
        # ★ 通过 Service 基类自动注册。
        Service.__init__(self, ctx, name)

    def init(self):
        """★ init 钩子。"""
        pass

    def register_provider(self, provider_id: str, provider: LLMProvider,
                          activate: bool = True) -> None:
        """
        注册一个 LLM Provider。

        :param provider_id: Provider 标识（如 "deepseek"、"openai"）。
        :param provider:    LLMProvider 实例。
        :param activate:    是否立即激活（设为当前活跃）。
        """
        provider.name = provider_id
        self._providers[provider_id] = provider

        if activate or self._active_provider is None:
            self.switch_provider(provider_id)

    def switch_provider(self, provider_id: str) -> None:
        """
        切换活跃 Provider。

        :param provider_id: Provider 标识。
        :raises KeyError:   provider_id 不存在时抛出。
        """
        if provider_id not in self._providers:
            raise KeyError(f"LLM Provider '{provider_id}' not registered")
        self._active_provider = provider_id
        provider = self._providers[provider_id]
        # 更新 ctx.llm 指向新 provider。
        self._ctx.set_service("llm", provider)

    def get_active_provider(self) -> Optional[LLMProvider]:
        """获取当前活跃的 Provider。"""
        if self._active_provider is None:
            return None
        return self._providers.get(self._active_provider)

    def list_providers(self) -> List[str]:
        """列出所有已注册的 Provider。"""
        return list(self._providers.keys())

    async def close_all(self) -> None:
        """关闭所有 Provider 的连接。"""
        for pid, provider in self._providers.items():
            try:
                await provider.close()
            except Exception as e:
                logger.error(f"Error closing LLM provider '{pid}': {e}")

    # ------------------------------------------------------------------
    # 代理方法（当 LLMAdapterService 自身作为 ctx.llm 时）
    # ------------------------------------------------------------------
    async def chat(self, messages: list, tools: list = None) -> dict:
        """代理到当前活跃 Provider 的 chat 方法。"""
        provider = self.get_active_provider()
        if provider is None:
            return {"content": "No LLM provider active", "tool_calls": []}
        return await provider.chat(messages, tools)

    async def chat_stream(self, messages: list, tools: list = None):
        """代理到当前活跃 Provider 的 chat_stream 方法。"""
        provider = self.get_active_provider()
        if provider is None:
            yield "No LLM provider active"
            return
        async for chunk in provider.chat_stream(messages, tools):
            yield chunk

    def get_model_info(self) -> Dict[str, str]:
        """获取当前活跃 Provider 的模型信息。"""
        provider = self.get_active_provider()
        if provider is None:
            return {"name": "none", "type": "none"}
        return provider.get_model_info()
