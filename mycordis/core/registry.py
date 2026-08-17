# ============================================================================
# mycordis/core/registry.py
# 注册表（Registry）：整个插件系统的"总控制器"与"反应式协调器"。
#
# 设计意图：
#   Registry 统一管理所有插件的生命周期——注册、激活、停用、卸载，
#   并驱动"反应性依赖管理"机制：
#     · 当某插件的依赖全部满足时，自动激活它（调用 plugin.apply）。
#     · 当某插件的依赖丢失时，自动停用它（卸载其提供的服务与副作用）。
#     · 当依赖恢复后，再次自动激活，实现依赖变化的自愈循环。
#
#   核心数据结构：
#     · _root_ctx（根上下文）：所有插件提供的服务被"提升"到这里，全局可见。
#     · _fibers（纤程表）：每个插件对应一个 Fiber，保存其运行时状态与专属上下文。
#     · _pending（待激活集合）：记录等待依赖满足的插件，供协调循环关注。
#     · _reconciling（协调锁）：防止 _reconcile 重入，避免无限递归。
# ============================================================================

import asyncio
import logging
from typing import Dict, Optional, Set
from .context import Context
from .plugin import Plugin

# 本模块的日志记录器，用于输出插件生命周期相关日志。
logger = logging.getLogger(__name__)


class Fiber:
    """
    纤程：插件在运行时的"实例状态"封装。

    把"插件定义"（Plugin）与其"运行时状态"解耦：
    - Plugin 是静态的类模板（inject/provide/apply）。
    - Fiber 是动态的运行实例（state/ctx）。

    每个注册的插件对应唯一一个 Fiber。
    """

    def __init__(self, name: str, plugin: Plugin, ctx: Context):
        """
        初始化一个纤程。

        :param name:   插件名（作为 _fibers 的键，全局唯一）。
        :param plugin: 插件对象（提供 inject/provide/apply 等契约）。
        :param ctx:    该插件专属的隔离子上下文（由根上下文 isolate 而来），
                       插件的服务与副作用都登记于此。
        """
        # 插件名。
        self.name = name

        # 插件对象。
        self.plugin = plugin

        # 插件专属的隔离子上下文。
        self.ctx = ctx

        # 当前状态机：
        #   'inactive'  尚未激活（依赖未满足或刚被停用），等待协调循环激活。
        #   'loading'   正在执行 apply，激活进行中。
        #   'active'    已成功激活，服务已提升到根上下文。
        #   'unloading' 正在卸载（本实现中卸载为同步完成，该状态保留语义预留）。
        #   'failed'    apply 抛出异常，激活失败。
        self.state: str = 'inactive'  # inactive, loading, active, unloading, failed


class Registry:
    """
    注册表：管理插件生命周期，驱动反应性依赖机制。
    """

    def __init__(self):
        """
        初始化注册表：创建根上下文，并准备纤程表、待激活集合与协调锁。

        根上下文（_root_ctx）不归属任何插件，是全局服务的最终存储位置；
        各插件通过它 isolate 出各自的子上下文。
        """
        # 根上下文：所有插件"提升"后的服务最终都集中存储于此，供全部插件查找。
        self._root_ctx = Context()

        # 纤程表：key 为插件名，value 为对应的 Fiber（插件运行时状态）。
        self._fibers: Dict[str, Fiber] = {}

        # 待激活集合：记录处于 'pending'（等待依赖满足）的插件名，
        # 供协调循环识别"还有哪些插件等着被激活"。
        self._pending: Set[str] = set()

        # 协调锁：标志当前是否正处于 _reconcile 循环中，防止重入。
        self._reconciling: bool = False

    # ------------------------------------------------------------------
    # 公开接口：注册 / 卸载
    # ------------------------------------------------------------------
    async def register(self, name: str, plugin: Plugin) -> None:
        """
        注册一个新插件：创建 Fiber，加入 _fibers，并触发协调循环尝试激活。

        若同名插件已注册，则忽略并给出警告（保证插件名全局唯一）。

        :param name:   插件名（全局唯一）。
        :param plugin: 插件对象。
        """
        # 防重复注册：同名插件已存在则直接跳过。
        if name in self._fibers:
            logger.warning(f"Plugin '{name}' already registered, skipping.")
            return

        # 为该插件创建专属的隔离子上下文（parent 指向根上下文）。
        plugin_ctx = self._root_ctx.isolate()

        # 创建纤程，并登记到纤程表。
        fiber = Fiber(name, plugin, plugin_ctx)
        self._fibers[name] = fiber

        # 标记为待激活（pending），供协调循环处理。
        self._pending.add(name)
        logger.info(f"Plugin '{name}' registered (pending).")

        # 触发协调循环，尝试激活该插件（及其依赖被满足的其他待激活插件）。
        await self._reconcile()

    async def unregister(self, name: str) -> None:
        """
        主动卸载插件：先执行内部卸载清理，再彻底删除纤程（用户显式调用）。

        与"依赖丢失导致的停用"不同，这里是完全移除：
        卸载后该插件不再存在于纤程表中，也不会被再次自动激活。

        :param name: 要卸载的插件名。
        """
        # 插件不存在则直接返回。
        if name not in self._fibers:
            return

        # 1) 先执行内部卸载（执行 revert 清理副作用、移除其服务、置为 inactive）。
        await self._unload_plugin_internal(name)

        # 2) 彻底删除纤程（区别于停用，此处从纤程表中移除）。
        if name in self._fibers:
            del self._fibers[name]
            self._pending.discard(name)
            logger.info(f"Plugin '{name}' completely removed.")

        # 3) 重新协调：可能因此导致依赖该插件的其他插件停用。
        await self._reconcile()

    # ------------------------------------------------------------------
    # 内部：卸载逻辑
    # ------------------------------------------------------------------
    async def _unload_plugin_internal(self, name: str) -> None:
        """
        内部卸载：将插件置为 'inactive' 状态，但保留纤程（以便依赖恢复后重新激活）。

        执行步骤：
            1. 调用 fiber.ctx.revert()，按 LIFO 执行该插件登记的全部清理副作用。
            2. 清空子上下文中的服务与提供者索引，避免残留污染。
            3. 从根上下文移除该插件提供的所有服务。
            4. 将 state 置为 'inactive'，并重新加入待激活集合，等待依赖满足后再次激活。

        :param name: 要停用的插件名。
        """
        # 插件不存在则直接返回。
        if name not in self._fibers:
            return

        fiber = self._fibers[name]

        # 若已是 inactive，无需重复卸载。
        if fiber.state == 'inactive':
            return

        # 1) 执行插件登记的所有副作用（disposers），释放资源、撤销改动。
        await fiber.ctx.revert()

        # 2) ★ 关键：清空子上下文中的服务与提供者索引，防止残留导致下次激活出错。
        fiber.ctx._services.clear()
        fiber.ctx._providers.clear()

        # 3) 从根上下文移除该插件提供的服务（根据 _providers 溯源精确删除）。
        for key, provider in list(self._root_ctx._providers.items()):
            if provider == name:
                del self._root_ctx._services[key]
                del self._root_ctx._providers[key]
                logger.info(f"Removed service '{key}' provided by '{name}'")

        # 4) 更新状态：置为 inactive，并加入待激活队列，等待依赖满足后再次激活。
        fiber.state = 'inactive'
        self._pending.add(name)   # 加入待激活队列
        logger.info(f"Plugin '{name}' set to inactive (awaiting dependencies).")

    # ------------------------------------------------------------------
    # 核心：反应式协调循环
    # ------------------------------------------------------------------
    async def _reconcile(self) -> None:
        """
        核心协调循环：反复扫描所有纤程，执行两阶段驱动插件状态迁移。

        第一阶段（激活）：对 state == 'inactive' 的插件，检查其 inject 依赖
                          是否全部存在于根上下文；若满足则调用 plugin.apply，
                          成功后把其 provide 声明的服务提升到根上下文，置为 active。

        第二阶段（停用）：对 state == 'active' 的插件，检查其 inject 依赖
                          是否仍然存在；若任一依赖丢失，则调用 _unload_plugin_internal
                          使其回到 inactive。

        循环持续进行，直到一轮扫描内没有任何状态变化（达到"安静"状态），
        或达到迭代上限（防循环）为止。
        """
        # 防重入：若已有协调循环在进行，直接返回，避免递归死循环。
        if self._reconciling:
            return
        self._reconciling = True

        try:
            changed = True       # 本轮是否有状态变化
            iteration = 0        # 迭代计数，防止死循环

            # 反复协调，直到没有变化为止（最多 20 轮）。
            while changed and iteration < 20:
                changed = False
                iteration += 1

                # ---- 第一阶段：激活可以激活的插件 ----
                # 遍历纤程表副本，避免遍历中修改字典导致的运行时错误。
                for name, fiber in list(self._fibers.items()):
                    # 只处理处于 inactive 的插件。
                    if fiber.state != 'inactive':
                        continue

                    # 检查依赖是否全部满足：inject 中的每个服务都必须在根上下文存在。
                    deps_ready = True
                    for dep_key in fiber.plugin.inject:
                        if dep_key not in self._root_ctx._services:
                            deps_ready = False
                            break

                    if deps_ready:
                        # 依赖满足，开始激活。
                        logger.info(f"Activating plugin '{name}'...")
                        fiber.state = 'loading'   # 进入加载中状态

                        try:
                            # 调用插件的 apply 方法（获取依赖、注册服务、登记副作用）。
                            await fiber.plugin.apply(fiber.ctx)

                            # 将插件提供的服务从子上下文"提升"到根上下文，供全局发现。
                            for svc_key in fiber.plugin.provide:
                                if svc_key in fiber.ctx._services:
                                    # 提升：以插件名作为提供者登记到根上下文。
                                    self._root_ctx.provide(
                                        svc_key,
                                        fiber.ctx._services[svc_key],
                                        name
                                    )
                                else:
                                    # 插件声明了 provide 却没有实际调用 ctx.provide，给出警告。
                                    logger.warning(
                                        f"Plugin '{name}' declared provide '{svc_key}' "
                                        f"but did not call ctx.provide()."
                                    )

                            # 激活成功：置为 active，移出待激活集合，标记本轮有变化。
                            fiber.state = 'active'
                            self._pending.discard(name)
                            changed = True
                            logger.info(f"Plugin '{name}' successfully activated.")

                        except Exception as e:
                            # 激活失败：记录错误，执行已登记的副作用清理，并清理部分服务。
                            logger.error(f"Plugin '{name}' activation failed: {e}")
                            fiber.state = 'failed'
                            await fiber.ctx.revert()

                            # 清理 apply 中途可能已提升到根上下文的部分服务（精确匹配提供者）。
                            for svc_key in fiber.plugin.provide:
                                if (svc_key in self._root_ctx._services
                                        and self._root_ctx._providers.get(svc_key) == name):
                                    del self._root_ctx._services[svc_key]
                                    del self._root_ctx._providers[svc_key]

                            # 从待激活集合移除，避免其被反复尝试激活。
                            self._pending.discard(name)

                # ---- 第二阶段：检查活跃插件是否丢失依赖 ----
                for name, fiber in list(self._fibers.items()):
                    # 只处理处于 active 的插件。
                    if fiber.state != 'active':
                        continue

                    # 检查依赖是否仍然存在：inject 中的服务是否仍在根上下文。
                    dep_lost = False
                    for dep_key in fiber.plugin.inject:
                        if dep_key not in self._root_ctx._services:
                            dep_lost = True
                            break

                    if dep_lost:
                        # 依赖丢失：停用该插件，标记有变化，并重新开始循环（break 出本阶段）。
                        logger.info(f"Plugin '{name}' lost dependencies, unloading...")
                        await self._unload_plugin_internal(name)
                        changed = True
                        break  # 重新开始 while 循环

                # 本轮无任何变化，说明已达"安静"状态，退出协调循环。
                if not changed:
                    break

            # 若循环因迭代上限退出，说明可能存在循环依赖（A 依赖 B，B 又依赖 A）。
            if iteration >= 20:
                logger.warning("Reconcile loop reached iteration limit. Possible cycle.")

        finally:
            # 无论成功或异常，最终都要释放协调锁，允许后续再次协调。
            self._reconciling = False

    # ------------------------------------------------------------------
    # 调试
    # ------------------------------------------------------------------
    def dump_state(self) -> None:
        """
        调试方法：打印当前所有插件的状态、根上下文服务以及各插件的依赖/提供声明。
        便于观察系统运行时各插件的生命周期与服务注册情况。
        """
        print("\n========== Registry State ==========")
        print(f"Root Context Services: {list(self._root_ctx._services.keys())}")
        print("Fibers:")
        for name, fiber in self._fibers.items():
            print(f"  - {name}: state={fiber.state}, inject={fiber.plugin.inject}, provide={fiber.plugin.provide}")
        print("=====================================\n")
