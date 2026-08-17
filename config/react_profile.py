# ============================================================================
# config/react_profile.py
# 示范 Profile：用配置声明方式组装 ReactAgent（对标 DSH 的 Profile + Bundle）。
#
# 本文件定义：
#   1. 一个 Bundle "react"：包含 LLM、Sessions、SystemPrompt、Tools、Agents、
#      AgentLoop、Examples 七个插件条目。
#   2. 一个 Profile "react-default"：引用该 Bundle，并演示一个 Patch
#      （例如：给 ExamplesPlugin 传一个自定义 config）。
# ============================================================================

from adapters.llm_plugin import LLMPlugin
from core_services.sessions_plugin import SessionsPlugin
from core_services.system_prompt_plugin import SystemPromptPlugin
from core_services.tools_plugin import ToolsPlugin
from agents.agent_interface import AgentsPlugin
from agents.agent_loop_plugin import AgentLoopPlugin
from tools.examples_plugin import ExamplesPlugin

from .bundle import Bundle
from .profile import Profile


def build_react_bundle() -> Bundle:
    """
    构建包含全部 ReactAgent 插件的 Bundle。
    """
    return Bundle("react", [
        {"id": "llm",            "plugin": LLMPlugin,          "config": {}},
        {"id": "sessions",       "plugin": SessionsPlugin,     "config": {}},
        {"id": "systemPrompt",   "plugin": SystemPromptPlugin, "config": {}},
        {"id": "tools",          "plugin": ToolsPlugin,        "config": {}},
        {"id": "agents",         "plugin": AgentsPlugin,       "config": {}},
        {"id": "agentLoop",      "plugin": AgentLoopPlugin,    "config": {}},
        {"id": "examples",       "plugin": ExamplesPlugin,     "config": {}},
    ])


def build_react_profile() -> Profile:
    """
    构建默认 ReactAgent Profile：引用 react Bundle，可附 Patch。
    """
    return Profile(
        name="react-default",
        bundles=["react"],
        patches=[
            # 示范 Patch：给 examples 配置（示例，ExamplesPlugin 当前无参数，保持空）。
            # 这里展示 Patch 机制，实际可替换成你自己的插件。
        ],
    )
