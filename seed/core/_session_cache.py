"""Shared in-memory session state used by server and cron scheduler.

This belongs in ``seed.core`` because both the server and ``cron_sched`` need
access to the same live sessions dict. Keeping it in seed avoids import cycles in host apps.
a reverse dependency.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, DefaultDict, Dict, List, Set

from seed.core.models import Session

# In-memory session cache, keyed by _memkey(agent_id, session_id, project_id)
SESSIONS: Dict[str, Session] = {}
# Active WebSocket connections by session key
WS_BY_SESSION: DefaultDict[str, Set[Any]] = defaultdict(set)
# Cancel events for active chats
ACTIVE_CHAT_CANCELS: Dict[str, threading.Event] = {}
# Pending in-flight message injections, keyed by _memkey(agent_id, session_id, project_id)
# When a chat session is busy, new messages are queued here and picked up by
# the running tool loop at the next round boundary.
PENDING_INJECTIONS: Dict[str, List[Dict[str, Any]]] = {}


def _memkey(agent_id: str, session_id: str, project_id: str = "") -> str:
    """内存会话缓存键。

    格式：``agent::project::session``；``project_id`` 为空时退化为
    ``agent::session``（兼容 cron / 未接入项目维度的调用点）。

    ⚠️ 必须包含 project_id：session_id 只在项目内唯一，不同项目可能使用
    相同的 session_id（如都叫 ``web-chat``）。若键里没有项目维度，两个项目
    会命中同一份内存 Session 对象，导致消息互相追加、project_id 被覆盖，
    即"跨项目上下文串台"。磁盘持久化本身按项目目录隔离，只有这一层会串。
    """
    aid = (agent_id or "default").strip() or "default"
    sid = str(session_id or "").strip()
    pid = str(project_id or "").strip()
    if not pid:
        return f"{aid}::{sid}"
    return f"{aid}::{pid}::{sid}"


def cancel_all_active_chats() -> int:
    """Signal every in-flight chat/tool loop to stop (server shutdown or SIGINT)."""
    n = 0
    for ev in list(ACTIVE_CHAT_CANCELS.values()):
        try:
            ev.set()
            n += 1
        except Exception:
            pass
    return n
