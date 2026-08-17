# ============================================================================
# config/bundle.py
# Bundle：一组配置条目（对标 DSH 的 Bundle——Cordis 配置行的分发单元）。
#
# 设计意图：
#   一个 Bundle 是一个"分发单元"，包含若干配置条目。
#   每个配置条目声明：id、插件类（或工厂）、实例化参数 config。
# ============================================================================

from typing import Dict, List


class Bundle:
    """
    配置条目组（对标 DSH Bundle）。

    config 是配置条目列表，每个条目形如：
        {
            "id": "llm",            # 唯一标识，用于 Patch 定位
            "plugin": LLMPlugin,     # 插件类（可调用，loader 用 plugin(**config) 实例化）
            "config": {...},         # 传给插件构造函数的参数
        }
    """

    def __init__(self, name: str, config: List[Dict]):
        """
        初始化一个 Bundle。

        :param name:   Bundle 名称（唯一，供 Profile 引用）。
        :param config: 配置条目列表。
        """
        self.name = name
        self.config = config

    @classmethod
    def from_dict(cls, name: str, data: List[Dict]) -> 'Bundle':
        """
        从字典列表创建 Bundle。

        :param name: Bundle 名称。
        :param data: 配置条目列表。
        :return:     Bundle 实例。
        """
        return cls(name, data)
