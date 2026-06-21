"""LLM projection audit snapshots."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from seed.core.agent_context import (
    clear_active_project_episodic,
    set_active_agent_id,
    set_active_llm_session,
    set_active_project_episodic,
)
from seed.core.projection_audit import (
    append_projection_audit_usage,
    load_projection_audit_snapshot,
    list_projection_audit_index,
    persist_llm_projection_audit,
    projection_audit_enabled,
)


@pytest.fixture
def audit_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SEED_LLM_PROJECTION_AUDIT", "1")
    monkeypatch.setenv("SEED_LLM_PROJECTION_AUDIT_DIR", str(tmp_path / "audit-root"))
    set_active_agent_id("test-agent")
    set_active_llm_session("test-agent::sess-audit-1")
    set_active_project_episodic(False)
    clear_active_project_episodic()
    return tmp_path


def test_persist_and_load_roundtrip(audit_env: Path) -> None:
    assert projection_audit_enabled()
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    path = persist_llm_projection_audit(msgs, kind="chat", round_index=1)
    assert path is not None and path.is_file()

    rows = list_projection_audit_index("sess-audit-1", agent_id="test-agent")
    assert len(rows) == 1
    assert rows[0].get("seq") == 1

    snap = load_projection_audit_snapshot(
        "sess-audit-1", seq=1, agent_id="test-agent"
    )
    assert snap is not None
    assert snap["messages"] == msgs
    assert snap["kind"] == "chat"
    assert snap["round"] == 1

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["body_bytes"] > 0


def test_persist_records_tools_and_usage(audit_env: Path) -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Run a shell command",
                "parameters": {"type": "object"},
            },
        }
    ]
    path = persist_llm_projection_audit(
        msgs,
        kind="chat",
        round_index=2,
        tools=tools,
        extra={"max_tool_rounds": 16},
    )
    assert path is not None and path.is_file()

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["tools"] == tools
    assert raw["tools_count"] == 1
    assert raw["tools_bytes"] > 0
    assert raw["request_bytes"] == raw["body_bytes"] + raw["tools_bytes"]

    ok = append_projection_audit_usage(
        path,
        {"prompt_tokens": 123, "completion_tokens": 4, "total_tokens": 127},
        meta={"tool_calls": 0, "finish_reason": "stop"},
    )
    assert ok is True

    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated["usage"]["prompt_tokens"] == 123
    assert updated["post_call_meta"]["finish_reason"] == "stop"

    rows = list_projection_audit_index("sess-audit-1", agent_id="test-agent")
    assert rows[-1]["meta"]["max_tool_rounds"] == 16
    assert rows[-1]["usage"]["prompt_tokens"] == 123
    assert rows[-1]["post_call_meta"]["tool_calls"] == 0
