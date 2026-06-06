"""Project registry per agent (JSON on disk). Kernel-local — no CodeAgent imports."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from seed.core.paths import agent_projects_registry_dir

# 虚拟项目 ID，用于收纳未关联工作目录的会话
UNASSIGNED_PROJECT_ID = "__unassigned__"
UNASSIGNED_PROJECT_NAME = "未分类"


def _registry_file(agent_id: str) -> Path:
    return agent_projects_registry_dir(agent_id) / "registry.json"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(agent_id: str) -> Dict[str, Any]:
    path = _registry_file(agent_id)
    if not path.is_file():
        return {"version": 3, "projects": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": 3, "projects": []}
    if not isinstance(raw, dict):
        return {"version": 3, "projects": []}
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


def _migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    """逐版本迁移到最新格式。"""
    ver = int(data.get("version") or 1)
    if ver < 2:
        # v1 → v2: add ``sessions`` list
        for r in data.get("projects", []):
            if isinstance(r, dict) and "sessions" not in r:
                r["sessions"] = []
        ver = 2
        data["version"] = 2
    if ver < 3:
        # v2 → v3: sessions 从 list[str] 转为 dict[str, meta]
        for r in data.get("projects", []):
            if not isinstance(r, dict):
                continue
            raw = r.get("sessions")
            if isinstance(raw, list):
                new_map: Dict[str, Dict[str, Any]] = {}
                for s in raw:
                    if isinstance(s, str) and s.strip():
                        new_map[s.strip()] = {}
                r["sessions"] = new_map
        ver = 3
        data["version"] = 3
    if ver < 4:
        # v3 → v4: 确保虚拟项目 __unassigned__ 存在
        items = data.get("projects", [])
        has_unassigned = False
        for r in items:
            if isinstance(r, dict) and str(r.get("id") or "").strip() == UNASSIGNED_PROJECT_ID:
                has_unassigned = True
                if "sessions" not in r or not isinstance(r["sessions"], dict):
                    r["sessions"] = {}
                break
        if not has_unassigned:
            now = _utc_iso()
            items.insert(0, {
                "id": UNASSIGNED_PROJECT_ID,
                "name": UNASSIGNED_PROJECT_NAME,
                "path": "",
                "sessions": {},
                "created_at": now,
                "updated_at": now,
            })
        ver = 4
        data["version"] = 4
    return data


def _load_migrated(agent_id: str) -> Dict[str, Any]:
    data = _load(agent_id)
    if int(data.get("version") or 1) < 4:
        data = _migrate(data)
        _save(agent_id, data)
    return data


def list_projects(agent_id: str, *, include_virtual: bool = True) -> List[Dict[str, Any]]:
    """返回 agent 的所有项目列表。

    ``include_virtual``：为 True（默认）时包含虚拟项目 ``__unassigned__``；
    为 False 时排除，适合 WebUI 项目树展示（虚拟项目由前端逻辑动态插入）。
    """
    data = _load_migrated(agent_id)
    rows = [r for r in data["projects"] if isinstance(r, dict)]
    if not include_virtual:
        rows = [r for r in rows if str(r.get("id") or "").strip() != UNASSIGNED_PROJECT_ID]
    rows.sort(key=lambda r: str(r.get("updated_at") or r.get("created_at") or ""), reverse=True)
    return rows


def get_project(agent_id: str, project_id: str) -> Optional[Dict[str, Any]]:
    pid = (project_id or "").strip()
    if not pid:
        return None
    for r in _load_migrated(agent_id)["projects"]:
        if isinstance(r, dict) and str(r.get("id") or "").strip() == pid:
            return dict(r)
    return None


def create_project(agent_id: str, name: str, path: str = "") -> Dict[str, Any]:
    n = (name or "").strip()
    if not n:
        raise ValueError("project name required")
    data = _load_migrated(agent_id)
    items: List[Dict[str, Any]] = [r for r in data["projects"] if isinstance(r, dict)]
    now = _utc_iso()
    row = {
        "id": uuid.uuid4().hex[:12],
        "name": n,
        "path": (path or "").strip(),
        "sessions": {},
        "created_at": now,
        "updated_at": now,
    }
    items.append(row)
    data["projects"] = items
    _save(agent_id, data)
    return row


def delete_project(agent_id: str, project_id: str) -> bool:
    pid = (project_id or "").strip()
    if not pid or pid == UNASSIGNED_PROJECT_ID:
        return False  # 不允许删除虚拟项目
    data = _load_migrated(agent_id)
    items = [r for r in data["projects"] if isinstance(r, dict)]
    new_items = [r for r in items if str(r.get("id") or "").strip() != pid]
    if len(new_items) == len(items):
        return False
    data["projects"] = new_items
    _save(agent_id, data)
    return True


# ── 会话注册（v3: sessions 为 dict[session_id, meta]）──


def register_session(
    agent_id: str,
    project_id: str,
    session_id: str,
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """将会话注册到项目，同时缓存元信息（幂等）。

    如果会话已存在于其他项目，自动从旧项目移除（迁移到新项目）。
    如果 ``project_id`` 为空，不注册（空会话等发消息后再注册）。
    如果 ``project_id`` 为 ``__unassigned__``，注册到虚拟项目。
    ``meta`` 可选，包含 ``display_title``、``updated_at``、``message_count``、``preview`` 等。
    """
    pid = (project_id or "").strip()
    if not pid:
        return False  # 无 project_id 不注册，等发消息后再注册
    if pid == UNASSIGNED_PROJECT_ID:
        pid = UNASSIGNED_PROJECT_ID
    sid = (session_id or "").strip()
    if not sid:
        return False
    data = _load_migrated(agent_id)

    # 先将会话从其他项目移除（避免跨项目重复）
    for r in data["projects"]:
        if not isinstance(r, dict):
            continue
        if str(r.get("id") or "").strip() == pid:
            continue  # 跳过目标项目
        sess_map = r.get("sessions")
        if isinstance(sess_map, dict):
            sess_map.pop(sid, None)

    # 注册到目标项目
    for r in data["projects"]:
        if not isinstance(r, dict):
            continue
        if str(r.get("id") or "").strip() != pid:
            continue
        sess_map: Dict[str, Any] = r.get("sessions")
        if not isinstance(sess_map, dict):
            sess_map = {}
            r["sessions"] = sess_map
        existing = sess_map.get(sid, {})
        if isinstance(existing, dict) and meta:
            existing.update(meta)
            sess_map[sid] = existing
        elif sid not in sess_map:
            sess_map[sid] = dict(meta or {})
        r["updated_at"] = _utc_iso()
        _save(agent_id, data)
        return True
    return False


def update_session_meta(
    agent_id: str,
    project_id: str,
    session_id: str,
    meta: Dict[str, Any],
) -> bool:
    """更新 registry 中会话的缓存元信息（会话必须已注册）。"""
    pid = (project_id or "").strip()
    sid = (session_id or "").strip()
    if not pid or not sid or not meta:
        return False
    data = _load_migrated(agent_id)
    for r in data["projects"]:
        if not isinstance(r, dict):
            continue
        if str(r.get("id") or "").strip() != pid:
            continue
        sess_map: Dict[str, Any] = r.get("sessions")
        if not isinstance(sess_map, dict):
            return False
        if sid not in sess_map:
            return False
        existing = sess_map[sid]
        if not isinstance(existing, dict):
            sess_map[sid] = dict(meta)
        else:
            existing.update(meta)
        r["updated_at"] = _utc_iso()
        _save(agent_id, data)
        return True
    return False


def unregister_session(agent_id: str, project_id: str, session_id: str) -> bool:
    """从项目的 sessions 中移除会话。"""
    pid = (project_id or "").strip()
    if not pid or pid == UNASSIGNED_PROJECT_ID:
        pid = UNASSIGNED_PROJECT_ID
    sid = (session_id or "").strip()
    if not sid:
        return False
    data = _load_migrated(agent_id)
    for r in data["projects"]:
        if not isinstance(r, dict):
            continue
        if str(r.get("id") or "").strip() != pid:
            continue
        sess_map: Dict[str, Any] = r.get("sessions")
        if not isinstance(sess_map, dict):
            return True
        sess_map.pop(sid, None)
        r["updated_at"] = _utc_iso()
        _save(agent_id, data)
        return True
    return False


def list_project_session_ids(agent_id: str, project_id: str) -> List[str]:
    """返回项目下注册的所有会话 ID。"""
    row = get_project(agent_id, project_id)
    if not row:
        return []
    raw = row.get("sessions")
    if not isinstance(raw, dict):
        return []
    return [str(s) for s in raw if s]


def list_project_sessions_meta(
    agent_id: str, project_id: str,
) -> Dict[str, Dict[str, Any]]:
    """返回项目下所有会话的缓存元信息 dict[session_id, meta]。"""
    row = get_project(agent_id, project_id)
    if not row:
        return {}
    raw = row.get("sessions")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for sid, meta in raw.items():
        if isinstance(sid, str) and sid.strip():
            out[sid.strip()] = dict(meta) if isinstance(meta, dict) else {}
    return out


def get_session_meta(
    agent_id: str, project_id: str, session_id: str,
) -> Optional[Dict[str, Any]]:
    """获取 registry 中缓存的单个会话元信息。"""
    all_meta = list_project_sessions_meta(agent_id, project_id)
    return all_meta.get(session_id)


def list_unassigned_session_ids(agent_id: str) -> List[str]:
    """返回虚拟项目（未分类）下的会话 ID。"""
    return list_project_session_ids(agent_id, UNASSIGNED_PROJECT_ID)


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
    data = _load_migrated(agent_id)
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
    data = _load_migrated(agent_id)
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
