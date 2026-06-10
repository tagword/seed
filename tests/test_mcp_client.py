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


def test_probe_mcp_server_config(tmp_path, monkeypatch) -> None:
    from seed.integrations.mcp_client import probe_mcp_server_config

    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    cfg = MCPServerConfig(
        server_id="fake",
        command=sys.executable,
        args=[str(_FAKE)],
    )
    out = probe_mcp_server_config(cfg)
    assert out.get("ok") is True
    assert "echo" in (out.get("tools") or [])

import threading
import time

import httpx
import pytest

from seed.integrations.mcp_client import MCPSseSession, reset_mcp_manager
from seed.integrations.mcp_config import MCPServerConfig
from tests.fixtures.mcp_fake_sse_server import run_server


@pytest.fixture(autouse=True)
def _reset_manager() -> None:
    reset_mcp_manager()
    yield
    reset_mcp_manager()


@pytest.fixture
def sse_server():
    """Start an SSE test server and yield its URL."""
    server = run_server()
    port = server.server_address[1]
    sse_url = f"http://127.0.0.1:{port}/sse"
    yield sse_url, server
    server._shutdown = True
    server.shutdown()


def test_mcp_sse_list_and_call(sse_server):
    """Test MCPSseSession: list_tools and call_tool via SSE transport."""
    sse_url, server = sse_server
    cfg = MCPServerConfig(
        server_id="fake-sse",
        transport="sse",
        url=sse_url,
    )
    sess = MCPSseSession(cfg)
    try:
        tools = sess.list_tools()
        tool_names = [t.name for t in tools]
        assert "echo" in tool_names, f"Expected 'echo' in tools, got {tool_names}"
        assert "add" in tool_names, f"Expected 'add' in tools, got {tool_names}"

        out = sess.call_tool("echo", {"text": "hello-sse"})
        assert "echo:hello-sse" in out, f"Expected echo:hello-sse, got {out!r}"

        out2 = sess.call_tool("add", {"a": 3, "b": 4})
        assert "7.0" in out2, f"Expected 7.0, got {out2!r}"
    finally:
        sess.close()


def test_mcp_sse_client_manager(sse_server, tmp_path, monkeypatch):
    """Test MCPSseSession through MCPClientManager."""
    sse_url, server = sse_server
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))

    from seed.integrations.mcp_client import get_mcp_manager, list_server_configs
    from seed.integrations.mcp_config import save_mcp_config

    save_mcp_config(
        {
            "servers": {
                "fake-sse": {
                    "enabled": True,
                    "transport": "sse",
                    "url": sse_url,
                }
            }
        },
        base=tmp_path,
    )

    # Verify config parsing
    configs = list_server_configs(base=tmp_path)
    assert len(configs) == 1
    assert configs[0].transport == "sse"
    assert configs[0].url == sse_url

    # Test through manager
    mgr = get_mcp_manager()
    sess = mgr.get_session("fake-sse")
    tools = sess.list_tools()
    assert any(t.name == "echo" for t in tools)

    out = sess.call_tool("echo", {"text": "via-manager"})
    assert "echo:via-manager" in out

    # Test server status
    status = mgr.list_servers_status(probe=True)
    sse_status = [s for s in status if s["id"] == "fake-sse"]
    assert len(sse_status) == 1
    assert sse_status[0]["connected"] is True
    assert sse_status[0]["transport"] == "sse"
    assert sse_status[0]["url"] == sse_url
    assert sse_status[0]["tool_count"] >= 2
