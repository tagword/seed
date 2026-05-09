"""Filesystem-backed session storage for LLM chat JSON (used by ``llm_sess``)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from seed.core.models import Session


class SessionNotFoundError(FileNotFoundError):
    pass


class SessionStore:
    """Read/write ``Session`` JSON files under ``base_path``."""

    def __init__(self, base_path: str) -> None:
        self.base_path = Path(base_path).expanduser().resolve()

    def save_session(self, session: Session) -> None:
        self.base_path.mkdir(parents=True, exist_ok=True)
        path = self.base_path / f"{session.id}.json"
        payload: Dict[str, Any] = {
            "id": session.id,
            "name": session.name,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "messages": session.messages,
            "turns": [],  # disk shape for chat UI matches llm_sess expectations
            "config": session.config,
            "metadata": session.metadata,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class SessionManager:
    """Compatibility shim for turn-loop auto-save (same directory layout as ``SessionStore``)."""

    def __init__(self, base_path: str) -> None:
        self._store = SessionStore(base_path)

    def update_session(self, session: Session) -> None:
        self._store.save_session(session)
