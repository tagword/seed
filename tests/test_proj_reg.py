"""Project registry path binding."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from seed.core.proj_reg import (
    create_project,
    default_project_workspace,
    ensure_project_path,
    get_project,
    resolve_project_path,
)


@pytest.fixture
def seed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def test_ensure_project_path_assigns_default_workspace(seed_home: Path) -> None:
    row = create_project("agent-a", "demo", path="")
    pid = row["id"]
    assert row["path"] == ""

    bound = ensure_project_path("agent-a", pid)
    expected = default_project_workspace("agent-a", pid)
    assert bound == expected
    assert Path(bound).is_dir()

    refreshed = get_project("agent-a", pid)
    assert refreshed is not None
    assert refreshed["path"] == bound
    assert resolve_project_path("agent-a", pid) == bound


def test_resolve_project_path_empty_when_unset(seed_home: Path) -> None:
    row = create_project("agent-a", "no-path", path="")
    assert resolve_project_path("agent-a", row["id"]) == ""


def test_resolve_project_path_uses_registry(seed_home: Path) -> None:
    custom = seed_home / "my-repo"
    custom.mkdir()
    row = create_project("agent-a", "custom", path=str(custom))
    assert resolve_project_path("agent-a", row["id"]) == str(custom.resolve())
