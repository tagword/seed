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
from seed.core.proj_reg import list_projects
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

    查找顺序：
    1. 如果指定了 project_id → 先从项目会话目录找
    2. 如果没找到或没指定 → 从默认 agent sessions 目录找（含 legacy llm_sessions 回退）
    3. 如果还没找到且未指定 project_id → 搜索所有项目目录

    ``handle`` is the user-visible key (CLI --session or browser session_id).
    """
    aid = (agent_id or "").strip() or agent_id_default()

    # 如果指定了项目，先查项目目录
    pid = (project_id or "").strip() if project_id else ""
    if pid:
        pstore = _project_session_store(pid, aid)
        found = _try_load_from_store(pstore, handle)
        if found is not None:
            return found

    # 再查默认目录（新布局，再 legacy 子目录）
    for d in _default_session_search_dirs(aid):
        found = _try_load_from_store(SessionStore(str(d)), handle)
        if found is not None:
            return found

    # 如果未指定 project_id，搜索所有项目目录作为后备
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


def list_stored_sessions_meta(
    *,
    limit: int = 100,
    agent_id: Optional[str] = None,
    filter_by_project: bool = False,
    filter_project_id: str = "",
) -> List[Dict[str, Any]]:
    """
    Recent chat sessions from disk (for Web UI / tooling).

    When ``filter_by_project`` is True:
      - If ``filter_project_id`` is set → scan that project's session dir
      - If ``filter_project_id`` is empty → scan default (non-project) sessions
    When ``filter_by_project`` is False:
      - Scan default directory + all project directories
    """
    aid = (agent_id or "").strip() or agent_id_default()
    lim = max(1, min(int(limit), 500))

    if filter_by_project:
        want = (filter_project_id or "").strip()
        if want:
            pdir = _project_sessions_dir(want, aid)
            return _scan_session_dir(
                pdir,
                limit=lim,
                filter_by_project=True,
                filter_project_id=want,
            )
        return _scan_default_agent_session_dirs(
            aid,
            limit=lim,
            filter_by_project=True,
            filter_project_id="",
        )

    rows = _scan_default_agent_session_dirs(
        aid,
        limit=lim,
        filter_by_project=False,
        filter_project_id="",
    )

    if len(rows) >= lim:
        return rows[:lim]

    for proj in list_projects(aid):
        if len(rows) >= lim:
            break
        pid = proj["id"]
        pdir = _project_sessions_dir(pid, aid)
        rows.extend(
            _scan_session_dir(
                pdir,
                limit=lim,
                filter_by_project=False,
                filter_project_id="",
            )
        )
    return _merge_session_meta_rows(rows, limit=lim)


def list_stored_session_ids(
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> List[str]:
    """列出会话 ID。如果指定 project_id，则只扫描项目目录。"""
    aid = (agent_id or "").strip() or agent_id_default()

    seen: List[str] = []
    added: set[str] = set()
    dirs = (
        [_project_sessions_dir(project_id, aid)]
        if project_id
        else _default_session_search_dirs(aid)
    )
    paths: List[Path] = []
    for d in dirs:
        if d.is_dir():
            paths.extend(sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True))
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
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
        if key in added:
            continue
        added.add(key)
        seen.append(key)
    return seen


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


def persist_chat_session(session: Session, agent_id: Optional[str] = None) -> Path:
    """持久化会话。

    根据 session.metadata.project_id 自动路由到：
      - 有项目 → projects-data/<project-id>/sessions/
      - 无项目 → agents/<id>/sessions/
    """
    session.touch_updated()
    store = _resolve_session_store(session, agent_id)
    store.save_session(session)
    out = store.base_path / f"{session.id}.json"
    handle = str(session.name or session.id or "").strip()
    if handle:
        _remove_legacy_session_copy(handle, agent_id)
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
    """Remove persisted chat session JSON for this session id."""
    path = _find_session_file(handle, agent_id, project_id)
    if path is None:
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


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


