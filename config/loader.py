# ============================================================================
# config/loader.py
# ConfigLoader：配置组装引擎（对标 DSH 的配置树 / --dump-config）。
#
# 设计意图：
#   负责把 Profile（含多个 Bundle + Patch）合并成一份"加载计划"：
#     1. 按顺序合并所有 bundle 的配置条目。
#     2. 应用 profile 的 patches（按 id 覆盖参数或替换整个插件）。
#     3. 返回按序待注册的插件条目列表。
#   该加载计划可直接交给 Registry 注册。
# ============================================================================

import logging
from typing import Dict, List

from .bundle import Bundle
from .profile import Profile

# 本模块的日志记录器。
logger = logging.getLogger(__name__)


class ConfigLoader:
    """
    配置组装引擎：合并 Bundle、应用 Patch、生成加载计划。
    """

    def __init__(self):
        """初始化配置装载器，准备 bundle 与 profile 仓库。"""
        self._bundles: Dict[str, Bundle] = {}
        self._profiles: Dict[str, Profile] = {}

    # ------------------------------------------------------------------
    # 注册 Bundle / Profile
    # ------------------------------------------------------------------
    def add_bundle(self, bundle: Bundle) -> None:
        """
        注册一个 Bundle。

        :param bundle: Bundle 实例。
        """
        self._bundles[bundle.name] = bundle
        logger.info(f"[Config] 注册 Bundle: {bundle.name}")

    def add_profile(self, profile: Profile) -> None:
        """
        注册一个 Profile。

        :param profile: Profile 实例。
        """
        self._profiles[profile.name] = profile
        logger.info(f"[Config] 注册 Profile: {profile.name}")

    # ------------------------------------------------------------------
    # 组装加载计划
    # ------------------------------------------------------------------
    def build_plan(self, profile_name: str) -> List[Dict]:
        """
        生成加载计划：合并 Profile 引用的所有 Bundle，并应用 Patch。

        :param profile_name: Profile 名称。
        :return:            加载计划（配置条目列表，已应用 patch）。
        :raises KeyError:    Profile 或引用的 Bundle 不存在时抛出。
        """
        if profile_name not in self._profiles:
            raise KeyError(f"Profile '{profile_name}' 不存在")

        profile = self._profiles[profile_name]

        # 1) 按顺序合并所有 bundle 的配置条目。
        merged: List[Dict] = []
        seen_ids = set()
        for bundle_name in profile.bundles:
            if bundle_name not in self._bundles:
                raise KeyError(f"Bundle '{bundle_name}' 不存在")
            for entry in self._bundles[bundle_name].config:
                # 同 id 后出现的覆盖先前的（DSH 层叠语义）。
                if entry["id"] in seen_ids:
                    merged = [e for e in merged if e["id"] != entry["id"]]
                else:
                    seen_ids.add(entry["id"])
                merged.append(entry)

        # 2) 应用 Profile 的 patches（按 id 覆盖或替换）。
        for patch in profile.patches:
            self._apply_patch(merged, patch)

        logger.info(f"[Config] Profile '{profile_name}' 生成加载计划：{len(merged)} 个插件条目")
        return merged

    def _apply_patch(self, merged: List[Dict], patch: Dict) -> None:
        """
        应用一个 Patch：按 id 定位配置条目，覆盖参数或替换插件实现。

        :param merged: 加载计划（就地修改）。
        :param patch:  Patch 字典，形如 {"id": ..., "plugin": ..., "config": ...}。
        """
        patch_id = patch["id"]
        for entry in merged:
            if entry["id"] == patch_id:
                # 替换整个插件实现（若提供了 plugin）。
                if "plugin" in patch:
                    entry["plugin"] = patch["plugin"]
                # 覆盖构造参数（若提供了 config）。
                if "config" in patch:
                    # 深度合并：保留未覆盖的字段。
                    merged_config = entry.get("config", {})
                    merged_config.update(patch["config"])
                    entry["config"] = merged_config
                logger.info(f"[Config] Patch 应用: {patch_id}")
                return
        # 未找到该 id 的配置条目。
        raise KeyError(f"Patch 引用不存在的配置条目 id: {patch_id}")
