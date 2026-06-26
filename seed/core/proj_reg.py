"""Project registry per agent (SQLite). Kernel-local — no CodeAgent imports."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from seed.core.paths import agent_projects_registry_dir

# 虚拟项目 ID，用于收纳未关联工作目录的会话
UNASSIGNED_PROJECT_ID = "__unassigned__"
UNASSIGNED_PROJECT_NAME = "未分类"

# ── SQLite 连接管理 ──────────────────────────────────────────────

_connections: Dict[str, sqlite3.Connection] = {}
_conn_lock = threading.Lock()

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    path        TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS session_meta (
    project_id    TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    display_title TEXT DEFAULT '',
    updated_at    TEXT DEFAULT '',
    message_count INTEGER DEFAULT 0,
    preview       TEXT DEFAULT '',
    channel       TEXT DEFAULT '',
    context_usage TEXT DEFAULT '{}',
    PRIMARY KEY (project_id, session_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sm_project_updated
    ON session_meta(project_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_sm_updated_all
    ON session_meta(updated_at DESC);

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
"""


def _registry_dir(agent_id: str) -> Path:
    """返回 agent 的 projects 目录（registry.db 所在位置）。"""
    return agent_projects_registry_dir(agent_id)


def _db_path(agent_id: str) -> Path:
    return _registry_dir(agent_id) / "registry.db"


def _json_path(agent_id: str) -> Path:
    return _registry_dir(agent_id) / "registry.json"


def _get_conn(agent_id: str) -> sqlite3.Connection:
    """获取 agent 对应的 SQLite 连接（懒初始化 + 连接缓存）。"""
    with _conn_lock:
        if agent_id not in _connections:
            db = _db_path(agent_id)
            # 先迁移，再建新库
            _migrate_from_json_if_needed(agent_id)
            conn = sqlite3.connect(str(db), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA_SQL)
            _connections[agent_id] = conn
        return _connections[agent_id]


def _close_conn(agent_id: str) -> None:
    """关闭并移除连接（测试/清理用）。"""
    with _conn_lock:
        conn = _connections.pop(agent_id, None)
        if conn:
            conn.close()


# ── JSON → SQLite 迁移 ──────────────────────────────────────────

def _migrate_from_json_if_needed(agent_id: str) -> None:
    """若 registry.json 存在且 registry.db 不存在，执行一次性迁移。

    迁移后重命名 registry.json → registry.json.bak。
    """
    db = _db_path(agent_id)
    js = _json_path(agent_id)
    if db.is_file() or not js.is_file():
        return

    # 读取并迁移 JSON 数据到最新版本
    data = _load_json(agent_id)
    data = _migrate(data)

    # 创建 SQLite 并写入
    conn = sqlite3.connect(str(db), check_same_thread=False)
    try:
        conn.executescript(_SCHEMA_SQL)
        _write_all(conn, data)
    finally:
        conn.close()

    # 备份 JSON
    bak = js.with_suffix(".json.bak")
    js.replace(bak)


def _load_json(agent_id: str) -> Dict[str, Any]:
    """仅用于迁移的 JSON 加载。"""
    path = _json_path(agent_id)
    if not path.is_file():
        return {"version": 4, "projects": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": 4, "projects": []}
    if not isinstance(raw, dict):
        return {"version": 4, "projects": []}
    items = raw.get("projects")
    if not isinstance(items, list):
        items = []
    return {"version": int(raw.get("version") or 1), "projects": items}


def _write_all(conn: sqlite3.Connection, data: Dict[str, Any]) -> None:
    """将迁移后的全量数据写入 SQLite（迁移专用）。"""
    for proj in data.get("projects", []):
        if not isinstance(proj, dict):
            continue
        conn.execute(
            """INSERT OR REPLACE INTO projects (id, name, path, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                str(proj.get("id", "")),
                str(proj.get("name", "")),
                str(proj.get("path", "")),
                str(proj.get("created_at", "")),
                str(proj.get("updated_at", "")),
            ),
        )
        sess_map = proj.get("sessions")
        if isinstance(sess_map, dict):
            for sid, smeta in sess_map.items():
                if not isinstance(smeta, dict):
                    smeta = {}
                conn.execute(
                    """INSERT OR REPLACE INTO session_meta
                       (project_id, session_id, display_title, updated_at, message_count, preview, channel)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(proj.get("id", "")),
                        str(sid),
                        str(smeta.get("display_title", "")),
                        str(smeta.get("updated_at", "")),
                        int(smeta.get("message_count", 0)),
                        str(smeta.get("preview", "")),
                        str(smeta.get("channel", "")),
                    ),
                )
    conn.commit()


# ── 迁移逻辑（v1→v4，与 JSON 版一致，仅用于迁移路径）─────────────

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    """逐版本迁移到最新格式（仅用于 JSON→SQLite 迁移路径）。"""
    ver = int(data.get("version") or 1)
    if ver < 2:
        for r in data.get("projects", []):
            if isinstance(r, dict) and "sessions" not in r:
                r["sessions"] = []
        ver = 2
        data["version"] = 2
    if ver < 3:
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


# ── 项目 CRUD ────────────────────────────────────────────────────


def list_projects(agent_id: str, *, include_virtual: bool = True) -> List[Dict[str, Any]]:
    """返回 agent 的所有项目列表。"""
    conn = _get_conn(agent_id)
    if include_virtual:
        cur = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC")
    else:
        cur = conn.execute(
            "SELECT * FROM projects WHERE id != ? ORDER BY updated_at DESC",
            (UNASSIGNED_PROJECT_ID,),
        )
    return [dict(r) for r in cur.fetchall()]


def get_project(agent_id: str, project_id: str) -> Optional[Dict[str, Any]]:
    pid = (project_id or "").strip()
    if not pid:
        return None
    conn = _get_conn(agent_id)
    cur = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,))
    r = cur.fetchone()
    return dict(r) if r else None


def create_project(agent_id: str, name: str, path: str = "") -> Dict[str, Any]:
    n = (name or "").strip()
    if not n:
        raise ValueError("project name required")
    conn = _get_conn(agent_id)
    now = _utc_iso()
    row = {
        "id": uuid.uuid4().hex[:12],
        "name": n,
        "path": (path or "").strip(),
        "created_at": now,
        "updated_at": now,
    }
    conn.execute(
        "INSERT INTO projects (id, name, path, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (row["id"], row["name"], row["path"], row["created_at"], row["updated_at"]),
    )
    conn.commit()
    return row


def delete_project(agent_id: str, project_id: str) -> bool:
    pid = (project_id or "").strip()
    if not pid or pid == UNASSIGNED_PROJECT_ID:
        return False
    conn = _get_conn(agent_id)
    cur = conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
    conn.commit()
    return cur.rowcount > 0


def rename_project(agent_id: str, project_id: str, new_name: str) -> bool:
    pid = (project_id or "").strip()
    nn = (new_name or "").strip()
    if not pid or not nn:
        return False
    conn = _get_conn(agent_id)
    now = _utc_iso()
    cur = conn.execute(
        "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?",
        (nn, now, pid),
    )
    conn.commit()
    return cur.rowcount > 0


def update_project_path(agent_id: str, project_id: str, path: str) -> bool:
    pid = (project_id or "").strip()
    if not pid:
        return False
    conn = _get_conn(agent_id)
    now = _utc_iso()
    cur = conn.execute(
        "UPDATE projects SET path = ?, updated_at = ? WHERE id = ?",
        ((path or "").strip(), now, pid),
    )
    conn.commit()
    return cur.rowcount > 0


# ── 会话注册 ─────────────────────────────────────────────────────


def register_session(
    agent_id: str,
    project_id: str,
    session_id: str,
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """将会话注册到项目，同时缓存元信息（幂等）。

    如果会话已存在于其他项目，自动从旧项目移除（迁移到新项目）。
    如果 ``project_id`` 为空，不注册（空会话等发消息后再注册）。
    ``meta`` 可选，包含 ``display_title``、``updated_at``、``message_count``、
    ``preview``、``channel``、``context_usage`` 等。
    """
    pid = (project_id or "").strip()
    if not pid:
        return False
    sid = (session_id or "").strip()
    if not sid:
        return False
    conn = _get_conn(agent_id)
    now = _utc_iso()
    m = dict(meta or {})

    # 序列化 context_usage（dict → JSON）
    cu = m.get("context_usage")
    cu_json = json.dumps(cu, ensure_ascii=False) if isinstance(cu, dict) else "{}"

    with conn:  # 事务
        # 从其他项目移除
        conn.execute(
            "DELETE FROM session_meta WHERE session_id = ? AND project_id != ?",
            (sid, pid),
        )
        # 插入或更新
        conn.execute(
            """INSERT OR REPLACE INTO session_meta
               (project_id, session_id, display_title, updated_at, message_count,
                preview, channel, context_usage)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pid,
                sid,
                str(m.get("display_title", "")),
                str(m.get("updated_at", now)),
                int(m.get("message_count", 0)),
                str(m.get("preview", "")),
                str(m.get("channel", "")),
                cu_json,
            ),
        )
        # 更新项目时间戳
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE id = ?",
            (now, pid),
        )
    return True


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
    conn = _get_conn(agent_id)
    now = _utc_iso()

    # 检查会话是否存在
    cur = conn.execute(
        "SELECT 1 FROM session_meta WHERE project_id = ? AND session_id = ?",
        (pid, sid),
    )
    if not cur.fetchone():
        return False

    # 构造 SET 子句（只更新 meta 中提供的字段）
    field_map: Dict[str, Any] = {}
    for key in ("display_title", "updated_at", "message_count", "preview", "channel"):
        if key in meta:
            field_map[key] = meta[key]
    # 单独处理 context_usage（需要序列化）
    cu = meta.get("context_usage")
    if isinstance(cu, dict):
        field_map["context_usage"] = json.dumps(cu, ensure_ascii=False)
    elif cu is not None:
        field_map["context_usage"] = str(cu)

    if not field_map:
        return False

    sets = ", ".join(f"{k} = ?" for k in field_map)
    vals = list(field_map.values())
    conn.execute(
        f"UPDATE session_meta SET {sets} WHERE project_id = ? AND session_id = ?",
        (*vals, pid, sid),
    )
    conn.execute(
        "UPDATE projects SET updated_at = ? WHERE id = ?",
        (now, pid),
    )
    conn.commit()
    return True


def unregister_session(agent_id: str, project_id: str, session_id: str) -> bool:
    """从项目的 sessions 中移除会话。"""
    pid = (project_id or "").strip()
    if not pid:
        pid = UNASSIGNED_PROJECT_ID
    sid = (session_id or "").strip()
    if not sid:
        return False
    conn = _get_conn(agent_id)
    now = _utc_iso()

    with conn:
        conn.execute(
            "DELETE FROM session_meta WHERE project_id = ? AND session_id = ?",
            (pid, sid),
        )
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE id = ?",
            (now, pid),
        )
    return True


# ── 查询 ─────────────────────────────────────────────────────────


def list_project_session_ids(agent_id: str, project_id: str) -> List[str]:
    """返回项目下注册的所有会话 ID。"""
    conn = _get_conn(agent_id)
    cur = conn.execute(
        "SELECT session_id FROM session_meta WHERE project_id = ? ORDER BY updated_at DESC",
        (project_id,),
    )
    return [str(r["session_id"]) for r in cur.fetchall()]


def list_project_sessions_meta(
    agent_id: str, project_id: str,
) -> Dict[str, Dict[str, Any]]:
    """返回项目下所有会话的缓存元信息 dict[session_id, meta]。"""
    conn = _get_conn(agent_id)
    cur = conn.execute(
        """SELECT session_id, display_title, updated_at, message_count, preview, channel, context_usage
           FROM session_meta WHERE project_id = ? ORDER BY updated_at DESC""",
        (project_id,),
    )
    out: Dict[str, Dict[str, Any]] = {}
    for r in cur.fetchall():
        row = dict(r)
        # 反序列化 context_usage
        raw_cu = row.pop("context_usage", None)
        if isinstance(raw_cu, str) and raw_cu.strip():
            try:
                row["context_usage"] = json.loads(raw_cu)
            except (json.JSONDecodeError, TypeError):
                pass
        out[str(r["session_id"])] = row
    return out


def get_session_meta(
    agent_id: str, project_id: str, session_id: str,
) -> Optional[Dict[str, Any]]:
    """获取 registry 中缓存的单个会话元信息。"""
    conn = _get_conn(agent_id)
    cur = conn.execute(
        """SELECT session_id, display_title, updated_at, message_count, preview, channel, context_usage
           FROM session_meta WHERE project_id = ? AND session_id = ?""",
        (project_id, session_id),
    )
    r = cur.fetchone()
    if not r:
        return None
    row = dict(r)
    # 反序列化 context_usage
    raw_cu = row.pop("context_usage", None)
    if isinstance(raw_cu, str) and raw_cu.strip():
        try:
            row["context_usage"] = json.loads(raw_cu)
        except (json.JSONDecodeError, TypeError):
            pass
    return row


def list_unassigned_session_ids(agent_id: str) -> List[str]:
    """返回虚拟项目（未分类）下的会话 ID。"""
    return list_project_session_ids(agent_id, UNASSIGNED_PROJECT_ID)


# ── 项目路径 ─────────────────────────────────────────────────────


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
