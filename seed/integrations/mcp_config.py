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
        "MCP servers for Seed/CodeAgent. Each entry under servers: id -> {enabled, transport, command, args, env, cwd}. "
        "transport must be stdio. Tools: mcp_servers, mcp_list_tools, mcp_call."
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
        env = entry.get("env")
        out.append(
            MCPServerConfig(
                server_id=sid_s,
                enabled=bool(entry.get("enabled", True)),
                transport=str(entry.get("transport") or "stdio").strip().lower(),
                command=str(entry.get("command") or "").strip(),
                args=[str(x) for x in args] if isinstance(args, list) else [],
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


def ensure_default_mcp_config(base: Optional[Path] = None) -> None:
    path = mcp_config_path(base)
    if path.is_file():
        return
    save_mcp_config({"servers": {}}, base=base or project_root())
