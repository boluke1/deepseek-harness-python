# ============================================================================
# core_services/system_prompt_plugin.py
# SystemPromptPlugin：系统提示（System Prompt）组装服务。
#
# 设计意图：
#   对标 DeepSeek Harness 的 ctx.systemPrompt 核心服务。
#   系统提示是 Agent 的"行为总纲"，包含三部分：
#     1. 身份/角色提示（基础行为准则）
#     2. 工具 Schema（告诉模型有哪些工具可用、怎么调用）
#     3. 循环规则（ReAct 的 Thought/Action/Observation/Final Answer 格式）
#
#   本服务提供"区块（block）"注册机制，任何插件都能通过 ctx.effect 可逆地
#   追加/移除提示区块，实现系统提示的动态、可插拔组装。
#
# 服务键：
#   'systemPrompt' —— 对外暴露一个 SystemPromptBuilder 实例。
# ============================================================================

import logging
from typing import Dict, List, Optional

from mycordis import Context, Plugin

# 本模块的日志记录器。
logger = logging.getLogger(__name__)


class SystemPromptBuilder:
    """
    系统提示组装器：按顺序把多个提示区块和工具 Schema 拼成最终系统提示。
    """

    def __init__(self, base_prompt: Optional[str] = None):
        """
        初始化组装器。

        :param base_prompt: 可选的初始基础提示（身份/角色），可为空。
        """
        # 基础提示（可选）：通常是 Agent 的身份与总体行为准则。
        self.base_prompt = base_prompt or ""

        # 提示区块表：key 为区块 id，value 为 (order, text)。
        # order 用于排序，数值越小越靠前；text 为区块内容。
        self._blocks: Dict[str, tuple] = {}

    def add_block(self, block_id: str, text: str, order: int = 100) -> None:
        """
        注册一个提示区块（可逆副作用由调用方通过 ctx.effect 管理，或本类提供 remove）。

        :param block_id: 区块唯一标识，用于后续移除。
        :param text:     区块内容。
        :param order:    排序权重，数值越小越靠前（默认 100）。
        """
        self._blocks[block_id] = (order, text)
        logger.debug(f"[SystemPrompt] 添加区块: {block_id}")

    def remove_block(self, block_id: str) -> None:
        """
        移除一个提示区块。

        :param block_id: 要移除的区块 id。
        """
        if block_id in self._blocks:
            del self._blocks[block_id]
            logger.debug(f"[SystemPrompt] 移除区块: {block_id}")

    def add_tool_schema(self, tool_name: str, schema: str) -> None:
        """
        注册一个工具 Schema（以文本形式，如 JSON Schema 字符串）。

        :param tool_name: 工具名，用于去重。
        :param schema:    工具的 JSON Schema 描述字符串。
        """
        self.add_block(f"tool_{tool_name}", schema, order=200)
        logger.debug(f"[SystemPrompt] 添加工具 Schema: {tool_name}")

    def remove_tool_schema(self, tool_name: str) -> None:
        """
        移除一个工具 Schema。

        :param tool_name: 工具名。
        """
        self.remove_block(f"tool_{tool_name}")

    def build(self) -> str:
        """
        按顺序组装最终系统提示。

        组装顺序：
          1. 基础提示（base_prompt）
          2. 所有区块按 order 升序排列（含工具 Schema）
          3. 各区块之间用空行分隔

        :return: 组装好的完整系统提示字符串。
        """
        # 收集所有区块的 (order, text)，按 order 升序排序。
        ordered_blocks = sorted(self._blocks.values(), key=lambda x: x[0])

        # 把所有文本拼成一段，区块间用两个换行分隔。
        sections = []
        if self.base_prompt:
            sections.append(self.base_prompt)
        for _, text in ordered_blocks:
            if text:
                sections.append(text)

        # 拼接并返回。
        return "\n\n".join(sections).strip()


class SystemPromptPlugin(Plugin):
    """
    提供系统提示组装服务（服务键 'systemPrompt'）。
    """

    # --- 插件声明 ---

    # inject：本插件不依赖任何其他服务。
    inject = []

    # provide：本插件对外提供 'systemPrompt' 服务。
    provide = ['systemPrompt']

    # --- 激活逻辑 ---
    async def apply(self, ctx: Context):
        # 创建系统提示组装器（此处传入一个基础身份提示，可自定义）。
        builder = SystemPromptBuilder(
            base_prompt=(
                "You are a helpful AI agent that uses tools to solve tasks. "
                "Follow the ReAct pattern: think step by step, call tools when needed, "
                "and give a final answer when done."
            )
        )

        # 注册为 'systemPrompt' 服务。
        ctx.provide('systemPrompt', builder, self.name or 'SystemPromptPlugin')
        logger.info("[SystemPromptPlugin] 系统提示服务已就绪")
