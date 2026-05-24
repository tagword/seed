"""Bash cwd allowlist includes active project workspace."""

from __future__ import annotations

from pathlib import Path

from seed.core.agent_context import (
    clear_active_project_workspace,
    set_active_project_workspace,
)
from seed.integrations.safety import SafetyConfig, check_bash_command


def test_bash_allowed_dirs_includes_active_workspace(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "codeagent-home"
    root.mkdir()
    ws = tmp_path / "external-project"
    ws.mkdir()
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(root))

    set_active_project_workspace(str(ws))
    try:
        allowed = SafetyConfig.bash_allowed_dirs()
        resolved_allowed = {str(Path(d).resolve()) for d in allowed}
        assert str(ws.resolve()) in resolved_allowed
        assert check_bash_command("ls -la", cwd=str(ws)) is None
    finally:
        clear_active_project_workspace()


def test_bash_allowed_dirs_skips_duplicate_when_under_project_root(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "codeagent-home"
    root.mkdir()
    ws = root / "agents" / "default" / "workspace"
    ws.mkdir(parents=True)
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(root))

    set_active_project_workspace(str(ws))
    try:
        allowed = SafetyConfig.bash_allowed_dirs()
        assert allowed.count(str(ws.resolve())) == 0
        assert check_bash_command("pwd", cwd=str(ws)) is None
    finally:
        clear_active_project_workspace()
