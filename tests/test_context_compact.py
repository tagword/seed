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
    # 确保 _summarizer_llm 回退到 mock LLM，而非读全局 env 创建真 executor
    monkeypatch.delenv("SEED_CONTEXT_COMPACT_SUMMARIZER_BASEURL", raising=False)
    monkeypatch.delenv("SEED_CONTEXT_COMPACT_SUMMARIZER_MODEL", raising=False)
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

    api = build_api_projection_messages(full)
    result = maybe_compact_context_messages(api, _llm("new summary"), api_prompt_tokens=5000)
    assert result is not None
    assert result["boundary_source_idx"] == 4

    assert persist_compact_summary(full, result) is True
    # summary 包含时间戳前缀 + 旧摘要链式追加
    assert "old summary\n\n[continued]\n\nnew summary" in full[4]["_compact_summary"]
    assert full[2]["_compact_summary"] == "old summary"


def test_bytes_forced_compact_with_two_user_rounds(seed_home: Path) -> None:
    full = [{"role": "system", "content": "sys"}]
    for i in range(2):
        full.append({"role": "user", "content": f"user-{i}"})
        full.append({"role": "assistant", "content": "x" * 1200})
        full.append({"role": "tool", "name": "bash", "content": "y" * 1200})

    api = build_api_projection_messages(full)
    result = maybe_compact_context_messages(api, _llm("forced summary"), api_prompt_tokens=5000)
    assert result is not None
    assert result["boundary_source_idx"] == 3
    assert persist_compact_summary(full, result) is True
    assert "forced summary" in full[3]["_compact_summary"]


def test_auto_continue_nudge_counted_as_user_round_in_compress(seed_home: Path) -> None:
    """Nudge 计入轮次，让分块渐进压缩能按 chunk 边界切分。"""
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

    api = build_api_projection_messages(full)
    result = maybe_compact_context_messages(api, _llm("summary"), api_prompt_tokens=5000)
    assert result is not None
    # user_idx（含 nudge）= [0,2,4,6], keep_rounds=3 → cut=user_idx[1]=2
    # boundary = body[1] = full[2] (assistant a1), _source_idx=2
    assert result["boundary_source_idx"] == 4
    # 注解：keep_rounds=2，user_idx（含 nudge）=[0,2,4,6]
    # effective_keep=2, cut=user_idx[2]=4, boundary=body[3]→_source_idx=4
    # 结果与旧行为（不计 nudge）相同，因为 4 个轮次 cut 后到第 2 个 real user。


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


def _multi_round_session(n_rounds: int) -> list[dict]:
    full = [{"role": "system", "content": "sys"}]
    for i in range(n_rounds):
        full.append({"role": "user", "content": f"u{i}"})
        full.append({"role": "assistant", "content": f"a{i}"})
    return full


def test_chat_user_rounds_disabled_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("SEED_CHAT_USER_ROUNDS", raising=False)
    full = _multi_round_session(10)
    api = build_api_projection_messages(full)
    # No trimming: system + 10 user + 10 assistant
    assert sum(1 for m in api if m.get("role") == "user") == 10


def test_chat_user_rounds_trims_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SEED_CHAT_USER_ROUNDS", "3")
    full = _multi_round_session(10)
    api = build_api_projection_messages(full)
    # system preserved, only last 3 user rounds kept
    assert api[0]["role"] == "system"
    assert sum(1 for m in api if m.get("role") == "user") == 3
    user_contents = [m["content"] for m in api if m.get("role") == "user"]
    assert user_contents == ["u7", "u8", "u9"]
