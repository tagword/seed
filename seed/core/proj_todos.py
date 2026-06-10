"""Per-project todo store (JSON)."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from seed.core.paths import agent_project_data_subdir


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store_path(agent_id: str, project_id: str) -> Path:
    d = agent_project_data_subdir(agent_id, project_id, "todos")
    return d / "store.json"


def _load(agent_id: str, project_id: str) -> Dict[str, Any]:
    path = _store_path(agent_id, project_id)
    if not path.is_file():
        return {"items": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"items": []}
    if not isinstance(raw, dict):
        return {"items": []}
    items = raw.get("items")
    if not isinstance(items, list):
        items = []
    return {"items": items}


_save_lock = threading.Lock()


def _save(agent_id: str, project_id: str, data: Dict[str, Any]) -> None:
    with _save_lock:
        path = _store_path(agent_id, project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)


def list_todos(
    agent_id: str,
    project_id: str,
    *,
    status: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    items = [x for x in _load(agent_id, project_id)["items"] if isinstance(x, dict)]
    st = (status or "").strip().lower() or None
    out: List[Dict[str, Any]] = []
    for it in items:
        if st and str(it.get("status") or "").strip().lower() != st:
            continue
        if session_id is not None:
            want = str(session_id).strip()
            got = str(it.get("session_id") or "").strip()
            if got != want:
                continue
        out.append(dict(it))
    return out


def create_todo(
    agent_id: str,
    project_id: str,
    *,
    content: str,
    session_id: str = "",
    status: str = "pending",
) -> Dict[str, Any]:
    c = (content or "").strip()
    if not c:
        raise ValueError("content required")
    data = _load(agent_id, project_id)
    items = [x for x in data["items"] if isinstance(x, dict)]
    now = _utc_iso()
    row = {
        "id": uuid.uuid4().hex[:12],
        "content": c,
        "status": (status or "pending").strip().lower() or "pending",
        "session_id": (session_id or "").strip(),
        "created_at": now,
        "updated_at": now,
    }
    items.append(row)
    data["items"] = items
    _save(agent_id, project_id, data)
    return row


def update_todo(
    agent_id: str,
    project_id: str,
    todo_id: str,
    updates: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    tid = (todo_id or "").strip()
    if not tid:
        return None
    data = _load(agent_id, project_id)
    items = [x for x in data["items"] if isinstance(x, dict)]
    found: Optional[Dict[str, Any]] = None
    for it in items:
        if str(it.get("id") or "").strip() != tid:
            continue
        if "content" in updates and isinstance(updates["content"], str):
            it["content"] = updates["content"].strip()
        if "status" in updates and isinstance(updates["status"], str):
            it["status"] = updates["status"].strip().lower()
        it["updated_at"] = _utc_iso()
        found = dict(it)
        break
    if found is None:
        return None
    data["items"] = items
    _save(agent_id, project_id, data)
    return found


def delete_todo(agent_id: str, project_id: str, todo_id: str) -> bool:
    tid = (todo_id or "").strip()
    if not tid:
        return False
    data = _load(agent_id, project_id)
    items = [x for x in data["items"] if isinstance(x, dict)]
    new_items = [x for x in items if str(x.get("id") or "").strip() != tid]
    if len(new_items) == len(items):
        return False
    data["items"] = new_items
    _save(agent_id, project_id, data)
    return True
