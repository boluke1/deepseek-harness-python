# ============================================================================
# mycordis/core/disposable.py
# 清理列表与效果元数据（对标 DSH utils.ts DisposalList + fiber.ts EffectMeta）。
#
#   · DisposalList：带错误组合的清理函数列表
#   · EffectMeta：嵌套效果标签树，用于诊断
#   · run_disposable：执行单个清理函数（带去重）
# ============================================================================

import asyncio
import logging
import weakref
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

_effect_inertia: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


class EffectMeta:
    """
    效果元数据树节点（对标 DSH EffectMeta）。

    用于 get_effects() 诊断：显示当前活跃的效果及其嵌套关系。
    """

    def __init__(self, label: str):
        self.label = label
        self.children: List['EffectMeta'] = []

    def __repr__(self):
        return f"EffectMeta({self.label!r}, children={len(self.children)})"


class DisposalList:
    """
    清理函数列表（对标 DSH DisposalList）。

    管理一组清理函数，支持逆序执行和错误组合。
    多次添加同一个清理函数会被去重。
    """

    def __init__(self):
        self._items: List[Callable] = []

    def push(self, dispose: Callable) -> None:
        """添加一个清理函数（去重）。"""
        if dispose not in self._items:
            self._items.append(dispose)

    def delete(self, dispose: Callable) -> None:
        """移除一个清理函数。"""
        if dispose in self._items:
            self._items.remove(dispose)

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __bool__(self):
        return bool(self._items)

    async def clear(self) -> List[Callable]:
        """
        返回所有清理函数并清空列表。

        :return: 清理函数列表的副本。
        """
        items = list(self._items)
        self._items.clear()
        return items


async def run_disposable(dispose: Callable) -> Any:
    """
    执行单个清理函数（对标 DSH runDisposable）。

    通过 effect_inertia 去重：如果同一个 dispose 函数正在被另一个
    调用者执行，则等待其完成而不是重复执行。

    :param dispose: 清理函数。
    :return:        清理函数的返回值。
    """
    result = dispose()
    pending = _effect_inertia.get(dispose)
    if pending:
        if asyncio.iscoroutine(result):
            return await result
        return result
    return result
