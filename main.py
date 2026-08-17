# ============================================================================
# main.py
# 程序入口：使用 Profile/Bundle 配置组装方式，启动完整的 Function-Calling ReactAgent。
#
# 整体流程（对标 DSH 的"配置即代码"）：
#   1. 加载环境变量，校验 DeepSeek API 密钥。
#   2. 创建 Registry。
#   3. 定义 Bundle（一组配置条目）与 Profile（命名组合 + Patch）。
#   4. 用 ConfigLoader 合并配置、应用 Patch，生成加载计划。
#   5. Registry.load_plan 按计划注册插件（依赖注入 + 反应式协调自动激活）。
#   6. 注入工具 Schema 到系统提示。
#   7. 获取 agentLoop，进入交互式 CLI 多轮对话。
#
#   ★ 本版本采用 ctx.xxx 属性访问服务（对标 DSH 反射层）。
# ============================================================================

import asyncio      # 异步编程
import json         # JSON 处理
import logging      # 日志
import os           # 环境变量
from dotenv import load_dotenv   # 读取 .env 文件

from mycordis import Registry                     # 注册表

# ---- 插件类（用于配置条目）----
from adapters.llm_plugin import LLMPlugin          # 真实 LLM（支持工具调用）
from core_services.sessions_plugin import SessionsPlugin
from core_services.system_prompt_plugin import SystemPromptPlugin
from core_services.tools_plugin import ToolsPlugin
from agents.agent_interface import AgentsPlugin
from agents.agent_loop_plugin import AgentLoopPlugin
from tools.examples_plugin import ExamplesPlugin

# ---- 配置组装（对标 DSH Profile/Bundle/Patch）----
from config.bundle import Bundle
from config.profile import Profile
from config.loader import ConfigLoader

# 加载 .env 文件。
load_dotenv()

# 校验 API 密钥。
if not os.getenv("DEEPSEEK_API_KEY"):
    print("⚠️  警告: 未设置 DEEPSEEK_API_KEY 环境变量。")
    print("   请在 .env 文件中添加 DEEPSEEK_API_KEY=sk-xxx，或直接设置系统环境变量。")
    exit(1)

# 配置全局日志。
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')


def build_react_bundle() -> Bundle:
    """
    定义 ReactAgent 的 Bundle（一组配置条目，对标 DSH Bundle）。

    每个条目声明：id（用于 Patch 定位）、plugin（插件类）、config（构造参数）。
    """
    return Bundle("react", [
        {"id": "llm",          "plugin": LLMPlugin,          "config": {}},
        {"id": "sessions",     "plugin": SessionsPlugin,     "config": {}},
        {"id": "systemPrompt", "plugin": SystemPromptPlugin, "config": {}},
        {"id": "tools",        "plugin": ToolsPlugin,        "config": {}},
        {"id": "agents",       "plugin": AgentsPlugin,       "config": {}},
        {"id": "agentLoop",    "plugin": AgentLoopPlugin,    "config": {}},
        {"id": "examples",     "plugin": ExamplesPlugin,     "config": {}},
    ])


def build_react_profile() -> Profile:
    """
    定义默认 Profile（对标 DSH Profile）。

    引用 react Bundle，并可附带 Patch 来覆盖/替换任意配置条目。
    """
    return Profile(
        name="react-default",
        bundles=["react"],
        patches=[
            # 示范 Patch 机制：这里给 examples 传一个空 config（实际可替换成你的插件）。
            # 例如替换 agentLoop 为另一个实现：
            #   {"id": "agentLoop", "plugin": AnotherLoopPlugin, "config": {"max_steps": 3}}
        ],
    )


async def main():
    """
    异步主函数：用配置组装方式构建完整 ReactAgent 系统并进入交互式 CLI 对话。
    """
    print("\n" + "=" * 60)
    print("   ReactAgent (Profile/Bundle 配置组装 + DeepSeek API)")
    print("=" * 60 + "\n")

    # ---- 1. 创建注册表 ----
    registry = Registry()

    # ---- 2. 配置组装：合并 Bundle、应用 Patch、生成加载计划 ----
    print("[启动] 配置组装 Profile...")
    loader = ConfigLoader()
    loader.add_bundle(build_react_bundle())
    loader.add_profile(build_react_profile())

    plan = loader.build_plan("react-default")
    print(f"[配置] 加载计划包含 {len(plan)} 个插件条目:")
    for entry in plan:
        print(f"       - {entry['id']} ({entry['plugin'].__name__})")

    # ---- 3. 按加载计划注册插件 ----
    print("[启动] 按加载计划注册插件...")
    await registry.load_plan(plan)

    # ---- 4. 把工具 Schema 注入系统提示 ----
    print("[启动] 组装系统提示与工具 Schema...")
    root = registry._root_ctx
    tools = root.tools
    system_prompt = root.systemPrompt
    for schema in tools.list_schemas():
        fn_name = schema["function"]["name"]
        system_prompt.add_tool_schema(fn_name, json.dumps(schema, ensure_ascii=False))

    # ---- 5. 打印注册表状态（调试）----
    registry.dump_state()

    # ---- 6. 获取 agentLoop 服务 ----
    agent = root.agentLoop

    # ---- 7. 交互式 CLI 多轮对话 ----
    print("\n" + "=" * 60)
    print("   进入对话模式。输入 'exit' / 'quit' / 'q' / '退出' 结束。")
    print("   可尝试：现在几点 / 计算 12*7+5 / 北京天气 / 我叫小明")
    print("=" * 60 + "\n")

    while True:
        try:
            q = input("[用户] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[对话结束]")
            break

        # 退出命令。
        if q.lower() in {"exit", "quit", "q", "退出"}:
            print("[对话结束]")
            break

        # 跳过空输入。
        if not q:
            continue

        print("-" * 60)
        try:
            response = await agent.run(q)
            print(f"[Agent] {response}")
        except Exception as e:
            print(f"[Agent] 出错: {e}")

    # ---- 8. 打印会话历史（可选）----
    print("\n[演示结束]")
    print("\n当前会话历史：")
    sessions = root.sessions
    for sid in sessions.list():
        session = sessions.get(sid)
        print(f"\n--- 会话 {sid} ---")
        for msg in session.get_messages():
            print(f"  [{msg['role']}] {msg['content'][:80]}...")


if __name__ == "__main__":
    asyncio.run(main())
