"""MCP stdio client integration (fake server)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from seed.integrations.mcp_client import MCPStdioSession, reset_mcp_manager
from seed.integrations.mcp_config import MCPServerConfig, save_mcp_config

_FAKE = Path(__file__).resolve().parent / "fixtures" / "mcp_fake_server.py"


@pytest.fixture(autouse=True)
def _reset_manager() -> None:
    reset_mcp_manager()
    yield
    reset_mcp_manager()


def test_mcp_stdio_list_and_call(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    save_mcp_config(
        {
            "servers": {
                "fake": {
                    "enabled": True,
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(_FAKE)],
                }
            }
        },
        base=tmp_path,
    )
    cfg = MCPServerConfig(
        server_id="fake",
        command=sys.executable,
        args=[str(_FAKE)],
    )
    sess = MCPStdioSession(cfg)
    try:
        tools = sess.list_tools()
        assert any(t.name == "echo" for t in tools)
        out = sess.call_tool("echo", {"text": "hello-mcp"})
        assert "echo:hello-mcp" in out
    finally:
        sess.close()
