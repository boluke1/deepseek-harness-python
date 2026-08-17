import asyncio
from mycordis import Context
from mycordis.core.events import ensure_events

async def main():
    # 验证 aget 异步服务解析
    ctx = Context()
    ctx.provide('llm', "LLM服务", "p")
    print("aget:", await ctx.aget('llm'))   # 期望: LLM服务

    # 验证 llm/stream 事件监听
    events = ensure_events(ctx)
    chunks = []
    events.on('llm/stream', lambda data: chunks.append(data['chunk']))
    # 模拟广播
    events.emit('llm/stream', {'chunk': 'hello', 'turn': 1, 'step': 1})
    print("llm/stream 收到:", chunks)   # 期望: ['hello']

asyncio.run(main())
