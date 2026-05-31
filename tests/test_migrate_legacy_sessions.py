"""migrate_legacy_agent_sessions moves llm_sessions/ layout to flat sessions/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from seed.core.llm_sess import (
    _legacy_llm_sessions_subdir,
    agent_sessions_dir,
    load_chat_session_from_disk,
    migrate_legacy_agent_sessions,
)
from seed.core.paths import agent_id_default


@pytest.fixture
def seed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def test_migrate_moves_json_and_removes_legacy_dir(seed_home) -> None:
    aid = agent_id_default()
    legacy = _legacy_llm_sessions_subdir(aid)
    primary = agent_sessions_dir(aid)
    legacy.mkdir(parents=True, exist_ok=True)
    sid = "migrate-all-test"
    legacy_file = legacy / f"{sid}.json"
    legacy_file.write_text(
        json.dumps(
            {
                "id": sid,
                "name": sid,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "messages": [{"role": "user", "content": "legacy"}],
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    (legacy / "_transcript").mkdir()
    (legacy / "_transcript" / "x.jsonl").write_text("{}\n", encoding="utf-8")

    stats = migrate_legacy_agent_sessions(aid, dry_run=False)
    assert stats["moved_json"] == 1
    assert stats["legacy_removed"] is True
    assert not legacy.is_dir()
    assert (primary / f"{sid}.json").is_file()
    assert not (primary / "_transcript").is_dir()

    loaded = load_chat_session_from_disk(sid, aid)
    assert loaded is not None
    assert loaded.messages[-1]["content"] == "legacy"
