# harness-python

一个对标 **DeepSeek Harness (DSH)** 架构的 ReactAgent 系统，基于自研的 `mycordis` 插件框架构建，践行"**没有特权内核、一切皆可替换**"的哲学。

核心子系统已对齐 DSH **90%+**，全面支持 Seam 模式、事件溯源、Scope 隔离、Agent 活跃跟踪等生产级特性。

## 设计理念

与主流单体 Agent 框架不同，本系统将 Agent 拆解为多个**可独立替换的插件**，通过事件总线解耦协作。核心服务（会话、工具、系统提示、代理循环）都只是普通插件，随时可以被替换——这就是"没有特权内核"。

## 核心特性

### mycordis 内核

- **配置即代码**：用 Profile / Bundle / Patch 声明式组装系统，运行时代码零改动即可换装任意插件。
- **服务即属性**：依赖注入通过 `ctx.llm`、`ctx.sessions` 等属性访问直接完成（反射层）。
- **类型化事件系统**：7 种分发模式（`on` / `once` / `emit` / `waterfall` / `parallel` / `serial` / `bail`），支持插件间解耦通信。
- **反应式协调**：插件依赖满足时自动激活、依赖丢失时自动停用，实现自愈循环。
- **可逆副作用**：插件卸载时，其注册的服务与监听器自动清理，不留残留。
- **Fiber 生命周期诊断**：每个插件的运行状态、依赖关系、副作用均可实时诊断。

### 核心子系统（90%+ 对齐 DSH）

| 子系统 | 服务键 | 能力 |
|--------|--------|------|
| **Sessions** | `ctx.sessions` | SessionEvent 类型系统 + 序列号 + 生命周期事件 + 投影查询 + InvariantService 桥接 |
| **Tools** | `ctx.tools` | scope-aware 工具过滤 + 执行事件（start/end）+ 结果自动截断 |
| **Agents** | `ctx.agents` | 活跃 Agent 跟踪 + 状态机（idle→running→finished/error）+ agent/* 事件协议 |
| **SystemPrompt** | `ctx.systemPrompt` | block 开关（enable/disable）+ 条件区块 + identity 感知注入 |
| **Scope** | `ctx.scope` | Agent 隔离作用域 + scope 内服务注册 + 销毁清理 + scope/destroyed 事件 |
| **Identity** | `ctx.identity` | 凭证存储 + Agent 身份 + metadata |
| **Invariant** | `ctx.invariant` | 内置规则（model_visible_logged / event_type_known / session_event_valid）+ 自定义规则 |
| **ContextService** | `ctx.contextService` | 上下文树 + find_by_service + 诊断快照 |

### Seam 模式（LLM 能力接缝）

LLM 能力通过三层抽象建模，实现"换一个 Provider 即改变整个产品行为"：

- **SeamDefinition**：声明接口（chat / chat_stream / close / get_model_info）
- **SeamProvider**：具体实现（DeepSeekSeamProvider 封装 AsyncOpenAI）
- **SeamConsumer**：通过 ctx.llm 消费，不关心实现

配合 **LLMAdapterService** 支持多 Provider 管理与热切换。

### Agent Loop

- **Turn / Step 双层状态机**：Turn = 一次用户交互，Step = 一次 LLM 调用 + 工具调用。
- **waterfall 拦截点**：`agent/pre-step`（改写输入）、`agent/request`（改写请求配置）。
- **流式输出**：最终答复逐 chunk 广播 `llm/stream` 事件。
- **死循环防护**：仅当"动作 + 观察结果"组合连续重复 ≥ 3 次才判定死循环。
- **活跃跟踪集成**：Agent 运行时自动标记为 active，结束后恢复 idle。
- **step 级不变式校验**：每次 LLM 回复自动触发 invariant.check()。

## 项目结构

```
harness-python/
├── mycordis/                     # 自研 Cordis 内核
│   └── core/
│       ├── context.py            # 服务容器 + 反射层 + 可逆副作用
│       ├── plugin.py             # 插件基类（标准/函数/对象三种形态）
│       ├── registry.py           # 注册表：每插件独立作用域 + 反应式协调
│       ├── events.py             # 7 种事件模式
│       ├── service.py            # Service 基类（自动注册 + 配置合并）
│       ├── fiber.py              # Fiber 生命周期 + await_ready
│       ├── scope.py              # ★ Scope 隔离 + 销毁清理 + 事件
│       ├── identity.py           # ★ Identity 凭证 + 身份 + metadata
│       ├── invariant.py          # ★ Invariant 内置规则 + session_event_valid
│       ├── seam.py               # ★ Seam 三层抽象（Definition + Provider + Consumer）
│       ├── llm.py                # ★ LLM 适配器（多 Provider + 热切换）
│       ├── context_service.py    # ★ 上下文管理（树 + 诊断 + find_by_service）
│       ├── logger.py             # 日志服务
│       ├── inject.py             # @Inject / @Provide 装饰器
│       └── disposable.py         # 可逆副作用基础设施
├── core_services/                # DSH 核心服务（皆为可替换插件）
│   ├── sessions_plugin.py        # ★ SessionEvent + 序列号 + 生命周期 + 投影查询
│   ├── system_prompt_plugin.py   # ★ block 开关 + 条件区块 + identity 感知
│   ├── tools_plugin.py           # ★ scope-aware + 执行事件 + 结果截断
│   └── invariant.py              # 会话级不变式校验器（I1–I6 状态机）
├── agents/
│   ├── agent_interface.py        # ★ Agent 抽象 + 活跃跟踪 + 状态机 + 事件协议
│   └── agent_loop_plugin.py      # ★ Turn/Step 循环 + 活跃集成 + step 级不变式
├── tools/
│   └── examples_plugin.py        # 示例工具（datetime / calculator / weather）
├── config/                       # 配置组装
│   ├── bundle.py                 # 一组配置条目
│   ├── profile.py                # 命名组合 + Patch
│   └── loader.py                 # 合并 Bundle / 应用 Patch / 生成加载计划
├── main.py                       # 入口：Seam + 核心子系统 + 交互式 CLI
├── demo.py                       # mycordis 12 项内核特性独立演示
├── test_integration.py           # 集成测试（Seam + 核心子系统 + 业务插件）
└── test_subsystems.py            # 核心子系统单元测试
```

## 快速开始

### 1. 安装依赖

```bash
pip install python-dotenv openai
```

### 2. 配置 API 密钥

在项目根目录创建 `.env` 文件：

```bash
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_MODEL=deepseek-chat    # 可选
```

### 3. 运行

```bash
python main.py
```

进入**交互式 CLI 多轮对话模式**。支持实时输入、会话记忆、工具调用（查时间、算术、查天气）。

```bash
# 对话示例：
#   现在几点 / 计算 12*7+5 / 北京天气

# CLI 诊断命令：
#   diag      — 全量诊断（Fiber + 上下文树 + 所有子系统状态）
#   scope     — Scope 隔离信息 + 每 scope 服务
#   identity  — 凭证 + Agent 身份 + metadata
#   invariant — 规则 + 违反记录
#   seam      — Seam 定义 + Provider + 活跃 Provider
#   ctx       — 上下文树 + find_by_service
#   sessions  — 会话摘要 + 事件投影（按类型过滤）
#   tools     — 工具列表 + scope 映射 + scope-aware 过滤
#   agents    — Agent 注册表 + 活跃跟踪 + 状态机
#   prompt    — SystemPrompt block 开关切换
```

## 工作方式

### 配置组装（Profile / Bundle / Patch）

```python
bundle = Bundle("react", [
    {"id": "sessions",     "plugin": SessionsPlugin,     "config": {}},
    {"id": "systemPrompt", "plugin": SystemPromptPlugin, "config": {}},
    {"id": "tools",        "plugin": ToolsPlugin,        "config": {}},
    {"id": "agents",       "plugin": AgentsPlugin,       "config": {}},
    {"id": "agentLoop",    "plugin": AgentLoopPlugin,    "config": {}},
])

profile = Profile(name="react-default", bundles=["react"], patches=[])

loader = ConfigLoader()
loader.add_bundle(bundle)
loader.add_profile(profile)
plan = loader.build_plan("react-default")
await registry.load_plan(plan)
```

> 注意：LLM 能力由 Seam 模式提供（`DeepSeekSeamProvider`），不在 Bundle 中声明。

### Seam 模式（LLM 能力接缝）

```python
# 1. 定义 Seam 接口
llm_seam = SeamDefinition(name="llm", methods=["chat", "chat_stream", "close"])

# 2. 注册 Provider
provider = DeepSeekSeamProvider(api_key, model, base_url)
llm_seam.register_provider(provider, root_ctx, "seam:llm")

# 3. 消费（AgentLoop 通过 ctx.llm 访问，不关心实现）
result = await ctx.llm.chat(messages, tools)
```

### 事件系统（7 种分发模式）

| 模式       | 说明                                                     |
| ---------- | -------------------------------------------------------- |
| `on`       | 常规监听，每次都触发                                     |
| `once`     | 只触发一次                                               |
| `emit`     | 广播通知，顺序触发，不等待返回值                         |
| `waterfall`| 中间件模式，每个监听器可改写传给下一个的值               |
| `parallel` | 并行扇出，所有监听器同时执行                             |
| `serial`   | 顺序执行，等待每个完成，并收集所有返回值                 |
| `bail`     | 短路求值，遇真值立即停止并返回该值                       |

### 会话日志不变式（I1–I6）

`core_services/invariant.py` 对会话日志执行约束校验，确保事件溯源日志的完整性：

- **I1**：`tool_result` 必须有前置的 `tool_call`。
- **I2**：每个 `tool_call` 必须闭合（有对应结果）。
- **I3**：`tool_result` 之后必须跟 `assistant`。
- **I4**：`user` 之后必须跟 `assistant` 或 `tool_call`。
- **I5**：Turn 配对正确且序号递增。
- **I6**：Step 必须嵌套在 Turn 内。

## 测试

```bash
# 集成测试（Seam + 核心子系统 + 业务插件）
python test_integration.py

# 核心子系统单元测试
python test_subsystems.py

# mycordis 内核特性演示
python demo.py
```

## 与 DeepSeek Harness 的对应关系

| harness-python                 | DeepSeek Harness                  | 对齐度 |
| ------------------------------ | --------------------------------- | ------ |
| `mycordis/core/*`              | `vendor/cordis` 内核              | ~92%   |
| `core_services/sessions_*`     | `ctx.sessions` + SessionEventMap  | ~90%   |
| `core_services/tools_*`        | `ctx.tools` + scope-aware         | ~90%   |
| `core_services/system_prompt_*`| `ctx.systemPrompt` + block 开关   | ~90%   |
| `agents/agent_interface.py`    | `ctx.agents` + 活跃跟踪           | ~90%   |
| `agents/agent_loop_plugin.py`  | `src/agents` Agent Loop           | ~90%   |
| `mycordis/core/seam.py`        | Seam 能力接缝                     | ~90%   |
| `mycordis/core/llm.py`         | LLM 适配器 + 热切换               | ~90%   |
| `config/` (Profile/Bundle)     | `src/config` 配置组装             | ~85%   |

## 已知限制与后续方向

- `ctx.xxx` 属性访问无法 `await`（Python 语言限制），异步解析需用 `ctx.aget()`。
- 尚未实现：多智能体协作、沙箱执行、审批策略（guardrails）、完整测试套件、打包分发。
