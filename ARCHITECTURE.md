# Architecture

## 项目定位

**seed** 是 Agent 引擎内核（Kernel Layer），提供 LLM 执行循环、工具运行时、会话管理、记忆系统等核心能力。上层产品（如 CodeAgent）在此之上构建人格层与 UI。

```
┌──────────────────────────────────────┐
│           CodeAgent 等宿主            │  ← 人格层 / UI 层
├──────────────────────────────────────┤
│              seed                     │  ← 引擎层（本包）
│    core · integrations · CLI         │
├──────────────────────────────────────┤
│       seed-model-providers           │  ← 模型提供商目录
├──────────────────────────────────────┤
│       seed-tools                     │  ← 内置工具层（可选依赖）
└──────────────────────────────────────┘
```

依赖方向：seed **不依赖**任何宿主包；seed 依赖 `seed-model-providers`，可选依赖 `seed-tools`。

## 目录结构

```
seed/
│
├── seed/
│   ├── __init__.py             # 公开 API 导出
│   │
│   ├── core/                   # ★ 核心运行时
│   │   ├── agent_runtime.py    # Agent 主循环（run_llm_tool_loop）
│   │   ├── llm_exec.py         # LLM 调用执行器
│   │   ├── turn_loop.py        # 多轮工具调用循环
│   │   ├── tool_runtime.py     # 工具注册表与执行器（ToolRegistry）
│   │   ├── engine.py           # 引擎入口（session run / stream）
│   │   ├── agent_context.py    # Agent 上下文（会话级状态）
│   │   ├── config_plane.py     # 配置路径解析（AGENT_HOME 等）
│   │   ├── env_access.py       # 环境变量统一访问（SEED_*）
│   │   ├── paths.py            # 路径工具函数
│   │   ├── models.py           # 类型定义（Message, Session 等）
│   │   ├── model_providers.py  # [已废弃] 迁移至 seed-model-providers
│   │   │
│   │   ├── sess_store.py       # 会话存储（SQLite）
│   │   ├── persistence.py      # 持久化基类
│   │   ├── _session_cache.py   # 会话缓存层
│   │   │
│   │   ├── mem_sys.py          # 记忆系统
│   │   ├── mem_bridge.py       # 记忆桥接器
│   │   │
│   │   ├── proj_reg.py         # 项目注册表
│   │   ├── proj_todos.py       # 项目待办管理
│   │   │
│   │   ├── llm_presets.py      # LLM 预设管理
│   │   ├── usage_accumulator.py# token 用量累计
│   │   ├── tool_output_cap.py  # 工具输出截断
│   │   ├── chat_events.py      # 聊天事件
│   │   ├── routing.py          # 路由 / 调度
│   │   ├── trace_audit.py      # 审计跟踪
│   │   ├── media_store.py      # 媒体文件存储
│   │   ├── projection_audit.py # 投影审计
│   │   ├── execution.py        # 执行上下文
│   │   ├── commands.py         # 命令定义
│   │   └── agent_registry.py   # Agent 注册与发现
│   │
│   ├── integrations/           # ★ 集成层（可选能力）
│   │   ├── mcp_client.py       # MCP 客户端（SSE + Streamable HTTP）
│   │   ├── browser/            # 浏览器自动化
│   │   ├── cron_sched/         # 定时任务调度
│   │   ├── webhook_auth.py     # Webhook 鉴权
│   │   ├── webhook_dedup.py    # Webhook 去重
│   │   ├── instruction_release.py # 指令发布与版本管理
│   │   ├── session_title.py    # 会话标题生成
│   │   ├── message_api.py      # 消息 API
│   │   ├── task_runner.py      # 任务执行器
│   │   └── prompt_task.py      # Prompt 任务
│   │
│   ├── cli.py                  # CLI 入口（seed info / seed check）
│   └── models.py               # 业务模型定义
│
├── docs/
│   ├── PACKAGE_LAYOUT.md       # 包架构说明
│   ├── INTEGRATION.md          # 宿主集成指引
│   ├── ENV_REFERENCE.md        # 环境变量参考
│   └── ...
│
├── tests/                      # 测试
├── CHANGELOG.md
├── CONTRIBUTING.md
└── pyproject.toml
```

## 核心模块职责

### core/ — 内核

| 模块 | 职责 |
|------|------|
| **agent_runtime** | Agent 主循环：接收消息 → LLM 调用 → 工具执行 → 生成回复 |
| **llm_exec** | LLM API 调用封装（流式/非流式），支持多提供商路由 |
| **turn_loop** | 多轮工具调用编排，含压缩、审计、终止判断 |
| **tool_runtime** | 工具注册（ToolRegistry）、查找、调用、结果处理 |
| **engine** | 外部统一入口：创建/恢复会话，触发 run，流式输出 |
| **sess_store** | 会话的 CRUD + 历史消息分页查询，SQLite 存储 |
| **mem_sys + mem_bridge** | 长期记忆存取，经验检索与沉淀 |
| **config_plane** | 基于 AGENT_HOME 的路径解析层，所有路径配置统一入口 |
| **env_access** | 环境变量安全读取，统一前缀 + 默认值管理 |
| **proj_reg + proj_todos** | 多项目管理与会话级待办 |

### integrations/ — 集成层

所有集成都设计为**可选的**，按需加载：

| 模块 | 职责 |
|------|------|
| **mcp_client** | MCP 协议客户端（SSE + Streamable HTTP），工具发现与调用 |
| **cron_sched** | APScheduler 定时任务调度，从 cron JSON 配置加载 |
| **browser** | 浏览器远程调试（CDP），页面导航与截图 |
| **webhook_auth** | Webhook 签名验证与鉴权 |
| **instruction_release** | 指令包的版本管理与发布，支持增量更新 |

## 关键设计决策

| 决策 | 理由 |
|------|------|
| **core 与 integrations 分离** | 内核可独立运行，集成层按需加载 |
| **SQLite 作为主存储** | 零配置、单文件、事务支持，适合单机 Agent 场景 |
| **环境变量统一前缀（SEED_\*）** | 避免与宿主环境变量冲突，所有配置可溯源 |
| **ToolRegistry 模式** | 工具注册与发现解耦，宿主可注入自定义工具 |
| **渐进压缩策略** | 长对话分段压缩，避免单次压缩丢失上下文 |
