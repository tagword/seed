"""Tests for tool output capping before LLM context."""
from __future__ import annotations

from pathlib import Path

import pytest

from seed.core.tool_output_cap import cap_tool_output_for_context, tool_output_max_chars


@pytest.fixture
def seed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def test_tool_output_max_chars_default(seed_home: Path) -> None:
    assert tool_output_max_chars() == 51200


def test_cap_passes_through_small_output(seed_home: Path) -> None:
    text = "hello" * 10
    assert cap_tool_output_for_context(text, tool_name="grep_tool") == text


def test_cap_truncates_large_output(seed_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEED_TOOL_OUTPUT_MAX_CHARS", "1000")
    monkeypatch.setenv("SEED_TOOL_ARTIFACTS", "0")
    text = "x" * 5000
    capped = cap_tool_output_for_context(text, tool_name="bash")
    assert len(capped) <= 1000
    assert "bash" in capped
    assert "5000 chars" in capped


def test_cap_writes_artifact_for_large_output(
    seed_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEED_TOOL_OUTPUT_MAX_CHARS", "2000")
    monkeypatch.setenv("SEED_TOOL_ARTIFACTS", "1")
    monkeypatch.setenv("SEED_TOOL_ARTIFACTS_MIN_CHARS", "100")
    monkeypatch.setenv("SEED_AGENT_ID", "default")

    from seed.core.agent_context import set_active_llm_session

    set_active_llm_session("default::test-session-cap")

    text = "line\n" * 8000
    capped = cap_tool_output_for_context(text, tool_name="grep_tool")
    assert len(capped) <= 2000
    assert "artifact_read" in capped
    assert "完整输出：" in capped

    artifact_root = seed_home / "agents" / "default" / "sessions" / "_artifacts" / "test-session-cap"
    assert artifact_root.is_dir()
    files = list(artifact_root.glob("*.txt"))
    assert files
    assert files[0].read_text(encoding="utf-8") == text
