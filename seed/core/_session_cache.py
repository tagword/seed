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

# In-memory session cache, keyed by _memkey(agent_id, session_id)
SESSIONS: Dict[str, Session] = {}
# Active WebSocket connections by session key
WS_BY_SESSION: DefaultDict[str, Set[Any]] = defaultdict(set)
# Cancel events for active chats
ACTIVE_CHAT_CANCELS: Dict[str, threading.Event] = {}
# Pending in-flight message injections, keyed by _memkey(agent_id, session_id)
# When a chat session is busy, new messages are queued here and picked up by
# the running tool loop at the next round boundary.
PENDING_INJECTIONS: Dict[str, List[Dict[str, Any]]] = {}


def _memkey(agent_id: str, session_id: str) -> str:
    return f"{(agent_id or 'default').strip() or 'default'}::{session_id}"


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
