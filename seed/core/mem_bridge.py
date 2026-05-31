"""Episodic memory: project-scoped experience snippets injected at compact boundaries."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from seed.core import env_access as _ea
from seed.core.mem_sys import MemorySystem

_METADATA_EPISODIC_BLOCK = "episodic_block"
_METADATA_EPISODIC_PROJECT_ID = "episodic_project_id"
_METADATA_EPISODIC_REFRESHED_AT = "episodic_refreshed_at"


_EPISODIC_START = "\n## Seed episodic memory (recent)\n"
_EPISODIC_END = "\n## End Seed episodic memory\n"

_EPISODIC_BLOCK = re.compile(
    r"(?:\n## CodeAgent episodic memory \(recent\)\n.*?\n## End CodeAgent episodic memory\n"
    r"|\n## Seed episodic memory \(recent\)\n.*?\n## End Seed episodic memory\n)",
    re.DOTALL,
)


# Match a ``## TTL`` section that gives a lifetime in seconds.
#   ## TTL
#   600
# Or on one line:
#   ## TTL: 600
_TTL_SECTION_RE = re.compile(
    r"^\s*##\s*TTL\s*[:：]?\s*(?P<inline>\d+)?\s*$(?:\r?\n\s*(?P<below>\d+)\s*$)?",
    re.IGNORECASE | re.MULTILINE,
)

# Match a ``## Expires`` section giving an absolute ISO-8601 timestamp.
#   ## Expires
#   2026-04-20T15:00:00Z
_EXPIRES_SECTION_RE = re.compile(
    r"^\s*##\s*Expires\s*[:：]?\s*(?P<inline>[0-9T:\-+Z \.]+)?\s*$"
    r"(?:\r?\n\s*(?P<below>[0-9T:\-+Z \.]+)\s*$)?",
    re.IGNORECASE | re.MULTILINE,
)


def strip_episodic_block(system_text: str) -> str:
    return _EPISODIC_BLOCK.sub("\n", system_text or "").strip()


def _parse_ttl_seconds(text: str) -> Optional[int]:
    m = _TTL_SECTION_RE.search(text or "")
    if not m:
        return None
    raw = (m.group("inline") or m.group("below") or "").strip()
    if not raw:
        return None
    try:
        v = int(raw)
    except ValueError:
        return None
    return v if v > 0 else None


def _parse_expires_dt(text: str) -> Optional[datetime]:
    m = _EXPIRES_SECTION_RE.search(text or "")
    if not m:
        return None
    raw = (m.group("inline") or m.group("below") or "").strip()
    if not raw:
        return None
    # Accept trailing Z (Zulu) as UTC.
    iso = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_expired(path: Path, text: str, *, now: Optional[datetime] = None) -> bool:
    """True if TTL / Expires markers say this experience file is stale."""
    cur = now or datetime.now(timezone.utc)
    exp = _parse_expires_dt(text)
    if exp is not None:
        return cur >= exp
    ttl = _parse_ttl_seconds(text)
    if ttl is not None:
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return False
        return cur >= mtime + timedelta(seconds=ttl)
    return False


def experience_file_expired(path: Path, text: str, *, now: Optional[datetime] = None) -> bool:
    """True if ``## TTL`` / ``## Expires`` in this experience markdown is past (same rules as episodic inject)."""
    return _is_expired(path, text, now=now)


def _td_seconds(seconds: int):
    # Imported lazily to avoid top-level import noise.
    from datetime import timedelta
    return timedelta(seconds=seconds)


def parsed_experience_project_id(text: str) -> Optional[str]:
    """Return canonical project id from ``## Project`` section, or None if absent."""
    m = re.search(r"(?im)^##\s*Project\s*\n\s*(\S+)", text or "")
    if not m:
        return None
    return (m.group(1) or "").strip() or None


def build_episodic_snippets(
    project_root: Path,
    *,
    session_id: Optional[str],
    max_chars: int,
    session_only: bool,
    project_id: Optional[str] = None,
    project_scope: bool = False,
) -> str:
    """
    Recent experience files (newest first), optional filter by logged ## Session field.

    Files carrying an explicit ``## TTL`` (seconds from mtime) or ``## Expires``
    (absolute ISO-8601 timestamp) are **skipped once expired** so stale
    runtime facts (PID/port/"running" snapshots) stop being re-injected.
    """
    try:
        # If caller passes agents/<id>/memory, MemorySystem will use it directly.
        ms = MemorySystem(base_path=project_root.resolve())
    except Exception:
        return ""
    exp_dir = ms.memory_path / "experiences"
    if not exp_dir.is_dir():
        return ""

    files = sorted(exp_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
    parts: List[str] = []
    total = 0
    now = datetime.now(timezone.utc)
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _is_expired(f, text, now=now):
            continue
        if session_only and session_id:
            if "## session" in text.lower():
                if session_id not in text:
                    continue
        if project_scope:
            exp_proj = parsed_experience_project_id(text)
            want = (project_id or "").strip()
            if want:
                if exp_proj != want:
                    continue
            else:
                if exp_proj is not None:
                    continue
        snippet = text.strip()
        if len(snippet) > 900:
            snippet = snippet[:450] + "\n…\n" + snippet[-400:]
        chunk = f"### {f.name}\n{snippet}\n"
        if total + len(chunk) > max_chars:
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts).strip()


def episodic_memory_base(agent_id: str, project_id: Optional[str] = None) -> Path:
    """Directory root passed to ``MemorySystem`` (``.../memory`` under agent or project)."""
    from seed.core.paths import agent_id_default, agent_memory_dir, agent_project_data_subdir

    aid = (agent_id or "").strip() or agent_id_default()
    pid = (project_id or "").strip()
    if pid:
        return agent_project_data_subdir(aid, pid, "memory")
    return agent_memory_dir(aid)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def episodic_project_changed(metadata: Optional[Dict[str, Any]], project_id: Optional[str]) -> bool:
    """True when a prior episodic snapshot exists and the bound project id changed."""
    if not isinstance(metadata, dict):
        return False
    if _METADATA_EPISODIC_PROJECT_ID not in metadata:
        return False
    want = (project_id or "").strip()
    got = str(metadata.get(_METADATA_EPISODIC_PROJECT_ID) or "").strip()
    return got != want


def episodic_needs_bootstrap(metadata: Optional[Dict[str, Any]]) -> bool:
    """True when this session has never scanned experiences (first LLM turn)."""
    if _ea.pick_default("1", *_ea.MEMORY_INJECT).lower() in ("0", "false", "no"):
        return False
    if not isinstance(metadata, dict):
        return True
    return _METADATA_EPISODIC_BLOCK not in metadata


def refresh_episodic_snapshot(
    metadata: Dict[str, Any],
    agent_id: str,
    session_id: Optional[str],
    project_id: Optional[str] = None,
) -> None:
    """
    Scan ``memory/experiences`` and store a frozen block on session metadata.

    Called on first LLM turn (bootstrap), after context compact, or when project binding changes.
    Not on every LLM turn thereafter.
    """
    pid = (project_id or "").strip()
    if _ea.pick_default("1", *_ea.MEMORY_INJECT).lower() in ("0", "false", "no"):
        metadata.pop(_METADATA_EPISODIC_BLOCK, None)
        metadata.pop(_METADATA_EPISODIC_PROJECT_ID, None)
        metadata.pop(_METADATA_EPISODIC_REFRESHED_AT, None)
        return

    max_c = int(_ea.pick_default("5000", *_ea.MEMORY_INJECT_MAX_CHARS))
    session_only = _ea.pick_default("", *_ea.MEMORY_INJECT_SESSION_ONLY).lower() in (
        "1",
        "true",
        "yes",
    )
    snippets = build_episodic_snippets(
        episodic_memory_base(agent_id, pid or None),
        session_id=session_id,
        max_chars=max_c,
        session_only=session_only,
        project_id=pid or None,
        project_scope=bool(pid),
    )
    if snippets:
        metadata[_METADATA_EPISODIC_BLOCK] = _EPISODIC_START + snippets + _EPISODIC_END
    else:
        metadata[_METADATA_EPISODIC_BLOCK] = ""
    metadata[_METADATA_EPISODIC_PROJECT_ID] = pid
    metadata[_METADATA_EPISODIC_REFRESHED_AT] = _utc_iso()


def apply_persisted_episodic_to_messages(
    messages: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
) -> None:
    """Append metadata episodic block to ``messages[0]`` (after compact/skills content)."""
    if not messages or messages[0].get("role") != "system":
        return
    base = strip_episodic_block(str(messages[0].get("content") or ""))
    if _ea.pick_default("1", *_ea.MEMORY_INJECT).lower() in ("0", "false", "no"):
        messages[0]["content"] = base
        return
    block = ""
    if isinstance(metadata, dict):
        raw = metadata.get(_METADATA_EPISODIC_BLOCK)
        if isinstance(raw, str):
            block = raw.strip()
    messages[0]["content"] = (base + block) if block else base


def finalize_episodic_for_llm(
    messages: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]],
    *,
    agent_id: str,
    session_id: Optional[str],
    project_id: Optional[str] = None,
    compact_happened: bool = False,
) -> None:
    """
    Refresh episodic snapshot on session bootstrap, compact, or project change; apply to api messages.
    """
    md = metadata if isinstance(metadata, dict) else {}
    if (
        episodic_needs_bootstrap(md)
        or compact_happened
        or episodic_project_changed(md, project_id)
    ):
        refresh_episodic_snapshot(md, agent_id, session_id, project_id)
    apply_persisted_episodic_to_messages(messages, md)


