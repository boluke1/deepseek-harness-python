# harness-python

一个对标 **DeepSeek Harness (DSH)** 架构的 ReactAgent 系统，基于自研的 `mycordis` 插件框架构建，践行"**没有特权内核、一切皆可替换**"的哲学。

## 设计理念

与主流单体 Agent 框架不同，本系统将 Agent 拆解为多个**可独立替换的插件**，通过事件总线解耦协作。核心服务（会话、工具、系统提示、代理循环）都只是普通插件，随时可以被替换——这就是"没有特权内核"。

## 特性

- **配置即代码**：用 Profile / Bundle / Patch 声明式组装系统，运行时代码零改动即可换装任意插件。
- **服务即属性**：依赖注入通过 `ctx.llm`、`ctx.sessions` 等属性访问直接完成（反射层）。
- **类型化事件系统**：6 种分发模式（`on` / `once` / `emit` / `waterfall` / `parallel` / `serial` / `bail`），支持插件间解耦通信。
- **反应式协调**：插件依赖满足时自动激活、依赖丢失时自动停用，实现自愈循环。
- **完整 Agent Loop**：Turn / Step 双层状态机，支持 Function-Calling、流式输出、死循环防护。
- **会话日志不变式校验**：对会话日志进行 I1–I6 约束校验，保证事件溯源日志的完整性。
- **可逆副作用**：插件卸载时，其注册的服务与监听器自动清理，不留残留。

## 项目结构

```
harness-python/
├── mycordis/                     # 自研 Cordis 内核
│   └── core/
│       ├── context.py            # 服务容器 + 反射层（服务即属性）+ 可逆副作用
│       ├── plugin.py             # 插件基类
│       ├── registry.py           # 注册表：每插件独立作用域 + 反应式协调
│       └── events.py             # 6 种事件模式事件系统
├── core_services/                # DSH 核心服务（皆为可替换插件）
│   ├── sessions_plugin.py        # 事件溯源会话日志 + 不变式校验
│   ├── system_prompt_plugin.py   # 系统提示与工具 Schema 组装
│   ├── tools_plugin.py           # 工具注册表 + 守卫管道
│   └── invariant.py              # 会话日志不变式校验器（I1–I6）
├── agents/
│   ├── agent_interface.py        # Agent 抽象接口 + AgentRegistry
│   └── agent_loop_plugin.py      # Turn/Step 双层循环 + 流式广播 + 死循环防护
├── tools/
│   └── examples_plugin.py        # 示例工具（datetime / calculator / weather）
├── adapters/
│   └── llm_plugin.py             # LLM 适配器（Function-Calling + chat_stream 流式）
├── config/                       # 配置组装
│   ├── bundle.py                 # 一组配置条目
│   ├── profile.py                # 命名组合 + Patch
│   ├── loader.py                 # 合并 Bundle / 应用 Patch / 生成加载计划
│   └── react_profile.py          # 示范 Profile（react-default）
├── main.py                       # 入口：配置组装 + 交互式 CLI 多轮对话
```

## 快速开始

### 1. 安装依赖

```bash
pip install python-dotenv openai
```

> 本系统使用 `python-dotenv` 读取 `.env`，使用 `openai` 客户端对接 DeepSeek API（可换成任意 OpenAI 兼容端点）。

### 2. 配置 API 密钥

在项目根目录创建 `.env` 文件：

```bash
DEEPSEEK_API_KEY=sk-xxx
```

或直接设置系统环境变量。

### 3. 运行演示

```bash
python main.py
```

启动一个完整的 ReactAgent，进入**交互式 CLI 多轮对话模式**：支持实时输入、会话记忆、工具调用（查时间、算术、查天气）等。输入 `exit` / `quit` / `q` / `退出` 结束对话，随后打印会话历史。

```bash
python main.py
# 进入对话模式，可尝试：
#   现在几点 / 计算 12*7+5 / 北京天气 / 我叫小明
```

> 提示：若觉得运行时 `[INFO]` 日志过吵，可将 `main.py` 中的日志级别从 `logging.INFO` 调为 `logging.WARNING`。

## 工作方式

### 配置组装（对标 DSH Profile / Bundle / Patch）

```python
# 1. 定义 Bundle：一组配置条目
bundle = Bundle("react", [
    {"id": "llm",          "plugin": LLMPlugin,          "config": {}},
    {"id": "sessions",     "plugin": SessionsPlugin,     "config": {}},
    {"id": "agentLoop",    "plugin": AgentLoopPlugin,    "config": {}},
    # ...
])

# 2. 定义 Profile：命名组合 + 可附 Patch 覆盖任意条目
profile = Profile(
    name="react-default",
    bundles=["react"],
    patches=[
        # 用 Patch 替换 agentLoop 为另一个实现
        {"id": "agentLoop", "plugin": AnotherLoopPlugin, "config": {"max_steps": 3}},
    ],
)

# 3. 合并配置、生成加载计划并注册
loader = ConfigLoader()
loader.add_bundle(bundle)
loader.add_profile(profile)
plan = loader.build_plan("react-default")
await registry.load_plan(plan)
```

### 事件系统（6 种分发模式）

| 模式       | 说明                                                     |
| ---------- | -------------------------------------------------------- |
| `on`       | 常规监听，每次都触发                                     |
| `once`     | 只触发一次                                               |
| `emit`     | 广播通知，顺序触发，不等待返回值                         |
| `waterfall`| 中间件模式，每个监听器可改写传给下一个的值（可拦截）     |
| `parallel` | 并行扇出，所有监听器同时执行                             |
| `serial`   | 顺序执行，等待每个完成，并收集所有返回值                 |
| `bail`     | 短路求值，遇真值立即停止并返回该值                       |

### 服务解析（反射层）

```python
# 依赖注入：服务即属性
ctx.llm            # 同步访问
ctx.sessions       # 同步访问
await ctx.aget()   # 异步解析（支持异步 waterfall 拦截）
```

> 注：`ctx.xxx` 为同步属性访问，Python 语言层面无法 `await` 该表达式本身；需要异步解析时使用 `ctx.aget()`。

### 核心服务（皆为可替换插件）

| 插件 id      | 职责                                                         |
| ------------ | ------------------------------------------------------------ |
| `llm`        | LLM 适配器：Function-Calling 请求 + 流式输出                 |
| `sessions`   | 事件溯源会话日志：记录每条消息、工具调用、流式 chunk，并做不变式校验 |
| `systemPrompt` | 系统提示组装 + 工具 Schema 注入                            |
| `tools`      | 工具注册表：统一登记工具、暴露 Schema、守卫管道              |
| `agents`     | Agent 抽象接口 + 注册表                                      |
| `agentLoop`  | Turn / Step 双层循环：驱动 LLM + 工具调用，广播流式事件，死循环防护 |
| `examples`   | 示例工具集：datetime / calculator / weather                  |

## 会话日志不变式（I1–I6）

`core_services/invariant.py` 对会话日志执行约束校验，确保事件溯源日志的完整性：

- **I1**：`tool_result` 必须有前置的 `tool_call`。
- **I2**：每个 `tool_call` 必须闭合（有对应结果）。
- **I3**：`tool_result` 之后必须跟 `assistant`。
- **I4**：`user` 之后必须跟 `assistant` 或 `tool_call`。
- **I5**：Turn 配对正确且序号递增。
- **I6**：Step 必须嵌套在 Turn 内。

元事件（`turn/start`、`step/start`、`turn/end`、`assistant/chunk`）不参与 I3 / I4 校验，避免污染模型上下文。

## Agent Loop

- **Turn** = 一次 `run()`，对应用户的完整回答过程。
- **Step** = Turn 内部的一次循环迭代（一次 LLM 调用 + 可能的工具调用）。
- `agent/pre-step`、`agent/request` 事件通过 `waterfall` 模式可被拦截改写。
- 最终答复逐 chunk 广播 `llm/stream` 事件，并写入 `assistant/chunk` 日志。
- **死循环防护**：仅当"动作 + 观察结果"组合连续重复 ≥ 3 次才判定死循环并中止。

## 与 DeepSeek Harness 的对应关系

| harness-python                 | DeepSeek Harness                  |
| ------------------------------ | --------------------------------- |
| `mycordis/core/context.py`     | `vendor/cordis` (context + reflect) |
| `mycordis/core/events.py`      | `vendor/cordis` (events)          |
| `mycordis/core/registry.py`    | `vendor/cordis` (reflect + fiber) |
| `core_services/*`              | 核心服务（sessions / system_prompt / tools）|
| `config/` (Profile/Bundle/Patch)| `src/config` 配置组装             |
| `agents/agent_loop_plugin.py`  | `src/agents` Agent Loop           |

## 已知限制与后续方向

- `ctx.xxx` 属性访问无法 `await`（Python 语言限制），异步解析需用 `ctx.aget()`。
- 尚未实现：多智能体协作、沙箱执行、审批策略、完整测试套件、打包分发。
