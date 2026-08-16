# main_simple.py
import asyncio
import logging
import os
from dotenv import load_dotenv

from mycordis import Registry
from plugins.llm_plugin import LLMPlugin
from plugins.agent_loop_plugin import AgentLoopPlugin

# 加载 .env 文件（如果存在）
load_dotenv()

# 检查 API 密钥是否存在
if not os.getenv("DEEPSEEK_API_KEY"):
    print("⚠️  警告: 未设置 DEEPSEEK_API_KEY 环境变量。")
    print("   请在 .env 文件中添加 DEEPSEEK_API_KEY=sk-xxx，或直接设置系统环境变量。")
    exit(1)

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

async def main():
    print("\n" + "=" * 50)
    print("   Cordis Agent (DeepSeek 真实 API)")
    print("=" * 50 + "\n")

    registry = Registry()

    print("[启动] 注册插件...")
    await registry.register("LLMPlugin", LLMPlugin())
    await registry.register("AgentLoopPlugin", AgentLoopPlugin())

    await asyncio.sleep(0.5)  # 等待协调
    registry.dump_state()

    agent = registry._root_ctx.get('agent')

    # 多轮对话（会自动维护历史）
    questions = [
        "你好，我是小明。",
        "我叫什么名字？",
        "我们刚才聊了什么？"
    ]

    for q in questions:
        print("\n" + "-" * 40)
        print(f"[用户] {q}")
        print("-" * 40)
        response = await agent.run(q)
        print(f"[Agent] {response}")

    print("\n[演示结束]")

if __name__ == "__main__":
    asyncio.run(main())