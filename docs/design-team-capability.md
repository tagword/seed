# Design: Seed 团队能力

> 基于 `requirement-team-capability.md` · 状态：draft  
> 目标：明确 Phase 1（P0）的实现方案

---

## 技术选型

| 层 | 选型 | 理由 |
|----|------|------|
| 工具包 | `seed-tools`（已有） | 所有 Agent 工具都在此包，`hub_send` 已在此验证模式 |
| Agent 注册 | `seed.core.agent_registry`（新增） | 单例模式，工具 handler 通过 import 访问，无需依赖注入 |
| 同进程调用 | 直接调用 `AutonomousAgent.run_task()` | 零延迟，开发友好 |
| 跨进程调用 | HTTP POST 目标 Agent 的聊天端点 | 宿主（CodeAgent 等）提供 HTTP API 端点；Phase 1 只做同进程，跨进程留到 Phase 1+ |

### 🧱 Seed 是库，不是独立运行的服务

Seed 是 Python **库**（内核引擎），不能单独运行。需要宿主（如 CodeAgent、自定义 CLI、Web 服务器等）来创建 Agent 实例、启动 TurnLoop、暴露入口。同进程模式下的 `call_agent` 在同一个进程内通过函数调用完成，完全独立于宿主。

### 🔒 子 Agent 无直接交互

同进程模式下，子 Agent 之间**没有直接通信**。所有调度通过负责人 Agent 的 `call_agent` / `dispatch` / `parallel` 路由。子 Agent 不知道彼此存在——这是设计意图，相当于微服务架构中的"API 网关"模式。

---

## 核心设计

### 1. AgentRegistry — Agent 注册表

**位置**：`seed/seed/core/agent_registry.py`

```python
# 核心接口
AgentRegistry.register(agent_id: str, handle: AgentHandle)
AgentRegistry.get(agent_id: str) -> AgentHandle | None
AgentRegistry.list() -> dict[str, AgentHandle]
AgentRegistry.unregister(agent_id: str) -> bool

# AgentHandle 封装同进程/跨进程差异
AgentHandle(agent: AutonomousAgent | None = None, url: str | None = None)
handle.run_task(task: str) -> str  # 内部自动路由
```

**设计要点**：
- `AgentHandle` 屏蔽同进程/跨进程差异，`call_agent` 工具 handler 只需调 `handle.run_task()`
- 同进程：`handle._agent.run_task(task)` → 返回 `result["content"]`
- 跨进程：`handle._url` → `httpx.post(url, json={"prompt": task})` 宿主提供的聊天端点（CodeAgent 的 `/api/chat` 或自定义端点）→ 返回响应文本。**Phase 1 先不做跨进程，仅保留接口**
- 单例而非依赖注入，避免改造工具 handler 的调用链

### 2. CallAgentTool — 一对一同步调用

**位置**：`seed-tools/seed_tools/team_tools.py`

```python
# Tool 定义
call_agent_tool_def = Tool(
    name="call_agent",
    description="Call another agent with a task and wait for its result. "
                "Use when you need a specialist agent to complete a sub-task.",
    parameters={
        "agent_id": {"type": "string", "required": True, "description": "Target agent id"},
        "task": {"type": "string", "required": True, "description": "Task description for the agent"},
    },
    returns="string: agent's response",
    category="team",
)

# Handler
async def call_agent(agent_id: str, task: str) -> str:
    """
    Call another agent synchronously.
    
    - 同进程: direct AutonomousAgent.run_task() call
    - 跨进程: HTTP POST to agent's /api/chat endpoint
    
    Returns the agent's output text, or an error message if agent not found.
    """
    handle = AgentRegistry.get(agent_id)
    if not handle:
        return f"Error: agent '{agent_id}' not found in registry"
    return handle.run_task(task)
```

**设计要点**：
- 同步调用（等结果），LLM 可以立即拿到结果继续决策
- 错误处理：Agent 不存在 → 返回错误字符串，LLM 可决定下一步
- 与 `hub_send` 互补：`call_agent` = 负责人→成员（同步等结果），`hub_send` = 成员→成员（异步不等待）

### 3. DispatchTool — 批量派发

```python
# Tool 定义
dispatch_tool_def = Tool(
    name="dispatch",
    description="Dispatch multiple tasks to agents in sequential or parallel mode.",
    parameters={
        "tasks": {
            "type": "array",
            "required": True,
            "description": "List of {agent_id, task} objects",
            "items": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "task": {"type": "string"}
                }
            }
        },
        "mode": {
            "type": "string",
            "required": False,
            "description": "'sequential' (default) or 'parallel'",
            "default": "sequential"
        },
    },
    returns="string: JSON-encoded results array",
    category="team",
)

# Handler
async def dispatch(tasks: list[dict], mode: str = "sequential") -> str:
    """
    Dispatch tasks to agents.
    
    sequential mode: execute one by one, stop on first error.
    parallel mode: execute concurrently via asyncio.gather.
    
    Returns JSON array of {agent_id, task, result, error?}.
    """
    ...
```

**设计要点**：
- `mode="sequential"`：顺序执行，前一个完成才做下一个。适合有依赖链的任务。
- `mode="parallel"`：并发执行（`asyncio.gather`）。适合独立子任务。
- 结果以 JSON 数组返回，LLM 可解析每个子任务的结果。
- **Phase 1 不做 `mode="manager"`**——「拆→派→合」是 Persona 层的职责，不是工具逻辑。

### 4. ParallelTool — 快捷并行

```python
parallel_tool_def = Tool(
    name="parallel",
    description="Shortcut for dispatch(tasks, mode='parallel'). "
                "Runs multiple tasks simultaneously across different agents.",
    parameters={
        "tasks": {
            "type": "array",
            "required": True,
            "description": "List of {agent_id, task} objects",
            "items": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "task": {"type": "string"}
                }
            }
        },
    },
    returns="string: JSON-encoded results array",
    category="team",
)

# Handler: 直接委托给 dispatch(..., mode="parallel")
async def parallel(tasks: list[dict]) -> str:
    return await dispatch(tasks, mode="parallel")
```

---

## 目录结构

### seed-tools 包（新增/修改）

```
seed-tools/
└── seed_tools/
    ├── team_tools.py          ← 新增：call_agent, dispatch, parallel
    ├── hub_tools.py           ← 已有：hub_send（异步通信备胎）
    └── _registration.py       ← 修改：注册三个新工具
```

### seed 包（新增/修改）

```
seed/
└── seed/
    └── core/
        ├── agent_registry.py  ← 新增：AgentRegistry + AgentHandle
        └── __init__.py        ← 修改（可选）：导出 AgentRegistry 方便使用
```

---

## 注册流程（_registration.py 改动）

```python
# 在 _registration.py 顶部新增导入
from seed_tools.team_tools import (
    call_agent, call_agent_tool_def,
    dispatch, dispatch_tool_def,
    parallel, parallel_tool_def,
)

# 在 setup_builtin_tools() 中新增注册
registry.register(call_agent_tool_def, call_agent)
registry.register(dispatch_tool_def, dispatch)
registry.register(parallel_tool_def, parallel)
```

---

## 同进程场景的代理工作流

```
用户: "帮我做个登录页面"
  │
  ▼
负责人 Agent（LLM 感知到需要后端+前端）
  │
  ├─ call_agent("backend", "写登录API")
  │   │
  │   ▼
  │   后端 Agent → LLM + tools → 实现 API → 返回结果
  │
  ├─ call_agent("frontend", "写登录页面")
  │   │
  │   ▼
  │   前端 Agent → LLM + tools → 实现页面 → 返回结果
  │
  └─ 合并结果 → 回复用户
```

负责人 Agent 的 Persona 中描述团队分工：
```
你是一个全栈项目负责人。你有以下团队成员可供调遣：
- backend: 后端开发专家（Python/FastAPI/数据库）
- frontend: 前端开发专家（React/TypeScript/UI）

使用 call_agent / dispatch / parallel 来分配任务。
完成后给用户一个整合的最终结果。
```

---

## Diagnostic 检查清单（已执行）

### ✅ 1. 选型 PoC 验证
**结果：通过**
- `hub_send`（seed-tools 现有工具）已验证 async handler + Tool def + _registration 模式可行
- `Tool` dataclass、`ToolRegistry.register()`、`ToolExecutor.execute_async()` 全链路已验证
- AgentRegistry 仅需 dict + 单例，无第三方依赖
- `seed_tools → seed.core` 的 import 链已验证（❯ verify.py 输出：`seed_tools.setup_builtin_tools ✅`）

### ✅ 2. 依赖检查
**结果：通过**
- `seed-tools/pyproject.toml` 已依赖 `seed`（无需新增）
- `httpx` 已在 `hub_tools.py` 中使用并可用（v0.28.1 ✅）
- `asyncio` 标准库，无新增依赖
- Phase 1 仅同进程，无需 HTTP 客户端

### ✅ 3. 安全风险
**结果：通过**
- 工具参数为纯文本，无 SQL/命令拼接风险
- 同进程调用无网络暴露面
- 跨进程（Phase 1 不做）需宿主提供认证，Seed 侧不引入新攻击面

### ✅ 4. 性能预估
**结果：通过**
- 同进程 `call_agent` → `AutonomousAgent.run_task()` = 函数调用（微秒级调度）
- `parallel` 模式用 `asyncio.gather` 并发调度子任务
- 瓶颈在子 Agent 的 LLM 调用，不是工具调度本身

### ✅ 5. YAGNI 检查
**结果：通过**
- ❌ 不做 `mode="manager"`（persona 层职责，不是工具逻辑）
- ❌ 不做消息队列/可靠投递（Phase 1 够用）
- ❌ 不做跨进程（Phase 1 仅同进程，接口预留即可）

### ✅ 6. 配置外部化
**结果：通过**
- AgentRegistry 运行时注册，无需环境变量
- 跨进程 URL 作为 `AgentHandle` 参数传入，非硬编码
- Phase 1 无新增环境变量需求

### ✅ 7. 五维再检
| 维度 | 结果 |
|------|------|
| [dev] | 3 个文件改动（`team_tools.py` + `agent_registry.py` + `_registration.py`），代码量 < 200 行 |
| [arch] | 模块边界清晰：工具在 seed-tools，注册表在 seed core，不侵入 TurnLoopEngine 核心循环  |
| [des] | LLM 调用工具后的结果统一为文本字符串，LLM 可直接理解 |
| [ops] | 无新增配置/部署步骤 |
| [pm] | Phase 1 约 1-2 天（同进程 `call_agent` + `dispatch` + `parallel`） |

### 结论
**✅ 全部通过，可以进入 Phase 3（任务拆解与执行）**

---

## 关键决策 & 理由

| 决策 | 选择 | 理由 |
|------|------|------|
| Agent 注册方式 | 单例 `AgentRegistry` | 工具 handler 无依赖注入机制，单例最简洁 |
| 同进程/跨进程封装 | `AgentHandle.run_task()` 统一接口 | 调用方（工具 handler）不需要知道部署模式 |
| Dispatch `mode` 参数 | 只做 sequential + parallel | manager 模式是 Persona 层的职责，不是工具逻辑 |
| ParallelTool | 独立工具 + dispatch 的快捷别名 | LLM 调用更自然（"并行执行"比"dispatch(mode=parallel)"更直接） |
| 工具名小写 | `call_agent` 而非 `CallAgent` | 与 `hub_send`、`git` 等现有工具风格一致 |
| 结果格式 | 纯文本字符串 | 兼容 LLM context，不引入结构化类型 |
| 错误处理 | 返回错误字符串而非抛出异常 | LLM 看到错误可自主决定重试/改方案 |
