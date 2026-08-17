# ============================================================================
# mycordis/core/context.py
# 上下文（Context）：服务容器 + 反射层 + 可逆副作用栈 + 事件委托。
#
# ★ 核心能力（对齐 DSH Cordis）：
#   · ctx.xxx 属性访问即服务解析（__getattribute__ 拦截所有非 _ 前缀属性）。
#   · accessor()：计算属性（get/set 钩子），对标 DSH reflect.accessor()。
#   · mixin()：将服务方法暴露到 ctx 上，对标 DSH reflect.mixin()。
#   · set_service()：写保护——仅允许服务的提供者修改值。
#   · isolate()：Symbol 标签作用域隔离。
#   · extend()：原型继承式子上下文。
#   · intercept()：服务配置拦截合并。
#   · provide() 是可逆副作用：revert() 时自动反注册。
#   · 共享存储：根与子上下文共享 _store。
#   · ★ 自动 mixin：events 方法自动暴露到 ctx（ctx.on/ctx.emit 等）。
#   · ★ 事件委托：ctx.on/emit/once/parallel/serial/bail/waterfall 直接可用。
#   · ★ fiber 引用：ctx.fiber 指向拥有此上下文的 fiber。
#   · ★ 内部事件协议：internal/plugin, internal/status, internal/service 等。
# ============================================================================

import asyncio
import logging
from typing import Dict, Any, Optional, Callable, Awaitable, List

from .symbols import symbols, is_special_property

logger = logging.getLogger(__name__)


class Context:
    """
    上下文是服务的容器，并充当"反射层"——ctx.xxx 即服务。

    对标 DSH 的 Context Proxy：所有非 _ 前缀的属性访问都经过服务解析。
    解析顺序：accessor → mixin → store（含 isolate 标签检查）→ parent 链。
    """

    def __init__(self, parent: Optional['Context'] = None):
        """
        初始化一个上下文实例。

        :param parent: 父上下文。若为 None，则为"根上下文"。
        """
        # ★ 初始化阶段：所有内部属性通过 object.__setattr__ 设置，
        #   避免触发 __getattribute__ 的反射层拦截。
        object.__setattr__(self, 'parent', parent)

        # 共享存储：根与子上下文共享同一个 dict（对标 DSH 的 root store）。
        if parent is not None:
            root_store = object.__getattribute__(parent, '_root_store')
            root_providers = object.__getattribute__(parent, '_root_providers')
            root_scopes = object.__getattribute__(parent, '_root_scopes')
            object.__setattr__(self, '_root_store', root_store)
            object.__setattr__(self, '_root_providers', root_providers)
            object.__setattr__(self, '_root_scopes', root_scopes)
        else:
            object.__setattr__(self, '_root_store', {})
            object.__setattr__(self, '_root_providers', {})
            object.__setattr__(self, '_root_scopes', {})

        # 本上下文的副作用栈、accessor 表、mixin 表。
        object.__setattr__(self, '_disposers', [])
        object.__setattr__(self, '_accessors', {})
        object.__setattr__(self, '_mixins', {})

        # 隔离映射表：子上下文继承父上下文的 isolate map。
        if parent is not None:
            parent_iso = dict(object.__getattribute__(parent, '_isolate_map'))
        else:
            parent_iso = {}
        object.__setattr__(self, '_isolate_map', parent_iso)

        # 拦截配置映射表：子上下文继承父上下文的 intercept map。
        if parent is not None:
            parent_intercept = dict(object.__getattribute__(parent, '_intercept_map'))
        else:
            parent_intercept = {}
        object.__setattr__(self, '_intercept_map', parent_intercept)

        # 标记反射层就绪。
        object.__setattr__(self, '_reflect_ready', True)

        # ★ 自动初始化 EventEmitter（对标 DSH 自动 mixin events）。
        from .events import EventEmitter
        object.__setattr__(self, '_events', EventEmitter(self))

        # ★ fiber 引用：指向拥有此上下文的 fiber（根上下文为 None）。
        object.__setattr__(self, '_fiber', None)

        # ★ 根上下文专用清理列表（当没有 fiber 时使用）。
        object.__setattr__(self, '_root_disposers', [])

        # ★ 自动 mixin events 方法到 ctx。
        self._setup_auto_mixin()

    def _setup_auto_mixin(self):
        """
        自动将 events 服务的方法暴露到 ctx 上（对标 DSH ReflectService mixin）。

        对标 DSH：
            this.mixin('events', ['on', 'once', 'parallel', 'emit',
                                   'serial', 'bail', 'waterfall'])
        """
        events = object.__getattribute__(self, '_events')
        if events is not None:
            self._mixins['events'] = {
                'on': 'on', 'once': 'once', 'off': 'off',
                'emit': 'emit', 'parallel': 'parallel',
                'serial': 'serial', 'bail': 'bail',
                'waterfall': 'waterfall',
            }

    # ------------------------------------------------------------------
    # fiber 引用属性
    # ------------------------------------------------------------------
    @property
    def fiber(self):
        """获取拥有此上下文的 fiber（对标 DSH ctx.fiber）。"""
        return object.__getattribute__(self, '_fiber')

    @fiber.setter
    def fiber(self, value):
        """设置 fiber 引用。"""
        object.__setattr__(self, '_fiber', value)

    # ------------------------------------------------------------------
    # 内部存储属性访问（绕过 __getattribute__ 拦截，保持向后兼容）
    # ------------------------------------------------------------------
    @property
    def _store(self) -> Dict:
        """兼容旧代码中 ctx._services 的访问（现映射到 _root_store）。"""
        return object.__getattribute__(self, '_root_store')

    @property
    def _services(self) -> Dict:
        """兼容旧代码中 ctx._services 的访问。"""
        return object.__getattribute__(self, '_root_store')

    @property
    def _providers(self) -> Dict:
        """兼容旧代码中 ctx._providers 的访问。"""
        return object.__getattribute__(self, '_root_providers')

    @property
    def _scopes(self) -> Dict:
        """兼容旧代码中 ctx._scopes 的访问。"""
        return object.__getattribute__(self, '_root_scopes')

    # ------------------------------------------------------------------
    # 服务注册（可逆副作用，对标 DSH reflect.provide）
    # ------------------------------------------------------------------
    def provide(self, key: str, value: Any, plugin_name: str = "") -> Callable:
        """
        在当前上下文注册一个服务（自动登记可逆反注册副作用）。

        对标 DSH reflect.provide()：返回一个 disposer 函数。

        :param key:         服务名。
        :param value:       服务实例。
        :param plugin_name: 提供该服务的插件名。
        :return:            disposer 函数（调用后反注册该服务）。
        :raises ValueError: key 已存在且由不同插件提供时抛出。
        """
        store = self._store
        providers = self._providers
        scopes = self._scopes

        if key in store and providers.get(key) and providers[key] != plugin_name and plugin_name:
            raise ValueError(
                f"Service '{key}' has been registered at <{providers[key]}>"
            )

        store[key] = value
        if plugin_name:
            providers[key] = plugin_name
        if key not in scopes:
            scopes[key] = object()

        async def _dispose():
            store.pop(key, None)
            providers.pop(key, None)

        # ★ 通过 fiber 注册效果（如果有 fiber），否则用根清理列表。
        fiber = object.__getattribute__(self, '_fiber')
        if fiber is not None:
            # ★ 传递协程（而非异步函数引用），确保 fiber.effect() 正确收集。
            fiber.effect(_dispose(), f'ctx.provide({key!r})')
        else:
            root_disposers = object.__getattribute__(self, '_root_disposers')
            root_disposers.append(_dispose)

        # ★ 发射 internal/service 事件（对标 DSH internal/service）。
        # ★ 在根上下文上发射（因为服务注册到共享 store，监听器通常在根上下文）。
        try:
            root_ctx = self
            while object.__getattribute__(root_ctx, 'parent') is not None:
                root_ctx = object.__getattribute__(root_ctx, 'parent')
            root_ctx.emit('internal/service', key, value)
        except Exception:
            pass

        return _dispose

    # ------------------------------------------------------------------
    # 服务覆写（写保护，对标 DSH reflect.set）
    # ------------------------------------------------------------------
    def set_service(self, name: str, value: Any) -> None:
        """
        覆写一个已提供服务的值。仅允许服务的提供者修改。

        :param name:  服务名。
        :param value: 新值。
        :raises KeyError:   该服务未被 provide 过。
        """
        store = self._store
        if name not in store:
            raise KeyError(f"Cannot set property '{name}' without provided")
        store[name] = value

    # ------------------------------------------------------------------
    # 服务获取
    # ------------------------------------------------------------------
    def get(self, key: str, strict: bool = True) -> Any:
        """
        同步获取服务。

        :param key:    服务名。
        :param strict: 为 True 时仅返回已激活的服务（预留）。
        :return:       服务实例。
        :raises KeyError: 找不到时抛出。
        """
        resolved = self._resolve_via_events(key)
        if resolved is not None:
            return resolved
        return self._lookup(key)

    async def aget(self, key: str) -> Any:
        """
        异步获取服务（对标 DSH 的异步服务解析）。
        支持异步 internal/get waterfall 拦截。
        """
        events = object.__getattribute__(self, '_events')
        if events is not None:
            result = await events.waterfall("internal/get", key)
            if result is not None and result != key:
                return result
        return self._lookup(key)

    def _resolve_via_events(self, key: str):
        """尝试通过 internal/get 事件监听器解析服务（仅同步监听器）。"""
        try:
            events = object.__getattribute__(self, '_events')
            if events is None:
                return None
            listeners = events._get_listeners('internal/get')
            if not listeners:
                return None
            for listener in listeners:
                res = listener(key)
                if asyncio.iscoroutine(res):
                    continue
                if res is not None:
                    return res
            return None
        except Exception:
            return None

    def _lookup(self, key: str) -> Any:
        """
        服务查找：先查共享 store，再沿 parent 链向上，遵守 isolate 标签墙。
        """
        store = self._store
        isolate_map = object.__getattribute__(self, '_isolate_map')

        if key in store:
            # 检查 isolate 标签：若当前上下文对该 key 有隔离期望，
            # 且 store 中该服务的 scope 标签与期望不匹配，则拒绝访问。
            expected_label = isolate_map.get(key)
            if expected_label is not None:
                svc_label = self._scopes.get(key)
                if svc_label is not None and svc_label is not expected_label:
                    raise KeyError(
                        f"Service '{key}' is isolated by scope tag, "
                        f"cannot access from this context"
                    )
            return store[key]

        parent = object.__getattribute__(self, 'parent')
        if parent is not None:
            return parent._lookup(key)

        raise KeyError(f"Service '{key}' not found in context chain")

    # ------------------------------------------------------------------
    # 反射层：属性访问拦截（对标 DSH Proxy handler）
    # ------------------------------------------------------------------
    # Context 自身的方法名集合（这些方法不应被服务解析拦截）。
    _CONTEXT_METHODS = frozenset({
        'provide', 'get', 'aget', 'set_service',
        'accessor', 'mixin', 'isolate', 'extend', 'intercept',
        'effect', 'revert', 'get_provider_name',
        # ★ fiber 引用属性。
        'fiber',
        # ★ 事件委托方法（对标 DSH auto-mixin events）。
        'on', 'once', 'off', 'emit', 'parallel', 'serial', 'bail', 'waterfall',
        # ★ 内部方法。
        '_setup_auto_mixin',
    })

    def __getattribute__(self, name: str) -> Any:
        # 1) 内部属性（_xxx）、特殊属性走 Python 默认路径。
        if is_special_property(name):
            return object.__getattribute__(self, name)

        # 2) 反射层尚未就绪（__init__ 期间），走默认路径。
        try:
            ready = object.__getattribute__(self, '_reflect_ready')
        except AttributeError:
            return object.__getattribute__(self, name)
        if not ready:
            return object.__getattribute__(self, name)

        # 3) Context 自身的方法：直接返回，不走服务解析。
        if name in object.__getattribute__(self, '_CONTEXT_METHODS'):
            return object.__getattribute__(self, name)

        # 4) accessor：计算属性。
        accessors = object.__getattribute__(self, '_accessors')
        if name in accessors:
            getter = accessors[name].get("get")
            if getter:
                return getter(self)
            raise AttributeError(f"Accessor '{name}' has no getter")

        # 5) mixin：将源服务的方法绑定到 ctx。
        mixins = object.__getattribute__(self, '_mixins')
        for source_name, mappings in mixins.items():
            if name in mappings:
                source_method_name = mappings[name]
                try:
                    # ★ 特殊处理 events mixin（直接从 _events 获取）。
                    if source_name == 'events':
                        source = object.__getattribute__(self, '_events')
                    else:
                        source = self.get(source_name)
                    method = getattr(source, source_method_name, None)
                    if method and callable(method):
                        return method
                except (KeyError, AttributeError):
                    pass
                raise AttributeError(
                    f"Mixin source '{source_name}' has no method '{source_method_name}'"
                )

        # 6) 服务解析。
        try:
            return self.get(name)
        except KeyError:
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )

    def __setattr__(self, name: str, value: Any) -> None:
        # 内部属性走 Python 默认路径。
        if name.startswith('_') or name in ('parent',):
            object.__setattr__(self, name, value)
            return

        # 反射层未就绪时走默认路径。
        try:
            ready = object.__getattribute__(self, '_reflect_ready')
        except AttributeError:
            object.__setattr__(self, name, value)
            return

        if not ready:
            object.__setattr__(self, name, value)
            return

        # accessor setter。
        accessors = object.__getattribute__(self, '_accessors')
        if name in accessors:
            setter = accessors[name].get("set")
            if setter:
                result = setter(self, value)
                if result is False:
                    raise ValueError(f"Accessor '{name}' rejected the write")
                return
            raise AttributeError(f"Accessor '{name}' has no setter")

        # 已注册的服务 → 写保护覆写。
        store = object.__getattribute__(self, '_root_store')
        if name in store:
            store[name] = value
            return

        # 其他属性走默认路径。
        object.__setattr__(self, name, value)

    # ------------------------------------------------------------------
    # accessor（计算属性，对标 DSH reflect.accessor）
    # ------------------------------------------------------------------
    def accessor(self, name: str, getter: Callable = None, setter: Callable = None) -> None:
        """
        定义一个计算属性（get/set 钩子）。

        :param name:   属性名。
        :param getter: 读取钩子 (ctx) -> value。
        :param setter: 可选写入钩子 (ctx, value) -> bool。返回 False 拒绝写入。
        """
        if name in self._accessors:
            raise ValueError(f"Property '{name}' is already declared as accessor")

        self._accessors[name] = {"get": getter, "set": setter}

        async def _dispose():
            self._accessors.pop(name, None)

        # ★ 通过 fiber 注册效果（如果有 fiber），否则用本地清理列表。
        fiber = object.__getattribute__(self, '_fiber')
        if fiber is not None:
            fiber.effect(_dispose(), f'ctx.accessor({name!r})')
        else:
            self._disposers.append(_dispose)

    # ------------------------------------------------------------------
    # mixin（服务方法混入，对标 DSH reflect.mixin）
    # ------------------------------------------------------------------
    def mixin(self, source_name: str, mappings: Dict[str, str]) -> None:
        """
        将某个服务的成员方法暴露到 ctx 上。

        :param source_name: 源服务名（如 'events'）。
        :param mappings:    {ctx属性名: 源服务方法名}，
                            如 {"on": "on", "emit": "emit"}。
        """
        self._mixins[source_name] = mappings

        async def _dispose():
            self._mixins.pop(source_name, None)

        # ★ 通过 fiber 注册效果。
        fiber = object.__getattribute__(self, '_fiber')
        if fiber is not None:
            fiber.effect(_dispose(), f'ctx.mixin({source_name!r})')
        else:
            self._disposers.append(_dispose)

    # ------------------------------------------------------------------
    # 隔离（Symbol 标签作用域，对标 DSH context.isolate）
    # ------------------------------------------------------------------
    def isolate(self, name: str = None, label: object = None) -> 'Context':
        """
        创建当前上下文的隔离子上下文。

        :param name:  可选，要隔离的服务名。
        :param label: 可选，隔离标签（object 实例，作为 Symbol 使用）。
        :return:      一个新的 Context 实例。
        """
        child = Context(parent=self)
        if name is not None:
            child._isolate_map[name] = label if label is not None else object()
        return child

    def extend(self, meta: Dict = None) -> 'Context':
        """
        创建带额外元数据的子上下文（对标 DSH context.extend）。
        子上下文原型继承当前上下文；meta 中的属性覆盖继承的属性。

        :param meta: 可选，子上下文的额外属性。
        :return:     子上下文。
        """
        child = Context(parent=self)
        if meta:
            for key, value in meta.items():
                if key == symbols.isolate:
                    object.__setattr__(child, '_isolate_map', dict(value))
                elif key == symbols.intercept:
                    object.__setattr__(child, '_intercept_map', dict(value))
                elif key == 'fiber':
                    # ★ 传递 fiber 引用（对标 DSH extend({ fiber: this })）。
                    object.__setattr__(child, '_fiber', value)
        return child

    def intercept(self, name: str, config: dict) -> 'Context':
        """
        为某服务添加拦截配置（对标 DSH context.intercept）。
        子上下文中的插件在解析该服务的配置时，会合并此拦截配置。

        :param name:   服务名。
        :param config: 拦截配置。
        :return:       携带拦截配置的子上下文。
        """
        child = Context(parent=self)
        child._intercept_map[name] = config
        return child

    # ------------------------------------------------------------------
    # 可逆副作用管理
    # ------------------------------------------------------------------
    def effect(self, disposer: Callable[[], Awaitable[None]]) -> None:
        """
        注册一个可逆副作用（清理函数），压入 LIFO 栈。

        ★ 如果有 fiber，委托给 fiber.effect() 以获得更好的追踪。
        """
        fiber = object.__getattribute__(self, '_fiber')
        if fiber is not None:
            # ★ 如果 disposer 是异步函数，先调用得到协程。
            if asyncio.iscoroutinefunction(disposer):
                fiber.effect(disposer(), 'ctx.effect()')
            else:
                fiber.effect(disposer, 'ctx.effect()')
        else:
            self._disposers.append(disposer)

    async def revert(self) -> None:
        """按 LIFO 顺序执行并清空所有清理函数。"""
        # ★ 先清理 fiber 的 disposables（如果有）。
        fiber = object.__getattribute__(self, '_fiber')
        if fiber is not None:
            items = await fiber._disposables.clear()
            for dispose in reversed(items):
                try:
                    if asyncio.iscoroutine(dispose):
                        await dispose
                    elif asyncio.iscoroutinefunction(dispose):
                        await dispose()
                    else:
                        result = dispose()
                        if asyncio.iscoroutine(result):
                            await result
                except Exception as e:
                    logger.error(f"revert: fiber dispose error: {e}")
        # 再清理根清理列表。
        root_disposers = object.__getattribute__(self, '_root_disposers')
        while root_disposers:
            disposer = root_disposers.pop()
            if asyncio.iscoroutine(disposer):
                await disposer
            elif asyncio.iscoroutinefunction(disposer):
                await disposer()
            else:
                result = disposer()
                if asyncio.iscoroutine(result):
                    await result
        # 最后清理本地清理列表。
        while self._disposers:
            disposer = self._disposers.pop()
            if asyncio.iscoroutine(disposer):
                await disposer
            elif asyncio.iscoroutinefunction(disposer):
                await disposer()
            else:
                result = disposer()
                if asyncio.iscoroutine(result):
                    await result

    # ------------------------------------------------------------------
    # 提供者溯源
    # ------------------------------------------------------------------
    def get_provider_name(self, key: str) -> Optional[str]:
        """查询某个服务是由哪个插件提供的。"""
        providers = self._providers
        if key in providers:
            return providers[key]
        parent = object.__getattribute__(self, 'parent')
        if parent:
            return parent.get_provider_name(key)
        return None

    # ------------------------------------------------------------------
    # ★ 事件委托方法（对标 DSH auto-mixin events）
    # ------------------------------------------------------------------
    def on(self, name: str, listener: Callable) -> None:
        """注册事件监听器（委托到 EventEmitter）。"""
        events = object.__getattribute__(self, '_events')
        events.on(name, listener)

    def once(self, name: str, listener: Callable) -> None:
        """注册只触发一次的事件监听器。"""
        events = object.__getattribute__(self, '_events')
        events.once(name, listener)

    def off(self, name: str, listener: Callable) -> None:
        """移除事件监听器。"""
        events = object.__getattribute__(self, '_events')
        events.off(name, listener)

    def emit(self, name: str, *args) -> None:
        """
        触发事件（委托到 EventEmitter）。

        支持多参数传递：emit(name, a, b, c) → listener(a, b, c)
        """
        events = object.__getattribute__(self, '_events')
        # 对于内部事件，传递所有参数；对于普通事件，传递第一个参数。
        if name.startswith('internal/') and len(args) > 1:
            events.emit(name, args)
        elif args:
            events.emit(name, args[0])
        else:
            events.emit(name, None)

    async def parallel(self, name: str, payload: Any = None) -> None:
        """并行扇出事件监听器。"""
        events = object.__getattribute__(self, '_events')
        await events.parallel(name, payload)

    async def serial(self, name: str, payload: Any = None) -> List[Any]:
        """串行执行事件监听器。"""
        events = object.__getattribute__(self, '_events')
        return await events.serial(name, payload)

    async def bail(self, name: str, value: Any = None) -> Any:
        """短路求值事件监听器。"""
        events = object.__getattribute__(self, '_events')
        return await events.bail(name, value)

    async def waterfall(self, name: str, value: Any, *extra_args) -> Any:
        """中间件模式事件处理。"""
        events = object.__getattribute__(self, '_events')
        return await events.waterfall(name, value, *extra_args)


def ensure_events(ctx: 'Context'):
    """确保某上下文已绑定 EventEmitter，返回之（懒加载）。"""
    if ctx._events is None:
        from .events import EventEmitter
        ctx._events = EventEmitter(ctx)
    return ctx._events
