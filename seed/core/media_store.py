"""Session-scoped media file storage (attachments for vision tools)."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Optional

_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_segment(name: str, *, max_len: int = 64) -> str:
    s = _SAFE.sub("_", (name or "").strip()).strip("._-") or "file"
    return s[:max_len]


def attachments_root(agent_id: str) -> Path:
    from seed.core.llm_sess import agent_sessions_dir

    root = agent_sessions_dir(agent_id) / "attachments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def session_attachments_dir(agent_id: str, session_id: str) -> Path:
    d = attachments_root(agent_id) / _safe_segment(session_id, max_len=128)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_session_media(
    *,
    agent_id: str,
    session_id: str,
    raw_bytes: bytes,
    filename: str,
    mime: str = "",
    attachment_id: Optional[str] = None,
) -> tuple[str, Path]:
    """Persist bytes under session attachments; return (attachment_id, absolute path)."""
    aid = (attachment_id or "").strip() or uuid.uuid4().hex[:12]
    ext = Path(filename or "blob").suffix.lower()
    if not ext or len(ext) > 10:
        ext = _guess_ext(mime) or ".bin"
    safe_name = _safe_segment(Path(filename or "file").stem, max_len=48)
    out = session_attachments_dir(agent_id, session_id) / f"{aid}_{safe_name}{ext}"
    out.write_bytes(raw_bytes)
    return aid, out.resolve()


def resolve_session_media_path(
    agent_id: str,
    session_id: str,
    attachment_id: str,
) -> Optional[Path]:
    """Find attachment file by id prefix in session directory."""
    aid = (attachment_id or "").strip()
    if not aid:
        return None
    base = session_attachments_dir(agent_id, session_id)
    if not base.is_dir():
        return None
    for p in base.iterdir():
        if not p.is_file():
            continue
        if p.name == aid or p.name.startswith(f"{aid}_"):
            return p.resolve()
    return None


def _guess_ext(mime: str) -> str:
    m = (mime or "").lower().split(";")[0].strip()
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "text/markdown": ".md",
    }.get(m, "")
