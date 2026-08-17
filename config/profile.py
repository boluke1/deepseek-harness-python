# ============================================================================
# config/profile.py
# Profile：命名的配置组合（对标 DSH Profile）。
#
# 设计意图：
#   Profile 定义一个"运行配置"，按顺序引用多个 Bundle，并可附带 Patch 覆盖。
#   加载时，Loader 会把所有 bundle 的配置条目合并，再应用 profile 的 patches。
# ============================================================================

from typing import Dict, List


class Profile:
    """
    命名组合配置（对标 DSH Profile）。

    :param name:    Profile 名称。
    :param bundles: 按顺序引用的 Bundle 名称列表。
    :param patches: Patch 列表，每个形如：
                    {
                        "id": "agentLoop",          # 要替换的配置条目 id
                        "plugin": OtherLoopPlugin,   # 可选：替换整个插件实现
                        "config": {...},             # 可选：覆盖构造参数
                    }
    """

    def __init__(self, name: str, bundles: List[str], patches: List[Dict] = None):
        self.name = name
        self.bundles = bundles
        self.patches = patches or []

    @classmethod
    def from_dict(cls, name: str, data: Dict) -> 'Profile':
        """
        从字典创建 Profile。

        :param name: Profile 名称。
        :param data: 形如 {"bundles": [...], "patches": [...]}。
        :return:     Profile 实例。
        """
        return cls(
            name,
            data.get("bundles", []),
            data.get("patches", []),
        )
