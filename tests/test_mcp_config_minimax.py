"""MiniMax Token Plan MCP config helpers."""

from __future__ import annotations

from pathlib import Path

from seed.integrations.mcp_config import (
    MINIMAX_MCP_SERVER_ID,
    build_minimax_token_plan_mcp_server,
    merge_minimax_mcp_server,
)


def test_build_minimax_token_plan_mcp_server(tmp_path: Path) -> None:
    row = build_minimax_token_plan_mcp_server(
        api_key="tp-key",
        base=tmp_path,
        uvx_command="/usr/local/bin/uvx",
    )
    assert row["command"] == "/usr/local/bin/uvx"
    assert row["args"] == ["minimax-coding-plan-mcp", "-y"]
    assert row["env"]["MINIMAX_API_KEY"] == "tp-key"
    assert row["env"]["MINIMAX_API_HOST"] == "https://api.minimaxi.com"
    assert (tmp_path / "mcp-minimax-out").is_dir()


def test_merge_minimax_mcp_server() -> None:
    out = merge_minimax_mcp_server({}, api_key="k")
    assert MINIMAX_MCP_SERVER_ID in out
    assert out[MINIMAX_MCP_SERVER_ID]["enabled"] is True


def test_server_config_from_dict() -> None:
    from seed.integrations.mcp_config import server_config_from_dict

    cfg = server_config_from_dict(
        "MyServer",
        {
            "enabled": True,
            "command": "uvx",
            "args": ["pkg", "-y"],
            "env": {"API_KEY": "x"},
            "cwd": "/tmp",
        },
    )
    assert cfg.server_id == "MyServer"
    assert cfg.command == "uvx"
    assert cfg.args == ["pkg", "-y"]
    assert cfg.env["API_KEY"] == "x"
    assert cfg.cwd == "/tmp"
