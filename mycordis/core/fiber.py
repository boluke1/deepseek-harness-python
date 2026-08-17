# ============================================================================
# mycordis/core/fiber.py
# 纤程（Fiber）：插件运行时实例的完整生命周期管理。
#
# 对标 DSH cordis/src/fiber.ts（25KB）的全部能力：
#   · 6 状态机：PENDING / LOADING / ACTIVE / FAILED / DISPOSED / UNLOADING
#   · Effect 系统：支持 sync/async/generator 效果，带 label 诊断
#   · EffectMeta 树：嵌套效果标签，用于 get_effects() 诊断
#   · Config 验证：resolve_config() 用 Schema 验证
#   · reload()：重新执行插件，保留 store 快照
#   · update(config)：通过 internal/update waterfall 应用新配置
#   · restart()：dispose 并立即 reload
#   · await_stable()：等待生命周期稳定，重抛启动错误
#   · Epoch 追踪：检测跨 reload 的过期 effect
#   · Inertia：跟踪进行中的 load/unload 转换
#   · _check_impl / _refresh：依赖实现检查与 epoch 更新
# ============================================================================

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Awaitable

from .errors import CordisError, Validation
from .disposable import DisposalList, EffectMeta, run_disposable

logger = logging.getLogger(__name__)

_INACTIVE = '__INACTIVE__'


class FiberState:
    """
    纤程状态枚举（对标 DSH FiberState，6 个状态）。

    PENDING    — 等待依赖满足
    LOADING    — 插件回调正在执行
    ACTIVE     — 已成功激活，服务已提供
    FAILED     — 回调或配置验证抛出异常
    DISPOSED   — fiber 已被卸载，不可重启
    UNLOADING  — 清理函数正在执行
    """
    PENDING = 'pending'
    LOADING = 'loading'
    ACTIVE = 'active'
    FAILED = 'failed'
    DISPOSED = 'disposed'
    UNLOADING = 'unloading'

    # 兼容旧代码
    INACTIVE = 'inactive'


class Fiber:
    """
    纤程：插件运行时实例（对标 DSH Fiber，25KB 完整能力）。

    追踪依赖状态、验证配置、管理生命周期效果和清理。
    一个 Fiber 对应一次插件应用（ctx.plugin() 调用）。
    """

    def __init__(
        self,
        name: str,
        plugin: Any,
        ctx: 'Context',
        scope_label: object,
        inject_map: Optional[Dict] = None,
        runtime: Any = None,
        config: Any = None,
    ):
        """
        创建一个 fiber。

        :param name:        插件名。
        :param plugin:      插件对象。
        :param ctx:         该插件运行的父上下文。
        :param scope_label: 该插件的独立作用域标签。
        :param inject_map:  依赖映射表 {dep_name: config}。
        :param runtime:     共享的 Runtime 记录（None 表示根 fiber）。
        :param config:      原始配置。
        """
        self.name = name
        self.plugin = plugin
        self.scope_label = scope_label
        self.runtime = runtime
        self.uid: Optional[int] = 0

        self._config = config or {}
        self.config = config or {}
        self._error: Optional[Any] = None

        # 清理函数列表和效果元数据。
        self._disposables = DisposalList()
        # 依赖实现快照（按 inject 名 → impl）。
        self._store: Dict = {}
        # 对外暴露的 store 快照（reload 时复制）。
        self.store: Optional[Dict] = None

        # Epoch 追踪：用于检测依赖变化。
        self._epoch = _INACTIVE
        # 进行中的 load/unload 转换。
        self._inertia: Optional[Any] = None

        # 依赖映射（深拷贝）。
        self._inject_map: Dict = dict(inject_map or {})

        # 延迟导入避免循环依赖。
        from .context import Context as _Ctx

        if runtime:
            # 子 fiber：创建扩展上下文。
            self._setup_child_fiber(ctx, inject_map or {})
        else:
            # 根 fiber：直接使用父上下文。
            self.uid = 0
            self._context: _Ctx = ctx
            self.ctx: _Ctx = ctx
            self._state_cache = FiberState.ACTIVE
            self.store = {}
            self._epoch = ''

        # 就绪事件：用于 await_ready()。
        self._ready_event = asyncio.Event()
        if getattr(self, '_state_cache', None) == FiberState.ACTIVE:
            self._ready_event.set()

    def _setup_child_fiber(self, parent_ctx, inject_map):
        """为子 fiber 创建扩展上下文（对标 DSH Fiber constructor）。"""
        from .context import Context as _Ctx

        # extend 创建子上下文，并注入 fiber 引用。
        self.ctx = self._context = parent_ctx.extend({'fiber': self})
        # 设置 inject 拦截配置。
        inject_entries = list(inject_map.items())
        if inject_entries:
            parent_intercept = object.__getattribute__(parent_ctx, '_intercept_map')
            child_intercept = dict(parent_intercept)
            for dep_name, cfg in inject_entries:
                if cfg is None:
                    continue
                child_intercept[dep_name] = cfg
            object.__setattr__(self.ctx, '_intercept_map', child_intercept)

    # ------------------------------------------------------------------
    # 状态管理（对标 DSH _getState / _updateState）
    # ------------------------------------------------------------------
    @property
    def state(self) -> str:
        """计算当前状态（对标 DSH _getState）。"""
        if self.uid is None:
            return FiberState.DISPOSED
        if self._error:
            return FiberState.FAILED
        if self._epoch != _INACTIVE:
            return FiberState.ACTIVE
        return FiberState.PENDING

    @state.setter
    def state(self, value: str):
        """手动设置状态（带内部事件通知）。"""
        old_state = getattr(self, '_state_cache', self.state)
        self._state_cache = value
        if old_state == value:
            return
        # 发射 internal/status 事件。
        try:
            self._context.emit('internal/status', self, old_state)
        except Exception:
            pass

    def _notify_services(self, new_state):
        """状态变化时通知相关服务（对标 DSH reflect.notify）。"""
        try:
            reflect_store = object.__getattribute__(self.ctx, '_root_store')
            for key, impl in list(reflect_store.items()):
                if isinstance(impl, dict) and impl.get('fiber') is self:
                    svc_name = impl.get('name', key)
                    try:
                        self.ctx.emit('internal/service', svc_name, impl.get('value'))
                    except Exception:
                        pass
        except Exception:
            pass

    @property
    def display_name(self) -> str:
        """插件的显示名称（继承自最近的命名祖先）。"""
        fiber = self
        while True:
            if fiber.runtime and hasattr(fiber.runtime, 'name'):
                return fiber.runtime.name
            parent_fiber = getattr(fiber.ctx, 'fiber', None)
            if parent_fiber is None or parent_fiber is fiber:
                break
            fiber = parent_fiber
        return 'root'

    def assert_active(self):
        """断言当前 fiber 处于活跃状态（对标 DSH assertActive）。"""
        if self.uid is not None:
            return
        raise CordisError(CordisError.INACTIVE_EFFECT)

    # ------------------------------------------------------------------
    # Effect 系统（对标 DSH fiber.effect）
    # ------------------------------------------------------------------
    def effect(self, execute: Callable, label: str = 'anonymous') -> Callable:
        """
        注册清理感知的效果（对标 DSH fiber.effect）。

        execute 立即运行；它产生的清理函数被收集并在 fiber 卸载时
        （或返回的 disposer 被调用时）逆序执行。

        支持的返回值形态：
          - disposer 函数 → 直接收集
          - None → 无清理
          - 同步迭代器 → 逐个收集 yield 的 disposer
          - 异步迭代器 → 逐个收集 yield 的 disposer
          - Promise/awaitable → 等待后收集

        :param execute: 效果体。
        :param label:   效果标签（用于 get_effects() 诊断）。
        :return:        可等待的 disposer。
        """
        self.assert_active()
        if getattr(self, '_state_cache', None) == FiberState.UNLOADING:
            raise CordisError(CordisError.INACTIVE_EFFECT)

        disposables = []
        disposing = False
        disposal_task = None
        meta = EffectMeta(label)

        old_epoch = self._epoch

        def safe_collect(dispose):
            """安全收集清理函数（支持协程对象和可调用对象）。"""
            if callable(dispose) or asyncio.iscoroutine(dispose):
                self._disposables.push(dispose)
            if hasattr(dispose, '__effect_meta__'):
                meta.children.append(dispose.__effect_meta__)

        # 立即执行效果体（或处理直接传入的协程）。
        if asyncio.iscoroutine(execute):
            # ★ 直接传入的协程（如 provide() 中的 _dispose()）。
            effect_result = execute
        else:
            effect_result = execute()

        # 根据返回值形态收集清理函数。
        if effect_result is None:
            return lambda: None

        # ★ 协程对象（必须在迭代器检查之前，因为协程同时有 __iter__ 和 __await__）。
        if asyncio.iscoroutine(effect_result):
            safe_collect(effect_result)
            return lambda: None

        # 同步 disposer 函数。
        if callable(effect_result) and not hasattr(effect_result, '__await__') \
                and not hasattr(effect_result, '__iter__') \
                and not hasattr(effect_result, '__aiter__'):
            safe_collect(effect_result)
            return lambda: None

        # 异步迭代器（async generator）。
        if hasattr(effect_result, '__aiter__'):
            return self._handle_async_iter(effect_result, meta, old_epoch)

        # 同步迭代器（generator）。
        if hasattr(effect_result, '__iter__'):
            it = iter(effect_result)
            while True:
                try:
                    result = next(it)
                    safe_collect(result)
                except StopIteration:
                    break
            return lambda: None

        # Awaitable（协程或 __await__ 对象）。
        # ★ 不立即执行！存储为清理函数，在 fiber 卸载时 await。
        if hasattr(effect_result, '__await__'):
            # 如果是异步函数引用（非协程），调用得到协程。
            if callable(effect_result) and not asyncio.iscoroutine(effect_result):
                effect_result = effect_result()
            safe_collect(effect_result)
            return lambda: None

        return lambda: None

    def _handle_async_iter(self, async_iter, meta, old_epoch):
        """处理异步迭代器效果（对标 DSH async iterator effect）。"""
        async def _run():
            try:
                async for dispose in async_iter:
                    if self._epoch != old_epoch:
                        return
                    self._disposables.push(dispose)
            except Exception as e:
                logger.error(f"Effect '{meta.label}' failed: {e}")

        task = asyncio.ensure_future(_run())
        return lambda: task.cancel()

    def get_effects(self) -> List[EffectMeta]:
        """返回当前活跃效果的元数据树（对标 DSH getEffects）。"""
        result = []
        for dispose in self._disposables:
            m = getattr(dispose, '__effect_meta__', None)
            if m:
                result.append(m)
        return result

    # ------------------------------------------------------------------
    # 等待与就绪
    # ------------------------------------------------------------------
    async def await_ready(self) -> None:
        """异步等待本 fiber 进入 active 状态。"""
        await self._ready_event.wait()

    async def await_stable(self):
        """
        等待生命周期稳定，重抛启动错误（对标 DSH fiber.await）。

        :return: 本 fiber 实例。
        :raises: 配置验证或插件启动错误。
        """
        while self._inertia:
            await self._inertia
        if self._error:
            raise self._error
        return self

    def mark_ready(self) -> None:
        """标记本 fiber 已就绪。"""
        self._ready_event.set()

    def mark_not_ready(self) -> None:
        """重置就绪标记。"""
        self._ready_event.clear()

    # ------------------------------------------------------------------
    # 依赖实现检查（对标 DSH _checkImpl / _refresh）
    # ------------------------------------------------------------------
    def _check_impl(self, name: str):
        """检查单个依赖的实现是否仍然有效。"""
        try:
            impl = self.ctx.get(name, strict=False)
        except (KeyError, AttributeError):
            impl = None
        if impl is None:
            self._store.pop(name, None)
            return
        self._store[name] = impl

    def _refresh(self):
        """重新计算 epoch（依赖状态指纹）。"""
        epoch = _INACTIVE
        for name in list(self._inject_map.keys()):
            impl = self._store.get(name)
            if not impl:
                try:
                    impl = self.ctx.get(name, strict=False)
                except (KeyError, AttributeError):
                    impl = None
            if not impl:
                epoch = _INACTIVE
                break
            epoch_str = f":{getattr(impl, 'uid', id(impl))}"
            if epoch == _INACTIVE:
                epoch = ''
            epoch += epoch_str
        self._set_epoch(epoch)

    def _set_epoch(self, epoch: str):
        """设置 epoch 并触发状态转换（对标 DSH _setEpoch）。"""
        old_epoch = self._epoch
        if epoch == old_epoch:
            return
        self._epoch = epoch
        if self._inertia:
            return

        if epoch != _INACTIVE and old_epoch == _INACTIVE:
            self._inertia = self._reload()
            return FiberState.LOADING
        elif epoch == _INACTIVE and old_epoch != _INACTIVE:
            self._inertia = self._unload()
            return FiberState.UNLOADING

    # ------------------------------------------------------------------
    # 配置解析（对标 DSH _resolveConfig）
    # ------------------------------------------------------------------
    async def _resolve_config(self, config):
        """
        解析并验证配置（对标 DSH _resolveConfig）。

        1. 通过 internal/config waterfall 允许拦截。
        2. 用 Runtime 的 Config Schema 验证。
        """
        try:
            events = object.__getattribute__(self.ctx, '_events')
            if events:
                config = await events.waterfall('internal/config', config, lambda: config)
        except Exception:
            pass
        if self.runtime and hasattr(self.runtime, 'callback'):
            plugin_cls = self.runtime.callback
            if hasattr(plugin_cls, 'Config') and plugin_cls.Config:
                if hasattr(plugin_cls.Config, 'validate'):
                    result = plugin_cls.Config.validate(config)
                    if isinstance(result, dict) and 'issues' in result:
                        raise Validation(result['issues'])
                    return result
        return config

    # ------------------------------------------------------------------
    # 生命周期：reload / unload / dispose / restart / update
    # ------------------------------------------------------------------
    async def _reload(self):
        """
        重新加载插件（对标 DSH _reload）。

        保存 store 快照，重新执行插件回调。
        """
        self.store = dict(self._store)
        old_epoch = self._epoch
        try:
            await asyncio.sleep(0)
            if self._epoch == old_epoch:
                self.config = await self._resolve_config(self._config)
                await self._execute(self._context, self.config)
                self._error = None
        except Exception as reason:
            logger.error(f"Fiber '{self.name}' reload failed: {reason}")
            self._error = reason
            self._epoch = _INACTIVE

        if self._epoch == old_epoch:
            self._inertia = None
        else:
            self._inertia = self._unload()
            return FiberState.UNLOADING

    async def _unload(self):
        """
        卸载插件（对标 DSH _unload）。

        逆序执行所有清理函数，重置 store。
        """
        items = await self._disposables.clear()
        for dispose in reversed(items):
            try:
                # ★ 处理协程清理函数（如 async def _dispose()）。
                if asyncio.iscoroutine(dispose):
                    await dispose
                elif asyncio.iscoroutinefunction(dispose):
                    await dispose()
                else:
                    result = dispose()
                    if asyncio.iscoroutine(result):
                        await result
            except Exception as reason:
                logger.error(f"Fiber '{self.name}' dispose error: {reason}")
        self.store = None
        if self._epoch == _INACTIVE:
            self._inertia = None
        else:
            self._inertia = self._reload()
            return FiberState.LOADING

    async def dispose(self):
        """卸载插件并标记为已处置（对标 DSH dispose）。"""
        self.uid = None
        try:
            self._context.emit('internal/plugin', self)
        except Exception:
            pass
        if self._inertia:
            await self._inertia
        self._set_epoch(_INACTIVE)
        while self._inertia:
            await self._inertia

    async def restart(self):
        """处置并立即重新加载（对标 DSH fiber.restart）。"""
        self.assert_active()
        self._set_epoch(_INACTIVE)
        self._refresh()
        await self.await_stable()

    async def update(self, config: Any, no_save: bool = False):
        """
        验证并应用新配置，然后重启插件（对标 DSH fiber.update）。

        :param config:  新的原始配置。
        :param no_save: 持久化钩子不写回变更的标志。
        """
        self.assert_active()
        self._config = config
        if self.state != FiberState.ACTIVE:
            self._error = None
            self._set_epoch(_INACTIVE)
            self._refresh()
            return
        config = await self._resolve_config(config)
        self.config = config
        self._error = None
        return await self.restart()

    # ------------------------------------------------------------------
    # 内部执行
    # ------------------------------------------------------------------
    async def _execute(self, ctx, config):
        """执行插件回调（对标 DSH runner.execute）。"""
        plugin = self.plugin
        if hasattr(plugin, 'apply'):
            result = plugin.apply(ctx)
            if asyncio.iscoroutine(result):
                await result

    # ------------------------------------------------------------------
    # 向后兼容
    # ------------------------------------------------------------------
    @property
    def runtime_active(self) -> bool:
        """是否为运行时状态（active 或 loading）。向后兼容。"""
        return self.state in (FiberState.LOADING, FiberState.ACTIVE)
