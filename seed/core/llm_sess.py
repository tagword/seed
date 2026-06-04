"""Chat session persistence — uses models.Session (same shape as TurnLoopEngine).

存储策略：
  - 无项目关联的会话 → agents/<agent_id>/sessions/<id>.json
  - 有项目关联的会话 → agents/<agent_id>/projects-data/<project-id>/sessions/<id>.json

旧布局 ``sessions/llm_sessions/`` 可用 ``migrate_legacy_agent_sessions()`` 一次性迁移；未迁移前仍只读回退。
"""
from __future__ import annotations


import contextlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from seed.core import env_access as _ea
from seed.core.models import Session
from seed.core.paths import agent_home, agent_id_default, agent_project_data_subdir
from seed.core.proj_reg import (
    UNASSIGNED_PROJECT_ID,
    get_session_meta,
    list_project_session_ids,
    list_project_sessions_meta,
    list_projects,
    list_unassigned_session_ids,
    register_session,
    unregister_session,
    update_session_meta,
)
from seed.core.sess_store import SessionStore

_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def agent_sessions_dir(agent_id: Optional[str] = None) -> Path:
    """
    Agent chat session storage root (non-project sessions).

    Priority:
    - ``SEED_AGENT_SESSIONS_DIR`` / ``SEED_LLM_SESSIONS_DIR`` (explicit override)
    - ``agents/<agent_id>/sessions`` under the multi-agent layout.
    """
    raw = _ea.pick_nonempty(*_ea.AGENT_CHAT_SESSIONS_DIR)
    if raw:
        return Path(raw).expanduser().resolve()
    aid = (agent_id or "").strip() or agent_id_default()
    d = (agent_home(aid) / "sessions").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _legacy_llm_sessions_subdir(agent_id: Optional[str] = None) -> Path:
    """Pre-migration layout: ``agents/<id>/sessions/llm_sessions`` (read + list fallback)."""
    aid = (agent_id or "").strip() or agent_id_default()
    return (agent_home(aid) / "sessions" / "llm_sessions").resolve()


def _default_session_search_dirs(agent_id: Optional[str] = None) -> List[Path]:
    """Directories to scan for non-project session JSON (new first, then legacy)."""
    aid = (agent_id or "").strip() or agent_id_default()
    primary = agent_sessions_dir(aid)
    legacy = _legacy_llm_sessions_subdir(aid)
    out: List[Path] = [primary]
    if legacy != primary and legacy.is_dir():
        out.append(legacy)
    return out


def _remove_legacy_session_copy(handle: str, agent_id: Optional[str] = None) -> None:
    """After persist to the new layout, drop duplicate JSON under ``sessions/llm_sessions/``."""
    slug = _safe_session_filename(handle)
    legacy = _legacy_llm_sessions_subdir(agent_id) / f"{slug}.json"
    if not legacy.is_file():
        return
    primary = agent_sessions_dir(agent_id) / f"{slug}.json"
    if primary.is_file() and primary.resolve() != legacy.resolve():
        with contextlib.suppress(OSError):
            legacy.unlink()


_LEGACY_SUBDIRS = ("archived", "attachments", "_artifacts", "_user_inputs")
_OBSOLETE_SUBDIRS = ("_transcript",)  # removed JSONL ledger


def _merge_tree_into_primary(src: Path, dest: Path, *, dry_run: bool) -> int:
    """Merge ``src`` into ``dest`` (files only; newer mtime wins on name clash). Returns move count."""
    if not src.is_dir():
        return 0
    n = 0
    dest.mkdir(parents=True, exist_ok=True)
    for root, _dirs, files in os.walk(src):
        root_p = Path(root)
        rel = root_p.relative_to(src)
        out_dir = dest / rel
        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            s = root_p / name
            d = out_dir / name
            if d.is_file():
                if s.stat().st_mtime > d.stat().st_mtime:
                    if not dry_run:
                        d.unlink()
                        shutil.move(str(s), str(d))
                    n += 1
                continue
            if not dry_run:
                shutil.move(str(s), str(d))
            n += 1
    return n


def migrate_legacy_agent_sessions(
    agent_id: Optional[str] = None,
    *,
    dry_run: bool = False,
    remove_jsonl_ledger: bool = True,
) -> Dict[str, Any]:
    """
    Move ``agents/<id>/sessions/llm_sessions/`` → flat ``agents/<id>/sessions/``.

    - Session ``*.json`` and subdirs ``archived``, ``attachments``, ``_artifacts``, ``_user_inputs``
    - Drops obsolete ``_transcript/`` JSONL (feature removed)
    - Removes empty legacy directory when done
    """
    aid = (agent_id or "").strip() or agent_id_default()
    primary = agent_sessions_dir(aid)
    legacy = _legacy_llm_sessions_subdir(aid)
    stats: Dict[str, Any] = {
        "agent_id": aid,
        "dry_run": dry_run,
        "legacy_dir": str(legacy),
        "primary_dir": str(primary),
        "moved_json": 0,
        "skipped_json": 0,
        "merged_files": 0,
        "removed_subdirs": [],
        "legacy_removed": False,
    }
    if not legacy.is_dir():
        return stats

    for path in sorted(legacy.glob("*.json")):
        dest = primary / path.name
        if dest.is_file():
            stats["skipped_json"] += 1
            if not dry_run and path.stat().st_mtime > dest.stat().st_mtime:
                dest.unlink()
                shutil.move(str(path), str(dest))
                stats["moved_json"] += 1
                stats["skipped_json"] -= 1
            continue
        if not dry_run:
            shutil.move(str(path), str(dest))
        stats["moved_json"] += 1

    for name in _LEGACY_SUBDIRS:
        src = legacy / name
        if src.is_dir():
            n = _merge_tree_into_primary(src, primary / name, dry_run=dry_run)
            stats["merged_files"] += n
            if not dry_run:
                shutil.rmtree(src, ignore_errors=True)

    if remove_jsonl_ledger:
        for base in (legacy, primary):
            for name in _OBSOLETE_SUBDIRS:
                p = base / name
                if p.is_dir():
                    stats["removed_subdirs"].append(str(p))
                    if not dry_run:
                        shutil.rmtree(p, ignore_errors=True)

    if not dry_run and legacy.is_dir():
        with contextlib.suppress(OSError):
            shutil.rmtree(legacy, ignore_errors=True)
        stats["legacy_removed"] = not legacy.exists()
    return stats


def _project_sessions_dir(project_id: str, agent_id: Optional[str] = None) -> Path:
    """项目关联会话的存储目录。"""
    aid = (agent_id or "").strip() or agent_id_default()
    return agent_project_data_subdir(aid, project_id, "sessions")


def _safe_session_filename(session_id: str) -> str:
    s = _SAFE_RE.sub("_", session_id).strip("._-") or "session"
    return s[:128]


def _session_store(agent_id: Optional[str] = None) -> SessionStore:
    return SessionStore(str(agent_sessions_dir(agent_id)))


def _project_session_store(project_id: str, agent_id: Optional[str] = None) -> SessionStore:
    """创建指向项目会话目录的 SessionStore。"""
    return SessionStore(str(_project_sessions_dir(project_id, agent_id)))


def _resolve_session_store(session: Session, agent_id: Optional[str] = None) -> SessionStore:
    """根据 session 的 metadata.project_id 解析正确的 SessionStore。"""
    pid = ""
    if isinstance(session.metadata, dict):
        pid = str(session.metadata.get("project_id") or "").strip()
    if pid:
        return _project_session_store(pid, agent_id)
    return _session_store(agent_id)


def _session_from_stored_json(data: Dict[str, Any], slug: str, handle: str) -> Session:
    """Build ``models_pkg.Session`` from on-disk JSON (no ``SessionStore._dict_to_session``).

    Web UI session history only needs ``messages``; ``turns`` are omitted to avoid brittle deserialization.
    """
    msgs = data.get("messages")
    if not isinstance(msgs, list):
        msgs = []
    dict_msgs = [m for m in msgs if isinstance(m, dict)]
    dict_msgs = _scrub_history_for_model(dict_msgs)

    sess_id = str(data.get("id") or slug)
    sid = data.get("session_id") or data.get("name") or handle
    name = str(data.get("name") or (sid if isinstance(sid, str) else handle))
    cfg = data.get("config") if isinstance(data.get("config"), dict) else {}
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    ca = str(data.get("created_at") or "")
    ua = str(data.get("updated_at") or ca)

    return Session(
        id=sess_id,
        name=name,
        created_at=ca,
        updated_at=ua,
        messages=list(dict_msgs),
        turns=[],
        config=dict(cfg),
        metadata=dict(meta),
    )


def _scrub_history_for_model(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip <think>/chain-of-thought markup from assistant content before it is
    replayed to the model.

    We keep the JSON on disk untouched (that is the forensic ground truth) but
    never let stray chain-of-thought leak back into the next turn's context,
    because that caused self-reinforcing exploratory repetition (same file
    sliced 5 different ways, same port re-queried, etc.).
    """
    try:
        from seed.core.agent_runtime import strip_inline_tool_markup_from_assistant_text as _strip
    except Exception:  # pragma: no cover - defensive; runtime always has this
        return msgs
    out: List[Dict[str, Any]] = []
    for m in msgs:
        if not isinstance(m, dict):
            out.append(m)
            continue
        if m.get("role") != "assistant":
            out.append(m)
            continue
        c = m.get("content")
        if isinstance(c, str) and c:
            cleaned = _strip(c)
            if cleaned != c:
                m = {**m, "content": cleaned}
        out.append(m)
    return out


def _try_load_from_store(store: SessionStore, handle: str) -> Optional[Session]:
    """尝试从指定的 SessionStore 加载会话。"""
    slug = _safe_session_filename(handle)
    path = store.base_path / f"{slug}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    msgs = data.get("messages")
    if not isinstance(msgs, list):
        return None

    if data.get("id") and all(isinstance(m, dict) for m in msgs):
        return _session_from_stored_json(data, slug, handle)

    return None


def load_chat_session_from_disk(
    handle: str,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Optional[Session]:
    """
    Load Session JSON from disk.

    查找顺序（v2）：
    1. 如果指定了 project_id → 先从 registry 定位，再从项目会话目录加载
    2. 如果没找到或没指定 → 从 registry 搜索所有项目
    3. 回退：从默认 agent sessions 目录扫描（兼容旧数据/未注册会话）

    ``handle`` is the user-visible key (CLI --session or browser session_id).
    """
    aid = (agent_id or "").strip() or agent_id_default()
    pid = (project_id or "").strip() if project_id else ""

    # 优先从 registry 定位
    reg_path = _session_file_path_from_registry(handle, aid, pid)
    if reg_path is not None:
        store = SessionStore(str(reg_path.parent))
        found = _try_load_from_store(store, handle)
        if found is not None:
            return found

    # 如果指定了项目，直接查项目目录
    if pid:
        pstore = _project_session_store(pid, aid)
        found = _try_load_from_store(pstore, handle)
        if found is not None:
            return found

    # 回退：默认目录
    for d in _default_session_search_dirs(aid):
        found = _try_load_from_store(SessionStore(str(d)), handle)
        if found is not None:
            return found

    # 回退：搜索所有项目目录
    if not pid:
        for proj in list_projects(aid):
            pstore = _project_session_store(proj["id"], aid)
            found = _try_load_from_store(pstore, handle)
            if found is not None:
                return found

    return None


def _scan_session_dir(
    d: Path,
    *,
    limit: int,
    filter_by_project: bool,
    filter_project_id: str,
) -> List[Dict[str, Any]]:
    """扫描单个目录的会话 JSON，返回元信息列表。"""
    if not d.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for path in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if len(rows) >= limit:
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        msgs = data.get("messages")
        n_msg = _count_user_messages(msgs)
        name = data.get("name")
        sid = str(name).strip() if isinstance(name, str) and name.strip() else path.stem
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        cfg = data.get("config") if isinstance(data.get("config"), dict) else {}
        sess_proj = str(meta.get("project_id") or "").strip()
        if filter_by_project:
            want = (filter_project_id or "").strip()
            if want:
                if sess_proj != want:
                    continue
            else:
                if sess_proj:
                    continue
        preview = ""
        if isinstance(msgs, list):
            for m in reversed(msgs):
                if isinstance(m, dict) and m.get("role") == "user":
                    preview = str(m.get("content") or "")[:120]
                    break
        display_title = _session_display_title(preview, meta)
        channel = _infer_channel(sid, meta, cfg)
        rows.append(
            {
                "session_id": sid,
                "file_id": str(data.get("id") or path.stem),
                "updated_at": str(data.get("updated_at") or ""),
                "message_count": n_msg,
                "preview": preview,
                "display_title": display_title,
                "channel": channel,
                "project_id": sess_proj,
            }
        )
    return rows


def _merge_session_meta_rows(rows: List[Dict[str, Any]], *, limit: int) -> List[Dict[str, Any]]:
    """Dedupe by session_id; keep row with newer updated_at."""
    by_sid: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        sid = str(row.get("session_id") or "").strip()
        if not sid:
            continue
        prev = by_sid.get(sid)
        if prev is None or str(row.get("updated_at") or "") >= str(prev.get("updated_at") or ""):
            by_sid[sid] = row
    merged = list(by_sid.values())
    merged.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return merged[:limit]


def _scan_default_agent_session_dirs(
    agent_id: str,
    *,
    limit: int,
    filter_by_project: bool,
    filter_project_id: str,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for d in _default_session_search_dirs(agent_id):
        if len(rows) >= limit:
            break
        rows.extend(
            _scan_session_dir(
                d,
                limit=limit,
                filter_by_project=filter_by_project,
                filter_project_id=filter_project_id,
            )
        )
    return _merge_session_meta_rows(rows, limit=limit)


def _session_file_path_from_registry(
    session_id: str,
    agent_id: str,
    project_id: str = "",
) -> Optional[Path]:
    """根据 registry 中的会话注册信息，返回会话文件路径。

    优先从 registry 定位，回退到目录扫描。
    """
    slug = _safe_session_filename(session_id)
    aid = (agent_id or "").strip() or agent_id_default()
    pid = (project_id or "").strip()

    # 如果指定了 project_id，先查该项目的注册列表
    if pid:
        registered = list_project_session_ids(aid, pid)
        if session_id in registered:
            p = _project_sessions_dir(pid, aid) / f"{slug}.json"
            if p.is_file():
                return p
        # 注册了但文件不存在，也尝试直接读目录
        p = _project_sessions_dir(pid, aid) / f"{slug}.json"
        if p.is_file():
            return p
        return None

    # 无 project_id：扫描所有项目注册表
    for proj in list_projects(aid):
        registered = list_project_session_ids(aid, proj["id"])
        if session_id in registered:
            p = _project_sessions_dir(proj["id"], aid) / f"{slug}.json"
            if p.is_file():
                return p
    return None


def _load_session_json(
    path: Path,
) -> Optional[Dict[str, Any]]:
    """安全加载会话 JSON 文件。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _session_meta_from_json(
    path: Path,
    data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """从 JSON 数据提取会话元信息行。"""
    if data is None:
        data = _load_session_json(path)
    if data is None:
        return None
    msgs = data.get("messages")
    n_msg = _count_user_messages(msgs)
    name = data.get("name")
    sid = str(name).strip() if isinstance(name, str) and name.strip() else path.stem
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    cfg = data.get("config") if isinstance(data.get("config"), dict) else {}
    preview = ""
    if isinstance(msgs, list):
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "user":
                preview = str(m.get("content") or "")[:120]
                break
    display_title = _session_display_title(preview, meta)
    channel = _infer_channel(sid, meta, cfg)
    return {
        "session_id": sid,
        "file_id": str(data.get("id") or path.stem),
        "updated_at": str(data.get("updated_at") or ""),
        "message_count": n_msg,
        "preview": preview,
        "display_title": display_title,
        "channel": channel,
        "project_id": str(meta.get("project_id") or "").strip(),
    }


def list_stored_sessions_meta(
    *,
    limit: int = 100,
    agent_id: Optional[str] = None,
    filter_by_project: bool = False,
    filter_project_id: str = "",
) -> List[Dict[str, Any]]:
    """
    Recent chat sessions from disk (for Web UI / tooling).

    查询策略（v4）：
      - 所有会话都通过 registry 管理（含虚拟项目 __unassigned__）
      - 优先从 registry 缓存读取元信息（无需加载 JSON 文件）
      - 缓存缺失时回退加载 JSON 文件并自动补全缓存

    When ``filter_by_project`` is True:
      - If ``filter_project_id`` is set → list sessions for that project from registry cache
      - If ``filter_project_id`` is empty → list sessions under virtual __unassigned__ project
    When ``filter_by_project`` is False:
      - List all sessions from all projects (including __unassigned__)
    """
    aid = (agent_id or "").strip() or agent_id_default()
    lim = max(1, min(int(limit), 500))

    if filter_by_project:
        want = (filter_project_id or "").strip()
        if want:
            return _list_sessions_for_project_from_cache(aid, want, lim)
        # 未分类 = 虚拟项目 __unassigned__
        return _list_sessions_for_project_from_cache(aid, UNASSIGNED_PROJECT_ID, lim)

    # 全部会话：所有项目（含虚拟项目）
    rows: List[Dict[str, Any]] = []
    seen_sids: set[str] = set()

    for proj in list_projects(aid):
        if len(rows) >= lim:
            break
        for sid, cached_meta in list_project_sessions_meta(aid, proj["id"]).items():
            if len(rows) >= lim:
                break
            if sid in seen_sids:
                continue
            seen_sids.add(sid)
            row = _meta_row_from_cache(sid, cached_meta, proj["id"])
            if row is not None:
                rows.append(row)

    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return rows[:lim]


def _meta_row_from_cache(
    session_id: str,
    cached_meta: Dict[str, Any],
    project_id: str,
) -> Optional[Dict[str, Any]]:
    """从 registry 缓存构建会话元信息行。缓存缺失时回退加载 JSON。"""
    if cached_meta and cached_meta.get("display_title"):
        return {
            "session_id": session_id,
            "file_id": session_id,
            "updated_at": str(cached_meta.get("updated_at") or ""),
            "message_count": int(cached_meta.get("message_count") or 0),
            "preview": str(cached_meta.get("preview") or "")[:120],
            "display_title": str(cached_meta.get("display_title") or "未命名对话")[:80],
            "channel": str(cached_meta.get("channel") or "Web 聊天")[:48],
            "project_id": project_id,
        }
    # 缓存缺失，回退加载 JSON
    meta = _load_session_meta_by_id(session_id, None, project_id)
    if meta is not None:
        # 尝试更新缓存
        try:
            from seed.core.proj_reg import update_session_meta
            update_session_meta(
                agent_id_default(), project_id, session_id,
                {
                    "display_title": meta.get("display_title", ""),
                    "updated_at": meta.get("updated_at", ""),
                    "message_count": meta.get("message_count", 0),
                    "preview": meta.get("preview", ""),
                    "channel": meta.get("channel", ""),
                },
            )
        except Exception:
            pass
    return meta


def _list_sessions_for_project_from_cache(
    agent_id: str, project_id: str, limit: int,
) -> List[Dict[str, Any]]:
    """从 registry 缓存列出项目下的会话（无需加载 JSON 文件）。"""
    aid = (agent_id or "").strip() or agent_id_default()
    pid = (project_id or "").strip()
    rows: List[Dict[str, Any]] = []
    for sid, cached_meta in list_project_sessions_meta(aid, pid).items():
        if len(rows) >= limit:
            break
        row = _meta_row_from_cache(sid, cached_meta, pid)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return rows[:limit]


def _list_unassigned_sessions(
    agent_id: str, limit: int,
    exclude_sids: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """列出未注册到任何项目的会话（回退扫描目录）。"""
    aid = (agent_id or "").strip() or agent_id_default()
    assigned = list_unassigned_session_ids(aid)
    exclude = set(exclude_sids or [])
    exclude.update(assigned)

    rows: List[Dict[str, Any]] = []
    for d in _default_session_search_dirs(aid):
        if len(rows) >= limit:
            break
        for path in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if len(rows) >= limit:
                break
            data = _load_session_json(path)
            if data is None:
                continue
            name = data.get("name")
            sid = str(name).strip() if isinstance(name, str) and name.strip() else path.stem
            if sid in exclude:
                continue
            meta = _session_meta_from_json(path, data)
            if meta is not None:
                rows.append(meta)
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return rows[:limit]


def _load_session_meta_by_id(
    session_id: str, agent_id: str, project_id: str = "",
) -> Optional[Dict[str, Any]]:
    """按 session_id 和 project_id 加载会话元信息。"""
    path = _session_file_path_from_registry(session_id, agent_id, project_id)
    if path is not None:
        return _session_meta_from_json(path)
    # 回退：直接扫描目录
    slug = _safe_session_filename(session_id)
    aid = (agent_id or "").strip() or agent_id_default()
    pid = (project_id or "").strip()
    if pid:
        p = _project_sessions_dir(pid, aid) / f"{slug}.json"
        if p.is_file():
            return _session_meta_from_json(p)
    for d in _default_session_search_dirs(aid):
        p = d / f"{slug}.json"
        if p.is_file():
            return _session_meta_from_json(p)
    return None


def list_stored_session_ids(
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> List[str]:
    """列出会话 ID。

    如果指定 project_id，则从 registry 读取该项目的会话列表。
    否则扫描所有目录（兼容旧数据）。
    """
    aid = (agent_id or "").strip() or agent_id_default()
    pid = (project_id or "").strip() if project_id else ""

    if pid:
        return list_project_session_ids(aid, pid)

    # 全部会话：从 registry 收集 + 扫描未注册的
    seen: set[str] = set()
    result: List[str] = []

    for proj in list_projects(aid):
        for sid in list_project_session_ids(aid, proj["id"]):
            if sid not in seen:
                seen.add(sid)
                result.append(sid)

    # 回退扫描未注册的
    for d in _default_session_search_dirs(aid):
        if d.is_dir():
            for path in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    name = data.get("name")
                    sid = data.get("session_id")
                    oid = data.get("id")
                    if isinstance(name, str) and name.strip():
                        key = name.strip()
                    elif isinstance(sid, str) and sid.strip():
                        key = sid.strip()
                    elif isinstance(oid, str) and oid.strip():
                        key = oid.strip()
                    else:
                        key = path.stem
                except (json.JSONDecodeError, OSError):
                    key = path.stem
                if key in seen:
                    continue
                seen.add(key)
                result.append(key)
    return result


def _strip_non_leading_system(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop system roles after index 0 — strict chat servers allow only one leading system."""
    if not msgs:
        return msgs
    out: List[Dict[str, Any]] = [msgs[0]]
    for m in msgs[1:]:
        if isinstance(m, dict) and m.get("role") == "system":
            continue
        out.append(m)
    return out


def merge_fresh_system(
    loaded: List[Dict[str, Any]],
    fresh_system: str,
) -> List[Dict[str, Any]]:
    """Use latest config/system text while restoring message tail."""
    if not loaded:
        return [{"role": "system", "content": fresh_system}]
    loaded = _strip_non_leading_system(loaded)
    if loaded and loaded[0].get("role") == "system":
        return [{"role": "system", "content": fresh_system}] + loaded[1:]
    return [{"role": "system", "content": fresh_system}] + loaded


def load_or_create_chat_session(
    handle: str,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Session:
    found = load_chat_session_from_disk(handle, agent_id, project_id)
    if found is not None:
        # 🐛 修复：如果传入了 project_id 但会话 metadata 中缺少或不同，
        # 则更新 project_id，确保后续 persist 存到正确的项目目录。
        if project_id:
            md = found.metadata
            if not isinstance(md, dict):
                md = {}
            cur_pid = str(md.get("project_id") or "").strip()
            if cur_pid != project_id:
                md["project_id"] = project_id
                found.metadata = md
        return found
    sess = Session.for_llm_handle(handle, _safe_session_filename(handle))
    if project_id:
        if not isinstance(sess.metadata, dict):
            sess.metadata = {}
        sess.metadata["project_id"] = project_id
    return sess


def read_stored_session_project_id(handle: str, agent_id: Optional[str] = None) -> str:
    s = load_chat_session_from_disk(handle, agent_id)
    if s is None:
        return ""
    md = s.metadata if isinstance(s.metadata, dict) else {}
    return str(md.get("project_id") or "").strip()


def _extract_session_meta(session: Session) -> Dict[str, Any]:
    """从 Session 对象提取用于 registry 缓存的元信息。"""
    handle = str(session.name or session.id or "").strip()
    meta: Dict[str, Any] = {
        "updated_at": str(session.updated_at or ""),
        "message_count": _count_user_messages(session.messages),
    }
    if isinstance(session.metadata, dict):
        dt = session.metadata.get("display_title")
        if isinstance(dt, str) and dt.strip():
            meta["display_title"] = dt.strip()[:80]
        channel = session.metadata.get("channel") or session.metadata.get("source")
        if isinstance(channel, str) and channel.strip():
            meta["channel"] = channel.strip()[:48]
    if not meta.get("display_title"):
        # 从最后一条 user 消息提取预览
        preview = ""
        if isinstance(session.messages, list):
            for m in reversed(session.messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    preview = str(m.get("content") or "")[:120]
                    break
        meta["preview"] = preview
        meta["display_title"] = _session_display_title(preview, session.metadata if isinstance(session.metadata, dict) else {})
    else:
        # 也尝试提取 preview
        if isinstance(session.messages, list):
            for m in reversed(session.messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    meta["preview"] = str(m.get("content") or "")[:120]
                    break
    return meta


def persist_chat_session(session: Session, agent_id: Optional[str] = None) -> Path:
    """持久化会话。

    根据 session.metadata.project_id 自动路由到：
      - 有项目 → projects-data/<project-id>/sessions/
      - 无项目 → agents/<id>/sessions/

    写入后将会话 ID 及元信息缓存到项目 registry 中（如有 project_id）。
    """
    session.touch_updated()
    store = _resolve_session_store(session, agent_id)
    store.save_session(session)
    out = store.base_path / f"{session.id}.json"
    handle = str(session.name or session.id or "").strip()
    if handle:
        _remove_legacy_session_copy(handle, agent_id)
    # 注册到项目 registry 并缓存元信息
    # 无 project_id 的会话暂不注册（等发消息时 project_id 设好后再注册）
    pid = ""
    if isinstance(session.metadata, dict):
        pid = str(session.metadata.get("project_id") or "").strip()
    if pid and handle:
        sess_meta = _extract_session_meta(session)
        register_session(agent_id or agent_id_default(), pid, handle, sess_meta)
    return out


def _session_json_path(handle: str) -> Path:
    slug = _safe_session_filename(handle)
    return agent_sessions_dir() / f"{slug}.json"


def _find_session_file(
    handle: str,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Optional[Path]:
    """查找会话文件路径，优先项目目录。"""
    slug = _safe_session_filename(handle)
    aid = (agent_id or "").strip() or agent_id_default()

    pid = (project_id or "").strip() if project_id else ""
    if pid:
        pdir = _project_sessions_dir(pid, aid)
        pp = pdir / f"{slug}.json"
        if pp.is_file():
            return pp

    for d in _default_session_search_dirs(aid):
        p = d / f"{slug}.json"
        if p.is_file():
            return p
    return None


def delete_stored_session(
    handle: str,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> bool:
    """Remove persisted chat session JSON for this session id.

    同时从项目 registry 中注销该会话。

    Bug fix: 当 project_id 为空时，会话可能同时存在于主目录（stub, 298B）
    和 projects-data/<pid>/sessions/（真实数据）中。此函数会一并清理干净。
    """
    from seed.core.proj_reg import list_projects, unregister_session

    aid = (agent_id or "").strip() or agent_id_default()
    pid = (project_id or "").strip() if project_id else ""
    slug = _safe_session_filename(handle)
    deleted_any = False

    # ── 策略：始终查找并删除所有副本（主目录 + 项目目录） ──

    # 1) 如果指定了 project_id：只删该项目
    if pid:
        path = _find_session_file(handle, aid, project_id)
        if path is not None:
            try:
                path.unlink()
                deleted_any = True
            except OSError:
                return False
        # 即使文件不存在也要清理 registry（孤儿注册清理）
        if handle:
            unregister_session(aid, pid, handle)
            deleted_any = True
        # 清理 artifacts 和 attachments
        sess_root = agent_sessions_dir(aid)
        for subdir in ("_artifacts", "attachments"):
            target = sess_root / subdir / handle
            if target.is_dir():
                try:
                    shutil.rmtree(target)
                    deleted_any = True
                except OSError:
                    pass
        return deleted_any

    # 2) 未指定 project_id：遍历 registry 查找会话所属的项目
    for proj in list_projects(aid):
        proj_pid = str(proj.get("id") or "").strip()
        if not proj_pid:
            continue
        registered = proj.get("sessions", {})
        if handle not in registered:
            continue
        # 删除项目目录下的真实文件
        p = _project_sessions_dir(proj_pid, aid) / f"{slug}.json"
        if p.is_file():
            try:
                p.unlink()
                deleted_any = True
            except OSError:
                pass
        # 注销 registry
        unregister_session(aid, proj_pid, handle)
        deleted_any = True
        break  # 一个会话只属于一个项目

    # 3) 清理主目录的 stub（无论上面是否找到项目）
    for d in _default_session_search_dirs(aid):
        stub = d / f"{slug}.json"
        if stub.is_file():
            try:
                stub.unlink()
                deleted_any = True
            except OSError:
                pass

    # 4) 清理关联的 artifacts 和 attachments（使用原始 session_id 作为子目录名）
    sess_root = agent_sessions_dir(aid)
    for subdir in ("_artifacts", "attachments"):
        target = sess_root / subdir / handle
        if target.is_dir():
            try:
                shutil.rmtree(target)
                deleted_any = True
            except OSError:
                pass

    return deleted_any


def archive_stored_llm_session(
    handle: str,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> bool:
    """Move session JSON to ``archived/`` under its storage directory."""
    path = _find_session_file(handle, agent_id, project_id)
    if path is None:
        return False
    arch = path.parent / "archived"
    try:
        arch.mkdir(parents=True, exist_ok=True)
        dest = arch / path.name
        if dest.is_file():
            dest.unlink()
        path.replace(dest)
    except OSError:
        return False
    return True


def rename_stored_llm_session(
    handle: str,
    new_title: str,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> bool:
    """Update display_title in session metadata and persist."""
    sess = load_chat_session_from_disk(handle, agent_id, project_id)
    if sess is None:
        return False
    if not isinstance(sess.metadata, dict):
        sess.metadata = {}
    sess.metadata["display_title"] = (new_title or "").strip()[:80] or "未命名对话"
    sess.metadata["display_title_source"] = "manual"
    try:
        persist_chat_session(sess, agent_id)
    except OSError:
        return False
    return True


def save_session_messages(
    session_id: str,
    messages: List[Dict[str, Any]],
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Path:
    """Upsert Session.messages and persist."""
    sess = load_chat_session_from_disk(session_id, agent_id, project_id)
    if sess is None:
        sess = Session.for_llm_handle(session_id, _safe_session_filename(session_id))
        if project_id:
            if not isinstance(sess.metadata, dict):
                sess.metadata = {}
            sess.metadata["project_id"] = project_id
    sess.messages = messages
    return persist_chat_session(sess, agent_id)


def load_session_messages(
    session_id: str,
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    sess = load_chat_session_from_disk(session_id, agent_id, project_id)
    if sess is None:
        return None
    return sess.messages


def _looks_like_cot_title(s: str) -> bool:
    """已写入 metadata 但实为思维链/英文 CoT 的标题，列表展示时回退到用户预览。"""
    t = (s or "").strip()
    if not t:
        return True
    low = t.lower()
    if "thinking process" in low or "analyze the" in low:
        return True
    if re.match(r"^\d+\.\s*\*+", t):
        return True
    if t.startswith("**") and "analyze" in low[:80]:
        return True
    return False


def _session_display_title(preview: str, metadata: Dict[str, Any]) -> str:
    dt = metadata.get("display_title")
    if isinstance(dt, str) and dt.strip():
        cand = dt.strip()[:80]
        if not _looks_like_cot_title(cand):
            return cand
    line = (preview or "").strip().replace("\n", " ")
    if line:
        return (line[:40] + "…") if len(line) > 40 else line
    return "未命名对话"


def _infer_channel(session_id: str, metadata: Dict[str, Any], config: Dict[str, Any]) -> str:
    ch = metadata.get("channel")
    if isinstance(ch, str) and ch.strip():
        return ch.strip()[:48]
    src = metadata.get("source")
    if isinstance(src, str) and src.strip():
        return src.strip()[:48]
    sid = (session_id or "").lower()
    cfg_kind = (config.get("kind") or "") if isinstance(config, dict) else ""
    if cfg_kind == "webhook" or "webhook" in sid:
        return "Webhook"
    return "Web 聊天"


def _count_user_messages(msgs: Any) -> int:
    if not isinstance(msgs, list):
        return 0
    return sum(1 for m in msgs if isinstance(m, dict) and m.get("role") == "user")


