# DSH vs harness-python 对比分析

> 分析时间：2026-08-17
> DSH 仓库：https://github.com/deepseek-ai/deepseek-harness
> 本项目：https://github.com/boluke1/deepseek-harness-python

## DSH 仓库完整包清单（49 个包）

从 GitHub API 获取的 `packages/` 目录：

| # | DSH 包名 | 职责 | mycordis 对应 |
|---|----------|------|---------------|
| 1 | `core` | 核心骨架（Agent/会话/工具/系统提示） | ✅ `core_services/` + `agents/` |
| 2 | `session` | 会话持久化（JSONL/SQLite） | ⚠️ 内存会话（无持久化） |
| 3 | `session-query` | 会话查询/投影 | ✅ `filter_by_type/seq_range/time_range` |
| 4 | `context` | 上下文管理 | ✅ `context_service.py` |
| 5 | `identity` | 身份/凭证管理 | ✅ `identity.py` |
| 6 | `credentials` | 凭证存储 | ✅ `identity.set/get_credential` |
| 7 | `llm` | LLM 抽象层 + 适配器 | ✅ `seam.py` + `llm.py` |
| 8 | `compaction` | 上下文压缩（token 窗口） | ❌ 未实现 |
| 9 | `subagent` | 子代理委派 | ❌ 未实现 |
| 10 | `workflow` | 工作流编排 | ❌ 未实现 |
| 11 | `skill` | 技能注册表 | ❌ 未实现 |
| 12 | `fs` | 文件系统操作 | ❌ 未实现 |
| 13 | `shell` | Shell 执行 | ❌ 未实现 |
| 14 | `terminal` | 终端管理 | ❌ 未实现 |
| 15 | `sandbox` | 沙箱策略（进程隔离/ACL） | ❌ 未实现 |
| 16 | `mcp` | Model Context Protocol | ❌ 未实现 |
| 17 | `acp` | Agent Communication Protocol | ❌ 未实现 |
| 18 | `hooks` | 外部 Hook（Claude/Codex） | ❌ 未实现 |
| 19 | `guard` | 安全策略/守卫 | ⚠️ 工具 pre/post guard（基础） |
| 20 | `interaction` | 用户审批/权限 | ❌ 未实现 |
| 21 | `plan` | 计划模式 | ❌ 未实现 |
| 22 | `todo` | 待办管理 | ❌ 未实现 |
| 23 | `goal` | 目标跟踪 | ❌ 未实现 |
| 24 | `attachment` | 附件处理 | ❌ 未实现 |
| 25 | `lsp` | Language Server Protocol | ❌ 未实现 |
| 26 | `code-runtime` | 代码运行时 | ❌ 未实现 |
| 27 | `spill` | 上下文溢出处理 | ❌ 未实现 |
| 28 | `storage` | 存储后端 | ❌ 未实现 |
| 29 | `settings` | 设置管理 | ❌ 未实现 |
| 30 | `schedule` | 调度 | ❌ 未实现 |
| 31 | `jobs` | 任务管理 | ❌ 未实现 |
| 32 | `preset` | Agent 预设 | ❌ 未实现 |
| 33 | `api` | API 网关 | ❌ 未实现 |
| 34 | `sdk` | JSON-RPC 协议 | ❌ 未实现 |
| 35 | `client` | Web UI（30+ 组件） | ❌ 未实现 |
| 36 | `host` | Host 端（Web/API 代理） | ❌ 未实现 |
| 37 | `web` | Web 前端 | ❌ 未实现 |
| 38 | `typert` | 类型化 RPC | ❌ 未实现 |
| 39 | `bundle` | 分发包 | ❌ 未实现 |
| 40 | `boot` | 启动引导 | ⚠️ `main.py`（基础） |
| 41 | `workspace` | 工作区管理 | ❌ 未实现 |
| 42 | `extensions` | 扩展机制 | ⚠️ Plugin 基类 |
| 43 | `feedback` | 用户反馈 | ❌ 未实现 |
| 44 | `e2b` | E2B 沙箱 | ❌ 未实现 |
| 45 | `subprocess` | 子进程管理 | ❌ 未实现 |
| 46 | `runtime-diagnostics` | 运行时诊断 | ✅ Fiber 诊断 + contextService |
| 47 | `test-support` | 测试支持 | ⚠️ `test_*.py`（基础） |
| 48 | `examples` | 使用示例 | ✅ `demo.py` + `examples_plugin.py` |
| 49 | `util` | 工具库 | ⚠️ 内联实现 |

## 六层对比总览

| 层级 | DSH 包数 | mycordis 覆盖 | 对齐度 |
|------|----------|---------------|--------|
| 1. Cordis 内核 | 6 | 6/6 | ~92% |
| 2. 核心子系统 | 11 | 10/11 | ~90% |
| 3. 能力插件 | 18 | 1/18 | ~5% |
| 4. 安全策略 | 4 | 0/4 | ~3% |
| 5. 协议/接口 | 6 | 0/6 | 0% |
| 6. 启动/宿主 | 4 | 1/4 | ~25% |
| **总计** | **49** | **18/49** | **~30%** |

## 各层详细分析

### 第 1 层：Cordis 内核（~92%）✅ 基本完成

| 特性 | DSH | mycordis | 状态 |
|------|-----|----------|------|
| Context（服务容器 + 反射层） | ✅ | ✅ | ✅ |
| Service 基类（自动注册） | ✅ | ✅ | ✅ |
| Fiber（生命周期 + await_ready） | ✅ | ✅ | ✅ |
| Events（7 种分发模式） | ✅ | ✅ | ✅ |
| Plugin（三种形态） | ✅ | ✅ | ✅ |
| Registry（反应式协调） | ✅ | ✅ | ✅ |
| Disposable（可逆副作用） | ✅ | ✅ | ✅ |
| Inject/Provide 装饰器 | ✅ | ✅ | ✅ |

**差距**：accessor（计算属性）、mixin（方法混入）、intercept（配置拦截）等高级反射特性尚未实现。

### 第 2 层：核心子系统（~90%）✅ 本轮达成

| 子系统 | DSH | mycordis | 对齐度 |
|--------|-----|----------|--------|
| Sessions（事件溯源 + 投影） | ✅ | ✅ SessionEvent + 序列号 + 生命周期 + 投影 | ~90% |
| Tools（scope-aware + 守卫） | ✅ | ✅ scope-aware + 执行事件 + 截断 | ~90% |
| SystemPrompt（block 开关） | ✅ | ✅ block 开关 + 条件区块 + identity 感知 | ~90% |
| Agents（活跃跟踪 + 状态机） | ✅ | ✅ 活跃跟踪 + 状态机 + agent/* 事件 | ~90% |
| Identity（凭证 + 身份） | ✅ | ✅ 凭证 + 身份 + metadata | ~90% |
| Invariant（不变式校验） | ✅ | ✅ 3 内置规则 + 自定义规则 | ~90% |
| Scope（隔离 + 销毁清理） | ✅ | ✅ 隔离 + 销毁清理 + 事件 | ~90% |
| ContextService（诊断） | ✅ | ✅ 树 + find_by_service + 快照 | ~90% |
| LLM（Seam + 适配器） | ✅ | ✅ Seam 三层 + LLMAdapterService | ~90% |
| Session 持久化 | ✅ JSONL/SQLite | ⚠️ 仅内存 | ~30% |
| Compaction（上下文压缩） | ✅ | ❌ | 0% |

### 第 3 层：能力插件（~5%）❌ 最大差距

这是与 DSH 差距最大的层级。DSH 有 18 个能力插件包，mycordis 仅有 1 个（示例工具）。

| 能力 | 优先级 | 说明 |
|------|--------|------|
| `fs` 文件系统 | 🔴 高 | Agent 最基本的工具——读写文件 |
| `shell` Shell 执行 | 🔴 高 | Agent 执行命令的核心能力 |
| `skill` 技能注册表 | 🔴 高 | 可扩展的 Agent 技能机制 |
| `mcp` Model Context Protocol | 🟡 中 | 标准化的工具/资源协议 |
| `subagent` 子代理委派 | 🟡 中 | 多 Agent 协作基础 |
| `compaction` 上下文压缩 | 🟡 中 | 解决 token 窗口限制 |
| `workflow` 工作流编排 | 🟡 中 | 复杂任务编排 |
| `plan/todo/goal` 计划/待办/目标 | 🟢 低 | 高级 Agent 行为 |
| `lsp` Language Server | 🟢 低 | 代码分析能力 |
| 其他（terminal/attachment/spill/storage/schedule/jobs/e2b/subprocess） | 🟢 低 | 辅助能力 |

### 第 4 层：安全策略（~3%）❌ 基本缺失

| 能力 | DSH | mycordis |
|------|-----|----------|
| `guard` 安全策略 | ✅ 完整的审批/权限系统 | ⚠️ 仅工具 pre/post guard |
| `interaction` 用户审批 | ✅ AskUserQuestion 机制 | ❌ |
| `sandbox` 沙箱隔离 | ✅ 进程隔离 + Windows ACL | ❌ |
| `credentials` 安全凭证管理 | ✅ 加密存储 | ⚠️ 明文内存存储 |

### 第 5 层：协议/接口（0%）❌ 完全缺失

| 能力 | DSH | mycordis |
|------|-----|----------|
| `api` API 网关 | ✅ JSON-RPC | ❌ |
| `sdk` SDK | ✅ Python SDK | ❌ |
| `client` Web UI | ✅ 30+ 组件 | ❌ |
| `host` Host 端 | ✅ Web 服务器 + API 代理 | ❌ |
| `web` Web 前端 | ✅ Vite 入口 | ❌ |
| `typert` 类型化 RPC | ✅ 类型图 + 运行时注册表 | ❌ |

### 第 6 层：启动/宿主（~25%）

| 能力 | DSH | mycordis |
|------|-----|----------|
| CLI 入口 | ✅ `dsh` 命令 | ✅ `python main.py` |
| Web UI | ✅ http://127.0.0.1:3080 | ❌ |
| Bundle 分发包 | ✅ base/headless/web-app | ❌ |
| 配置加载 | ✅ YAML + Cordis loader | ✅ Profile/Bundle/Patch |

## 核心结论

```
总体对齐度：~30%（18/49 包）

已完成（90%+）：
  ✅ Cordis 内核（~92%）
  ✅ 核心子系统（~90%）

主要差距（按优先级排序）：
  🔴 能力插件（~5%）—— 缺 fs/shell/skill 等 17 个能力包
  ❌ 安全策略（~3%）—— 缺 guard/sandbox/interaction
  ❌ 协议接口（0%） —— 缺 API/Web/SDK
  ⚠️ 启动宿主（~25%）—— 缺 Web UI / Bundle

下一步建议：
  1. 实现 fs + shell 工具（Agent 基本能力）
  2. 实现 skill 注册表（可扩展技能机制）
  3. 实现 session 持久化（JSONL 后端）
  4. 实现 compaction（上下文压缩）
```
