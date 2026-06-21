"""Full-chain trace audit events."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seed.core.agent_context import (
    clear_active_project_episodic,
    set_active_agent_id,
    set_active_llm_session,
    set_active_project_episodic,
)
from seed.core.trace_audit import append_trace_event, trace_audit_enabled


@pytest.fixture
def trace_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SEED_LLM_PROJECTION_AUDIT", "1")
    monkeypatch.setenv("SEED_LLM_TRACE_AUDIT_DIR", str(tmp_path / "trace-root"))
    set_active_agent_id("test-agent")
    set_active_llm_session("test-agent::sess-trace-1")
    set_active_project_episodic(False)
    clear_active_project_episodic()
    return tmp_path


def test_trace_enabled_by_projection_audit(trace_env: Path) -> None:
    assert trace_audit_enabled()

    path = append_trace_event(
        "llm_response",
        round=1,
        usage={"prompt_tokens": 123},
        audit_file="00000001-chat-r001.json",
    )

    assert path is not None and path.is_file()
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[-1]["type"] == "llm_response"
    assert rows[-1]["session_id"] == "sess-trace-1"
    assert rows[-1]["usage"]["prompt_tokens"] == 123
