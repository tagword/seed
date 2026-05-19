"""Project registry per agent (JSON on disk). Kernel-local — no CodeAgent imports."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from seed.core.paths import agent_projects_registry_dir


def _registry_file(agent_id: str) -> Path:
    return agent_projects_registry_dir(agent_id) / "registry.json"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(agent_id: str) -> Dict[str, Any]:
    path = _registry_file(agent_id)
    if not path.is_file():
        return {"version": 1, "projects": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "projects": []}
    if not isinstance(raw, dict):
        return {"version": 1, "projects": []}
    items = raw.get("projects")
    if not isinstance(items, list):
        items = []
    return {"version": int(raw.get("version") or 1), "projects": items}


def _save(agent_id: str, data: Dict[str, Any]) -> None:
    path = _registry_file(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def list_projects(agent_id: str) -> List[Dict[str, Any]]:
    data = _load(agent_id)
    rows = [r for r in data["projects"] if isinstance(r, dict)]
    rows.sort(key=lambda r: str(r.get("updated_at") or r.get("created_at") or ""), reverse=True)
    return rows


def get_project(agent_id: str, project_id: str) -> Optional[Dict[str, Any]]:
    pid = (project_id or "").strip()
    if not pid:
        return None
    for r in _load(agent_id)["projects"]:
        if isinstance(r, dict) and str(r.get("id") or "").strip() == pid:
            return dict(r)
    return None


def create_project(agent_id: str, name: str, path: str = "") -> Dict[str, Any]:
    n = (name or "").strip()
    if not n:
        raise ValueError("project name required")
    data = _load(agent_id)
    items: List[Dict[str, Any]] = [r for r in data["projects"] if isinstance(r, dict)]
    now = _utc_iso()
    row = {
        "id": uuid.uuid4().hex[:12],
        "name": n,
        "path": (path or "").strip(),
        "created_at": now,
        "updated_at": now,
    }
    items.append(row)
    data["projects"] = items
    _save(agent_id, data)
    return row


def delete_project(agent_id: str, project_id: str) -> bool:
    pid = (project_id or "").strip()
    if not pid:
        return False
    data = _load(agent_id)
    items = [r for r in data["projects"] if isinstance(r, dict)]
    new_items = [r for r in items if str(r.get("id") or "").strip() != pid]
    if len(new_items) == len(items):
        return False
    data["projects"] = new_items
    _save(agent_id, data)
    return True


def default_project_workspace(agent_id: str, project_id: str) -> str:
    """Per-project workspace under ``agents/<id>/projects-data/<project_id>``."""
    from seed.core.paths import agent_project_data_dir

    pid = (project_id or "").strip()
    if not pid:
        return ""
    return str(agent_project_data_dir(agent_id, pid).resolve())


def resolve_project_path(agent_id: str, project_id: str) -> str:
    """Resolved registry ``path`` only; returns ``\"\"`` when unset (workspace is optional)."""
    row = get_project(agent_id, project_id)
    if not row:
        return ""
    raw = str(row.get("path") or "").strip()
    if not raw:
        return ""
    return str(Path(raw).expanduser().resolve())


def ensure_project_path(agent_id: str, project_id: str) -> str:
    """
    Return the project workspace path, persisting the default when registry ``path`` is empty.
    Creates the directory when assigning the default.
    """
    row = get_project(agent_id, project_id)
    if not row:
        return ""
    raw = str(row.get("path") or "").strip()
    if raw:
        return str(Path(raw).expanduser().resolve())
    default = default_project_workspace(agent_id, project_id)
    if not default:
        return ""
    Path(default).mkdir(parents=True, exist_ok=True)
    update_project_path(agent_id, project_id, default)
    return default


def update_project_path(agent_id: str, project_id: str, path: str) -> bool:
    pid = (project_id or "").strip()
    if not pid:
        return False
    data = _load(agent_id)
    changed = False
    for r in data["projects"]:
        if not isinstance(r, dict):
            continue
        if str(r.get("id") or "").strip() != pid:
            continue
        r["path"] = (path or "").strip()
        r["updated_at"] = _utc_iso()
        changed = True
        break
    if not changed:
        return False
    _save(agent_id, data)
    return True


def rename_project(agent_id: str, project_id: str, new_name: str) -> bool:
    pid = (project_id or "").strip()
    nn = (new_name or "").strip()
    if not pid or not nn:
        return False
    data = _load(agent_id)
    changed = False
    for r in data["projects"]:
        if not isinstance(r, dict):
            continue
        if str(r.get("id") or "").strip() != pid:
            continue
        r["name"] = nn
        r["updated_at"] = _utc_iso()
        changed = True
        break
    if not changed:
        return False
    _save(agent_id, data)
    return True


def list_project_plan_files(agent_id: str, project_id: str) -> List[str]:
    from seed.core.paths import agent_project_data_subdir

    plans = agent_project_data_subdir(agent_id, project_id, "plans")
    if not plans.is_dir():
        return []
    out: List[str] = []
    for p in sorted(plans.glob("*.md")):
        if p.is_file():
            out.append(p.name)
    return out
