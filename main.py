# ============================================================================
# main.py
# 程序入口：注册全部插件，启动真实的 Function-Calling ReactAgent。
#
# 整体流程（对标 DSH 的核心服务组装）：
#   1. 加载环境变量，校验 DeepSeek API 密钥。
#   2. 创建 Registry（注册表）。
#   3. 注册全部插件（依赖注入 + 反应式协调会自动激活）：
#        - LLMPlugin              → 提供 'llm'（真实 DeepSeek，支持工具调用）
#        - SessionsPlugin         → 提供 'sessions'（事件溯源会话日志）
#        - SystemPromptPlugin     → 提供 'systemPrompt'（系统提示组装）
#        - ToolsPlugin            → 提供 'tools'（工具注册表 + 守卫管道）
#        - AgentsPlugin           → 提供 'agents'（Agent 实时注册表）
#        - AgentLoopPlugin        → 提供 'agentLoop'（默认 Function-Calling 循环）
#        - ExamplesPlugin         → 向 'tools' 注册示例工具
#   4. 把工具 Schema 注入系统提示。
#   5. 获取 'agentLoop'，进行多轮对话演示（可触发真实工具调用）。
# ============================================================================

import asyncio      # 异步编程
import json         # JSON 处理
import logging      # 日志
import os           # 环境变量
from dotenv import load_dotenv   # 读取 .env 文件

from mycordis import Registry                     # 注册表
from adapters.llm_plugin import LLMPlugin          # 真实 LLM（支持工具调用）
from core_services.sessions_plugin import SessionsPlugin
from core_services.system_prompt_plugin import SystemPromptPlugin
from core_services.tools_plugin import ToolsPlugin
from agents.agent_interface import AgentsPlugin
from agents.agent_loop_plugin import AgentLoopPlugin
from tools.examples_plugin import ExamplesPlugin

# 加载 .env 文件（如果存在）。
load_dotenv()

# 校验 API 密钥。
if not os.getenv("DEEPSEEK_API_KEY"):
    print("⚠️  警告: 未设置 DEEPSEEK_API_KEY 环境变量。")
    print("   请在 .env 文件中添加 DEEPSEEK_API_KEY=sk-xxx，或直接设置系统环境变量。")
    exit(1)

# 配置全局日志。
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')


async def main():
    """
    异步主函数：构建完整 ReactAgent 系统并演示多轮对话。
    """
    print("\n" + "=" * 60)
    print("   ReactAgent (DeepSeek 真实 API + Function Calling)")
    print("=" * 60 + "\n")

    # ---- 1. 创建注册表 ----
    registry = Registry()

    # ---- 2. 注册核心服务插件 ----
    # 每个 register 都会触发反应式协调，自动解析依赖并激活。
    print("[启动] 注册核心服务插件...")
    await registry.register("LLMPlugin", LLMPlugin())              # 'llm'
    await registry.register("SessionsPlugin", SessionsPlugin())    # 'sessions'
    await registry.register("SystemPromptPlugin", SystemPromptPlugin())  # 'systemPrompt'
    await registry.register("ToolsPlugin", ToolsPlugin())          # 'tools'
    await registry.register("AgentsPlugin", AgentsPlugin())        # 'agents'
    await registry.register("AgentLoopPlugin", AgentLoopPlugin())  # 'agentLoop' (依赖前四个)
    await registry.register("ExamplesPlugin", ExamplesPlugin())    # 往 tools 注册示例工具

    # ---- 3. 把工具 Schema 注入系统提示 ----
    # 让 Agent 知道有哪些工具可用、如何调用。
    print("[启动] 组装系统提示与工具 Schema...")
    tools = registry._root_ctx.get('tools')
    system_prompt = registry._root_ctx.get('systemPrompt')
    for schema in tools.list_schemas():
        fn_name = schema["function"]["name"]
        system_prompt.add_tool_schema(fn_name, json.dumps(schema, ensure_ascii=False))

    # ---- 4. 打印注册表状态（调试）----
    registry.dump_state()

    # ---- 5. 获取 agentLoop 服务（ReAct 循环）----
    agent = registry._root_ctx.get('agentLoop')

    # ---- 6. 多轮对话演示（可触发真实工具调用）----
    questions = [
        "你好，我叫小明。",
        "现在几点钟了？",           # 触发 get_current_datetime 工具
        "帮我算一下 12 * 7 + 5 等于多少？",   # 触发 calculator 工具
        "北京今天的天气怎么样？",      # 触发 get_weather_info 工具
        "我叫什么名字？我们刚才聊了什么？",  # 测试会话记忆
    ]

    for q in questions:
        print("\n" + "-" * 60)
        print(f"[用户] {q}")
        print("-" * 60)
        try:
            response = await agent.run(q)
            print(f"[Agent] {response}")
        except Exception as e:
            print(f"[Agent] 出错: {e}")

    print("\n[演示结束]")
    print("\n当前会话历史：")
    sessions = registry._root_ctx.get('sessions')
    for sid in sessions.list():
        session = sessions.get(sid)
        print(f"\n--- 会话 {sid} ---")
        for msg in session.get_messages():
            print(f"  [{msg['role']}] {msg['content'][:80]}...")


if __name__ == "__main__":
    # 程序入口：以异步方式运行主函数。
    asyncio.run(main())
