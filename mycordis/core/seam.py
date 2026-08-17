# ============================================================================
# mycordis/core/seam.py
# Seam（能力接缝）模式：可替换能力的三层抽象。
#
# 对标 DSH 的 Seam 概念：
#   · Service Definition（接口声明）：声明能力的方法签名。
#   · Service Provider（实现）：提供具体实现，注册到 ctx。
#   · Consumer（消费者）：通过 ctx 反射层使用能力，不关心实现。
#
# 三者缺一不可，单角色不构成 seam。
# 核心价值：换一个 Provider 就能改变整个产品行为。
#   例如文件系统与子进程 Provider 共享同一执行世界，
#   把它们指向远程沙箱，就能让 Bash、PTY、LSP 一起迁移。
#
# 使用示例：
#   class FileSystemDef(SeamDefinition):
#       name = "fs"
#       methods = ["read", "write", "list", "exists"]
#
#   class LocalFSProvider(SeamProvider):
#       name = "fs-local"
#       async def read(self, path): ...
#       async def write(self, path, content): ...
#
#   # 注册
#   seam = FileSystemDef()
#   seam.register(LocalFSProvider(), ctx)
#
#   # 使用（消费者通过 ctx 反射层）
#   fs = ctx.fs   # 获取当前 provider
#   content = await fs.read("/path/to/file")
# ============================================================================

from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Context


class SeamDefinition:
    """
    能力接缝定义（接口声明层）。

    声明一个能力的名称、方法签名、以及默认实现（可选）。
    对标 DSH Seam 的 Service Definition 角色。
    """

    # 能力名称（将注册为 ctx.<name>）。
    name: str = ""

    # 声明的方法列表（消费者可调用哪些方法）。
    methods: List[str] = []

    # 方法签名描述（可选，用于文档/诊断）。
    signatures: Dict[str, str] = {}

    def __init__(self, name: str = None, methods: List[str] = None):
        if name is not None:
            self.name = name
        if methods is not None:
            self.methods = list(methods)
        self._providers: Dict[str, 'SeamProvider'] = {}
        self._active_provider: Optional[str] = None

    def register_provider(self, provider: 'SeamProvider', ctx: 'Context',
                          plugin_name: str = "") -> Callable:
        """
        注册一个 Provider 到此 Seam。

        :param provider:    SeamProvider 实例。
        :param ctx:         上下文。
        :param plugin_name: 提供者的插件名。
        :return:            反注册函数（可逆副作用）。
        """
        provider_id = provider.name or provider.__class__.__name__
        self._providers[provider_id] = provider

        # 如果是第一个 provider，自动设为活跃。
        if self._active_provider is None:
            self._active_provider = provider_id
            # 将 provider 注册为 ctx 服务。
            ctx.provide(self.name, provider, plugin_name)
        else:
            # 非活跃 provider 只是登记，不暴露到 ctx。
            pass

        async def _dispose():
            self._providers.pop(provider_id, None)
            if self._active_provider == provider_id:
                self._active_provider = None
                # 尝试切换到其他 provider。
                if self._providers:
                    fallback_id = next(iter(self._providers))
                    self.switch_provider(fallback_id, ctx)
                else:
                    # 没有 provider 了，从 ctx 移除。
                    try:
                        store = ctx._store
                        store.pop(self.name, None)
                    except Exception:
                        pass

        return _dispose

    def switch_provider(self, provider_id: str, ctx: 'Context') -> None:
        """
        切换活跃 Provider（对标 DSH "换一个 Provider 即改变整个产品行为"）。

        :param provider_id: 新 Provider 的标识。
        :param ctx:         上下文。
        :raises KeyError:   provider_id 不存在时抛出。
        """
        if provider_id not in self._providers:
            raise KeyError(f"Provider '{provider_id}' not registered for seam '{self.name}'")
        self._active_provider = provider_id
        provider = self._providers[provider_id]
        ctx.set_service(self.name, provider)

    def get_active_provider(self) -> Optional['SeamProvider']:
        """获取当前活跃的 Provider。"""
        if self._active_provider is None:
            return None
        return self._providers.get(self._active_provider)

    def list_providers(self) -> List[str]:
        """列出所有已注册的 Provider。"""
        return list(self._providers.keys())

    def validate_provider(self, provider: 'SeamProvider') -> List[str]:
        """
        校验 Provider 是否实现了所有声明的方法。

        :param provider: 要校验的 Provider。
        :return:         缺失的方法名列表（空 = 全部实现）。
        """
        missing = []
        for method in self.methods:
            if not hasattr(provider, method) or not callable(getattr(provider, method)):
                missing.append(method)
        return missing


class SeamProvider:
    """
    能力接缝提供者（实现层）。

    实现 SeamDefinition 声明的方法。
    对标 DSH Seam 的 Service Provider 角色。
    """

    # Provider 名称（唯一标识）。
    name: str = ""

    def __repr__(self):
        return f"SeamProvider({self.name!r})"


class SeamConsumer:
    """
    能力接缝消费者（使用层）。

    提供类型安全的消费接口，通常通过 ctx 反射层自动获取。
    对标 DSH Seam 的 Consumer 角色（通常是面向模型的工具）。

    使用示例：
        consumer = SeamConsumer(ctx, "fs")
        fs = consumer.proxy  # 获取代理，fs.read(...) 等
    """

    def __init__(self, ctx: 'Context', seam_name: str):
        self.ctx = ctx
        self.seam_name = seam_name

    @property
    def proxy(self) -> Any:
        """
        获取当前活跃 Provider 的代理（通过 ctx 反射层）。

        :return: Provider 实例（已绑定到 ctx.<seam_name>）。
        """
        return self.ctx.get(self.seam_name)

    def is_available(self) -> bool:
        """检查该 Seam 是否有活跃 Provider。"""
        try:
            self.ctx.get(self.seam_name)
            return True
        except (KeyError, AttributeError):
            return False


class SeamRegistry:
    """
    Seam 注册表：管理所有 Seam 定义的全局注册表。

    可作为 ctx 服务注册（key 为 'seams'），提供 Seam 的发现与管理。
    """

    def __init__(self):
        self._seams: Dict[str, SeamDefinition] = {}

    def register(self, definition: SeamDefinition) -> None:
        """注册一个 Seam 定义。"""
        if not definition.name:
            raise ValueError("Seam definition must have a name")
        self._seams[definition.name] = definition

    def get(self, name: str) -> SeamDefinition:
        """获取一个 Seam 定义。"""
        if name not in self._seams:
            raise KeyError(f"Seam '{name}' not registered")
        return self._seams[name]

    def list(self) -> List[str]:
        """列出所有已注册的 Seam。"""
        return list(self._seams.keys())

    def get_with_provider(self, name: str) -> Optional[SeamDefinition]:
        """获取有活跃 Provider 的 Seam。"""
        seam = self._seams.get(name)
        if seam and seam.get_active_provider() is not None:
            return seam
        return None
