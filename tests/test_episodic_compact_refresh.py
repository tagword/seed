"""Episodic memory: snapshot on compact, apply from session metadata."""
from __future__ import annotations

from pathlib import Path

from seed.core.mem_bridge import (
    _METADATA_EPISODIC_BLOCK,
    _METADATA_EPISODIC_PROJECT_ID,
    apply_persisted_episodic_to_messages,
    episodic_needs_bootstrap,
    episodic_project_changed,
    finalize_episodic_for_llm,
    refresh_episodic_snapshot,
)


def test_episodic_project_changed_only_after_snapshot():
    assert episodic_project_changed({}, "p1") is False
    assert episodic_project_changed({_METADATA_EPISODIC_PROJECT_ID: "a"}, "b") is True
    assert episodic_project_changed({_METADATA_EPISODIC_PROJECT_ID: "a"}, "a") is False


def test_episodic_needs_bootstrap():
    assert episodic_needs_bootstrap({}) is True
    assert episodic_needs_bootstrap({_METADATA_EPISODIC_BLOCK: ""}) is False


def test_refresh_and_apply_without_compact(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEED_MEMORY_INJECT", "1")
    agent_id = "test-agent"
    mem = tmp_path / "agents" / agent_id / "memory"
    exp = mem / "experiences"
    exp.mkdir(parents=True)
    (exp / "note.md").write_text("# run\nok\n", encoding="utf-8")
    monkeypatch.setattr(
        "seed.core.mem_bridge.episodic_memory_base",
        lambda aid, pid=None: mem,
    )

    md: dict = {}
    refresh_episodic_snapshot(md, agent_id, "sess-1", None)
    assert _METADATA_EPISODIC_BLOCK in md
    assert "note.md" in md[_METADATA_EPISODIC_BLOCK]

    msgs = [{"role": "system", "content": "base config"}]
    finalize_episodic_for_llm(
        msgs,
        md,
        agent_id=agent_id,
        session_id="sess-1",
        compact_happened=False,
    )
    assert "## Seed episodic memory" in msgs[0]["content"]
    assert "base config" in msgs[0]["content"]


def test_new_session_bootstraps_then_compact_refreshes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEED_MEMORY_INJECT", "1")
    monkeypatch.setenv("SEED_MEMORY_INJECT_SESSION_ONLY", "")
    agent_id = "test-agent"
    mem = tmp_path / "agents" / agent_id / "memory"
    exp = mem / "experiences"
    exp.mkdir(parents=True)
    (exp / "shared.md").write_text("## Project\nproj-1\n\nfrom other session\n", encoding="utf-8")

    monkeypatch.setattr(
        "seed.core.mem_bridge.episodic_memory_base",
        lambda aid, pid=None: mem,
    )

    md: dict = {}
    msgs = [{"role": "system", "content": "cfg"}]
    finalize_episodic_for_llm(
        msgs,
        md,
        agent_id=agent_id,
        session_id="s",
        project_id="proj-1",
        compact_happened=False,
    )
    assert "shared.md" in md.get(_METADATA_EPISODIC_BLOCK, "")
    assert "## Seed episodic memory" in msgs[0]["content"]
    first_block = md[_METADATA_EPISODIC_BLOCK]

    (exp / "new_after_bootstrap.md").write_text("## Project\nproj-1\n\nfresh file\n", encoding="utf-8")
    finalize_episodic_for_llm(
        msgs,
        md,
        agent_id=agent_id,
        session_id="s",
        project_id="proj-1",
        compact_happened=False,
    )
    assert md[_METADATA_EPISODIC_BLOCK] == first_block
    assert "new_after_bootstrap.md" not in md[_METADATA_EPISODIC_BLOCK]

    finalize_episodic_for_llm(
        msgs,
        md,
        agent_id=agent_id,
        session_id="s",
        project_id="proj-1",
        compact_happened=True,
    )
    assert "new_after_bootstrap.md" in md.get(_METADATA_EPISODIC_BLOCK, "")


def test_apply_strips_old_block_before_reapply():
    md = {_METADATA_EPISODIC_BLOCK: "\n## Seed episodic memory (recent)\nold\n## End Seed episodic memory\n"}
    msgs = [
        {
            "role": "system",
            "content": "x\n## Seed episodic memory (recent)\nstale\n## End Seed episodic memory\n",
        }
    ]
    apply_persisted_episodic_to_messages(msgs, md)
    assert "stale" not in msgs[0]["content"]
    assert "old" in msgs[0]["content"]
