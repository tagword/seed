"""LSP server configuration (``config/lsp.json``)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from seed.core.config_plane import config_dir, project_root

logger = logging.getLogger(__name__)

LSP_CONFIG_FILENAME = "lsp.json"

_DEFAULT: dict[str, Any] = {
    "_readme": "Per-language LSP servers (stdio). Tools: lsp_diagnostics, lsp_definition.",
    "language_map": {".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript"},
    "servers": {
        "python": {
            "enabled": True,
            "command": "pyright-langserver",
            "args": ["--stdio"],
        }
    },
}


@dataclass
class LSPServerConfig:
    language_id: str
    enabled: bool = True
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def argv(self) -> list[str]:
        if not self.command.strip():
            raise ValueError(f"LSP server {self.language_id}: command required")
        return [self.command.strip(), *[str(a) for a in self.args]]


def lsp_config_path(base: Optional[Path] = None) -> Path:
    root = config_dir() if base is None else (base.resolve() / "config")
    return root / LSP_CONFIG_FILENAME


def load_lsp_config(base: Optional[Path] = None) -> dict[str, Any]:
    path = lsp_config_path(base)
    if not path.is_file():
        return dict(_DEFAULT)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("ignore bad %s: %s", path, e)
        return dict(_DEFAULT)
    if not isinstance(raw, dict):
        return dict(_DEFAULT)
    out = dict(_DEFAULT)
    if isinstance(raw.get("language_map"), dict):
        out["language_map"] = raw["language_map"]
    if isinstance(raw.get("servers"), dict):
        out["servers"] = raw["servers"]
    return out


def ensure_default_lsp_config(base: Optional[Path] = None) -> None:
    path = lsp_config_path(base)
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_DEFAULT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def server_for_filepath(filepath: str | Path, base: Optional[Path] = None) -> Optional[LSPServerConfig]:
    cfg = load_lsp_config(base)
    lm = cfg.get("language_map") or {}
    servers = cfg.get("servers") or {}
    if not isinstance(lm, dict) or not isinstance(servers, dict):
        return None
    suffix = Path(filepath).suffix.lower()
    lang_key = lm.get(suffix)
    if not lang_key:
        return None
    entry = servers.get(str(lang_key))
    if not isinstance(entry, dict):
        return None
    if not entry.get("enabled", True):
        return None
    env = entry.get("env")
    args = entry.get("args")
    return LSPServerConfig(
        language_id=str(lang_key),
        enabled=True,
        command=str(entry.get("command") or "").strip(),
        args=[str(x) for x in args] if isinstance(args, list) else [],
        env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
    )
