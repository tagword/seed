# Session 存储（重构后）

## 路径

| 范围 | 目录 |
|------|------|
| 无项目 | `agents/<agent_id>/sessions/<session-id>.json` |
| 有项目 | `agents/<agent_id>/projects-data/<project-id>/sessions/<id>.json` |
| 子目录 | `archived/`、`attachments/`、`_artifacts/`、`_user_inputs/` |

环境变量覆盖根目录：`SEED_AGENT_SESSIONS_DIR`（优先）、`SEED_LLM_SESSIONS_DIR`。

## 已移除

- `sessions/llm_sessions/` 双层目录（请迁移）
- JSONL `transcript_store` / `_transcript/`
- `GET /api/ui/session/transcript`
- `llm_sessions_dir()`、`list_stored_llm_sessions_meta()`、`list_stored_llm_session_ids()`、`delete_stored_llm_session()` 等旧 API 名（现分别为 `list_stored_session_ids`、`delete_stored_session`）

## 内核 API

- `agent_sessions_dir()`
- `list_stored_sessions_meta()`
- `load_or_create_chat_session` / `persist_chat_session`
- `load_session_messages` / `save_session_messages`
- `migrate_legacy_agent_sessions(agent_id, dry_run=False)`

## Web UI

- `GET /api/ui/session/history`
- `CODEAGENT_WEBUI_SESSION_HISTORY_*`

## LLM 请求全文审计（可选）

设置 `SEED_LLM_PROJECTION_AUDIT=1` 后，每次调用主模型（及 compact 摘要模型）前，会在会话目录旁写入**当轮 exact `messages` 列表**：

```text
agents/<agent>/sessions/_audit/<session-slug>/00000001-chat-r001.json
agents/<agent>/sessions/_audit/<session-slug>/index.jsonl
```

查看：

```bash
codeagent session audit-list <session_id>
codeagent session audit-show <session_id> --seq 1
```

这与 `Session.messages` 互补：Session 保留完整对话；审计文件保留**模型当轮实际看到的 payload**（含 trim、compact 注入 system、skills、episodic 之后）。

Episodic 块存在 Session `metadata`（`episodic_block`、`episodic_project_id`）：新会话首轮扫一次项目/agent 级 `memory/experiences`，compact 时再刷新；其余轮次只贴快照，不每轮扫盘。

## 迁移

```bash
# 预览
codeagent session migrate --dry-run

# 执行（默认 agent）
codeagent session migrate

# 指定 agent
codeagent session migrate --agent-id myagent
```

将 `agents/<id>/sessions/llm_sessions/` 下 JSON 与子目录并入 `sessions/`，删除 `_transcript/`，并移除空的 legacy 目录。
