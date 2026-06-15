# Task: Seed 团队能力 Phase 1

> 基于 `requirement-team-capability.md` + `design-team-capability.md`

---

## Wave 1: AgentRegistry — Agent 注册表（seed core 层）

**依赖**: 无  
**验收条件**: `AgentRegistry` 单例可注册/获取/列出/注销 Agent；`AgentHandle` 封装同进程 `run_task` 调用，协方差透明

任务:
- [W1.1] 创建 `seed/seed/core/agent_registry.py`：`AgentRegistry`（单例） + `AgentHandle`（封装同进程函数调用）
- [W1.2] 更新 `seed/seed/core/__init__.py` 导出 `AgentRegistry`、`AgentHandle`

## Wave 2: 团队工具（seed-tools 层）

**依赖**: Wave 1  
**验收条件**: `call_agent`、`dispatch`（sequential/parallel）、`parallel` 三个工具注册生效，handler 能从 AgentRegistry 获取 handle 并调用

任务:
- [W2.1] 创建 `seed-tools/seed_tools/team.py`：三个工具定义 + handler
- [W2.2] 更新 `seed-tools/seed_tools/_registration.py`：导入并注册三个新工具

## Wave 3: 集成验证

**依赖**: Wave 2  
**验收条件**: 编写一个完整的集成测试——创建负责人 Agent + 两个成员 Agent，验证 call_agent、dispatch sequential、dispatch parallel 都正常工作

任务:
- [W3.1] 编写集成测试 `test_team_basic.py`：注册三个 Agent，验证 call_agent 返回成员结果
- [W3.2] 验证 dispatch sequential 和 parallel 模式的正确性

## Wave 4: 交付

**依赖**: Wave 3  
**验收条件**: code_check 通过、todo 清零、git commit

任务:
- [W4.1] code_check + 自审
- [W4.2] git commit + 清理
