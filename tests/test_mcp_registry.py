"""Dynamic MCP tool registration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from seed.core.tool_runtime import ToolRegistry
from seed.integrations.mcp_config import save_mcp_config
from seed.integrations.mcp_client import reset_mcp_manager
from seed.integrations.mcp_registry import (
    input_schema_to_parameters,
    mcp_registry_tool_name,
    register_mcp_tools_into_registry,
)

_FAKE = Path(__file__).resolve().parent / "fixtures" / "mcp_fake_server.py"


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_mcp_manager()
    yield
    reset_mcp_manager()


def test_input_schema_to_parameters() -> None:
    params = input_schema_to_parameters(
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "payload"},
                "count": {"type": "integer"},
            },
            "required": ["text"],
        }
    )
    assert params["text"]["required"] is True
    assert params["text"]["type"] == "string"
    assert params["count"]["required"] is False


def test_registry_name_sanitizes() -> None:
    name = mcp_registry_tool_name("my-server", "read/file")
    assert name.startswith("mcp__")
    assert "/" not in name


def test_register_dynamic_tools(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SEED_MCP_ENABLED", "1")
    monkeypatch.setenv("SEED_MCP_REGISTER_TOOLS", "1")
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
    reg = ToolRegistry()
    n = register_mcp_tools_into_registry(reg)
    assert n >= 1
    echo_name = mcp_registry_tool_name("fake", "echo")
    assert reg.exists(echo_name)
    out = reg.handlers[echo_name](text="dyn")
    assert "echo:dyn" in out
