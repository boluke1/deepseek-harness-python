# ============================================================================
# main.py
# 程序入口：全面接入 mycordis 内核 + 核心子系统 + Seam 模式。
#
# 整体流程（对标 DSH "配置即代码" + Seam 可替换能力）：
#   1. 加载环境变量，校验 DeepSeek API 密钥。
#   2. 创建 Registry（自动创建核心子系统：logger/scope/identity/invariant/contextService）。
#   3. ★ Seam 模式：LLM 能力建模为三层抽象（Definition + Provider + Consumer）。
#   4. 定义 Bundle（无 LLMPlugin，改由 Seam 提供 ctx.llm）。
#   5. 用 ConfigLoader 合并配置、应用 Patch，生成加载计划。
#   6. Registry.load_plan 按计划注册插件（依赖注入 + 反应式协调自动激活）。
#   7. ★ 内部事件监控：监听 internal/plugin, internal/status, internal/service。
#   8. ★ 核心子系统全面启用：
#      · ctx.identity  — 凭证存储 + Agent 身份 + metadata
#      · ctx.scope     — Agent 隔离作用域 + scope 内注册服务
#      · ctx.invariant — 内置规则 + 自定义规则（user_non_empty / tool_call_valid）
#      · ctx.llm       — LLM Seam Provider（可热切换）
#   9. 注入工具 Schema 进入交互式 CLI 多轮对话。
#  10. ★ 优雅关闭：不变式总结 + contextService 快照 + revert + 卸载。
#
#   ★ 本版本全面接入 mycordis 所有特性：
#     · Cordis 内核：ctx.logger / ctx.on/ctx.emit / ctx.waterfall / ctx.effect()
#     · 内部事件协议：internal/plugin, internal/status, internal/service
#     · Fiber 生命周期诊断 + 优雅关闭
#     · ★ 核心子系统（90%+）：
#       - ctx.sessions  — SessionEvent 类型系统 + 序列号 + 生命周期 + 投影查询
#       - ctx.tools     — scope-aware 过滤 + 执行事件 + 结果截断
#       - ctx.agents    — 活跃 Agent 跟踪 + 状态机 + agent/* 事件协议
#       - ctx.systemPrompt — block 开关 + 条件区块 + identity 感知
#       - ctx.scope     — Agent 隔离 + 销毁清理 + scope/destroyed 事件
#       - ctx.identity  — 凭证存储 + Agent 身份 + metadata
#       - ctx.invariant — 内置规则 + 自定义规则 + session_event_valid
#       - ctx.contextService — 上下文树 + find_by_service
#     · ★ Seam 模式：LLM 能力接缝（Definition + Provider + Consumer + Registry）
#     · ★ LLM 适配器：LLMAdapterService 多 Provider 管理 + 热切换
# ============================================================================

import asyncio
import json
import logging
import os
from datetime import datetime
from dotenv import load_dotenv

from openai import AsyncOpenAI

# ---- mycordis 内核 ----
from mycordis import (
    Registry, Context, Plugin,
    SeamDefinition, SeamProvider, SeamRegistry,
    LLMAdapterService,
)

# ---- 业务插件 ----
from core_services.sessions_plugin import SessionsPlugin
from core_services.system_prompt_plugin import SystemPromptPlugin
from core_services.tools_plugin import ToolsPlugin
from agents.agent_interface import AgentsPlugin
from agents.agent_loop_plugin import AgentLoopPlugin
from tools.examples_plugin import ExamplesPlugin

# ---- 配置组装 ----
from config.bundle import Bundle
from config.profile import Profile
from config.loader import ConfigLoader

load_dotenv()

if not os.getenv("DEEPSEEK_API_KEY"):
    print("WARNING: DEEPSEEK_API_KEY not set.")
    print("   Please add DEEPSEEK_API_KEY=sk-xxx to .env file.")
    exit(1)

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')


# ============================================================================
# ★ Seam 模式：LLM 能力接缝
# ============================================================================

class DeepSeekSeamProvider(SeamProvider):
    """
    ★ DeepSeek LLM Provider（Seam 实现层）。

    封装 AsyncOpenAI 客户端，实现 Seam 声明的 chat / chat_stream 接口。
    对标 DSH "换一个 Provider 即改变整个产品行为"。
    """

    def __init__(self, api_key: str, model: str, base_url: str, ctx_logger=None):
        self.name = "deepseek"
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._log = ctx_logger

    async def chat(self, messages: list, tools: list = None) -> dict:
        kwargs = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            response = await self._client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            message = choice.message
            if getattr(message, "tool_calls", None):
                tool_calls = []
                for tc in message.tool_calls:
                    try:
                        arguments = json.loads(tc.function.arguments)
                    except Exception:
                        arguments = {}
                    tool_calls.append({"name": tc.function.name, "arguments": arguments})
                if self._log:
                    self._log.info(f"[Seam:DeepSeek] tool calls: {[tc['name'] for tc in tool_calls]}")
                return {"content": message.content or "", "tool_calls": tool_calls}
            content = message.content or ""
            if self._log:
                self._log.info(f"[Seam:DeepSeek] text reply: {content[:50]}...")
            return {"content": content, "tool_calls": []}
        except Exception as e:
            if self._log:
                self._log.error(f"[Seam:DeepSeek] API error: {e}")
            return {"content": f"API error: {e}", "tool_calls": []}

    async def chat_stream(self, messages: list, tools: list = None):
        kwargs = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        try:
            stream = await self._client.chat.completions.create(**kwargs)
            tool_calls_accum = {}
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    yield delta.content
                if getattr(delta, "tool_calls", None):
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_accum:
                            tool_calls_accum[idx] = {
                                "name": tc.function.name or "",
                                "arguments_str": tc.function.arguments or "",
                            }
                        else:
                            if tc.function.name:
                                tool_calls_accum[idx]["name"] = tc.function.name
                            tool_calls_accum[idx]["arguments_str"] += tc.function.arguments or ""
            if tool_calls_accum:
                tool_calls = []
                for idx in sorted(tool_calls_accum):
                    try:
                        arguments = json.loads(tool_calls_accum[idx]["arguments_str"])
                    except Exception:
                        arguments = {}
                    tool_calls.append({"name": tool_calls_accum[idx]["name"], "arguments": arguments})
                yield ("__TOOL_CALLS__", tool_calls)
        except Exception as e:
            if self._log:
                self._log.error(f"[Seam:DeepSeek] stream error: {e}")
            yield f"API error: {e}"

    async def close(self):
        try:
            await self._client.close()
        except Exception:
            pass

    def get_model_info(self) -> dict:
        return {"name": self._model, "type": "deepseek"}


# ============================================================================
# 配置组装（Bundle / Profile）
# ============================================================================

def build_react_bundle() -> Bundle:
    """
    ReactAgent Bundle。

    ★ 注意：不包含 LLMPlugin — LLM 能力由 Seam 模式提供（ctx.llm 由 SeamProvider 注册）。
    """
    return Bundle("react", [
        {"id": "sessions",     "plugin": SessionsPlugin,     "config": {}},
        {"id": "systemPrompt", "plugin": SystemPromptPlugin, "config": {}},
        {"id": "tools",        "plugin": ToolsPlugin,        "config": {}},
        {"id": "agents",       "plugin": AgentsPlugin,       "config": {}},
        {"id": "agentLoop",    "plugin": AgentLoopPlugin,    "config": {}},
        {"id": "examples",     "plugin": ExamplesPlugin,     "config": {}},
    ])


def build_react_profile() -> Profile:
    return Profile(name="react-default", bundles=["react"], patches=[])


# ============================================================================
# 事件监控
# ============================================================================

def setup_internal_event_monitor(registry: Registry) -> None:
    """★ 设置内部事件监控（对标 DSH internal event protocol）。"""
    root_ctx = registry._root_ctx

    root_ctx.on('internal/plugin', lambda fiber: print(
        f"  [internal/plugin] plugin registered: {fiber.name} (uid={fiber.uid})"
    ))

    root_ctx.on('internal/status', lambda fiber, old_state: print(
        f"  [internal/status] {fiber.name}: {old_state} -> {fiber.state}"
    ))

    def on_service(key_or_tuple, value=None):
        if isinstance(key_or_tuple, tuple):
            key, val = key_or_tuple
        else:
            key, val = key_or_tuple, value
        print(f"  [internal/service] service registered: {key}")

    root_ctx.on('internal/service', on_service)


# ============================================================================
# ★ 核心子系统初始化
# ============================================================================

def setup_core_subsystems(root: Context) -> dict:
    """
    ★ 全面初始化核心子系统。

    · ctx.identity  — 凭证 + Agent 身份 + metadata
    · ctx.scope     — Agent 隔离作用域 + scope 内注册服务
    · ctx.invariant — 启用不变式运行时校验
    """
    extra = {}

    # ---- Identity：凭证 + 身份 + metadata ----
    identity = root.identity
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    identity.set_credential("deepseek_api_key", api_key)
    identity.set_credential("deepseek_model", model)

    agent_id = identity.create_agent_identity(
        "react-loop",
        name="ReAct Agent",
        role="coder",
        description="Function-calling ReAct loop agent with Seam-based LLM",
    )
    # ★ 设置身份 metadata（对标 DSH identity metadata）。
    agent_id.set_metadata("version", "2.0")
    agent_id.set_metadata("created_at", datetime.now().isoformat())
    agent_id.set_metadata("llm_provider", "deepseek")
    agent_id.set_metadata("seam_pattern", True)
    root.logger.info(
        f"[Identity] credentials stored, identity created: "
        f"{agent_id} metadata={list(agent_id.metadata.keys())}"
    )

    # ---- Scope：Agent 隔离作用域 + scope 内注册服务 ----
    scope_svc = root.scope
    agent_scope = scope_svc.create_scope("react-loop")
    # ★ 在 scope 内注册 Agent 专属服务（对标 DSH per-agent scope）。
    session_counter = {"count": 0}
    dispose_scope_counter = agent_scope.register("session_counter", session_counter)
    # ★ 注册 scope 清理到根 ctx.effect()（对标可逆副作用）。
    root.effect(dispose_scope_counter)
    root.logger.info(
        f"[Scope] scope created: {agent_scope.id}, "
        f"services={agent_scope.list_services()}"
    )
    extra["agent_scope"] = agent_scope

    # ---- Invariant：启用不变式运行时校验 ----
    invariant = root.invariant
    invariant.enable()
    root.logger.info(f"[Invariant] enabled, rules: {invariant.list_rules()}")

    return extra


# ============================================================================
# ★ Seam 模式：创建 LLM Seam + 注册 Provider + LLM 适配器
# ============================================================================

def setup_seam_pattern(root: Context) -> dict:
    """
    ★ 创建 LLM Seam、注册 DeepSeek Provider、创建 LLM 适配器。

    Seam 三层抽象：
      · SeamDefinition("llm")     — 声明 LLM 接口（chat, chat_stream, close）
      · DeepSeekSeamProvider       — DeepSeek 实现
      · AgentLoopPlugin (consumer) — 通过 ctx.llm 消费，不关心实现
    """
    result = {}
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    # ---- 1. 创建 SeamRegistry（管理所有 Seam 定义）----
    seam_registry = SeamRegistry()

    # ---- 2. 定义 LLM Seam（接口声明层）----
    llm_seam = SeamDefinition(
        name="llm",
        methods=["chat", "chat_stream", "close", "get_model_info"],
    )

    # ---- 3. 创建 DeepSeek Provider（实现层）----
    deepseek_provider = DeepSeekSeamProvider(
        api_key=api_key, model=model, base_url=base_url, ctx_logger=root.logger
    )

    # ---- 4. 注册 Provider 到 Seam（自动 provide ctx.llm）----
    dispose_llm = llm_seam.register_provider(deepseek_provider, root, "seam:llm")
    root.effect(dispose_llm)
    seam_registry.register(llm_seam)

    root.logger.info(
        f"[Seam] LLM Seam created: methods={llm_seam.methods}, "
        f"providers={llm_seam.list_providers()}, active={llm_seam.get_active_provider().name}"
    )

    # ---- 5. 创建 LLM 适配器服务（支持多 Provider 热切换）----
    adapter = LLMAdapterService(root, "llmAdapter")
    adapter.register_provider("deepseek", deepseek_provider, activate=True)
    root.logger.info(
        f"[LLM Adapter] registered: providers={adapter.list_providers()}, "
        f"model_info={adapter.get_model_info()}"
    )

    # ---- 6. 注册 AsyncOpenAI 客户端清理 ----
    async def _cleanup_llm():
        await adapter.close_all()
        root.logger.info("[Seam] LLM providers closed")
    root.effect(_cleanup_llm)

    result["seam_registry"] = seam_registry
    result["llm_seam"] = llm_seam
    result["adapter"] = adapter
    result["provider"] = deepseek_provider
    return result


# ============================================================================
# ★ 自定义不变式规则
# ============================================================================

def setup_custom_invariant_rules(root: Context) -> None:
    """★ 注册自定义不变式规则（扩展内置规则）。"""
    from mycordis import InvariantViolation
    invariant = root.invariant

    def rule_user_non_empty(event):
        """用户消息不能为空。"""
        if isinstance(event, dict) and event.get("type") == "user_message":
            content = event.get("content", "")
            if not content or not content.strip():
                return InvariantViolation(
                    "user_non_empty",
                    "User message content must not be empty",
                    event,
                )
        return None

    def rule_tool_call_valid(event):
        """工具调用必须有 name 和 arguments。"""
        if isinstance(event, dict) and event.get("type") == "tool_call":
            content = event.get("content", "")
            if isinstance(content, str):
                try:
                    data = json.loads(content)
                    if "name" not in data:
                        return InvariantViolation(
                            "tool_call_valid",
                            "Tool call must have 'name' field",
                            event,
                        )
                except (json.JSONDecodeError, TypeError):
                    pass
        return None

    invariant.register_rule("user_non_empty", rule_user_non_empty)
    invariant.register_rule("tool_call_valid", rule_tool_call_valid)
    root.logger.info(f"[Invariant] custom rules registered: {invariant.list_rules()}")


# ============================================================================
# 主函数
# ============================================================================

async def main():
    """
    ★ 全面版：Seam 模式 + 核心子系统 + 内部事件监控 + Fiber 诊断 + 优雅关闭。
    """
    print("\n" + "=" * 60)
    print("   ReactAgent (Profile/Bundle + Seam + Core Subsystems)")
    print("   ★ Full mycordis: Seam / Scope / Identity / Invariant / LLM Adapter")
    print("=" * 60 + "\n")

    # ---- 1. Create Registry (auto-creates core subsystems) ----
    print("[boot] Creating Registry...")
    registry = Registry()
    root = registry._root_ctx

    core_services = ['logger', 'scope', 'identity', 'invariant', 'contextService']
    print(f"[boot] Core subsystems ready: {core_services}")

    # ---- 2. Internal event monitor ----
    print("[boot] Setting up internal event monitor...")
    setup_internal_event_monitor(registry)

    # ---- 3. ★ Core subsystems ----
    print("\n[boot] Initializing core subsystems...")
    subsystem_extra = setup_core_subsystems(root)

    # ---- 4. ★ Seam pattern: LLM capability seam ----
    print("[boot] Setting up Seam pattern (LLM capability seam)...")
    seam_extra = setup_seam_pattern(root)

    # ---- 5. ★ Custom invariant rules ----
    print("[boot] Registering custom invariant rules...")
    setup_custom_invariant_rules(root)

    # ---- 6. Config assembly ----
    print("\n[boot] Config assembly Profile...")
    loader = ConfigLoader()
    loader.add_bundle(build_react_bundle())
    loader.add_profile(build_react_profile())

    plan = loader.build_plan("react-default")
    print(f"[config] Load plan: {len(plan)} plugins:")
    for entry in plan:
        print(f"       - {entry['id']} ({entry['plugin'].__name__})")

    # ---- 7. Register plugins ----
    print("\n[boot] Registering plugins...")
    await registry.load_plan(plan)

    # ---- 8. ctx.logger status ----
    root.logger.info("All plugins loaded")

    # ---- 8.5 ★ SystemPrompt：条件区块 + block 开关演示（插件加载后） ----
    system_prompt = root.systemPrompt
    system_prompt.add_conditional_block(
        "env_info", "Environment: Windows, Python 3.13", order=50,
        condition_fn=lambda: True,
    )
    system_prompt.add_block("debug_hints", "Debug mode: enabled", order=300)
    root.logger.info(
        f"[SystemPrompt] blocks: {system_prompt.get_block_ids()}, "
        f"disabled: {system_prompt.get_disabled_blocks()}"
    )

    # ---- 9. Inject tool schemas into system prompt ----
    print("\n[boot] Assembling system prompt + tool schemas...")
    tools = root.tools
    system_prompt = root.systemPrompt
    for schema in tools.list_schemas():
        fn_name = schema["function"]["name"]
        system_prompt.add_tool_schema(fn_name, json.dumps(schema, ensure_ascii=False))
    root.logger.info(f"Injected {len(tools.list_schemas())} tool schemas")

    # ---- 10. ★ Fiber diagnostics ----
    print("\n[diag] Fiber state:")
    for name, fiber in registry._fibers.items():
        effects_count = len(fiber._disposables._items) if hasattr(fiber._disposables, '_items') else '?'
        print(f"  - {name}: state={fiber.state}, uid={fiber.uid}, effects={effects_count}")

    # ---- 11. ★ Full core subsystem diagnostics ----
    print("\n[diag] Core subsystems:")
    # Identity
    identity = root.identity
    agent_id = identity.get_agent_identity("react-loop")
    print(f"  - identity: creds={identity.list_credentials()}, "
          f"agents={identity.list_identities()}, "
          f"metadata={agent_id.metadata if agent_id else {}}")
    # Scope
    scope_svc = root.scope
    agent_scope = subsystem_extra.get("agent_scope")
    print(f"  - scope: scopes={scope_svc.list_scopes()}, "
          f"agent_scope_services={agent_scope.list_services() if agent_scope else []}")
    # Invariant
    invariant = root.invariant
    print(f"  - invariant: enabled={invariant.is_enabled}, rules={invariant.list_rules()}")
    # ContextService
    ctx_svc = root.contextService
    print(f"  - contextService: active_contexts={ctx_svc.active_count}")
    # ★ Seam
    seam_reg = seam_extra["seam_registry"]
    llm_seam = seam_extra["llm_seam"]
    print(f"  - seams: {seam_reg.list()}, "
          f"llm providers={llm_seam.list_providers()}, "
          f"active={llm_seam.get_active_provider().name}")
    # ★ LLM Adapter
    adapter = seam_extra["adapter"]
    print(f"  - llmAdapter: providers={adapter.list_providers()}, "
          f"model={adapter.get_model_info()}")
    # ★ Sessions
    try:
        sessions = root.sessions
        summaries = sessions.get_all_summaries()
        print(f"  - sessions: {len(summaries)} sessions, "
              f"total_events={sum(s['event_count'] for s in summaries)}")
    except Exception:
        pass
    # ★ Tools scope mapping
    try:
        tools_svc = root.tools
        scopes = tools_svc.get_tool_scopes()
        print(f"  - tools: {len(tools_svc.list_tools())} tools, scopes={scopes}")
    except Exception:
        pass
    # ★ Agents active tracking
    try:
        agents_reg = root.agents
        print(f"  - agents: registered={agents_reg.list()}, "
              f"active={agents_reg.list_active()}, "
              f"status={agents_reg.get_all_status()}")
    except Exception:
        pass
    # ★ SystemPrompt blocks
    try:
        sp = root.systemPrompt
        print(f"  - systemPrompt: blocks={sp.get_block_ids()}, "
              f"disabled={sp.get_disabled_blocks()}")
    except Exception:
        pass

    # ---- 12. Get agentLoop ----
    agent = root.agentLoop

    # ---- 13. List registered agents ----
    try:
        agents_registry = root.agents
        root.logger.info(f"Registered agents: {agents_registry.list()}")
    except (KeyError, AttributeError):
        pass

    # ---- 14. Interactive CLI ----
    print("\n" + "=" * 60)
    print("   Dialog mode. Type 'exit'/'quit'/'q' to end.")
    print("   Commands: diag | scope | identity | invariant | seam | ctx")
    print("             sessions | tools | agents | prompt")
    print("   Try: what time is it / calculate 12*7+5")
    print("=" * 60 + "\n")

    try:
        while True:
            try:
                q = input("[user] ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[dialog ended]")
                break

            if q.lower() in {"exit", "quit", "q"}:
                print("[dialog ended]")
                break

            if not q:
                continue

            # ★ diag: full diagnostics with contextService tree.
            if q.lower() in ("diag", "status"):
                print("\n[diag] Fiber state:")
                for fname, fiber in registry._fibers.items():
                    print(f"  - {fname}: state={fiber.state}")
                print(f"\n[diag] Root context services: {list(root._store.keys())}")
                print(f"\n[diag] Context tree:")
                print(root.contextService.dump())
                print()
                continue

            # ★ scope: scope info + per-scope services.
            if q.lower() == "scope":
                print(f"\n[Scope] Active scopes: {root.scope.list_scopes()}")
                for sid in root.scope.list_scopes():
                    s = root.scope.get_scope(sid)
                    print(f"  - {s.id}: services={s.list_services()}")
                    # ★ Show scope-isolated service values.
                    for svc_key in s.list_services():
                        val = s.get(svc_key)
                        print(f"      {svc_key} = {val}")
                print()
                continue

            # ★ identity: credentials + agent identities + metadata.
            if q.lower() == "identity":
                print(f"\n[Identity] Credentials: {root.identity.list_credentials()}")
                print(f"[Identity] Agent identities:")
                for aid in root.identity.list_identities():
                    ai = root.identity.get_agent_identity(aid)
                    print(f"  - {ai}")
                    print(f"    metadata: {ai.metadata}")
                print()
                continue

            # ★ invariant: rules + violations + log.
            if q.lower() == "invariant":
                inv = root.invariant
                print(f"\n[Invariant] Enabled: {inv.is_enabled}")
                print(f"[Invariant] Rules: {inv.list_rules()}")
                print(f"[Invariant] Violations: {len(inv.violations)}")
                for v in inv.violations[-5:]:
                    print(f"  - {v}")
                print()
                continue

            # ★ seam: list seams + providers + active.
            if q.lower() == "seam":
                print(f"\n[Seam] Registered seams: {seam_reg.list()}")
                for seam_name in seam_reg.list():
                    seam_def = seam_reg.get(seam_name)
                    active = seam_def.get_active_provider()
                    print(f"  - {seam_name}:")
                    print(f"    methods: {seam_def.methods}")
                    print(f"    providers: {seam_def.list_providers()}")
                    print(f"    active: {active.name if active else 'none'}")
                print(f"\n[Seam] LLM Adapter providers: {adapter.list_providers()}")
                print(f"[Seam] LLM Adapter model info: {adapter.get_model_info()}")
                print()
                continue

            # ★ ctx: context tree + find by service/fiber.
            if q.lower() == "ctx":
                print(f"\n[Ctx] Context tree:")
                print(root.contextService.dump())
                # Show find_by_service for key services.
                for svc in ['llm', 'sessions', 'tools', 'agentLoop', 'agents', 'systemPrompt']:
                    ctxs = root.contextService.find_by_service(svc)
                    fibers = [c.fiber.name if c.fiber else '?' for c in ctxs]
                    print(f"  service '{svc}' found in fibers: {fibers}")
                print()
                continue

            # ★ sessions: session summary + event projection.
            if q.lower() == "sessions":
                try:
                    sessions = root.sessions
                    summaries = sessions.get_all_summaries()
                    print(f"\n[Sessions] Total: {len(summaries)}")
                    for s in summaries:
                        print(f"  - {s['id']}: events={s['event_count']}, "
                              f"last_seq={s['last_seq']}, closed={s['is_closed']}")
                        print(f"    type_counts: {s['type_counts']}")
                    # ★ 投影查询演示。
                    for sid in sessions.list():
                        session = sessions.get(sid)
                        user_msgs = session.filter_by_type("user_message")
                        if user_msgs:
                            print(f"  [{sid}] user_message projection: "
                                  f"{len(user_msgs)} events, "
                                  f"seqs=[{user_msgs[0].seq}..{user_msgs[-1].seq}]")
                    print()
                except Exception as e:
                    print(f"  Error: {e}\n")
                continue

            # ★ tools: scope-aware listing + scope mapping.
            if q.lower() == "tools":
                try:
                    tools_svc = root.tools
                    all_tools = tools_svc.list_tools()
                    print(f"\n[Tools] All tools: {len(all_tools)}")
                    for t in all_tools:
                        print(f"  - {t.name}: {t.description}")
                    scopes = tools_svc.get_tool_scopes()
                    print(f"[Tools] Scope mapping: {scopes}")
                    # ★ scope-aware 过滤演示。
                    for scope_name in set(scopes.values()):
                        filtered = tools_svc.list_tools(scope_name)
                        print(f"  scope '{scope_name}': {[t.name for t in filtered]}")
                    print()
                except Exception as e:
                    print(f"  Error: {e}\n")
                continue

            # ★ agents: active tracking + state machine.
            if q.lower() == "agents":
                try:
                    agents_reg = root.agents
                    all_agents = agents_reg.list()
                    active = agents_reg.list_active()
                    all_status = agents_reg.get_all_status()
                    print(f"\n[Agents] Registered: {all_agents}")
                    print(f"[Agents] Active: {active}")
                    print(f"[Agents] Status: {all_status}")
                    for aid in all_agents:
                        a = agents_reg.get(aid)
                        d = a.to_dict()
                        print(f"  - {d['id']}: status={d['status']}")
                    print()
                except Exception as e:
                    print(f"  Error: {e}\n")
                continue

            # ★ prompt: system prompt block management.
            if q.lower() == "prompt":
                try:
                    sp = root.systemPrompt
                    print(f"\n[SystemPrompt] Blocks: {sp.get_block_ids()}")
                    print(f"[SystemPrompt] Disabled: {sp.get_disabled_blocks()}")
                    print(f"[SystemPrompt] Built length: {len(sp.build())} chars")
                    # ★ 演示 toggle。
                    if "debug_hints" in sp.get_block_ids():
                        if "debug_hints" in sp.get_disabled_blocks():
                            sp.enable_block("debug_hints")
                            print("  → enabled 'debug_hints'")
                        else:
                            sp.disable_block("debug_hints")
                            print("  → disabled 'debug_hints'")
                    print(f"[SystemPrompt] After toggle - Disabled: {sp.get_disabled_blocks()}")
                    print()
                except Exception as e:
                    print(f"  Error: {e}\n")
                continue

            # ★ Invariant check on user input (model-visible = logged).
            root.invariant.check({"type": "user_message", "content": q})

            # ★ Update scope session counter.
            if agent_scope:
                try:
                    counter = agent_scope.get("session_counter")
                    counter["count"] += 1
                except Exception:
                    pass

            print("-" * 60)
            try:
                response = await agent.run(q)
                print(f"[Agent] {response}")
                # ★ Invariant check on agent reply.
                root.invariant.check({"type": "assistant_message", "content": response})
            except Exception as e:
                root.logger.error(f"[Agent] Error: {e}")
                print(f"[Agent] Error: {e}")

    finally:
        # ---- ★ Graceful shutdown ----
        print("\n[shutdown] Starting graceful shutdown...")

        # ★ Invariant summary.
        inv = root.invariant
        if inv.violations:
            root.logger.warn(f"Invariant violations: {len(inv.violations)} total")
            for v in inv.violations[-3:]:
                root.logger.warn(f"  - {v}")
        else:
            root.logger.info("No invariant violations")

        # ★ Scope summary.
        if agent_scope:
            try:
                counter = agent_scope.get("session_counter")
                root.logger.info(f"Session counter (scope): {counter['count']}")
            except Exception:
                pass

        # ★ Context diagnostics snapshot.
        try:
            ctx_info = root.contextService.inspect()
            root.logger.info(f"Context services: {len(ctx_info.services)}")
            root.logger.info(f"Context tree depth: {ctx_info.children.__len__()}")
        except Exception:
            pass

        # ★ Session 投影查询 + 摘要（90%+ 新特性）。
        try:
            sessions = root.sessions
            summaries = sessions.get_all_summaries()
            root.logger.info(f"Sessions summary: {len(summaries)} sessions")
            for s in summaries:
                root.logger.info(
                    f"  {s['id']}: events={s['event_count']}, "
                    f"last_seq={s['last_seq']}, closed={s['is_closed']}, "
                    f"types={s['type_counts']}"
                )
                # ★ 投影：user_message 事件。
                session = sessions.get(s['id'])
                user_msgs = session.filter_by_type("user_message")
                if user_msgs:
                    root.logger.info(
                        f"    user_message projection: {len(user_msgs)} events, "
                        f"seqs=[{user_msgs[0].seq}..{user_msgs[-1].seq}]"
                    )
        except Exception:
            pass

        # ★ Agent 状态快照（90%+ 新特性）。
        try:
            agents_reg = root.agents
            all_status = agents_reg.get_all_status()
            root.logger.info(f"Agent status: {all_status}")
            active = agents_reg.list_active()
            root.logger.info(f"Active agents: {active}")
        except Exception:
            pass

        # ★ Scope 销毁清理（90%+ 新特性）。
        try:
            scope_svc = root.scope
            for sid in scope_svc.list_scopes():
                scope_svc.destroy_scope(sid)
                root.logger.info(f"Scope '{sid}' destroyed + cleaned")
        except Exception:
            pass

        # ★ SystemPrompt block 状态。
        try:
            sp = root.systemPrompt
            root.logger.info(
                f"SystemPrompt blocks: {sp.get_block_ids()}, "
                f"disabled: {sp.get_disabled_blocks()}"
            )
        except Exception:
            pass

        # ★ Revert root context.
        try:
            await root.revert()
            root.logger.info("Root context reverted")
        except Exception as e:
            root.logger.error(f"Error reverting root context: {e}")

        # Unload all plugins.
        for name in list(registry._fibers.keys()):
            try:
                await registry.unregister(name)
            except Exception as e:
                root.logger.error(f"Error unloading plugin '{name}': {e}")

        print("[shutdown] All plugins unloaded, exiting.")


if __name__ == "__main__":
    asyncio.run(main())
