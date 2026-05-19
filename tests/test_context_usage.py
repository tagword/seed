"""Context usage estimate for Web UI (distinct from billing tokens)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from seed.core.agent_runtime import build_context_usage_snapshot, estimate_context_usage


@pytest.fixture
def seed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def test_estimate_context_usage_body_bytes(seed_home: Path) -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    est = estimate_context_usage(msgs)
    body = msgs[1:]
    expected = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    assert est["body_bytes"] == expected
    assert est["compact_min_bytes"] >= 1
    assert est["message_count"] == 3


def test_build_context_usage_prefers_api_prompt_tokens(seed_home: Path) -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    snap = build_context_usage_snapshot(
        msgs,
        {"usage": {"prompt_tokens": 12000, "completion_tokens": 50, "total_tokens": 12050}},
    )
    assert snap["prompt_tokens"] == 12000
    assert snap["source"] == "api"
    assert snap["body_bytes"] > 0
