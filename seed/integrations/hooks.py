"""Dispatch configured shell hooks on agent lifecycle events."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any, Dict, Optional

from seed.core.env_access import HOOKS_ENABLED, env_truthy
from seed.integrations.hooks_config import HookEntry, hooks_for_event

logger = logging.getLogger(__name__)


def hooks_globally_enabled() -> bool:
    return env_truthy(*HOOKS_ENABLED, default="1")


def dispatch_hooks(event: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Run all enabled hooks for ``event`` (best-effort, never raises)."""
    if not hooks_globally_enabled():
        return
    data = dict(payload or {})
    for entry in hooks_for_event(event):
        try:
            _run_hook(entry, event, data)
        except Exception:
            logger.exception("hook %s failed", entry.id)


def _run_hook(entry: HookEntry, event: str, payload: Dict[str, Any]) -> None:
    env = dict(os.environ)
    env["SEED_HOOK_EVENT"] = event
    env["SEED_HOOK_HOOK_ID"] = entry.id
    env["SEED_HOOK_TOOL_NAME"] = str(payload.get("tool_name") or "")
    env["SEED_HOOK_SESSION_ID"] = str(payload.get("session_id") or "")
    env["SEED_HOOK_AGENT_ID"] = str(payload.get("agent_id") or "")
    env["SEED_HOOK_STOPPED_REASON"] = str(payload.get("stopped_reason") or "")
    try:
        env["SEED_HOOK_PAYLOAD"] = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        env["SEED_HOOK_PAYLOAD"] = "{}"
    proc = subprocess.run(
        entry.command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=entry.timeout_sec,
        env=env,
    )
    if proc.returncode != 0:
        logger.warning(
            "hook %s exit %s stderr=%s",
            entry.id,
            proc.returncode,
            (proc.stderr or "")[:500],
        )
    elif proc.stdout and proc.stdout.strip():
        logger.debug("hook %s stdout: %s", entry.id, proc.stdout.strip()[:200])
