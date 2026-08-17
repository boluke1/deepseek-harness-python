# ============================================================================
# mycordis/core/registry.py
# 注册表（Registry）：整个插件系统的"总控制器"与"反应式协调器"。
#
# 设计意图（对标 DeepSeek Harness / Cordis 的 reflect.ts + fiber.ts）：
#   Registry 统一管理所有插件的生命周期——注册、激活、停用、卸载，
#   并驱动"反应性依赖管理"机制：
#     · 当某插件的依赖全部满足时，自动激活它（调用 plugin.apply）。
#     · 当某插件的依赖丢失时，自动停用它（卸载其提供的服务与副作用）。
#     · 当依赖恢复后，再次自动激活，实现依赖变化的自愈循环。
#
#   ★ 本版本对标 DSH 的适配：
#     · 每个插件用 isolate(name, 唯一标签) 获得独立作用域上下文（标签隔离）。
#     · provide 在 context.py 已是"可逆副作用"（自动反注册）。
#     · 插件提供的服务被"提升"到根上下文供全局解析。
#
#   核心数据结构：
#     · _root_ctx（根上下文）：全局服务的最终存储位置，各插件依赖从这里解析。
#     · _fibers（纤程表）：每个插件对应一个 Fiber，保存运行时状态与专属上下文。
#     · _pending（待激活集合）：记录等待依赖满足的插件。
#     · _reconciling（协调锁）：防止 _reconcile 重入。
# ============================================================================

import logging
from typing import Dict, Optional, Set

from .context import Context
from .plugin import Plugin

# 本模块的日志记录器。
logger = logging.getLogger(__name__)


class Fiber:
    """
    纤程：插件在运行时的"实例状态"封装。

    把"插件定义"（Plugin）与其"运行时状态"解耦：
    - Plugin 是静态的类模板（inject/provide/apply）。
    - Fiber 是动态的运行实例（state/ctx）。
    """

    def __init__(self, name: str, plugin: Plugin, ctx: Context, scope_label: object):
        """
        初始化一个纤程。

        :param name:        插件名（作为 _fibers 的键，全局唯一）。
        :param plugin:      插件对象。
        :param ctx:         该插件专属的隔离子上下文（由根上下文 isolate 而来）。
        :param scope_label: 该插件的独立作用域标签（用于隔离同名服务）。
        """
        self.name = name
        self.plugin = plugin
        self.ctx = ctx
        self.scope_label = scope_label

        # 当前状态机：
        #   'inactive'  尚未激活（依赖未满足或刚被停用）。
        #   'loading'   正在执行 apply，激活进行中。
        #   'active'    已成功激活，服务已提升到根上下文。
        #   'failed'    apply 抛出异常，激活失败。
        self.state: str = 'inactive'


class Registry:
    """
    注册表：管理插件生命周期，驱动反应性依赖机制。
    """

    def __init__(self):
        """
        初始化注册表：创建根上下文，并准备纤程表、待激活集合与协调锁。
        """
        # 根上下文：全局服务的最终存储位置。
        self._root_ctx = Context()

        # 纤程表：key 为插件名，value 为对应的 Fiber。
        self._fibers: Dict[str, Fiber] = {}

        # 待激活集合。
        self._pending: Set[str] = set()

        # 协调锁。
        self._reconciling: bool = False

    # ------------------------------------------------------------------
    # 公开接口：注册 / 卸载
    # ------------------------------------------------------------------
    async def register(self, name: str, plugin: Plugin) -> None:
        """
        注册一个新插件：创建 Fiber，加入 _fibers，并触发协调循环尝试激活。

        :param name:   插件名（全局唯一）。
        :param plugin: 插件对象。
        """
        if name in self._fibers:
            logger.warning(f"Plugin '{name}' already registered, skipping.")
            return

        # ★ 每个插件获得独立作用域标签，用于隔离同名服务。
        scope_label = object()
        # 为该插件创建专属的隔离子上下文（parent 指向根上下文，带独立标签）。
        plugin_ctx = self._root_ctx.isolate(name, scope_label)

        # 创建纤程并登记。
        fiber = Fiber(name, plugin, plugin_ctx, scope_label)
        self._fibers[name] = fiber

        self._pending.add(name)
        logger.info(f"Plugin '{name}' registered (pending).")

        await self._reconcile()

    async def load_plan(self, plan: list) -> None:
        """
        按加载计划注册一批插件（对标 DSH 从配置树加载）。

        加载计划是由 ConfigLoader 生成的配置条目列表，每个条目形如：
            {"id": "llm", "plugin": LLMPlugin, "config": {...}}

        :param plan: 加载计划。
        """
        for entry in plan:
            plugin_cls = entry["plugin"]
            entry_id = entry["id"]
            config = entry.get("config", {})
            # 用 config 参数实例化插件。
            try:
                plugin = plugin_cls(**config)
            except TypeError:
                # 插件不接受 config 参数，则直接无参实例化。
                plugin = plugin_cls()
            # 以 entry_id 作为注册名。
            await self.register(entry_id, plugin)


    async def unregister(self, name: str) -> None:
        """
        主动卸载插件：先执行内部卸载清理，再彻底删除纤程（用户显式调用）。

        :param name: 要卸载的插件名。
        """
        if name not in self._fibers:
            return

        await self._unload_plugin_internal(name)

        if name in self._fibers:
            del self._fibers[name]
            self._pending.discard(name)
            logger.info(f"Plugin '{name}' completely removed.")

        await self._reconcile()

    # ------------------------------------------------------------------
    # 内部：卸载逻辑
    # ------------------------------------------------------------------
    async def _unload_plugin_internal(self, name: str) -> None:
        """
        内部卸载：将插件置为 'inactive' 状态，但保留纤程（以便依赖恢复后重新激活）。

        ★ 适配新 context：provide 已是可逆副作用，因此：
            1. 调用 fiber.ctx.revert()，按 LIFO 执行该插件的反注册副作用（移除子上下文服务）。
            2. 从根上下文移除该插件提升上去的服务（按提供者名精确删除）。
            3. 将 state 置为 'inactive'，并重新加入待激活集合。

        :param name: 要停用的插件名。
        """
        if name not in self._fibers:
            return

        fiber = self._fibers[name]

        if fiber.state == 'inactive':
            return

        # 1) 执行插件登记的所有副作用（含 provide 的反注册），释放资源。
        await fiber.ctx.revert()

        # 2) 从根上下文移除该插件提升上去的服务（根据 _providers 溯源精确删除）。
        for key, provider in list(self._root_ctx._providers.items()):
            if provider == name:
                del self._root_ctx._services[key]
                del self._root_ctx._providers[key]
                del self._root_ctx._scopes[key]
                logger.info(f"Removed service '{key}' provided by '{name}'")

        # 3) 更新状态：置为 inactive，并加入待激活队列。
        fiber.state = 'inactive'
        self._pending.add(name)
        logger.info(f"Plugin '{name}' set to inactive (awaiting dependencies).")

    # ------------------------------------------------------------------
    # 核心：反应式协调循环
    # ------------------------------------------------------------------
    async def _reconcile(self) -> None:
        """
        核心协调循环：反复扫描所有纤程，执行两阶段驱动插件状态迁移。

        第一阶段（激活）：对 inactive 的插件，检查其 inject 依赖是否都可在
                          根上下文解析；若满足则调用 plugin.apply，成功后把其
                          provide 声明的服务提升到根上下文，置为 active。

        第二阶段（停用）：对 active 的插件，检查其 inject 依赖是否仍在根上下文；
                          若任一依赖丢失，则调用 _unload_plugin_internal 停用。

        循环持续到一轮内无变化，或达到迭代上限。
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

                # ---- 第一阶段：激活 ----
                for name, fiber in list(self._fibers.items()):
                    if fiber.state != 'inactive':
                        continue

                    deps_ready = True
                    for dep_key in fiber.plugin.inject:
                        if not self._can_resolve(dep_key):
                            deps_ready = False
                            break

                    if deps_ready:
                        logger.info(f"Activating plugin '{name}'...")
                        fiber.state = 'loading'

                        try:
                            await fiber.plugin.apply(fiber.ctx)

                            # 将插件提供的服务从子上下文"提升"到根上下文。
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

                            # 清理 apply 中途可能已提升到根上下文的部分服务。
                            for svc_key in fiber.plugin.provide:
                                if (svc_key in self._root_ctx._services
                                        and self._root_ctx._providers.get(svc_key) == name):
                                    del self._root_ctx._services[svc_key]
                                    del self._root_ctx._providers[svc_key]
                                    del self._root_ctx._scopes[svc_key]

                            self._pending.discard(name)

                # ---- 第二阶段：检查活跃插件是否丢失依赖 ----
                for name, fiber in list(self._fibers.items()):
                    if fiber.state != 'active':
                        continue

                    dep_lost = False
                    for dep_key in fiber.plugin.inject:
                        if not self._can_resolve(dep_key):
                            dep_lost = True
                            break

                    if dep_lost:
                        logger.info(f"Plugin '{name}' lost dependencies, unloading...")
                        await self._unload_plugin_internal(name)
                        changed = True
                        break

                if not changed:
                    break

            if iteration >= 20:
                logger.warning("Reconcile loop reached iteration limit. Possible cycle.")

        finally:
            self._reconciling = False

    def _can_resolve(self, key: str) -> bool:
        """
        判断某服务是否能在根上下文解析（依赖检查用）。

        :param key: 服务名。
        :return:    能否解析。
        """
        try:
            self._root_ctx.get(key)
            return True
        except KeyError:
            return False

    # ------------------------------------------------------------------
    # 调试
    # ------------------------------------------------------------------
    def dump_state(self) -> None:
        """
        调试方法：打印当前所有插件的状态、根上下文服务。
        """
        print("\n========== Registry State ==========")
        print(f"Root Context Services: {list(self._root_ctx._services.keys())}")
        print("Fibers:")
        for name, fiber in self._fibers.items():
            print(f"  - {name}: state={fiber.state}, inject={fiber.plugin.inject}, provide={fiber.plugin.provide}")
        print("=====================================\n")
