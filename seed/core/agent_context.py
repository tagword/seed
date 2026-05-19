"""Context-local active LLM session id (for tools and logging during a turn).

Uses contextvars (not threading.local) so that asyncio.to_thread()
execution (sync tool handlers) also inherits the active context.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_session_id: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
_episodic_project_scoped: ContextVar[bool] = ContextVar(
    "episodic_project_scoped", default=False
)
_episodic_project_id: ContextVar[str] = ContextVar("episodic_project_id", default="")
_project_workspace_cwd: ContextVar[Optional[str]] = ContextVar(
    "project_workspace_cwd", default=None
)


def set_active_llm_session(session_id: Optional[str]) -> None:
    if session_id:
        _session_id.set(session_id)
    else:
        _session_id.set(None)


def get_active_llm_session() -> Optional[str]:
    return _session_id.get()


def set_active_project_episodic(scoped: bool, project_id: str = "") -> None:
    """
    Web UI chat: scoped=True 时 episodic 注入与 memory_search 按 project_id 隔离。
    project_id 为空表示「无项目」会话，只匹配未带 ## Project 的经验文件。
    CLI/cron：不要调用或 scoped=False，保持原有「不按项目过滤」行为。
    """
    _episodic_project_scoped.set(bool(scoped))
    _episodic_project_id.set((project_id or "").strip() if scoped else "")


def clear_active_project_episodic() -> None:
    _episodic_project_scoped.set(False)
    _episodic_project_id.set("")


def episodic_project_scope_active() -> bool:
    return _episodic_project_scoped.get()


def active_episodic_project_id() -> str:
    """当前 Web 请求下的项目 id（仅当 scope 开启时有效；否则返回空字符串）。"""
    if not _episodic_project_scoped.get():
        return ""
    return _episodic_project_id.get() or ""


def set_active_project_workspace(path: Optional[str]) -> None:
    """Web UI chat: default shell cwd when tools omit ``cwd``."""
    p = (path or "").strip()
    _project_workspace_cwd.set(p if p else None)


def get_active_project_workspace_cwd() -> Optional[str]:
    return _project_workspace_cwd.get()


def clear_active_project_workspace() -> None:
    _project_workspace_cwd.set(None)
