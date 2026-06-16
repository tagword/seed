"""Context usage snapshot for Web UI (API prompt_tokens only)."""
from __future__ import annotations

from pathlib import Path

import pytest

from seed.core.agent_runtime import (
    apply_context_usage_metadata,
    build_context_usage_from_run,
    build_context_usage_snapshot,
    estimate_context_usage,
)


@pytest.fixture
def seed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def test_estimate_context_usage_without_api(seed_home: Path) -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    est = estimate_context_usage(msgs)
    assert est["prompt_tokens"] == 0
    assert est["compact_min_tokens"] >= 1
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
    assert snap.get("completion_tokens") == 50


def test_build_context_usage_from_run_uses_peak(seed_home: Path) -> None:
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    snap = build_context_usage_from_run(
        msgs,
        loop_meta={"peak_prompt_tokens": 312000},
        last_meta={"usage": {"prompt_tokens": 280000, "completion_tokens": 10}},
    )
    assert snap["prompt_tokens"] == 312000
    assert snap["peak_prompt_tokens"] == 312000


def test_apply_context_usage_metadata_stores_peak(seed_home: Path) -> None:
    md: dict = {}
    apply_context_usage_metadata(
        md,
        {"prompt_tokens": 312000, "peak_prompt_tokens": 312000, "context_limit": 128000, "message_count": 2},
        updated_at="t",
    )
    assert md["context_usage"]["prompt_tokens"] == 312000
    assert md["context_usage"]["peak_prompt_tokens"] == 312000
    assert "estimated_tokens" not in md["context_usage"]
