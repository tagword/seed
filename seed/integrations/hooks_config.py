"""Agent hooks configuration (``config/hooks.json``)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

from seed.core.config_plane import config_dir, project_root

logger = logging.getLogger(__name__)

HOOKS_CONFIG_FILENAME = "hooks.json"
HookEvent = Literal["pre_tool_call", "post_tool_call", "turn_end"]

_VALID_EVENTS = frozenset({"pre_tool_call", "post_tool_call", "turn_end"})

_DEFAULT: dict[str, Any] = {
    "_readme": (
        "Shell hooks run on agent events. Env: SEED_HOOK_EVENT, SEED_HOOK_TOOL_NAME, "
        "SEED_HOOK_SESSION_ID, SEED_HOOK_AGENT_ID, SEED_HOOK_PAYLOAD (JSON)."
    ),
    "enabled": True,
    "hooks": [],
}


@dataclass
class HookEntry:
    id: str
    event: HookEvent
    command: str
    enabled: bool = True
    timeout_sec: int = 30


def hooks_config_path(base: Optional[Path] = None) -> Path:
    root = config_dir() if base is None else (base.resolve() / "config")
    return root / HOOKS_CONFIG_FILENAME


def load_hooks_config(base: Optional[Path] = None) -> dict[str, Any]:
    path = hooks_config_path(base)
    if not path.is_file():
        return dict(_DEFAULT)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("ignore bad %s: %s", path, e)
        return dict(_DEFAULT)
    return raw if isinstance(raw, dict) else dict(_DEFAULT)


def save_hooks_config(data: dict[str, Any], base: Optional[Path] = None) -> Path:
    path = hooks_config_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    hooks = data.get("hooks")
    if hooks is not None and not isinstance(hooks, list):
        raise ValueError("hooks must be a list")
    payload = {
        "_readme": _DEFAULT["_readme"],
        "enabled": bool(data.get("enabled", True)),
        "hooks": hooks if isinstance(hooks, list) else [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def ensure_default_hooks_config(base: Optional[Path] = None) -> None:
    path = hooks_config_path(base)
    if path.is_file():
        return
    save_hooks_config({"enabled": True, "hooks": []}, base=base or project_root())


def list_hook_entries(base: Optional[Path] = None) -> list[HookEntry]:
    raw = load_hooks_config(base)
    if not raw.get("enabled", True):
        return []
    hooks = raw.get("hooks")
    if not isinstance(hooks, list):
        return []
    out: list[HookEntry] = []
    for i, h in enumerate(hooks):
        if not isinstance(h, dict):
            continue
        ev = str(h.get("event") or "").strip()
        if ev not in _VALID_EVENTS:
            continue
        cmd = str(h.get("command") or "").strip()
        if not cmd:
            continue
        hid = str(h.get("id") or f"hook-{i}").strip()
        out.append(
            HookEntry(
                id=hid,
                event=ev,  # type: ignore[arg-type]
                command=cmd,
                enabled=bool(h.get("enabled", True)),
                timeout_sec=max(1, min(int(h.get("timeout_sec") or 30), 300)),
            )
        )
    return out


def hooks_for_event(event: str, base: Optional[Path] = None) -> list[HookEntry]:
    return [h for h in list_hook_entries(base) if h.enabled and h.event == event]
