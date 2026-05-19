"""Agent hooks dispatch."""

from __future__ import annotations

import sys
from pathlib import Path

from seed.integrations.hooks import dispatch_hooks
from seed.integrations.hooks_config import save_hooks_config


def test_dispatch_pre_tool_call(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SEED_HOOKS_ENABLED", "1")
    marker = tmp_path / "hook_ran.txt"
    cmd = f"{sys.executable} -c \"import os, pathlib; pathlib.Path(os.environ['SEED_HOOK_MARKER']).write_text(os.environ.get('SEED_HOOK_TOOL_NAME',''), encoding='utf-8')\""
    save_hooks_config(
        {
            "enabled": True,
            "hooks": [
                {
                    "id": "mark",
                    "event": "pre_tool_call",
                    "command": cmd,
                    "enabled": True,
                }
            ],
        },
        base=tmp_path,
    )
    monkeypatch.setenv("SEED_HOOK_MARKER", str(marker))
    dispatch_hooks("pre_tool_call", {"tool_name": "echo", "arguments": {}})
    assert marker.read_text(encoding="utf-8") == "echo"
