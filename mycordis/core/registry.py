# ============================================================================
# mycordis/core/registry.py
# 注册表（Registry）：插件系统的"总控制器"与"反应式协调器"。
#
# 对标 DSH cordis/src/registry.ts + fiber.ts：
#   · Runtime 概念：同一插件类可多次 ctx.plugin() 创建多个 fiber
#   · 精确通知：服务变化时只重新检查依赖该服务的 fiber（对标 reflect.notify）
#   · 多形态插件：支持类插件、函数插件、对象插件
#   · 反应式协调：依赖满足自动激活、丢失自动停用
#   · ★ 集成完整 Fiber 生命周期：effect/reload/update/restart/dispose
#   · ★ 内部事件协议：internal/plugin, internal/status
#   · ★ 根 Fiber：全局根 fiber（对标 DSH root fiber）
# ============================================================================

import logging
from typing import Any, Dict, List, Optional, Set, Type

from .context import Context
from .plugin import Plugin, resolve_plugin
from .fiber import Fiber, FiberState

logger = logging.getLogger(__name__)


class Runtime:
    """
    运行时：同一插件回调的所有活跃 fiber 的共享记录。
    对标 DSH Plugin.Runtime。
    """

    def __init__(self, name: str, callback: type):
        self.name = name
        self.callback = callback
        self.fibers: List[str] = []


class Registry:
    """
    注册表：管理插件生命周期，驱动反应式依赖机制。
    """

    def __init__(self):
        """
        初始化注册表：创建根上下文、根 fiber，并准备纤程表、待激活集合与协调锁。
        """
        # 根上下文：全局服务的最终存储位置。
        self._root_ctx = Context()

        # 纤程表：key 为插件名，value 为对应的 Fiber。
        self._fibers: Dict[str, Fiber] = {}

        # 待激活集合。
        self._pending: Set[str] = set()

        # 协调锁。
        self._reconciling: bool = False

        # 运行时表：同一插件类可注册多个实例（多 fiber）。
        self._runtimes: Dict[type, Runtime] = {}

        # ★ UID 计数器（对标 DSH registry.counter）。
        self._counter: int = 0

        # ★ 创建根 fiber（对标 DSH root fiber）。
        # 根 fiber 的 uid=0，直接使用根上下文，不可被 dispose。
        root_fiber = Fiber(
            name='root',
            plugin=None,
            ctx=self._root_ctx,
            scope_label=None,
            inject_map={},
            runtime=None,
            config={},
        )
        root_fiber.uid = 0
        self._root_ctx.fiber = root_fiber
        self._root_fiber = root_fiber

        # ★ 自动创建 LoggerService（对标 DSH 内置 logger 服务）。
        # 必须在所有插件加载之前，因为 Service 子类的 init() 钩子会使用 ctx.logger。
        from .logger import LoggerService
        LoggerService(self._root_ctx, "logger")

        # ★ 自动创建核心子系统服务（对标 DSH core/* 包）。
        # scope：作用域管理（per-agent 注册空间）。
        from .scope import ScopeService
        ScopeService(self._root_ctx, "scope")

        # identity：身份与凭证管理。
        from .identity import IdentityService
        IdentityService(self._root_ctx, "identity")

        # invariant：不变式运行时强制（model-visible = logged）。
        from .invariant import InvariantService
        InvariantService(self._root_ctx, "invariant")

        # contextService：上下文生命周期管理与诊断。
        from .context_service import ContextService
        ContextService(self._root_ctx, "contextService")

    # ------------------------------------------------------------------
    # 公开接口：注册 / 卸载
    # ------------------------------------------------------------------
    async def register(self, name: str, plugin: Any) -> None:
        """
        注册一个新插件。支持类插件、函数插件、对象插件。

        :param name:   插件名（全局唯一）。
        :param plugin: 插件对象（Plugin 实例、async 函数、或带 apply 的对象）。
        """
        if name in self._fibers:
            logger.warning(f"Plugin '{name}' already registered, skipping.")
            return

        # 解析插件形态。
        resolved = resolve_plugin(plugin)
        if resolved is None:
            raise TypeError(
                f"Invalid plugin: expected Plugin instance, function, or object with apply(), "
                f"got {type(plugin)}"
            )

        # 记录运行时（支持同一插件类多实例）。
        plugin_cls = type(plugin) if isinstance(plugin, Plugin) else type(resolved)
        if plugin_cls not in self._runtimes:
            self._runtimes[plugin_cls] = Runtime(name, plugin_cls)
        runtime = self._runtimes[plugin_cls]
        runtime.fibers.append(name)

        # ★ 分配 UID（对标 DSH registry.counter）。
        self._counter += 1

        # 创建纤程。
        scope_label = object()
        inject_map = {dep: None for dep in resolved.inject}
        plugin_ctx = self._root_ctx.isolate(name, scope_label)

        fiber = Fiber(
            name=name,
            plugin=resolved,
            ctx=plugin_ctx,
            scope_label=scope_label,
            inject_map=inject_map,
            runtime=runtime,
            config={},
        )
        fiber.uid = self._counter
        # ★ 设置 fiber 引用到子上下文。
        plugin_ctx.fiber = fiber

        self._fibers[name] = fiber

        self._pending.add(name)

        # ★ 发射 internal/plugin 事件（对标 DSH internal/plugin）。
        try:
            self._root_ctx.emit('internal/plugin', fiber)
        except Exception:
            pass

        logger.info(f"Plugin '{name}' registered (pending).")

        await self._reconcile()

    async def load_plan(self, plan: list) -> None:
        """
        按加载计划注册一批插件（对标 DSH 从配置树加载）。

        :param plan: 加载计划列表。
        """
        for entry in plan:
            plugin_cls = entry["plugin"]
            entry_id = entry["id"]
            config = entry.get("config", {})
            try:
                plugin = plugin_cls(**config)
            except TypeError:
                plugin = plugin_cls()
            await self.register(entry_id, plugin)

    async def unregister(self, name: str) -> None:
        """
        主动卸载插件：先执行内部卸载清理，再彻底删除纤程。

        :param name: 要卸载的插件名。
        """
        if name not in self._fibers:
            return

        await self._unload_plugin_internal(name)

        if name in self._fibers:
            fiber = self._fibers[name]
            # 清理运行时记录。
            plugin_cls = type(fiber.plugin)
            if plugin_cls in self._runtimes:
                rt = self._runtimes[plugin_cls]
                if name in rt.fibers:
                    rt.fibers.remove(name)
                if not rt.fibers:
                    del self._runtimes[plugin_cls]

            del self._fibers[name]
            self._pending.discard(name)
            logger.info(f"Plugin '{name}' completely removed.")

        await self._reconcile()

    # ------------------------------------------------------------------
    # 内部：卸载逻辑
    # ------------------------------------------------------------------
    async def _unload_plugin_internal(self, name: str) -> None:
        """
        内部卸载：将插件置为 'inactive' 状态，执行清理。

        :param name: 要停用的插件名。
        """
        if name not in self._fibers:
            return

        fiber = self._fibers[name]
        # ★ 检查 fiber 是否处于活跃状态（非 PENDING/INACTIVE）。
        if fiber.state in (FiberState.PENDING, FiberState.INACTIVE):
            return

        # 1) 执行插件登记的所有副作用（含 provide 的反注册），释放资源。
        await fiber.ctx.revert()

        # 2) 从根上下文移除该插件提升上去的服务。
        store = self._root_ctx._store
        providers = self._root_ctx._providers
        scopes = self._root_ctx._scopes

        removed_services = []
        for key in list(store.keys()):
            if providers.get(key) == name:
                del store[key]
                del providers[key]
                scopes.pop(key, None)
                removed_services.append(key)
                logger.info(f"Removed service '{key}' provided by '{name}'")

        # 3) ★ 使用新的 epoch 机制重置状态（对标 DSH _setEpoch(INACTIVE)）。
        fiber._epoch = '__INACTIVE__'
        fiber._error = None
        fiber.mark_not_ready()
        self._pending.add(name)
        logger.info(f"Plugin '{name}' set to inactive (awaiting dependencies).")

        # 4) 精确通知：告知依赖已变化服务的 fiber。
        if removed_services:
            await self._notify_dependents(removed_services)

    # ------------------------------------------------------------------
    # 精确通知（对标 DSH reflect.notify）
    # ------------------------------------------------------------------
    async def _notify_dependents(self, changed_names: List[str]) -> None:
        """
        当某些服务变化时，只重新检查依赖这些服务的 fiber。

        :param changed_names: 变化的服务名列表。
        """
        for fiber_name, fiber in list(self._fibers.items()):
            # ★ 只通知等待中的 fiber。
            if fiber.state not in (FiberState.PENDING, FiberState.INACTIVE):
                continue
            for name in changed_names:
                if name in fiber._inject_map:
                    logger.info(
                        f"Plugin '{fiber_name}' notified: dependency '{name}' changed"
                    )
                    break

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
                    # ★ 检查 fiber 是否处于等待状态（PENDING 或 INACTIVE）。
                    if fiber.state not in (FiberState.PENDING, FiberState.INACTIVE):
                        continue

                    deps_ready = all(
                        self._can_resolve(dep_key)
                        for dep_key in fiber.plugin.inject
                    )

                    if deps_ready:
                        logger.info(f"Activating plugin '{name}'...")
                        # ★ 设置 epoch 为非 INACTIVE（对标 DSH _setEpoch('')）。
                        fiber._epoch = ''

                        try:
                            # ★ 解析配置（对标 DSH _resolveConfig）。
                            fiber.config = await fiber._resolve_config(fiber._config)
                            # ★ 执行插件回调。
                            await fiber._execute(fiber._context, fiber.config)

                            # 共享存储模式下，服务已在根 store 中（子上下文与根共享）。
                            # 只需验证服务存在。
                            for svc_key in fiber.plugin.provide:
                                if svc_key not in fiber.ctx._store:
                                    logger.warning(
                                        f"Plugin '{name}' declared provide '{svc_key}' "
                                        f"but did not call ctx.provide()."
                                    )
                                # 共享存储下，服务已可见，无需额外 promote。

                            # ★ 标记成功（对标 DSH _error = undefined）。
                            fiber._error = None
                            fiber.mark_ready()
                            self._pending.discard(name)
                            changed = True

                            # ★ 发射 internal/status 事件。
                            try:
                                self._root_ctx.emit('internal/status', fiber, FiberState.INACTIVE)
                            except Exception:
                                pass

                            logger.info(f"Plugin '{name}' successfully activated.")

                        except Exception as e:
                            logger.error(f"Plugin '{name}' activation failed: {e}")
                            # ★ 记录错误并重置 epoch。
                            fiber._error = e
                            fiber._epoch = '__INACTIVE__'
                            await fiber.ctx.revert()

                            # 清理 apply 中途可能已提升到根上下文的部分服务。
                            store = self._root_ctx._store
                            providers = self._root_ctx._providers
                            scopes = self._root_ctx._scopes
                            for svc_key in fiber.plugin.provide:
                                if (svc_key in store
                                        and providers.get(svc_key) == name):
                                    del store[svc_key]
                                    del providers[svc_key]
                                    scopes.pop(svc_key, None)

                            self._pending.discard(name)

                # ---- 第二阶段：检查活跃插件是否丢失依赖 ----
                for name, fiber in list(self._fibers.items()):
                    if fiber.state != FiberState.ACTIVE:
                        continue

                    dep_lost = any(
                        not self._can_resolve(dep_key)
                        for dep_key in fiber.plugin.inject
                    )

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
        """判断某服务是否能在根上下文解析。"""
        try:
            self._root_ctx.get(key)
            return True
        except KeyError:
            return False

    # ------------------------------------------------------------------
    # 调试
    # ------------------------------------------------------------------
    def dump_state(self) -> None:
        """调试方法：打印当前所有插件的状态、根上下文服务。"""
        print("\n========== Registry State ==========")
        print(f"Root Context Services: {list(self._root_ctx._store.keys())}")
        print("Runtimes:")
        for cls, rt in self._runtimes.items():
            print(f"  - {rt.name}: fibers={rt.fibers}")
        print("Fibers:")
        for name, fiber in self._fibers.items():
            print(f"  - {name}: state={fiber.state}, inject={fiber.plugin.inject}, provide={fiber.plugin.provide}")
        print("=====================================\n")
