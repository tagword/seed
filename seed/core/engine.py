"""
Stateful prompt/query loop with routing hooks and optional session persistence.

``QueryEngine`` is a lighter-weight loop than ``TurnLoopEngine`` (see
``seed.core.turn_loop``). Prefer ``TurnLoopEngine`` for full agent turns,
tools, and autonomous mode; use ``QueryEngine`` when you only need routing
and token/session bookkeeping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from seed.core.models import QueryTurnResult, UsageMetrics
from seed.core.persistence import SESSIONS_DIR as _SESSIONS_DIR
from seed.core.routing import find_commands

DEFAULT_MAX_TURNS = 8
DEFAULT_BUDGET_TOKENS = 2000
DEFAULT_COMPACT_AFTER = 12


@dataclass
class EngineConfig:
    max_turns: int = DEFAULT_MAX_TURNS
    token_budget: int = DEFAULT_BUDGET_TOKENS
    compact_after: int = DEFAULT_COMPACT_AFTER
    auto_save_dir: Optional[str] = None
    session_id: Optional[str] = None


class QueryEngine:
    """
    Stateful turn loop engine (Seed kernel).
    Handles message routing, token tracking, and session management.
    """

    def __init__(self, config: Optional[EngineConfig] = None, session_id: Optional[str] = None):
        self.config = config or EngineConfig()
        self.session_id = session_id or "session"
        self.message_history: List[str] = []
        self.turn_results: List[QueryTurnResult] = []
        self.metrics = UsageMetrics()
        self.turn_count = 0
        self.is_terminated = False
        self.token_input = 0
        self.token_output = 0

    def submit_message(self, message: str, turn_limit: Optional[int] = None) -> QueryTurnResult:
        """Process a message through the turn loop."""
        if self.is_terminated:
            return QueryTurnResult(
                status="terminated",
                turn_type="turn",
                matched_commands=[],
                stop_reason="session_terminated",
                error="Session already terminated",
            )

        effective_limit = turn_limit or self.config.max_turns
        if self.turn_count >= effective_limit:
            self.is_terminated = True
            return QueryTurnResult(
                status="stop",
                turn_type="max_turns",
                matched_commands=[],
                stop_reason="max_turns_reached",
            )

        if self.token_input + self.token_output > self.config.token_budget:
            self.is_terminated = True
            return QueryTurnResult(
                status="stop",
                turn_type="budget",
                matched_commands=[],
                stop_reason="budget_exceeded",
            )

        self.message_history.append(message)
        self.turn_count += 1

        matched = find_commands(message, limit=5)
        matches = [m.name for m in matched if hasattr(m, "name")]

        return QueryTurnResult(
            status="success",
            turn_type="prompt",
            matched_commands=matches,
            output=message,
            stop_reason=None,
        )

    def persist_session(self) -> Optional[str]:
        """Write a JSON snapshot of engine state under ``auto_save_dir`` or the default sessions path."""
        session_dir = (
            Path(self.config.auto_save_dir).resolve()
            if self.config.auto_save_dir
            else Path(_SESSIONS_DIR)
        )
        session_dir.mkdir(parents=True, exist_ok=True)
        sid = self.session_id or "session"
        data: Dict[str, Any] = {
            "session_id": sid,
            "messages": self.message_history,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "metrics": {"turn_count": self.turn_count, "message_count": len(self.message_history)},
            "tokens": {"input": self.token_input, "output": self.token_output},
            "turn_count": self.turn_count,
        }
        path = session_dir / f"{sid}.json"
        try:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return str(path)
        except OSError as e:
            print(f"Error persisting session: {e}")
            return None

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate (4 chars per token)."""
        return len(text.split()) * 4

    def render_summary(self) -> str:
        """Render usage summary report."""
        token_total = self.token_input + self.token_output
        lines = [
            "=== CodeAgent Session Summary ===",
            f"Session ID: {self.session_id or 'N/A'}",
            f"Turn count: {self.turn_count}",
            f"Message count: {len(self.message_history)}",
            f"Input tokens: {self.token_input}",
            f"Output tokens: {self.token_output}",
            f"Total tokens: {token_total}",
            f"Status: {'terminated' if self.is_terminated else 'active'}",
            f"Last turn: {datetime.now().isoformat()}",
        ]
        return "\n".join(lines)

    def reset(self):
        """Reset the engine to initial state."""
        self.turn_count = 0
        self.is_terminated = False
        self.message_history = []
        self.turn_results = []
        self.token_input = 0
        self.token_output = 0
