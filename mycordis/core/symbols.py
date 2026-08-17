# ============================================================================
# mycordis/core/symbols.py
# Symbol 注册表：全局唯一的符号常量，对标 DSH cordis/utils.ts 中的 symbols。
#
# 在 DSH 中，所有内部键都用 Symbol 表示（如 symbols.isolate、symbols.effect），
# 避免与用户定义的服务名冲突。Python 中用字符串 + 哨兵值模拟。
# ============================================================================

_SYMBOL_REGISTRY = {}


def symbol(name: str) -> str:
    """创建或获取全局唯一的 Symbol 字符串。"""
    if name not in _SYMBOL_REGISTRY:
        _SYMBOL_REGISTRY[name] = f"@@cordis.{name}"
    return _SYMBOL_REGISTRY[name]


class symbols:
    """
    全局 Symbol 常量集合（对标 DSH utils.ts 中的 symbols 对象）。
    所有内部键都用 Symbol 表示，避免与用户定义的服务名冲突。
    """
    isolate = symbol('isolate')
    intercept = symbol('intercept')
    effect = symbol('effect')
    filter = symbol('filter')
    config = symbol('config')
    check = symbol('check')
    invoke = symbol('invoke')
    extend = symbol('extend')
    tracker = symbol('tracker')
    resolveConfig = symbol('resolveConfig')
    receiver = symbol('receiver')
    shadow = symbol('shadow')
    init = symbol('init')
    meta = symbol('metadata')
    initHooks = symbol('initHooks')
    checkProto = symbol('checkProto')


# 内部事件名集合（对标 DSH internal event protocol）。
INTERNAL_EVENTS = frozenset({
    'internal/get',
    'internal/set',
    'internal/service',
    'internal/plugin',
    'internal/status',
    'internal/config',
    'internal/update',
})


# 保留字集合：这些属性名不应被反射层拦截。
RESERVED_WORDS = frozenset({'prototype', 'then'})


def is_special_property(prop: str) -> bool:
    """
    判断某属性是否为"特殊属性"（不应走反射层解析）。

    :param prop: 属性名。
    :return:     True 表示应走 Python 默认路径。
    """
    if isinstance(prop, str) and prop.startswith('_'):
        return True
    if prop in RESERVED_WORDS:
        return True
    if isinstance(prop, str) and prop.isdigit():
        return True
    return False
