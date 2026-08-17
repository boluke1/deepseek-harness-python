# ============================================================================
# core_services/system_prompt_plugin.py
# SystemPromptPlugin：系统提示（System Prompt）组装服务。
#
# 设计意图：
#   对标 DeepSeek Harness 的 ctx.systemPrompt 核心服务。
#
#   ★ 增强版（90%+）：
#     · block 开关：enable_block / disable_block（不删除，只是禁用）
#     · identity 感知：自动从 ctx.identity 注入 Agent 身份信息
#     · 条件区块：only_if 条件函数，动态决定是否包含
#     · 区块查询：get_block_ids / get_disabled_blocks
#
#   ★ SystemPromptBuilder 继承 Service 基类，自动注册 + init 钩子。
# ============================================================================

import logging
from typing import Callable, Dict, List, Optional

from mycordis import Context, Plugin, Service

logger = logging.getLogger(__name__)


class SystemPromptBuilder(Service):
    """
    系统提示组装器（对标 DSH Service 基类）。

    ★ 增强版（90%+）：
      · block 开关：enable_block / disable_block
      · identity 感知：自动从 ctx.identity 注入 Agent 身份信息
      · 条件区块：add_conditional_block
    """

    def __init__(self, ctx: Context, name: str = "systemPrompt",
                 base_prompt: Optional[str] = None):
        self.base_prompt = base_prompt or ""
        self._blocks: Dict[str, tuple] = {}
        self._disabled_blocks: set = set()
        self._conditional_blocks: Dict[str, Callable] = {}
        super().__init__(ctx, name)

    def init(self):
        """★ init 钩子。"""
        self.ctx.logger.info("系统提示组装器已初始化 (init hook)")

    def add_block(self, block_id: str, text: str, order: int = 100) -> None:
        """
        注册一个提示区块。

        :param block_id: 区块唯一标识。
        :param text:     区块内容。
        :param order:    排序权重，数值越小越靠前。
        """
        self._blocks[block_id] = (order, text)
        self._disabled_blocks.discard(block_id)
        self.ctx.logger.debug(f"[SystemPrompt] 添加区块: {block_id}")

    def remove_block(self, block_id: str) -> None:
        """移除一个提示区块。"""
        if block_id in self._blocks:
            del self._blocks[block_id]
            self._disabled_blocks.discard(block_id)
            self._conditional_blocks.pop(block_id, None)
            self.ctx.logger.debug(f"[SystemPrompt] 移除区块: {block_id}")

    def add_tool_schema(self, tool_name: str, schema: str) -> None:
        """注册一个工具 Schema。"""
        self.add_block(f"tool_{tool_name}", schema, order=200)
        self.ctx.logger.debug(f"[SystemPrompt] 添加工具 Schema: {tool_name}")

    def remove_tool_schema(self, tool_name: str) -> None:
        """移除一个工具 Schema。"""
        self.remove_block(f"tool_{tool_name}")

    # ------------------------------------------------------------------
    # ★ block 开关
    # ------------------------------------------------------------------
    def enable_block(self, block_id: str) -> None:
        """★ 启用一个已禁用的区块。"""
        self._disabled_blocks.discard(block_id)

    def disable_block(self, block_id: str) -> None:
        """★ 禁用一个区块（不删除，只是不参与组装）。"""
        if block_id in self._blocks:
            self._disabled_blocks.add(block_id)

    def add_conditional_block(self, block_id: str, text: str, order: int,
                              condition_fn: Callable) -> None:
        """
        ★ 添加条件区块：只有 condition_fn() 返回 True 时才包含。
        """
        self.add_block(block_id, text, order)
        self._conditional_blocks[block_id] = condition_fn

    def get_block_ids(self) -> List[str]:
        """★ 获取所有已注册的区块 ID。"""
        return list(self._blocks.keys())

    def get_disabled_blocks(self) -> List[str]:
        """★ 获取当前被禁用的区块 ID。"""
        return list(self._disabled_blocks)

    # ------------------------------------------------------------------
    # 组装
    # ------------------------------------------------------------------
    def build(self) -> str:
        """
        按顺序组装最终系统提示。

        ★ 增强：
          · 跳过被 disable 的区块
          · 跳过条件不满足的区块
          · ★ 自动注入 identity 信息
        """
        sections = []
        if self.base_prompt:
            sections.append(self.base_prompt)

        # ★ identity 感知注入。
        identity_section = self._build_identity_section()
        if identity_section:
            sections.append(identity_section)

        for block_id, (order, text) in sorted(
            [(bid, bv) for bid, bv in self._blocks.items()],
            key=lambda x: x[1][0]
        ):
            if block_id in self._disabled_blocks:
                continue
            if block_id in self._conditional_blocks:
                try:
                    if not self._conditional_blocks[block_id]():
                        continue
                except Exception:
                    continue
            if text:
                sections.append(text)

        return "\n\n".join(sections).strip()

    def _build_identity_section(self) -> str:
        """★ 从 ctx.identity 构建 Agent 身份信息区块。"""
        try:
            identity = self.ctx.get("identity")
            agent_ids = identity.list_identities()
            if not agent_ids:
                return ""
            parts = []
            for aid in agent_ids:
                if aid.startswith("__"):
                    continue
                ai = identity.get_agent_identity(aid)
                if ai:
                    parts.append(f"You are {ai.name} (role: {ai.role}). {ai.description}")
                    if ai.metadata:
                        meta_str = ", ".join(
                            f"{k}={v}" for k, v in ai.metadata.items()
                            if not k.startswith("_")
                        )
                        if meta_str:
                            parts.append(f"Metadata: {meta_str}")
            return "\n".join(parts) if parts else ""
        except (KeyError, AttributeError):
            return ""


class SystemPromptPlugin(Plugin):
    """提供系统提示组装服务（服务键 'systemPrompt'）。"""

    inject = []
    provide = ['systemPrompt']

    async def apply(self, ctx: Context):
        SystemPromptBuilder(
            ctx,
            base_prompt=(
                "You are a helpful AI agent that uses tools to solve tasks. "
                "Follow the ReAct pattern: think step by step, call tools when needed, "
                "and give a final answer when done."
            ),
        )
        ctx.logger.info("[SystemPromptPlugin] 系统提示服务已就绪 (block 开关 + identity 感知)")
