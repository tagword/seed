"""Append-only full-chain trace events for a chat session."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from seed.core import env_access as _ea

logger = logging.getLogger(__name__)

_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_TRACE_AUDIT: Tuple[str, ...] = ("SEED_LLM_TRACE_AUDIT",)
_TRACE_AUDIT_DIR: Tuple[str, ...] = ("SEED_LLM_TRACE_AUDIT_DIR",)
_PROJECTION_AUDIT: Tuple[str, ...] = ("SEED_LLM_PROJECTION_AUDIT",)


def trace_audit_enabled() -> bool:
    """Trace is enabled explicitly or alongside projection audit."""
    return _ea.env_truthy(*_TRACE_AUDIT, default="0") or _ea.env_truthy(
        *_PROJECTION_AUDIT,
        default="0",
    )


def _safe_slug(session_id: str) -> str:
    s = _SAFE_RE.sub("_", (session_id or "").strip()).strip("._-") or "session"
    return s[:128]


def _parse_active_session() -> Tuple[str, str, str]:
    from seed.core.agent_context import (
        active_episodic_project_id,
        get_active_agent_id,
        get_active_llm_session,
    )

    raw = (get_active_llm_session() or "").strip()
    aid = (get_active_agent_id() or "").strip() or "default"
    sid = raw
    if "::" in raw:
        left, _, right = raw.partition("::")
        if left.strip():
            aid = left.strip()
        sid = right.strip() or raw
    return aid, sid or "unknown", active_episodic_project_id()


def _trace_root(
    agent_id: str,
    session_id: str,
    project_id: Optional[str] = None,
) -> Path:
    custom = _ea.pick_nonempty(*_TRACE_AUDIT_DIR)
    if custom:
        base = Path(custom).expanduser().resolve()
    else:
        from seed.core.llm_sess import agent_project_data_subdir, agent_sessions_dir

        aid = (agent_id or "").strip() or "default"
        pid = (project_id or "").strip()
        if pid:
            base = agent_project_data_subdir(aid, pid, "sessions")
        else:
            base = agent_sessions_dir(aid)
    return (base / "_trace" / _safe_slug(session_id)).resolve()


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


def append_trace_event(
    event_type: str,
    *,
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    **fields: Any,
) -> Optional[Path]:
    """Append one JSONL event to the current session trace."""
    if not trace_audit_enabled():
        return None
    aid, sid, pid = _parse_active_session()
    if agent_id:
        aid = (agent_id or "").strip() or aid
    if session_id:
        sid = (session_id or "").strip() or sid
    if project_id:
        pid = (project_id or "").strip()
    root = _trace_root(aid, sid, pid or None)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("trace audit mkdir failed: %s", exc)
        return None

    record: Dict[str, Any] = {
        "version": 1,
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": (event_type or "event").strip() or "event",
        "agent_id": aid,
        "session_id": sid,
        "project_id": pid or None,
    }
    for key, value in fields.items():
        record[key] = _json_safe(value)

    path = root / "trace.jsonl"
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("trace audit append failed: %s", exc)
        return None
    return path
