# ============================================================================
# mycordis/core/identity.py
# Identity（身份）服务：Agent 身份与凭证管理。
#
# 对标 DSH core/identity：
#   · 管理 Agent 的身份信息（名称、角色、描述）。
#   · 管理凭证存储（API keys、tokens 等）。
#   · 支持身份切换（per-agent identity）。
#   · 提供身份验证接口。
#
# 使用示例：
#   identity = ctx.identity
#   identity.set_credential("deepseek_api", "sk-xxx")
#   key = identity.get_credential("deepseek_api")
#   identity.set_agent_identity("agent-1", {"role": "coder"})
# ============================================================================

from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import Context


class AgentIdentity:
    """
    单个 Agent 的身份信息。
    """

    def __init__(self, agent_id: str, name: str = "", role: str = "",
                 description: str = ""):
        self.agent_id = agent_id
        self.name = name or agent_id
        self.role = role
        self.description = description
        self.metadata: Dict[str, Any] = {}

    def set_metadata(self, key: str, value: Any) -> None:
        """设置身份元数据。"""
        self.metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """获取身份元数据。"""
        return self.metadata.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。"""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "metadata": dict(self.metadata),
        }

    def __repr__(self):
        return f"AgentIdentity({self.agent_id!r}, role={self.role!r})"


class IdentityService:
    """
    身份与凭证管理服务（对标 DSH core/identity）。

    ★ 继承 Service 基类：构造时自动注册为 ctx.identity。
    """

    def __init__(self, ctx: 'Context', name: str = "identity"):
        from .service import Service
        self._ctx = ctx
        self._credentials: Dict[str, str] = {}
        self._agent_identities: Dict[str, AgentIdentity] = {}
        self._default_identity: Optional[AgentIdentity] = None
        # ★ 通过 Service 基类自动注册。
        Service.__init__(self, ctx, name)

    def init(self):
        """★ init 钩子。"""
        # 创建系统默认身份。
        self._default_identity = AgentIdentity(
            agent_id="__system__",
            name="System",
            role="system",
            description="Default system identity",
        )

    # ------------------------------------------------------------------
    # 凭证管理
    # ------------------------------------------------------------------
    def set_credential(self, key: str, value: str) -> None:
        """
        存储凭证。

        :param key:   凭证标识（如 "deepseek_api_key"）。
        :param value: 凭证值。
        """
        self._credentials[key] = value

    def get_credential(self, key: str, default: str = None) -> Optional[str]:
        """
        获取凭证。

        :param key:     凭证标识。
        :param default: 默认值。
        :return:        凭证值或默认值。
        """
        return self._credentials.get(key, default)

    def has_credential(self, key: str) -> bool:
        """检查凭证是否存在。"""
        return key in self._credentials

    def remove_credential(self, key: str) -> None:
        """移除凭证。"""
        self._credentials.pop(key, None)

    def list_credentials(self) -> List[str]:
        """列出所有凭证标识（不返回值）。"""
        return list(self._credentials.keys())

    # ------------------------------------------------------------------
    # Agent 身份管理
    # ------------------------------------------------------------------
    def set_agent_identity(self, agent_id: str, identity: AgentIdentity) -> None:
        """
        设置 Agent 身份。

        :param agent_id: Agent 标识。
        :param identity: AgentIdentity 实例。
        """
        self._agent_identities[agent_id] = identity

    def get_agent_identity(self, agent_id: str) -> Optional[AgentIdentity]:
        """
        获取 Agent 身份。

        :param agent_id: Agent 标识。
        :return:         AgentIdentity 实例或 None。
        """
        return self._agent_identities.get(agent_id)

    def create_agent_identity(self, agent_id: str, name: str = "",
                               role: str = "", description: str = "") -> AgentIdentity:
        """
        创建并注册 Agent 身份。

        :param agent_id:    Agent 标识。
        :param name:        显示名称。
        :param role:        角色（如 "coder"、"researcher"）。
        :param description: 描述。
        :return:            新建的 AgentIdentity 实例。
        """
        identity = AgentIdentity(agent_id, name, role, description)
        self._agent_identities[agent_id] = identity
        return identity

    def list_identities(self) -> List[str]:
        """列出所有已注册的 Agent 身份标识。"""
        return list(self._agent_identities.keys())

    @property
    def default(self) -> Optional[AgentIdentity]:
        """获取默认系统身份。"""
        return self._default_identity
