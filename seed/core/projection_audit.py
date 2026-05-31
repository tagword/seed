"""Persist exact LLM request message lists (api_msgs) for later replay/audit."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from seed.core import env_access as _ea

logger = logging.getLogger(__name__)

_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_SEQ_LOCK = threading.Lock()
_SEQ_BY_DIR: Dict[str, int] = {}

_LLM_PROJECTION_AUDIT: Tuple[str, ...] = ("SEED_LLM_PROJECTION_AUDIT",)
_LLM_PROJECTION_AUDIT_DIR: Tuple[str, ...] = ("SEED_LLM_PROJECTION_AUDIT_DIR",)


def projection_audit_enabled() -> bool:
    return _ea.env_truthy(*_LLM_PROJECTION_AUDIT, default="0")


def _safe_slug(session_id: str) -> str:
    s = _SAFE_RE.sub("_", (session_id or "").strip()).strip("._-") or "session"
    return s[:128]


def _parse_active_session() -> Tuple[str, str]:
    from seed.core.agent_context import get_active_agent_id, get_active_llm_session

    raw = (get_active_llm_session() or "").strip()
    aid = (get_active_agent_id() or "").strip() or "default"
    sid = raw
    if "::" in raw:
        left, _, right = raw.partition("::")
        if left.strip():
            aid = left.strip()
        sid = right.strip() or raw
    return aid, sid or "unknown"


def _audit_root(
    agent_id: str,
    session_id: str,
    project_id: Optional[str] = None,
) -> Path:
    custom = _ea.pick_nonempty(*_LLM_PROJECTION_AUDIT_DIR)
    if custom:
        base = Path(custom).expanduser().resolve()
    else:
        from seed.core.llm_sess import agent_sessions_dir, agent_project_data_subdir

        aid = (agent_id or "").strip() or "default"
        pid = (project_id or "").strip()
        if pid:
            base = agent_project_data_subdir(aid, pid, "sessions")
        else:
            base = agent_sessions_dir(aid)
    slug = _safe_slug(session_id)
    return (base / "_audit" / slug).resolve()


def _next_seq(audit_dir: Path) -> int:
    key = str(audit_dir)
    with _SEQ_LOCK:
        if key not in _SEQ_BY_DIR:
            seq_file = audit_dir / "_seq"
            try:
                if seq_file.is_file():
                    _SEQ_BY_DIR[key] = max(0, int(seq_file.read_text(encoding="utf-8").strip()))
                else:
                    _SEQ_BY_DIR[key] = 0
            except (OSError, ValueError):
                _SEQ_BY_DIR[key] = 0
        _SEQ_BY_DIR[key] += 1
        n = _SEQ_BY_DIR[key]
    seq_file = audit_dir / "_seq"
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        seq_file.write_text(str(n) + "\n", encoding="utf-8")
    except OSError:
        pass
    return n


def _json_safe_copy(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        return json.loads(json.dumps(messages, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        out: List[Dict[str, Any]] = []
        for m in messages:
            if isinstance(m, dict):
                out.append({k: v for k, v in m.items()})
        return out


def persist_llm_projection_audit(
    messages: List[Dict[str, Any]],
    *,
    kind: str = "chat",
    round_index: int = 0,
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """
    Write a snapshot of the exact ``messages`` list sent to the LLM (or summarizer).

    Layout::
      <sessions>/_audit/<session_slug>/000042-chat-r001.json
      <sessions>/_audit/<session_slug>/index.jsonl   # one line per snapshot

    Enable with ``SEED_LLM_PROJECTION_AUDIT=1`` (``CODEAGENT_LLM_PROJECTION_AUDIT`` via bridge).
    Override root with ``SEED_LLM_PROJECTION_AUDIT_DIR`` (otherwise next to session JSON).
    """
    if not projection_audit_enabled():
        return None
    if not messages:
        return None

    aid, sid = _parse_active_session()
    if agent_id:
        aid = (agent_id or "").strip() or aid
    if session_id:
        sid = (session_id or "").strip() or sid

    pid = (project_id or "").strip()
    if not pid:
        from seed.core.agent_context import active_episodic_project_id

        pid = active_episodic_project_id()

    audit_dir = _audit_root(aid, sid, pid or None)
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("projection audit mkdir failed: %s", exc)
        return None

    seq = _next_seq(audit_dir)
    k = re.sub(r"[^a-z0-9_]+", "_", (kind or "chat").strip().lower()) or "chat"
    fname = f"{seq:08d}-{k}-r{int(round_index):03d}.json"
    out_path = audit_dir / fname

    payload_messages = _json_safe_copy(messages)
    body_bytes = len(
        json.dumps(payload_messages, ensure_ascii=False, default=str).encode("utf-8")
    )
    record: Dict[str, Any] = {
        "version": 1,
        "ts": datetime.now(timezone.utc).isoformat(),
        "seq": seq,
        "kind": k,
        "round": int(round_index),
        "agent_id": aid,
        "session_id": sid,
        "project_id": pid or None,
        "message_count": len(payload_messages),
        "body_bytes": body_bytes,
        "messages": payload_messages,
    }
    if extra:
        record["meta"] = dict(extra)

    try:
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(out_path)
        index_line = {
            "seq": seq,
            "file": fname,
            "ts": record["ts"],
            "kind": k,
            "round": int(round_index),
            "body_bytes": body_bytes,
            "message_count": len(payload_messages),
        }
        with (audit_dir / "index.jsonl").open("a", encoding="utf-8") as idx:
            idx.write(json.dumps(index_line, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("projection audit write failed: %s", exc)
        return None
    return out_path


def list_projection_audit_index(
    session_id: str,
    *,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Read ``index.jsonl`` entries for a session (newest last)."""
    aid = (agent_id or "").strip() or "default"
    audit_dir = _audit_root(aid, session_id, project_id)
    idx_path = audit_dir / "index.jsonl"
    if not idx_path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        for line in idx_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            if isinstance(raw, dict):
                rows.append(raw)
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def load_projection_audit_snapshot(
    session_id: str,
    *,
    seq: Optional[int] = None,
    path: Optional[Path] = None,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Load one audit JSON by ``seq`` or explicit ``path``."""
    if path is not None:
        p = Path(path)
    else:
        if seq is None:
            return None
        aid = (agent_id or "").strip() or "default"
        audit_dir = _audit_root(aid, session_id, project_id)
        matches = sorted(audit_dir.glob(f"{int(seq):08d}-*.json"))
        if not matches:
            return None
        p = matches[0]
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None
