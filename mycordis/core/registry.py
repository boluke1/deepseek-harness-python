# mycordis/core/registry.py
# 注册表：管理插件生命周期，驱动反应性依赖机制

import asyncio
import logging
from typing import Dict, Optional, Set
from .context import Context
from .plugin import Plugin

logger = logging.getLogger(__name__)

class Fiber:
    """纤程：插件在运行时的实例"""
    def __init__(self, name: str, plugin: Plugin, ctx: Context):
        self.name = name
        self.plugin = plugin
        self.ctx = ctx
        self.state: str = 'inactive'  # inactive, loading, active, unloading, failed


class Registry:
    def __init__(self):
        self._root_ctx = Context()
        self._fibers: Dict[str, Fiber] = {}
        self._pending: Set[str] = set()
        self._reconciling: bool = False

    async def register(self, name: str, plugin: Plugin) -> None:
        """注册一个插件（若已存在则忽略）"""
        if name in self._fibers:
            logger.warning(f"Plugin '{name}' already registered, skipping.")
            return

        plugin_ctx = self._root_ctx.isolate()
        fiber = Fiber(name, plugin, plugin_ctx)
        self._fibers[name] = fiber
        self._pending.add(name)
        logger.info(f"Plugin '{name}' registered (pending).")
        await self._reconcile()

    async def unregister(self, name: str) -> None:
        """主动卸载插件（彻底删除纤程）"""
        if name not in self._fibers:
            return
        # 先执行内部卸载（清理副作用，置为 inactive）
        await self._unload_plugin_internal(name)
        # 彻底删除
        if name in self._fibers:
            del self._fibers[name]
            self._pending.discard(name)
            logger.info(f"Plugin '{name}' completely removed.")
        await self._reconcile()

    async def _unload_plugin_internal(self, name: str) -> None:
        """
        内部卸载：将插件变为 inactive 状态，但保留纤程。
        清空子上下文，以便重新激活时干净。
        """
        if name not in self._fibers:
            return
        fiber = self._fibers[name]
        if fiber.state == 'inactive':
            return

        # 执行插件注册的所有副作用（disposers）
        await fiber.ctx.revert()

        # ★ 关键修复：清空子上下文中的服务，防止残留
        fiber.ctx._services.clear()
        fiber.ctx._providers.clear()

        # 从根上下文中移除该插件提供的服务
        for key, provider in list(self._root_ctx._providers.items()):
            if provider == name:
                del self._root_ctx._services[key]
                del self._root_ctx._providers[key]
                logger.info(f"Removed service '{key}' provided by '{name}'")

        fiber.state = 'inactive'
        self._pending.add(name)   # 加入待激活队列
        logger.info(f"Plugin '{name}' set to inactive (awaiting dependencies).")

    async def _reconcile(self) -> None:
        """
        核心协调循环：反复检查所有插件，激活依赖满足的，停用依赖丢失的。
        """
        if self._reconciling:
            return
        self._reconciling = True

        try:
            changed = True
            iteration = 0
            while changed and iteration < 20:
                changed = False
                iteration += 1

                # ---- 第一阶段：激活可以激活的插件 ----
                for name, fiber in list(self._fibers.items()):
                    if fiber.state != 'inactive':
                        continue

                    deps_ready = True
                    for dep_key in fiber.plugin.inject:
                        if dep_key not in self._root_ctx._services:
                            deps_ready = False
                            break

                    if deps_ready:
                        logger.info(f"Activating plugin '{name}'...")
                        fiber.state = 'loading'
                        try:
                            await fiber.plugin.apply(fiber.ctx)

                            # 将插件提供的服务提升到根上下文
                            for svc_key in fiber.plugin.provide:
                                if svc_key in fiber.ctx._services:
                                    self._root_ctx.provide(
                                        svc_key,
                                        fiber.ctx._services[svc_key],
                                        name
                                    )
                                else:
                                    logger.warning(
                                        f"Plugin '{name}' declared provide '{svc_key}' "
                                        f"but did not call ctx.provide()."
                                    )

                            fiber.state = 'active'
                            self._pending.discard(name)
                            changed = True
                            logger.info(f"Plugin '{name}' successfully activated.")

                        except Exception as e:
                            logger.error(f"Plugin '{name}' activation failed: {e}")
                            fiber.state = 'failed'
                            await fiber.ctx.revert()
                            # 清理可能已注册的部分服务
                            for svc_key in fiber.plugin.provide:
                                if svc_key in self._root_ctx._services and self._root_ctx._providers.get(svc_key) == name:
                                    del self._root_ctx._services[svc_key]
                                    del self._root_ctx._providers[svc_key]
                            self._pending.discard(name)

                # ---- 第二阶段：检查活跃插件是否丢失依赖 ----
                for name, fiber in list(self._fibers.items()):
                    if fiber.state != 'active':
                        continue

                    dep_lost = False
                    for dep_key in fiber.plugin.inject:
                        if dep_key not in self._root_ctx._services:
                            dep_lost = True
                            break

                    if dep_lost:
                        logger.info(f"Plugin '{name}' lost dependencies, unloading...")
                        await self._unload_plugin_internal(name)
                        changed = True
                        break  # 重新开始 while 循环

                if not changed:
                    break

            if iteration >= 20:
                logger.warning("Reconcile loop reached iteration limit. Possible cycle.")

        finally:
            self._reconciling = False

    def dump_state(self) -> None:
        """调试：打印当前状态"""
        print("\n========== Registry State ==========")
        print(f"Root Context Services: {list(self._root_ctx._services.keys())}")
        print("Fibers:")
        for name, fiber in self._fibers.items():
            print(f"  - {name}: state={fiber.state}, inject={fiber.plugin.inject}, provide={fiber.plugin.provide}")
        print("=====================================\n")