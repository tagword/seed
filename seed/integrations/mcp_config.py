"""Load MCP server definitions from ``<project>/config/mcp.json``."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from seed.core.config_plane import config_dir, project_root

logger = logging.getLogger(__name__)

MCP_CONFIG_FILENAME = "mcp.json"

_DEFAULT_CONFIG: dict[str, Any] = {
    "_readme": (
        "MCP servers for Seed/CodeAgent. Each entry under servers: id -> {enabled, transport, command, args, url, env, cwd}. "
        "transport: stdio (local subprocess) or sse (HTTP SSE remote). "
        "Tools: mcp_servers, mcp_list_tools, mcp_call."
    ),
    "servers": {},
}


@dataclass
class MCPServerConfig:
    server_id: str
    enabled: bool = True
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""  # SSE endpoint URL (required when transport == "sse")
    headers: dict[str, str] = field(default_factory=dict)  # custom HTTP headers (SSE)
    env: dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None

    def argv(self) -> list[str]:
        if not self.command.strip():
            raise ValueError(f"MCP server {self.server_id!r}: command is required")
        return [self.command.strip(), *[str(a) for a in self.args]]


def mcp_config_path(base: Optional[Path] = None) -> Path:
    root = config_dir() if base is None else (base.resolve() / "config")
    return root / MCP_CONFIG_FILENAME


def load_mcp_config(base: Optional[Path] = None) -> dict[str, Any]:
    path = mcp_config_path(base)
    if not path.is_file():
        return dict(_DEFAULT_CONFIG)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("ignore bad %s: %s", path, e)
        return dict(_DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        return dict(_DEFAULT_CONFIG)
    out = dict(_DEFAULT_CONFIG)
    if isinstance(raw.get("servers"), dict):
        out["servers"] = raw["servers"]
    return out


def save_mcp_config(data: dict[str, Any], base: Optional[Path] = None) -> Path:
    path = mcp_config_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    servers = data.get("servers")
    if not isinstance(servers, dict):
        raise ValueError("servers must be an object")
    payload = {"_readme": _DEFAULT_CONFIG["_readme"], "servers": servers}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def list_server_configs(base: Optional[Path] = None) -> list[MCPServerConfig]:
    raw = load_mcp_config(base)
    servers = raw.get("servers") or {}
    if not isinstance(servers, dict):
        return []
    out: list[MCPServerConfig] = []
    for sid, entry in sorted(servers.items()):
        if not isinstance(entry, dict):
            continue
        sid_s = str(sid).strip()
        if not sid_s:
            continue
        args = entry.get("args")
        url = entry.get("url")
        env = entry.get("env")
        out.append(
            MCPServerConfig(
                server_id=sid_s,
                enabled=bool(entry.get("enabled", True)),
                transport=str(entry.get("transport") or "stdio").strip().lower(),
                command=str(entry.get("command") or "").strip(),
                args=[str(x) for x in args] if isinstance(args, list) else [],
                url=str(url).strip() if url else "",
                headers={str(k): str(v) for k, v in entry.get("headers", {}).items()} if isinstance(entry.get("headers"), dict) else {},
                env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
                cwd=str(entry["cwd"]).strip() if entry.get("cwd") else None,
            )
        )
    return out


def get_server_config(server_id: str, base: Optional[Path] = None) -> Optional[MCPServerConfig]:
    sid = (server_id or "").strip()
    for cfg in list_server_configs(base):
        if cfg.server_id == sid:
            return cfg
    return None


def server_config_from_dict(server_id: str, entry: Dict[str, Any]) -> MCPServerConfig:
    """Parse API / form payload into ``MCPServerConfig``."""
    if not isinstance(entry, dict):
        raise ValueError("server entry must be an object")
    sid = str(server_id or entry.get("id") or "").strip()
    if not sid:
        raise ValueError("server id required")
    args = entry.get("args")
    url = entry.get("url")
    headers = entry.get("headers")
    env = entry.get("env")
    return MCPServerConfig(
        server_id=sid,
        enabled=bool(entry.get("enabled", True)),
        transport=str(entry.get("transport") or "stdio").strip().lower() or "stdio",
        command=str(entry.get("command") or "").strip(),
        args=[str(x) for x in args] if isinstance(args, list) else [],
        url=str(url).strip() if url else "",
        headers={str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {},
        env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
        cwd=str(entry["cwd"]).strip() if entry.get("cwd") else None,
    )


def minimax_mcp_output_dir(base: Optional[Path] = None) -> Path:
    """Writable directory for MiniMax Token Plan MCP (``MINIMAX_MCP_BASE_PATH``)."""
    root = project_root() if base is None else Path(base).resolve()
    return root / "mcp-minimax-out"


MINIMAX_MCP_SERVER_ID = "MiniMax"


def build_minimax_token_plan_mcp_server(
    *,
    api_key: str,
    api_host: str = "https://api.minimaxi.com",
    uvx_command: str = "uvx",
    base: Optional[Path] = None,
    resource_mode: str = "",
) -> Dict[str, Any]:
    """
    Stdio MCP entry for ``uvx minimax-coding-plan-mcp`` (``understand_image``, ``web_search``).
    See https://platform.minimaxi.com/docs/guides/token-plan-mcp-guide
    """
    out_dir = minimax_mcp_output_dir(base)
    out_dir.mkdir(parents=True, exist_ok=True)
    env: Dict[str, str] = {
        "MINIMAX_API_KEY": (api_key or "").strip(),
        "MINIMAX_API_HOST": (api_host or "https://api.minimaxi.com").strip().rstrip("/"),
        "MINIMAX_MCP_BASE_PATH": str(out_dir),
    }
    mode = (resource_mode or "").strip().lower()
    if mode in ("url", "local"):
        env["MINIMAX_API_RESOURCE_MODE"] = mode
    return {
        "enabled": True,
        "transport": "stdio",
        "command": (uvx_command or "uvx").strip() or "uvx",
        "args": ["minimax-coding-plan-mcp", "-y"],
        "env": env,
    }


def merge_minimax_mcp_server(
    servers: Dict[str, Any],
    *,
    api_key: str,
    api_host: str = "https://api.minimaxi.com",
    uvx_command: str = "uvx",
    base: Optional[Path] = None,
    resource_mode: str = "",
) -> Dict[str, Any]:
    """Return ``servers`` with/overwriting the MiniMax Token Plan MCP entry."""
    out = dict(servers) if isinstance(servers, dict) else {}
    out[MINIMAX_MCP_SERVER_ID] = build_minimax_token_plan_mcp_server(
        api_key=api_key,
        api_host=api_host,
        uvx_command=uvx_command,
        base=base,
        resource_mode=resource_mode,
    )
    return out


def ensure_default_mcp_config(base: Optional[Path] = None) -> None:
    path = mcp_config_path(base)
    if path.is_file():
        return
    save_mcp_config({"servers": {}}, base=base or project_root())
