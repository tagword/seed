# Changelog

## 1.0.17 (2026-08-18)

- fix(cron): 调度器启动失败不再静默 — 新增模块级 `cron_startup_error`，`start_cron_scheduler()` 失败（APScheduler 缺失 / `sched.start()` 异常）时记录，`cron_status_for_ui()` 返回 `startup_error` 字段，Web UI 可直接看到调度器未运行/失败原因（此前失败只进日志，cron 无声停摆）
- fix(cron): `save_cron_job` 保留 `max_continuations` 字段（此前 UI 保存任务会丢失该配置）
- fix(cron): `cron_status_for_ui()` 回显 `max_tool_rounds` / `max_continuations`（此前 UI 编辑已有任务时轮数/续接段数静默回退默认值，如 30→12）
- chore: 同步三处版本号（pyproject / seed/__init__ / seed/core/__init__）统一为 1.0.17

## 1.0.16 (2026-08-06)

- fix(cron): `reload_cron_scheduler()` 线程安全化 — 优先在当前线程 running loop 执行；否则通过 `run_coroutine_threadsafe` 调度到 `register_main_loop()` 注册的进程主循环；两者都不可用时安全失败，**不再先 shutdown 后失败**（此前 agent 工具在无 loop 的 worker 线程调用会把运行中的调度器打成停摆）
- fix(cron): `CronTrigger.from_crontab` 的 day-of-week 数字语义修正 — APScheduler 按 Python weekday 约定解析（0=Monday），与标准 crontab（0/7=Sunday）不一致，导致 `0 9 * * 0` 在周一而非周日触发。新增 `_convert_crontab_dow` 将第 5 字段从标准 crontab 语义转换为 APScheduler 语义（数字映射 + 范围/列表/步进展开 + 文本名保留）
- fix(cron): `save_cron_job` 保留 `max_tool_rounds` 字段（此前 UI 保存任务会丢失该配置）
- test: 新增 tests/test_cron_dow_semantics.py（14 用例）+ tests/test_cron_reload_threadsafe.py（5 用例）

## 1.0.15 (2026-08-06)

- fix(core): `$WORKSPACE` 变量改为基于进程 cwd 解析（而非 agent root），与运行时工作目录保持一致

## 1.0.14 (2026-08-03)

- fix(mcp): Streamable HTTP 会话自动重连 — 服务器重启/会话失效（连接失败、404 无 MCP-Session-Id、SSE 流中断）后自动重新 initialize 并重试一次
- test: 新增 MCP Streamable HTTP 重连测试 4 例

## 1.0.13 (2026-07-24)

- fix: `delete_stored_session` 错误查询 projects 表不存在的 sessions 列→改为查询 session_meta 表
- feat: 新增 `SEED_LLM_NO_TOPP` 环境变量，支持按 provider 关闭 top_p 参数透传
- fix: `generate` / `generate_stream` 中 top_p 改为按 env var 条件发送
- docs: 新增 ARCHITECTURE.md / CHANGELOG.md / CONTRIBUTING.md 项目文档
- chore: 添加项目级 skill（pypi-release, git）到 .codeagent/

## 1.0.12 (2026-07-??)

- fix: Windows 下子进程输出 GBK 编码解码崩溃问题
- fix: merge_llm_tail_into_full 补充消息 ts 时间戳字段
- fix(cron): 传递 max_tool_rounds 配置到 LLM 循环
- refactor: registry 存储从 JSON 迁移到 SQLite（WAL 模式）

## 1.0.11 (2026-07-??)

- fix(mcp): Streamable HTTP spec compliance — Accept header, sessionId from HTTP header, version negotiation, DELETE on close
- feat: MCP Streamable HTTP 新增 Skills 支持 — MCPSkillInfo + list_skills/call_skill
- feat: 添加 MCPStreamableHttpSession 实现 Streamable HTTP 传输

## 1.0.10 (2026-07-??)

- feat: 本地 LLM 代理透传 + CHAT_USER_ROUNDS 硬上限
- feat(context): 上下文使用量只取 API prompt_tokens，不再本地估算
- clean: 移除 agent_runtime.py 中已废弃的 75 行死代码
- fix: 空 SEED_LLM_CONTEXT_SIZE 导致 int('') ValueError

## 1.0.9 (2026-06-??)

- fix(env): 注册 seed-tools SEED_* tuples 到 env_access
- fix: provider/ 前缀模型名无法匹配已知 context window
- fix: tool_calls JSON token counting 在 content 非空时被跳过
- feat: 分块渐进压缩 — run_llm_tool_loop 拆成多段，每段后压缩

## 1.0.8 (2026-06-??)

- refactor: rename config/seed.env → config/env
- refactor: seed.models.default.txt → seed.default_model
- refactor: token_counter 导入改为 seed-model-providers

## 1.0.7 (2026-06-??)

- feat: team capability Phase 1 — AgentRegistry, call_agent/dispatch/parallel 工具
- feat: prompt_enrichment: 路径表注入 $AGENT_HOME/$WORKSPACE 等变量
- feat: skill 发现与注入 — 按 user intent 动态加载 skills
- refactor: 构建 pipeline 工作流 — fix-and-commit / new-feature / audit-project

## 1.0.6 (2026-06-??)

- refactor: 移除 plan-driven TopK scheduling，改用 keep compact + audit + full tools
- fix: MCP SSE/Streamable HTTP 双协议兼容
- feat: 会话压缩逻辑重构，ContextVar 线程安全修复

## 1.0.5 (2026-05-??)

- feat: 浏览器集成层（browser_ensure_running/connect/navigate/screenshot）
- feat: 定时任务集成层（cron_sched）
- feat: Webhook 集成层（webhook_auth, webhook_dedup）
- feat: 指令发布与版本管理（instruction_release）

## 1.0.4 (2026-05-??)

- feat: 初始公开发布 — 核心 LLM 执行循环、工具运行时、会话管理、记忆系统
- feat: CLI 入口（seed info / seed check）
- feat: 环境配置层（env_access, config_plane）
- feat: SQLite 持久化（会话/记忆/项目注册表）
- docs: README, PACKAGE_LAYOUT, INTEGRATION, ENV_REFERENCE
