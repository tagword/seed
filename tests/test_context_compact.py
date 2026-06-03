"""Tests for chained compact persistence and projection mapping."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from seed.core.agent_runtime import (
    build_api_projection_messages,
    maybe_compact_context_messages,
    merge_llm_tail_into_full,
    persist_compact_summary,
    strip_ephemeral_message_fields,
)


@pytest.fixture
def seed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SEED_CONTEXT_COMPACT", "1")
    monkeypatch.setenv("SEED_CONTEXT_COMPACT_MIN_TOKENS", "250")
    monkeypatch.setenv("SEED_CONTEXT_COMPACT_KEEP_USER_ROUNDS", "2")
    return tmp_path


def _llm(summary: str = "compressed summary") -> MagicMock:
    llm = MagicMock()
    llm.generate.return_value = (summary, {})
    llm.api_key = "test"
    llm.auth_scheme = "Bearer"
    return llm


def test_persist_compact_summary_uses_boundary_source_idx(seed_home: Path) -> None:
    full = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]
    full[2]["_compact_summary"] = "old summary"
    for i in (1, 3, 5, 6):
        full[i]["content"] = full[i]["content"] + ("x" * 800)

    api = build_api_projection_messages(full, max_user_rounds=12)
    result = maybe_compact_context_messages(api, _llm("new summary"))
    assert result is not None
    assert result["boundary_source_idx"] == 4

    assert persist_compact_summary(full, result) is True
    assert full[4]["_compact_summary"] == "new summary"
    assert full[2]["_compact_summary"] == "old summary"


def test_bytes_forced_compact_with_two_user_rounds(seed_home: Path) -> None:
    full = [{"role": "system", "content": "sys"}]
    for i in range(2):
        full.append({"role": "user", "content": f"user-{i}"})
        full.append({"role": "assistant", "content": "x" * 1200})
        full.append({"role": "tool", "name": "bash_exec", "content": "y" * 1200})

    api = build_api_projection_messages(full, max_user_rounds=12)
    result = maybe_compact_context_messages(api, _llm("forced summary"))
    assert result is not None
    assert result["boundary_source_idx"] == 2
    assert persist_compact_summary(full, result) is True
    assert "forced summary" in full[2]["_compact_summary"]


def test_auto_continue_nudge_not_counted_as_user_round(seed_home: Path) -> None:
    full = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "real-1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "请继续完成未完成事项；继续推进"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "real-2"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "real-3"},
        {"role": "assistant", "content": "a4"},
    ]
    for i in range(1, len(full)):
        if full[i]["role"] in {"assistant", "tool", "user"} and full[i]["content"].startswith("real"):
            full[i]["content"] = full[i]["content"] + ("z" * 800)

    api = build_api_projection_messages(full, max_user_rounds=12)
    result = maybe_compact_context_messages(api, _llm("summary"))
    assert result is not None
    assert result["boundary_source_idx"] == 2


def test_merge_llm_tail_skips_auto_continue_nudge(seed_home: Path) -> None:
    full = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    api = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "请继续完成未完成事项", "_auto_continue_nudge": True},
        {"role": "assistant", "content": "done"},
    ]
    tail = merge_llm_tail_into_full(full, api, 2)
    assert len(tail) == 2
    assert full[-1]["content"] == "done"
    assert all(m.get("role") != "user" or "请继续" not in m.get("content", "") for m in full)


def test_strip_ephemeral_message_fields(seed_home: Path) -> None:
    msgs = [{"role": "user", "content": "hi", "_source_idx": 3, "_auto_continue_nudge": True}]
    strip_ephemeral_message_fields(msgs)
    assert "_source_idx" not in msgs[0]
    assert "_auto_continue_nudge" not in msgs[0]
