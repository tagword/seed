"""Cap tool outputs before they enter the LLM message history."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from seed.core import env_access as _ea

logger = logging.getLogger(__name__)


def tool_output_max_chars() -> int:
    raw = _ea.pick_default("51200", *_ea.TOOL_OUTPUT_MAX_CHARS)
    try:
        max_c = int(raw)
    except (TypeError, ValueError):
        max_c = 51200
    if max_c <= 0:
        return 0
    return max(500, min(max_c, 200_000))


def _artifact_summary_chars() -> int:
    raw = _ea.pick_default("4000", "SEED_TOOL_ARTIFACTS_SUMMARY_CHARS")
    try:
        keep = int(raw)
    except (TypeError, ValueError):
        keep = 4000
    return max(200, min(keep, 20_000))


def _artifacts_enabled() -> bool:
    return _ea.env_truthy("SEED_TOOL_ARTIFACTS", "1")


def _artifact_min_chars() -> int:
    raw = _ea.pick_default("20000", "SEED_TOOL_ARTIFACTS_MIN_CHARS")
    try:
        min_chars = int(raw)
    except (TypeError, ValueError):
        min_chars = 20000
    return max(0, min_chars)


def _active_agent_and_session() -> tuple[str, str]:
    try:
        from seed.core.agent_context import get_active_llm_session

        raw = (get_active_llm_session() or "").strip()
        if "::" in raw:
            agent_id, session_id = raw.split("::", 1)
            agent_id = (agent_id or "").strip() or "default"
            session_id = (session_id or "").strip() or "session"
            return agent_id, session_id
        return "default", raw or "session"
    except Exception:
        return "default", "session"


def _write_artifact(*, kind: str, name_hint: str, text: str) -> Optional[str]:
    if not _artifacts_enabled():
        return None
    if len(text or "") < _artifact_min_chars():
        return None
    try:
        from seed.core.llm_sess import agent_sessions_dir

        agent_id, session_id = _active_agent_and_session()
        base = os.path.join(str(agent_sessions_dir(agent_id)), "_artifacts", session_id)
        os.makedirs(base, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_kind = re.sub(r"[^\w\-]", "_", (kind or "tool"))[:48]
        safe_hint = re.sub(r"[^\w\-]", "_", (name_hint or "output"))[:64]
        path = os.path.join(base, f"{ts}_{safe_kind}_{safe_hint}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text or "")
        return path
    except Exception:
        logger.debug("artifact write failed", exc_info=True)
        return None


def cap_tool_output_for_context(text: str, *, tool_name: str = "tool") -> str:
    """Truncate tool output and optionally spill the full text to an artifact."""
    if not isinstance(text, str):
        text = str(text)
    max_chars = tool_output_max_chars()
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    artifact_path = _write_artifact(kind=tool_name, name_hint=tool_name, text=text)
    original_len = len(text)

    header_lines = [
        f"[{tool_name}] 工具输出过长（{original_len} chars），已进入上下文摘要。",
    ]
    if artifact_path:
        header_lines.append(f"完整输出：{artifact_path}")
        header_lines.append("可用 artifact_read(path=..., start_line=..., end_line=..., pattern=...) 读取片段。")
    else:
        header_lines.append(f"超出 {original_len - max_chars} chars 已截断（未写入 artifact）。")
    header = "\n".join(header_lines)

    excerpt_budget = max(200, max_chars - len(header) - 2)
    keep = min(excerpt_budget, _artifact_summary_chars())
    head = keep // 2
    tail = keep - head
    if len(text) <= keep:
        excerpt = text
    else:
        excerpt = text[:head] + "\n...[中间省略]...\n" + text[-tail:]

    result = f"{header}\n\n{excerpt}"
    if len(result) > max_chars:
        suffix = "\n...[truncated for context limit]"
        keep_len = max(0, max_chars - len(suffix))
        result = result[:keep_len] + suffix
    return result
