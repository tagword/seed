# Seed 团队能力需求文档

> 版本: v0.1 · 状态: draft  
> 目标: Seed 从「单 Agent 引擎」升级为「Agent 构建与组织框架」

---

## 需求概述

Seed 目前是单 Agent 运行时——一个 Agent 对话、调用工具、记忆，不知道其他 Agent 的存在。  
需求是将 Seed 升级为**一套从单兵到团队的组织构建框架**，让 Agent 可以：

1. **单兵作战**：当前能力，保持不动  
2. **团队作战**：一个负责人 Agent 调多个成员 Agent，用户只跟负责人聊  
3. **自我进化**：每次协作后复盘 → 优化 → 持续变强（二期）

**核心哲学**：团队不是新实体类型，而是 Agent + 不同的 tools + persona。

---

## 功能列表 & 验收标准

### Phase 1 核心能力（P0）

| # | 功能 | 验收条件 | 优先级 |
|---|------|---------|--------|
| 1 | `CallAgentTool` — 负责人调单个成员 | 负责人 Agent 调用 `call_agent("backend", "实现登录 API")` → 返回子 Agent 的输出结果 | P0 |
| 2 | `DispatchTool` — 批量派发 | `dispatch(tasks=[...], mode="sequential\|parallel\|manager")` 按指定模式执行并返回合并结果 | P0 |
| 3 | `ParallelTool` — 快捷并行 | `parallel(task_list)` = `dispatch(mode="parallel")` 的快捷别名 | P0 |
| 4 | 同进程模式 | 所有 Agent 在同一个进程中 → `call_agent` 是函数调用，零网络延迟 | P0 |
| 5 | 跨进程模式 | 子 Agent 可独立部署 → `call_agent` 自动降级为 HTTP POST /api/chat | P0 |
| 6 | 接缝透明 | 负责人 Agent 的代码不需要区分同进程还是跨进程，底层自动路由 | P0 |

### Phase 2 团队管理（P1）

| # | 功能 | 验收条件 | 优先级 |
|---|------|---------|--------|
| 7 | 团队记忆共享 | 团队内所有 Agent 可读写同一个知识库 | P1 |
| 8 | 动态成员管理 | 不重启，给团队加减成员 | P1 |
| 9 | 自动复盘 | 团队任务完成后自动生成协作分析报告 | P1 |

### Phase 3 自我进化 & 跨团队（P2）

| # | 功能 | 验收条件 | 优先级 |
|---|------|---------|--------|
| 10 | 自我迭代循环 | 复盘 → 调整 persona/tools → 下次变强 | P2 |
| 11 | 跨团队路由 | 一个团队的负责人可以派任务给另一团队 | P2 |

---

## 边界（本版本不做什么）

- ❌ 不做 `seed.team` 独立包（团队工具放 `seed-tools/seed_tools/team_tools.py`）
- ❌ 子 Agent 之间无直接交互——所有通信通过负责人 Agent 的 `call_agent`/`dispatch`/`parallel` 路由，不实现子 Agent 间自动发现/调用
- ❌ 不做 Agent 市场/注册中心
- ❌ 不做图形化团队管理界面
- ❌ 不做跨进程消息可靠性保障（先单进程跑通）
- ❌ 不做 Agent 间安全认证（先同进程，跨进程用现有 `/api/chat` 认证）

---

## 架构设计要点

### 🧱 Seed 是库，不是独立运行的服务

Seed 是 Python **库**（内核引擎），不能单独运行。需要宿主（如 CodeAgent、自定义 CLI、Web 服务器等）来：

1. 创建 `AutonomousAgent` 实例
2. 启动 TurnLoop
3. 暴露 HTTP/CLI/WebSocket 入口
4. 管理 Agent 生命周期

同进程模式下 `call_agent` 在同一个进程内通过函数调用完成，零网络开销——这完全独立于宿主的能力。

### 核心原则

```
负责人 Agent 和成员 Agent 是同一个类
   ↓
Agent(persona=..., tools=[...])
   ↓
负责人 = Agent(tools=[code_tools, call_agent, dispatch, parallel])
前端   = Agent(tools=[code_tools])
```

### 同进程 vs 跨进程

```
call_agent("backend", task)
   │
   ├─ 同进程：AgentRegistry.get("backend").run(task)  → 函数调用
   │
   └─ 跨进程：HTTP POST /api/chat {"agent_id":"backend", "task": task}
               → 从 Hub 或注册表获取 backend 的 URL
               → 发请求、等结果、返回
```

配置化判定：

```python
# seed-tools/seed_tools/team_tools.py
class call_agent:
    def __init__(self, available_agents: dict[str, Agent | str]):
        # value = Agent 对象（同进程）或 HTTP URL（跨进程）
        # 调用时自动判断
        ...
```

### 模块位置

```
seed-tools/
└── seed_tools/
    ├── team_tools.py        ← 新增：CallAgentTool, DispatchTool, ParallelTool
    ├── hub_tools.py         ← 已有：Agent 间异步通信（备胎方案）
    └── _registration.py     ← 已有：注册新工具

seed/
└── seed/
    └── core/
        └── agent_registry.py    ← 新增：Agent 注册表（管理 Agent 的注册与查找）
```

团队工具本质是 **给 LLM 调用的工具**，自然放在 `seed-tools` 包中。`hub_tools.py` 已在那里，团队工具放隔壁 `team_tools.py`，一起引入注册。

---

## 五维扫描

- [dev] 技术风险：低。CallAgentTool 本质是工具封装，不涉及 LLM 逻辑改造。跨进程复用现有 `/api/chat` 端点即可。
- [arch] 模块边界：团队工具放 `seed-tools/seed_tools/team_tools.py`，Agent 注册管理放在 `seed/seed/core/agent_registry.py`。不侵入 TurnLoopEngine 核心。
- [des] 用户流程：负责人 Agent 的对话体验不变，只是多了新工具。子 Agent 完全无感知。
- [ops] 部署：单进程开发模式零配置。跨进程需要配置 Agent URL 注册表。
- [pm] 工期预估：Phase 1（CallAgent + Dispatch + Parallel + 同进程）约 2-3 天；Phase 2（团队记忆 + 复盘）单独排期。

---

## 关键决策 & 理由

| 决策 | 选择 | 理由 |
|------|------|------|
| 团队工具放哪 | `seed-tools/seed_tools/team_tools.py` | 复用 seed-tools 现有 tool 体系和注册机制，负责人 Agent 加载工具即可用 |
| CallAgent 调用方式 | 同进程=函数调用，跨进程=HTTP | 开发阶段零延迟，生产阶段可分布式 |
| Agent 注册 | `AgentRegistry` 单例 | 简单、够用、易切换 |
| 负责人 vs 成员 | 同一 Agent 类 | 避免过早抽象，保持灵活 |

---

## 后续步骤

1. 确认需求 → 签入 v0.1
2. Phase 1: 实现 `CallAgentTool` + `AgentRegistry`（同进程）
3. Phase 1: 实现 `DispatchTool` + `ParallelTool`
4. Phase 1: 实现跨进程自动降级
5. 测试 → 集成到 CodeAgent 的负责人 Agent persona
6. Phase 2 排期
