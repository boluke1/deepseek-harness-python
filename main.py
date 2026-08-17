# ============================================================================
# main.py
# 程序入口：注册插件、启动 Agent、演示多轮对话。
#
# 整体流程：
#   1. 加载环境变量，校验 API 密钥。
#   2. 创建 Registry（注册表）。
#   3. 注册 LLMPlugin 与 AgentLoopPlugin，触发反应式依赖解析。
#   4. 等待协调完成，dump 状态便于观察。
#   5. 从根上下文获取 'agent' 服务，进行多轮对话演示。
# ============================================================================

import asyncio      # 异步编程
import logging      # 日志
import os           # 环境变量
from dotenv import load_dotenv   # 读取 .env 文件

from mycordis import Registry                     # 注册表：插件生命周期控制器
from plugins.llm_plugin import LLMPlugin          # LLM 插件（提供 'llm' 服务）
from plugins.agent_loop_plugin import AgentLoopPlugin  # Agent 插件（依赖 'llm'，提供 'agent'）

# 加载 .env 文件（如果存在），把其中定义的键值写入进程环境变量。
load_dotenv()

# 检查 API 密钥是否存在；缺失则给出提示并退出程序。
if not os.getenv("DEEPSEEK_API_KEY"):
    print("⚠️  警告: 未设置 DEEPSEEK_API_KEY 环境变量。")
    print("   请在 .env 文件中添加 DEEPSEEK_API_KEY=sk-xxx，或直接设置系统环境变量。")
    exit(1)

# 配置全局日志：输出级别为 INFO，简化格式为 [级别] 消息。
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')


async def main():
    """
    异步主函数：构建注册表、注册插件、演示多轮对话。
    """
    print("\n" + "=" * 50)
    print("   Cordis Agent (DeepSeek 真实 API)")
    print("=" * 50 + "\n")

    # ---- 1. 创建注册表 ----
    # Registry 会创建根上下文，并负责插件的注册、激活、依赖解析与卸载。
    registry = Registry()

    # ---- 2. 注册插件 ----
    # 每个 register 都会触发一次协调循环：
    #   - LLMPlugin（inject=[]）：注册后立即满足依赖，被激活，提供 'llm'。
    #   - AgentLoopPlugin（inject=['llm']）：注册时 'llm' 已存在，依赖满足，也被激活，提供 'agent'。
    print("[启动] 注册插件...")
    await registry.register("LLMPlugin", LLMPlugin())
    await registry.register("AgentLoopPlugin", AgentLoopPlugin())

    # ---- 3. 等待协调循环稳定（异步任务让出，确保各 apply 完成）----
    await asyncio.sleep(0.5)  # 等待协调

    # ---- 4. 打印当前注册表状态（调试用）----
    registry.dump_state()

    # ---- 5. 从根上下文获取 'agent' 服务 ----
    # 'agent' 由 AgentLoopPlugin 提供，已被提升到根上下文，因此可直接 get。
    agent = registry._root_ctx.get('agent')

    # ---- 6. 多轮对话演示（AgentRunner 会自动维护历史，记住上下文）----
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
    # 程序入口：以异步方式运行主函数。
    asyncio.run(main())
