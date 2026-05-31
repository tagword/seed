"""Agent session directory layout (flat sessions/ + legacy llm_sessions/ fallback)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seed.core.llm_sess import (
    _legacy_llm_sessions_subdir,
    agent_sessions_dir,
    load_chat_session_from_disk,
    persist_chat_session,
)
from seed.core.paths import agent_id_default


@pytest.fixture
def seed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def test_agent_sessions_dir_is_flat_not_llm_sessions_subdir(seed_home) -> None:
    aid = agent_id_default()
    d = agent_sessions_dir(aid)
    assert d.name == "sessions"
    assert d.parent.name == aid
    legacy = _legacy_llm_sessions_subdir(aid)
    assert legacy.name == "llm_sessions"
    assert legacy.parent == d


def test_load_from_legacy_subdir_and_persist_migrates(seed_home) -> None:
    aid = agent_id_default()
    sid = "legacy-migrate-test"
    legacy = _legacy_llm_sessions_subdir(aid)
    legacy.mkdir(parents=True, exist_ok=True)
    from seed.core.llm_sess import _safe_session_filename

    slug = _safe_session_filename(sid)
    legacy_file = legacy / f"{slug}.json"
    legacy_file.write_text(
        json.dumps(
            {
                "id": slug,
                "name": sid,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "messages": [{"role": "user", "content": "hi"}],
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_chat_session_from_disk(sid, aid)
    assert loaded is not None
    assert loaded.messages[-1]["content"] == "hi"

    loaded.messages.append({"role": "assistant", "content": "ok"})
    persist_chat_session(loaded, aid)

    primary = agent_sessions_dir(aid) / f"{slug}.json"
    assert primary.is_file()
    assert not legacy_file.is_file()
