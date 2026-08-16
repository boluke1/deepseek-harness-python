# mycordis/core/context.py
# 上下文：服务容器 + 可逆副作用栈

from typing import Dict, Any, Optional, Callable, Awaitable, List

class Context:
    """
    上下文是服务的容器。
    负责存储插件提供的服务（provide），以及向上查找依赖（get）。
    同时维护一个“可逆副作用栈”（disposers），用于卸载时清理。
    """

    def __init__(self, parent: Optional['Context'] = None):
        self.parent = parent
        self._services: Dict[str, Any] = {}
        self._providers: Dict[str, str] = {}
        self._disposers: List[Callable[[], Awaitable[None]]] = []

    def provide(self, key: str, value: Any, plugin_name: str) -> None:
        """在当前上下文中注册一个服务，若 key 已存在则抛出异常"""
        if key in self._services:
            raise ValueError(f"Service '{key}' is already provided by {self._providers.get(key)}")
        self._services[key] = value
        self._providers[key] = plugin_name

    def get(self, key: str) -> Any:
        """获取服务，若当前没有则向父级查找"""
        if key in self._services:
            return self._services[key]
        if self.parent:
            return self.parent.get(key)
        raise KeyError(f"Service '{key}' not found in context chain")

    def isolate(self) -> 'Context':
        """创建隔离子上下文（继承父级，但修改不影响父级）"""
        return Context(parent=self)

    def effect(self, disposer: Callable[[], Awaitable[None]]) -> None:
        """注册一个可逆副作用（清理函数），压入 LIFO 栈"""
        self._disposers.append(disposer)

    async def revert(self) -> None:
        """按 LIFO 顺序执行所有清理函数"""
        while self._disposers:
            disposer = self._disposers.pop()
            await disposer()

    def get_provider_name(self, key: str) -> Optional[str]:
        """查询某个服务是由哪个插件提供的"""
        if key in self._providers:
            return self._providers[key]
        if self.parent:
            return self.parent.get_provider_name(key)
        return None