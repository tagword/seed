"""LLM + tool registry: OpenAI-style multi-turn tool loop."""
from __future__ import annotations


import asyncio
import contextlib
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from seed.core import env_access as _ea
from seed.core.chat_events import emit_chat_event, is_chat_cancelled
from seed.core.llm_exec import LLMAPIExecutor, LLMError
from seed.core.tool_runtime import ToolExecutor, ToolRegistry


logger = logging.getLogger(__name__)


def _message_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


_COMPACT_BLOCK = re.compile(
    r"\n<<<(?:SEED_COMPACT|CODEAGENT_COMPACT)>>>\n.*?\n<<<END_(?:SEED_COMPACT|CODEAGENT_COMPACT)>>>\n",
    re.DOTALL,
)


# Grouping of tool names into "failure domains". When a loop ends with a streak
# of same-domain errors, the outer server loop can use this to switch strategy
# instead of naively replaying "please continue".
_FAILURE_DOMAIN_PREFIXES: Tuple[str, ...] = (
    "browser_",
    "bash",
    "file_",
    "web_",
)


def _classify_failure_domain(name: str) -> Optional[str]:
    if not name:
        return None
    for p in _FAILURE_DOMAIN_PREFIXES:
        if name.startswith(p):
            return p
    return None


def _is_tool_error_payload(payload: str) -> bool:
    """Heuristic: did this tool result represent a failure the model should
    NOT blindly retry with the same parameters?

    We match:
    - ``error:`` / ``Error:`` prefix produced by our executor wrappers
    - raw Python exceptions that still made it through: ``Tool '...' execution error: ...``
    - CDP timeouts that surface as normal tool strings containing
      ``CDP timeout`` / ``timeout calling``
    """
    if not isinstance(payload, str):
        return False
    s = payload.lstrip()
    if not s:
        return False
    low = s[:512].lower()
    if low.startswith("error:"):
        return True
    if "execution error" in low:
        return True
    if "cdp timeout" in low or "timeout calling" in low:
        return True
    return False


def _detect_failure_streak(
    outcomes: List[Tuple[str, bool, str]],
    *,
    min_streak: int = 3,
) -> Optional[Dict[str, Any]]:
    """Return info about a tail streak of same-domain errors, or ``None``.

    ``outcomes``: list of ``(tool_name, is_error, payload_excerpt)`` in call order.
    """
    if not outcomes:
        return None
    tail: List[Tuple[str, str]] = []
    for name, is_err, msg in reversed(outcomes):
        if is_err:
            tail.append((name, msg))
        else:
            break
    if len(tail) < min_streak:
        return None
    domains = {_classify_failure_domain(n) for n, _ in tail}
    if len(domains) != 1:
        return None
    dom = next(iter(domains))
    if dom is None:
        return None
    tail.reverse()
    return {
        "domain": dom,
        "streak": len(tail),
        "recent_errors": [
            {"tool": n, "error": (m or "")[:400]} for n, m in tail[-min_streak * 2 :]
        ],
    }





def _consecutive_error_tail_count(outcomes: List[Tuple[str, bool, str]]) -> int:
    n = 0
    for _name, is_err, _msg in reversed(outcomes):
        if is_err:
            n += 1
        else:
            break
    return n


def _last_consecutive_error_block(
    outcomes: List[Tuple[str, bool, str]], k: int
) -> List[Tuple[str, str]]:
    """Most recent ``k`` outcomes among the trailing error run (newest last)."""
    buf: List[Tuple[str, str]] = []
    for name, is_err, msg in reversed(outcomes):
        if not is_err:
            break
        buf.append((name or "", (msg or "")[:500]))
        if len(buf) >= k:
            break
    buf.reverse()
    return buf


def _format_tool_error_streak_nudge(block: List[Tuple[str, str]]) -> str:
    lines = [
        "[CodeAgent] 已连续多次工具调用返回错误。请阅读下列摘要，**换一种策略**"
        "（换工具、换参数、或先排查环境），不要重复同一失败路径。",
        "",
        "最近错误摘要：",
    ]
    for i, (name, excerpt) in enumerate(block, 1):
        lines.append(f"{i}. `{name}`: {excerpt[:320]}{'…' if len(excerpt) > 320 else ''}")
    return "\n".join(lines)


def format_tool_segment_summary(
    tools_used: List[str],
    tool_trace: List[Dict[str, str]],
    *,
    max_trace: int = 8,
) -> str:
    """工具链调用摘要已禁用。保留签名兼容，始终返回空字符串。"""
    _ = tools_used, tool_trace, max_trace  # 标记参数已用（避免 lint warning）
    return ""


DEFAULT_SYSTEM = """你是 CodeAgent：谨慎的编程与系统助手（此为配置缺失时的后备 system）。

工具纪律：
- 问候、闲聊、致谢或无需文件/命令/搜索的一般问答：**只用自然语言回复，不要调工具**。
- 仅在用户明确需要执行时调用工具：读写文件、运行命令、搜索/代码分析、`calculate` 等。
- 若信息不足：先简短回答，**最多提一个**澄清问题，不要用工具瞎试。

合法调用工具后，用简短文字向用户归纳结果。"""


def registry_to_openai_tools(
    registry: ToolRegistry,
    *,
    include_names: Optional[Sequence[str]] = None,
    exclude_prefixes: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Build Chat Completions ``tools`` payload from a ``ToolRegistry``."""
    tools: List[Dict[str, Any]] = []
    include_set = {n for n in (include_names or []) if n}
    # Stable ordering matters for cache-prefix reuse (e.g. DeepSeek KV cache).
    # Sort by tool name so plugin load order does not perturb the tools schema.
    for tool in sorted(registry.list_all(), key=lambda t: (t.name or "")):
        if include_set and tool.name not in include_set:
            continue
        if exclude_prefixes and any(
            tool.name.startswith(p) for p in exclude_prefixes if p
        ):
            continue
        props: Dict[str, Any] = {}
        required: List[str] = []
        for pname, pdef in (tool.parameters or {}).items():
            props[pname] = {
                "type": pdef.get("type", "string"),
                "description": pdef.get("description", ""),
            }
            if pdef.get("required"):
                required.append(pname)
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            }
        )
    return tools




# Chain-of-thought / reasoning markup (Qwen, DeepSeek, some proxies). Stripped
# before persisting assistant content so history does not reinforce repetition.
_THINK_BLOCK_RE = re.compile(
    r"(?:"
    r"<\s*think\s*>[\s\S]*?<\s*/\s*think\s*>"
    r"|"
    r"<\s*redacted_thinking\s*>[\s\S]*?<\s*/\s*redacted_thinking\s*>"
    r")",
    re.IGNORECASE,
)
_THINK_CLOSE_RE = re.compile(r"<\s*/\s*(?:think|redacted_thinking)\s*>", re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<\s*(?:think|redacted_thinking)\s*>", re.IGNORECASE)
_HARMONY_CHANNEL_RE = re.compile(
    r"<\|(?:start|channel|message|end|constrain|return)\|>",
    re.IGNORECASE,
)


def _strip_think_markup(text: str) -> str:
    """Remove <think>...</think> chain-of-thought from assistant content.

    Handles three forms we have seen in the wild:
    - balanced ``<think>...</think>``
    - dangling ``</think>`` only (upstream stripped opening tag but left body)
    - dangling ``<think>`` only (stream truncated before closing tag)
    """
    if not text:
        return text
    t = _THINK_BLOCK_RE.sub("", text)
    # Dangling closing tag: treat everything up to and including it as thought.
    m_close = _THINK_CLOSE_RE.search(t)
    if m_close:
        t = t[m_close.end():]
    # Dangling opening tag: treat everything from it to end as thought.
    m_open = _THINK_OPEN_RE.search(t)
    if m_open:
        t = t[:m_open.start()]
    t = _HARMONY_CHANNEL_RE.sub("", t)
    return t


# ---------------------------------------------------------------------------
# Bare chain-of-thought detection.
#
# Some local stacks (sglang without --reasoning-parser, plain vLLM, certain
# Ollama builds) stream Qwen3 / DeepSeek-R1 "thinking tokens" straight into
# ``content`` with NO ``<think>`` markup at all. The model then sees these
# prose thought monologues in its own history and produces more of them,
# which looks to the user like infinite repetition / "表演思考".
#
# Observed pathological prefixes (20k+ char bodies):
#   "Here's a thinking process that leads to the suggested response: ..."
#   "The user wants to confirm if ..."
#   "User says ... I need to clarify ... Plan: 1. ... 2. ..."
#   "用户纠正了我对...的理解。用户指出：1. ..."
#   "我需要重新调整...的定义 ... **修正后的...**:"
#
# We can't strip partial CoT (no anchor), so we detect the whole-message
# pattern and redact it to an empty string. Upstream callers see assistant
# content=""/None and must either produce real output or call a tool.
# ---------------------------------------------------------------------------

# Strong CoT opener signals (single hit is enough when combined with length).
_BARE_COT_STRONG_OPENERS = (
    # English (gpt-oss / R1-distill / Qwen3-Thinking leak)
    "here's a thinking process",
    "here is a thinking process",
    "here is my thinking process",
    "here's my thinking",
    "let me think step by step",
    "let me analyze this",
    "let me analyze the user",
    "let me analyze what",
    "let me break this down",
    "let me think through this",
    "okay, let me think",
    "okay, so the user",
    "ok, so the user",
    "the user wants to",
    "the user is asking",
    "the user says",
    "the user's comment",
    "the user's feedback",
    "the user's question",
    "the user is essentially",
    "user wants to confirm",
    "user says",
    "user states",
    "i need to clarify",
    "i need to analyze",
    "i need to understand",
    "i need to figure out",
    "first, i need to",
    "first, let me",
    "my plan is",
    "plan:\n1.",
    "plan:\n- ",
    "plan:\n* ",
    "step 1:",
    "step 1.",
    # Chinese CoT (observed in ae507677 / f7de03f6)
    "用户纠正了我",
    "用户指出：",
    "用户指出:",
    "用户说的是",
    "用户的意思是",
    "用户想要确认",
    "用户想让我",
    "用户希望我",
    "用户的核心需求",
    "我需要重新调整",
    "我需要分析一下",
    "我需要澄清",
    "我需要先",
    "我需要弄清楚",
    "让我想一想",
    "让我分析一下",
    "让我思考一下",
    "让我先",
    "好的，让我",
    "好的，我来思考",
    "好，我来想一下",
    "我的失误",
    "修正后的",
    "修正版分析",
    "修正后角色定位",
    "思考过程：",
    "思维过程：",
    "分析用户的",
    "分析一下用户",
)

# Structural markers that amplify suspicion. Any single hit in the opening
# of the body is enough to classify a CoT-prefixed message as a monologue.
_BARE_COT_STRUCTURE_MARKERS = (
    re.compile(r"(?im)^\s*(?:plan|steps?|thinking)\s*[:：]\s*$"),
    re.compile(r"(?im)^\s*\d+\.\s+\*\*(?:analyze|evaluate|identify|determine|check|confirm|verify|explain|clarify)\b"),
    re.compile(r"(?im)^\s*\*\*(?:analyze|evaluate|identify|determine)\s"),
    re.compile(r"(?im)^\s*\d+\.\s+\*\*\s*(?:分析|评估|识别|确认|澄清|思考|判断|检查)"),
    re.compile(r"(?im)^\s*\*\s+\*\*(?:user|agent|model)'s?\s"),
    # Enumerated self-quote of user statements ("用户指出:" followed by "1. ... 2. ...").
    re.compile(r"(?s)用户(?:指出|说的是|的意思|想要|希望)[：:].{0,30}\n\s*\d+\."),
    # Self-plan heading in English ("Plan:\n1." or "Steps:\n1.")
    re.compile(r"(?im)^\s*(?:plan|steps?|outline)\s*[:：]\s*\n\s*\d+\."),
    # Two or more "Let me ..." / "I need to ..." / "First, I ..." in a row
    # strongly signal a monologue.
    re.compile(r"(?is)\blet me\b.{1,200}\b(?:let me|i need to|first,? i|then i|next,? i)\b"),
    re.compile(r"(?is)我需要.{1,200}(?:我需要|首先|然后|接下来|让我)"),
    re.compile(r"(?is)用户.{1,300}\b(?:我需要|我应该|我必须|我来|让我)"),
)


def _is_bare_chain_of_thought(text: str) -> bool:
    """Heuristic: does this assistant content look like raw chain-of-thought
    leaked into the message body (without any ``<think>`` markup)?

    Design goals:
    - Extremely high precision on multi-kilobyte CoT dumps like
      "Here's a thinking process that leads to the suggested response: ..."
    - Near-zero false positives on real answers. A real answer might start
      with "我需要" once, but will not also contain the Plan/Step/Analyze
      skeleton *and* be >1200 chars.

    We require BOTH:
    1. The opening (first 400 chars, lower-cased) hits a strong CoT phrase.
    2. Either (a) the body is long (>1200 chars) OR (b) there is at least one
       structural marker (numbered analyze/evaluate/identify skeleton).
    """
    if not text:
        return False
    body = text.strip()
    if len(body) < 400:
        # Short replies are almost never full CoT dumps; leave them alone.
        return False
    head = body[:400].lower()
    strong = False
    for needle in _BARE_COT_STRONG_OPENERS:
        if needle in head:
            strong = True
            break
    if not strong:
        return False
    # Length alone is sufficient once we're past the soft threshold, because
    # the opener already matched and real answers rarely exceed ~600 chars
    # while still opening with "Let me analyze" / "我需要" / "The user wants".
    if len(body) > 600:
        return True
    for rx in _BARE_COT_STRUCTURE_MARKERS:
        if rx.search(body[:2000]):
            return True
    return False


def scrub_bare_cot_from_assistant_text(text: str) -> str:
    """If the content is a bare CoT monologue, redact to empty so it doesn't
    pollute the next turn's context. Otherwise return as-is.

    This runs AFTER ``_strip_think_markup`` in the assistant-content pipeline,
    so by the time we get here we are looking at markup-free prose.
    """
    if not text:
        return text
    if _is_bare_chain_of_thought(text):
        return ""
    return text




def _clean_invalid_tool_call_arguments(messages: List[Dict[str, Any]]) -> None:
    """In-place: strip tool_calls whose ``function.arguments`` is not valid JSON.

    Historical self-heal for sessions that were saved *before* llm_exec started
    validating LLM tool_call arguments.  When a streaming response was truncated
    (network blip, token limit, mid-arg server drop), the accumulated
    ``function.arguments`` was often ``""`` or partial JSON.  Persisting that
    poisons the conversation: the next LLM call rejects the whole request with
    ``HTTP 400 invalid function arguments json string``.

    For each assistant message we drop the malformed entries.  If the assistant
    message has no remaining tool_calls after the sweep but does have text, we
    keep the message as plain text.  If the message was *only* a malformed
    tool_call (no content / whitespace-only content), we remove the message
    entirely so we don't leave a content-less assistant turn.

    This is best-effort: it only inspects a message's *own* tool_calls.  Tool
    responses (role=tool) for the dropped ids become orphaned; the
    ``_clean_orphaned_tool_calls`` pass that follows will deal with them.
    """
    if not messages:
        return
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        raw_tc = m.get("tool_calls")
        if not raw_tc or not isinstance(raw_tc, list):
            continue
        kept: List[Dict[str, Any]] = []
        dropped_ids: List[str] = []
        for tc in raw_tc:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if not isinstance(fn, dict):
                continue
            args = fn.get("arguments")
            if not isinstance(args, str) or not args.strip():
                dropped_ids.append(str(tc.get("id") or "?"))
                continue
            try:
                json.loads(args)
            except (json.JSONDecodeError, TypeError, ValueError):
                dropped_ids.append(str(tc.get("id") or "?"))
                continue
            kept.append(tc)
        if dropped_ids:
            logger.warning(
                "Stripped %d tool_call(s) with invalid JSON arguments "
                "(likely from prior streaming truncation): %s",
                len(dropped_ids), dropped_ids[:5],
            )
            if kept:
                m["tool_calls"] = kept
            else:
                m.pop("tool_calls", None)
                # If the message was only a broken tool_call with no text content,
                # drop the empty assistant turn entirely.
                if not str(m.get("content") or "").strip():
                    # Mark for removal: tag with sentinel, sweeper removes.
                    m["_invalid_tc_drop_turn"] = True


def _sweep_empty_invalid_tc_turns(messages: List[Dict[str, Any]]) -> None:
    """In-place: remove assistant turns tagged by ``_clean_invalid_tool_call_arguments``
    that have no text content left (only had malformed tool_calls)."""
    if not messages:
        return
    i = 0
    while i < len(messages):
        m = messages[i]
        if (
            isinstance(m, dict)
            and m.get("_invalid_tc_drop_turn")
        ):
            messages.pop(i)
            continue
        i += 1


def _clean_orphaned_tool_calls(messages: List[Dict[str, Any]]) -> None:
    """In-place: remove assistant tool_calls that lack matching tool responses.

    When the tool loop is interrupted mid-execution (crash, disconnect, restart),
    the messages list may end with an assistant message containing ``tool_calls``
    but no corresponding ``tool`` role responses.  DeepSeek (and other strict
    OpenAI-compatible APIs) reject such incomplete histories with HTTP 400.

    This function scans backwards for orphaned tool_calls and either:
    - strips ``tool_calls`` from the assistant message (if it has text content)
    - removes the entire message (if content is empty or whitespace-only)
    """
    if not messages:
        return
    i = len(messages) - 1
    while i >= 0:
        msg = messages[i]
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            i -= 1
            continue
        raw_tc = msg.get("tool_calls")
        if not raw_tc:
            i -= 1
            continue
        # Collect tool_call_ids from this assistant message
        call_ids = set()
        for tc in raw_tc:
            if isinstance(tc, dict):
                tid = (tc.get("id") or "").strip()
                if tid:
                    call_ids.add(tid)
        if not call_ids:
            i -= 1
            continue
        # Check messages after this position for matching tool responses
        following = messages[i + 1:]
        responded_ids = set()
        for fm in following:
            if isinstance(fm, dict) and fm.get("role") == "tool":
                tid = (fm.get("tool_call_id") or "").strip()
                if tid in call_ids:
                    responded_ids.add(tid)
        missing = call_ids - responded_ids
        if missing:
            content = msg.get("content") or ""
            if content.strip():
                # Keep assistant message as regular text, drop tool_calls
                del msg["tool_calls"]
                logger.warning(
                    "Cleaned orphaned tool_calls from assistant msg "
                    "(missing %d tool response(s)): %s",
                    len(missing), sorted(missing)[:3],
                )
            else:
                # No text content → remove the entire orphaned message
                messages.pop(i)
                logger.warning(
                    "Removed empty assistant msg with orphaned tool_calls "
                    "(missing %d tool response(s)): %s",
                    len(missing), sorted(missing)[:3],
                )
        i -= 1


def _clean_orphaned_tool_results(messages: List[Dict[str, Any]]) -> None:
    """In-place: remove ``tool`` messages whose ``tool_call_id`` is unmatched.

    ``_clean_invalid_tool_call_arguments`` can strip malformed ``tool_calls`` from
    an assistant turn while leaving its ``tool`` responses in place.  Strict
    providers (MiniMax, DeepSeek, …) then reject the next request with HTTP 400
    ``tool result's tool id … not found``.
    """
    if not messages:
        return
    valid_ids: set[str] = set()
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            tid = (tc.get("id") or "").strip()
            if tid:
                valid_ids.add(tid)
    i = 0
    while i < len(messages):
        msg = messages[i]
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            i += 1
            continue
        tid = (msg.get("tool_call_id") or "").strip()
        if tid and tid not in valid_ids:
            logger.warning(
                "Removing orphaned tool result (tool_call_id=%r has no assistant tool_call)",
                tid,
            )
            messages.pop(i)
            continue
        i += 1


_AUTO_CONTINUE_NUDGE_PREFIXES = (
    "请继续完成未完成事项",
    "上一段连续在",
)

_EPHEMERAL_MESSAGE_KEYS = (
    "_source_idx",
    "_auto_continue_nudge",
    "_streaming",
)


def _is_auto_continue_nudge_message(message: Dict[str, Any]) -> bool:
    if message.get("_auto_continue_nudge"):
        return True
    if message.get("role") != "user":
        return False
    content = message.get("content")
    if not isinstance(content, str):
        return False
    return any(content.startswith(prefix) for prefix in _AUTO_CONTINUE_NUDGE_PREFIXES)


def _user_round_indices(body: List[Dict[str, Any]]) -> List[int]:
    """User-started blocks, excluding auto-continue nudge injections."""
    return [
        i
        for i, m in enumerate(body)
        if isinstance(m, dict)
        and m.get("role") == "user"
        and not _is_auto_continue_nudge_message(m)
    ]


def strip_ephemeral_message_fields(messages: List[Dict[str, Any]]) -> None:
    """Remove in-memory-only metadata before LLM/tool persistence."""
    for message in messages:
        if not isinstance(message, dict):
            continue
        for key in _EPHEMERAL_MESSAGE_KEYS:
            message.pop(key, None)


def persist_compact_summary(
    full_messages: List[Dict[str, Any]],
    compact_result: Optional[Dict[str, Any]],
) -> bool:
    """Write compact summary onto the correct message in persisted history."""
    if not compact_result or not full_messages:
        return False
    summary = compact_result.get("compact_summary")
    if not isinstance(summary, str) or not summary.strip():
        return False
    raw_idx = compact_result.get("boundary_source_idx", compact_result.get("boundary_idx"))
    try:
        idx = int(raw_idx)
    except (TypeError, ValueError):
        return False
    if 0 <= idx < len(full_messages):
        full_messages[idx]["_compact_summary"] = summary.strip()
        return True
    return False


def build_api_projection_messages(
    full_messages: List[Dict[str, Any]],
    *,
    skills_suffix: Optional[str] = None,
    cursor: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Deep-copy ``full_messages`` and apply in-memory-only shaping for the LLM:
    optional skills suffix on ``system``.

    **链式摘要重建**：扫描 ``_compact_summary`` 字段（见 ``maybe_compact_context_messages``），
    找到最新的边界消息，将其之前的所有消息替换为 ``<<<SEED_COMPACT>>>`` 块注入 system prompt，
    从而避免每轮用全部原始消息做投影。

    **会话游标**（``cursor`` 参数）：
      - None / ``{"mode": "tail"}``：默认行为，从末尾向后取全部消息
        （由上层 ``maybe_compact_context_messages`` 负责压缩控制）
      - ``{"mode": "head", "from_idx": N}``：从 ``full_messages[N]`` 开始投影（回滚模式），
        此时跳过链式摘要重建（因为用户想从头看原始消息）

    Pass the result to ``maybe_compact_context_messages`` / ``run_llm_tool_loop``;
    do not replace persisted ``Session.messages`` with this list.
    """
    import copy as _copy

    api = _copy.deepcopy(full_messages)
    for i, message in enumerate(api):
        if isinstance(message, dict):
            message["_source_idx"] = i

    # ── 会话游标 ──
    _cursor = cursor or {}
    _cursor_mode = _cursor.get("mode", "tail")
    if _cursor_mode == "head":
        from_idx = int(_cursor.get("from_idx", 0))
        if from_idx > 0 and from_idx < len(api):
            has_sys = bool(api and api[0].get("role") == "system")
            if has_sys and from_idx > 0:
                # 保留 system，从 from_idx 开始取消息
                api = [api[0]] + api[from_idx:]
            else:
                api = api[from_idx:]
        # head 模式下跳过链式摘要重建（用户想从该点重新开始）
    elif _cursor_mode == "tail":
        # ── 链式摘要重建：扫描 _compact_summary，取最晚一条 ──
        _last_bound_idx: int | None = None
        _last_summary: str | None = None
        for i, m in enumerate(api):
            cs = m.get("_compact_summary")
            if isinstance(cs, str) and cs.strip():
                _last_bound_idx = i
                _last_summary = cs.strip()

        if _last_bound_idx is not None and _last_summary and _last_bound_idx > 0:
            if api and api[0].get("role") == "system":
                sys_msg = api[0]
                base = strip_compact_block_from_system(str(sys_msg.get("content") or ""))
                block = (
                    "\n\n<<<SEED_COMPACT>>>\n"
                    "## Earlier conversation (compressed)\n"
                    f"{_last_summary}\n"
                    "<<<END_SEED_COMPACT>>>\n"
                )
                sys_msg["content"] = base + block
                # 保留 system + 边界消息及之后的所有消息
                api = [sys_msg] + api[_last_bound_idx:]

    if skills_suffix and api and api[0].get("role") == "system":
        api[0]["content"] = str(api[0].get("content") or "").rstrip() + skills_suffix
    # Hard cap on conversation history by user-rounds (opt-in via
    # SEED_CHAT_USER_ROUNDS; 0 = disabled). Bounds prompt growth even when the
    # API-token-driven compaction is unavailable (CLI / task / cron paths).
    # Trimming happens on a user-message boundary; orphan tool_calls/results
    # left behind are removed by the cleaning passes below.
    _max_user_rounds = _ea.pick_int(0, *_ea.CHAT_USER_ROUNDS)
    if _max_user_rounds > 0:
        api = trim_messages_by_user_rounds(api, _max_user_rounds)
    # Drop tool_calls with invalid JSON arguments first (historical self-heal
    # for sessions saved before llm_exec started validating them), then remove
    # assistant turns that became empty as a result, then clean orphans.
    _clean_invalid_tool_call_arguments(api)
    _sweep_empty_invalid_tc_turns(api)
    _clean_orphaned_tool_calls(api)
    _clean_orphaned_tool_results(api)
    return api


def merge_llm_tail_into_full(
    full_messages: List[Dict[str, Any]],
    api_messages: List[Dict[str, Any]],
    n_before_llm: int,
) -> List[Dict[str, Any]]:
    """Append messages produced during ``run_llm_tool_loop`` (``api_messages[n_before_llm:]``) onto ``full_messages``."""
    if n_before_llm < 0:
        n_before_llm = 0
    # ── mid-loop compact 容错 ──
    # run_llm_tool_loop 在工具循环中可能触发 mid-loop compact
    # （SEED_CONTEXT_COMPACT_MID_LOOP=1）：它通过 `messages[:] = [sys] + recent`
    # 原地重建 api_messages，使列表长度骤减、n_before_llm 索引直接失效。
    # 此时 api_messages[n_before_llm:] 为空 → 本轮 LLM 全部输出（assistant + tool）
    # 全部丢失（cron/CLI/task_runner 曾因此只持久化 user 消息、无任何回复）。
    # 修复：退化为以「最后一条 user 消息之后」为合并边界——LLM 工具循环只追加
    # assistant/tool 消息，最后一条 user（通常是本轮触发消息）之后即为本轮新增输出；
    # compact 保留的 recent 轮次（更早的 user/assistant/tool）位于该 user 之前，不会重复。
    if len(api_messages) < n_before_llm:
        last_user_idx = -1
        for _i, _m in enumerate(api_messages):
            if isinstance(_m, dict) and _m.get("role") == "user":
                last_user_idx = _i
        n_before_llm = last_user_idx + 1
    tail = [
        message
        for message in api_messages[n_before_llm:]
        if not (isinstance(message, dict) and _is_auto_continue_nudge_message(message))
    ]
    if tail:
        # If the last message in full is a streaming placeholder and tail starts
        # with an assistant message, replace the placeholder to avoid duplicate.
        if (full_messages
                and full_messages[-1].get("_streaming")
                and tail[0].get("role") == "assistant"):
            persisted = dict(tail[0])
            strip_ephemeral_message_fields([persisted])
            if "ts" not in persisted or not persisted["ts"]:
                persisted["ts"] = datetime.now(timezone.utc).isoformat()
            full_messages[-1] = persisted
            tail = tail[1:]
        if tail:
            persisted_tail = []
            for message in tail:
                if not isinstance(message, dict):
                    persisted_tail.append(message)
                    continue
                copied = dict(message)
                strip_ephemeral_message_fields([copied])
                # 确保每条消息都带时间戳，刷新后历史时间显示
                if "ts" not in copied or not copied["ts"]:
                    copied["ts"] = datetime.now(timezone.utc).isoformat()
                persisted_tail.append(copied)
            full_messages.extend(persisted_tail)
    return tail


def trim_messages_by_user_rounds(
    messages: List[Dict[str, Any]],
    max_user_rounds: int,
) -> List[Dict[str, Any]]:
    """
    Keep the system message (if present) and the last N user-started conversation blocks.
    Prevents unbounded growth of `messages` when tool outputs are large.
    """
    if max_user_rounds <= 0 or len(messages) <= 2:
        return messages
    has_system = bool(messages and messages[0].get("role") == "system")
    body_start = 1 if has_system else 0
    body = messages[body_start:]
    # 包括所有 role=user 的消息作为轮次边界（含 _auto_continue_nudge），
    # 让自主模式的分块渐进压缩能按 chunk 边界切分。
    user_idx = [
        i for i, m in enumerate(body)
        if isinstance(m, dict) and m.get("role") == "user"
    ]
    if len(user_idx) <= max_user_rounds:
        return messages
    cut = user_idx[len(user_idx) - max_user_rounds]
    trimmed = body[cut:]
    if has_system:
        return [messages[0]] + trimmed
    return trimmed


def strip_compact_block_from_system(system_text: str) -> str:
    """Remove a previously injected compact summary from system prompt text."""
    return _COMPACT_BLOCK.sub("\n", system_text).strip()


def _context_compact_enabled() -> bool:
    return _ea.pick_default("", *_ea.CONTEXT_COMPACT).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _context_compact_mid_loop_enabled() -> bool:
    if not _context_compact_enabled():
        return False
    return _ea.pick_default("0", *_ea.CONTEXT_COMPACT_MID_LOOP).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _context_compact_adaptive_keep_enabled() -> bool:
    return _ea.pick_default("0", *_ea.CONTEXT_COMPACT_ADAPTIVE_KEEP).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _api_prompt_tokens(
    messages: List[Dict[str, Any]],
    *,
    api_prompt_tokens: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> int:
    """LLM API ``usage.prompt_tokens`` only — no local message estimation."""
    _ = messages  # signature kept for call-site clarity
    if api_prompt_tokens is not None and int(api_prompt_tokens) > 0:
        return int(api_prompt_tokens)
    if isinstance(meta, dict):
        usage = meta.get("usage")
        if isinstance(usage, dict):
            pt = usage.get("prompt_tokens")
            if isinstance(pt, (int, float)) and int(pt) > 0:
                return int(pt)
    return 0


def resolve_compact_trigger_tokens(
    *,
    persisted: Optional[Dict[str, Any]] = None,
    loop_meta: Optional[Dict[str, Any]] = None,
    api_prompt_tokens: Optional[int] = None,
) -> int:
    """Best-effort compact trigger size from API usage (never local estimation).

    Takes the maximum of explicit ``api_prompt_tokens``, persisted session
    ``context_usage`` (``peak_prompt_tokens`` / ``prompt_tokens``), and the
    in-flight ``loop_meta.peak_prompt_tokens``.
    """
    candidates: List[int] = []
    if api_prompt_tokens is not None:
        try:
            pt = int(api_prompt_tokens)
            if pt > 0:
                candidates.append(pt)
        except (TypeError, ValueError):
            pass
    if isinstance(persisted, dict):
        for key in ("peak_prompt_tokens", "prompt_tokens"):
            raw = persisted.get(key)
            if isinstance(raw, (int, float)) and int(raw) > 0:
                candidates.append(int(raw))
    if isinstance(loop_meta, dict):
        raw = loop_meta.get("peak_prompt_tokens")
        if isinstance(raw, (int, float)) and int(raw) > 0:
            candidates.append(int(raw))
    return max(candidates) if candidates else 0


def _record_peak_prompt_tokens(loop_meta: Dict[str, Any], meta: Optional[Dict[str, Any]]) -> None:
    if not isinstance(meta, dict):
        return
    usage = meta.get("usage")
    if not isinstance(usage, dict):
        return
    pt = usage.get("prompt_tokens")
    if not isinstance(pt, (int, float)) or int(pt) <= 0:
        return
    prev = int(loop_meta.get("peak_prompt_tokens") or 0)
    loop_meta["peak_prompt_tokens"] = max(prev, int(pt))


# Runtime override for compact min tokens (set via API, not persisted)
_compact_min_tokens_override: Optional[int] = None

def set_compact_min_tokens(val: int) -> None:
    """Set runtime override for compact min tokens (0 = disable)."""
    global _compact_min_tokens_override
    _compact_min_tokens_override = max(0, int(val))

def _get_compact_min_tokens() -> int:
    """Get compact trigger threshold in tokens (default: 30000).

    Priority: runtime override > env var > default 30000.
    """
    if _compact_min_tokens_override is not None:
        return _compact_min_tokens_override
    tok = _ea.pick_nonempty(*_ea.CONTEXT_COMPACT_MIN_TOKENS)
    if tok:
        return max(1000, int(tok))
    return 30000


# Known model context windows (beyond MiniMax catalog)
_KNOWN_CONTEXT_WINDOWS: Dict[str, int] = {
    "deepseek": 128_000,
    "gpt-4": 128_000,
    "gpt-3.5": 16_000,
    "claude": 200_000,
    "gemini": 1_000_000,
    "qwen": 128_000,
    "glm": 128_000,
    "yi": 128_000,
    "moonshot": 128_000,
    "baichuan": 128_000,
    "mistral": 128_000,
    "llama": 128_000,
}


def _resolve_context_limit(model_name: Optional[str] = None) -> int:
    """Resolve the model's context window limit in tokens.

    Priority:
    1. SEED_LLM_CONTEXT_SIZE env override
    2. MINIMAX_CONTEXT_WINDOWS lookup by model name
    3. _KNOWN_CONTEXT_WINDOWS prefix match
    4. Fallback: min_tok * 10 (legacy behavior)
    """
    override = _ea.pick_nonempty("SEED_LLM_CONTEXT_SIZE")
    if override:
        return max(1000, int(override))
    if model_name:
        try:
            from seed.core.llm_exec import MINIMAX_CONTEXT_WINDOWS
            _mn = str(model_name).strip().lower()
            # Try exact match first
            ctx = MINIMAX_CONTEXT_WINDOWS.get(_mn, 0)
            if ctx <= 0:
                for _key, _val in MINIMAX_CONTEXT_WINDOWS.items():
                    if _mn == _key.lower() or _mn.startswith(_key.lower()):
                        ctx = _val
                        break
            if ctx > 0:
                logger.info("[CTX_LIMIT] model=%s resolved=%s (minimax)", model_name, ctx)
                return ctx
            # Try known model prefixes (including after provider/ prefix)
            _match_names = [_mn]
            if "/" in _mn:
                _match_names.append(_mn.split("/", 1)[1])
            for _prefix, _ctx in _KNOWN_CONTEXT_WINDOWS.items():
                for _n in _match_names:
                    if _n.startswith(_prefix):
                        logger.info("[CTX_LIMIT] model=%s resolved=%s (known=%s)", model_name, _ctx, _prefix)
                        return _ctx
            logger.info("[CTX_LIMIT] model=%s not found in any catalog, fallback", model_name)
        except Exception as e:
            logger.info("[CTX_LIMIT] lookup failed: %s", e)
    min_tok = _get_compact_min_tokens()
    _fallback = min_tok * 10
    logger.info("[CTX_LIMIT] fallback min_tok=%s context_limit=%s", min_tok, _fallback)
    return _fallback


def estimate_context_usage(
    messages: List[Dict[str, Any]],
    model_name: Optional[str] = None,
) -> Dict[str, int]:
    """Context usage metadata for Web UI (prompt_tokens filled only from API)."""
    context_limit = _resolve_context_limit(model_name)
    return {
        "prompt_tokens": 0,
        "context_limit": context_limit,
        "message_count": len(messages),
        "compact_min_tokens": _get_compact_min_tokens(),
    }


def build_context_usage_snapshot(
    messages: List[Dict[str, Any]],
    meta: Optional[Dict[str, Any]] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Context bar for Web UI — API ``usage.prompt_tokens`` only."""
    snap: Dict[str, Any] = dict(estimate_context_usage(messages, model_name=model_name))
    pt = _api_prompt_tokens(messages, meta=meta)
    if pt > 0:
        snap["prompt_tokens"] = pt
        snap["source"] = "api"
        if isinstance(meta, dict):
            usage = meta.get("usage")
            if isinstance(usage, dict):
                snap["completion_tokens"] = int(usage.get("completion_tokens") or 0)
    return snap


def build_context_usage_from_run(
    messages: List[Dict[str, Any]],
    *,
    loop_meta: Optional[Dict[str, Any]] = None,
    last_meta: Optional[Dict[str, Any]] = None,
    model_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Context snapshot for persistence — always prefer ``peak_prompt_tokens`` from the run."""
    peak = 0
    if isinstance(loop_meta, dict):
        peak = int(loop_meta.get("peak_prompt_tokens") or 0)
    meta_for_snap: Optional[Dict[str, Any]] = None
    usage: Dict[str, Any] = {}
    if isinstance(last_meta, dict) and isinstance(last_meta.get("usage"), dict):
        usage.update(last_meta["usage"])
    if peak > 0:
        usage["prompt_tokens"] = max(int(usage.get("prompt_tokens") or 0), peak)
    if usage:
        meta_for_snap = {"usage": usage}
    snap = build_context_usage_snapshot(messages, meta_for_snap, model_name=model_name)
    snap["compact_min_tokens"] = _get_compact_min_tokens()
    if peak > int(snap.get("prompt_tokens") or 0):
        snap["prompt_tokens"] = peak
        snap["source"] = "api"
    if peak > 0:
        snap["peak_prompt_tokens"] = peak
    return snap


def apply_context_usage_metadata(
    metadata: Dict[str, Any],
    snap: Dict[str, Any],
    *,
    updated_at: str = "",
) -> None:
    """Write API-only context bar values into session metadata (for page refresh)."""
    pt = int(snap.get("prompt_tokens") or 0)
    peak = int(snap.get("peak_prompt_tokens") or pt)
    best = max(pt, peak)
    metadata["context_usage"] = {
        "prompt_tokens": best,
        "peak_prompt_tokens": peak if peak > 0 else best,
        "context_limit": int(snap.get("context_limit") or 0),
        "message_count": int(snap.get("message_count") or 0),
        "compact_min_tokens": int(snap.get("compact_min_tokens") or _get_compact_min_tokens()),
        "updated_at": updated_at,
    }
    if snap.get("source"):
        metadata["context_usage"]["source"] = snap["source"]


def _format_transcript_for_summary(chunks: List[Dict[str, Any]], max_chars: int) -> str:
    from seed.core.llm_exec import msg_text_to_str

    lines: List[str] = []
    for m in chunks:
        role = m.get("role", "?")
        content = m.get("content")
        if content is None and m.get("tool_calls"):
            content = json.dumps(m.get("tool_calls"), ensure_ascii=False)[:2000]
        text = msg_text_to_str(content).strip()
        if len(text) > 8000:
            text = text[:4000] + "\n...[mid omitted]...\n" + text[-4000:]
        lines.append(f"### {role}\n{text}\n")
    blob = "\n".join(lines)
    if len(blob) <= max_chars:
        return blob
    head = max_chars // 2
    tail = max_chars - head
    return (
        blob[:head]
        + "\n\n...[transcript truncated for summarizer input]...\n\n"
        + blob[-tail:]
    )


def _get_compact_recent_max_chars() -> int:
    """Character budget for the post-compact recent tail (0 disables)."""
    raw = _ea.pick_default("120000", *_ea.CONTEXT_COMPACT_RECENT_MAX_CHARS)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 120000


def _messages_visible_chars(messages: List[Dict[str, Any]]) -> int:
    from seed.core.llm_exec import msg_text_to_str

    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        total += len(str(message.get("role") or ""))
        total += len(msg_text_to_str(message.get("content")))
        if message.get("tool_calls"):
            try:
                total += len(json.dumps(message.get("tool_calls"), ensure_ascii=False))
            except Exception:
                total += len(str(message.get("tool_calls")))
    return total


def _compact_recent_message_for_projection(
    message: Dict[str, Any],
    *,
    max_content_chars: int,
) -> bool:
    """Shrink one oversized recent-tail message in the API projection only."""
    from seed.core.llm_exec import msg_text_to_str

    content = msg_text_to_str(message.get("content"))
    if len(content) <= max_content_chars:
        return False

    head_chars = max(1000, max_content_chars // 2)
    tail_chars = max(1000, max_content_chars - head_chars)
    if head_chars + tail_chars >= len(content):
        return False

    role = str(message.get("role") or "?")
    name = str(message.get("name") or message.get("tool") or "").strip()
    title = f"Role: {role}" + (f"\nName: {name}" if name else "")
    replacement = (
        "[Recent message compacted in API projection; persisted history keeps the full original]\n"
        f"{title}\n"
        f"Original content chars: {len(content)}\n"
        "Reason: recent tail exceeded SEED_CONTEXT_COMPACT_RECENT_MAX_CHARS.\n\n"
        "Head excerpt:\n"
        f"{content[:head_chars].rstrip()}\n\n"
        "...[middle omitted from projection]...\n\n"
        "Tail excerpt:\n"
        f"{content[-tail_chars:].lstrip()}"
    )
    message["content"] = replacement
    message["_recent_tail_compacted"] = True
    return True


def _compact_recent_tail_for_projection(
    recent: List[Dict[str, Any]],
    *,
    max_chars: int,
) -> Dict[str, int]:
    """Apply role-prioritized recent-tail shrinking to projection messages."""
    before = _messages_visible_chars(recent)
    if max_chars <= 0 or before <= max_chars:
        return {"before": before, "after": before, "count": 0}

    latest_user_idx = -1
    for i, message in enumerate(recent):
        if isinstance(message, dict) and message.get("role") == "user":
            latest_user_idx = i

    def _indices_for_role(role: str, *, include_latest_user: bool = True) -> List[int]:
        idxs: List[int] = []
        for i, message in enumerate(recent):
            if not isinstance(message, dict) or message.get("role") != role:
                continue
            if role == "user" and not include_latest_user and i == latest_user_idx:
                continue
            idxs.append(i)
        return sorted(
            idxs,
            key=lambda idx: len(str(recent[idx].get("content") or "")),
            reverse=True,
        )

    compacted = 0
    # Keep the latest user instruction as raw as possible; compact it only if
    # tool/assistant/older-user shrinking still cannot satisfy the budget.
    passes = [
        (_indices_for_role("tool"), 12000),
        (_indices_for_role("assistant"), 16000),
        (_indices_for_role("user", include_latest_user=False), 24000),
        ([latest_user_idx] if latest_user_idx >= 0 else [], 32000),
    ]
    for indices, per_message_max in passes:
        if _messages_visible_chars(recent) <= max_chars:
            break
        for idx in indices:
            if idx < 0 or idx >= len(recent):
                continue
            if _messages_visible_chars(recent) <= max_chars:
                break
            target_chars = min(per_message_max, max(1000, max_chars // 2))
            if _compact_recent_message_for_projection(
                recent[idx],
                max_content_chars=target_chars,
            ):
                compacted += 1

    after = _messages_visible_chars(recent)
    return {"before": before, "after": after, "count": compacted}



def _get_compact_summarizer_max_tokens() -> int:
    """Completion budget for compact summarizer calls (independent of ``SEED_LLM_MAX_TOKENS``)."""
    raw = _ea.pick_default("4096", *_ea.CONTEXT_COMPACT_SUMMARIZER_MAX_TOKENS)
    try:
        return max(256, min(int(raw), 65536))
    except (TypeError, ValueError):
        return 4096


def _default_compact_summarizer_system_prompt(sum_max_tok: int) -> str:
    return (
        "You compress prior agent chat for continuation. "
        "Output structured bullet points in the same language as the transcript. "
        "Preserve continuation-critical state: user goals, current task status, "
        "files changed or inspected, shell commands, test results, error messages, "
        "tool names used, decisions already made, blockers, and unresolved questions. "
        "Prefer concise summaries, but do not make them so short that the next agent "
        "would need to rediscover important context. "
        "Do not invent facts.\n\n"
        "TRANSIENT-FACT RULE (CRITICAL): Any runtime state that can change "
        "silently — e.g. process PIDs, listening ports, 'running/stopped' "
        "status, temp files, cwd, currently-open sessions — MUST be written "
        "as a snapshot, not as a lasting fact. Format such lines like:\n"
        "  - 『截至压缩时』PID 26364 监听 3001（需重新核对）\n"
        "  - 『As of compression』port 3000 was listening on PID 18064 (re-verify before use)\n"
        "Never write an unqualified 'PID X is running' / 'port Y is up' — "
        "the downstream agent will treat that as current truth and skip "
        "re-checking, which causes wrong conclusions when the process has "
        "since died.\n\n"
        "LENGTH POLICY: The hard generation budget is "
        f"{sum_max_tok} tokens, configured by SEED_CONTEXT_COMPACT_SUMMARIZER_MAX_TOKENS. "
        "Stay concise by default, but use the available budget when needed to preserve "
        "critical continuation state."
    )


def _compact_summarizer_system_prompt(sum_max_tok: int) -> str:
    """Load optional ``SEED_CONTEXT_COMPACT_SUMMARIZER_PROMPT_FILE`` or use the default."""
    raw = _ea.pick_nonempty(*_ea.CONTEXT_COMPACT_SUMMARIZER_PROMPT_FILE)
    if raw:
        try:
            path = Path(raw).expanduser()
            if path.is_file():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text.replace("{sum_max_tok}", str(sum_max_tok))
        except Exception:
            logger.debug("compact summarizer prompt file read failed", exc_info=True)
    return _default_compact_summarizer_system_prompt(sum_max_tok)


def _recent_tool_char_ratio(messages: List[Dict[str, Any]]) -> float:
    from seed.core.llm_exec import msg_text_to_str

    total = 0
    tool_chars = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        chars = len(msg_text_to_str(message.get("content")))
        if message.get("tool_calls"):
            try:
                chars += len(json.dumps(message.get("tool_calls"), ensure_ascii=False))
            except Exception:
                chars += len(str(message.get("tool_calls")))
        total += chars
        if role == "tool":
            tool_chars += chars
    if total <= 0:
        return 0.0
    return tool_chars / total


def effective_keep_user_rounds(
    default_keep: int,
    *,
    trigger_tokens: int,
    min_tokens: int,
    recent_tool_char_ratio: float,
) -> tuple[int, str]:
    """Rule-based keep adjustment (never LLM). Returns ``(keep, reason)``."""
    keep = max(1, int(default_keep))
    reasons: List[str] = []
    if not _context_compact_adaptive_keep_enabled():
        return keep, ""
    if trigger_tokens >= int(min_tokens * 1.5):
        new_keep = max(1, keep - 1)
        if new_keep < keep:
            keep = new_keep
            reasons.append("trigger>=1.5x_min")
    if recent_tool_char_ratio >= 0.75:
        new_keep = max(1, keep - 1)
        if new_keep < keep:
            keep = new_keep
            reasons.append("tool_ratio>=0.75")
    return keep, ",".join(reasons)


def _coalesce_llm_visible_text(
    content: str,
    meta: Optional[Dict[str, Any]],
    *,
    max_chars: int = 12000,
) -> str:
    """Prefer assistant content; fall back to reasoning when thinking models emit CoT only."""
    text = (content or "").strip()
    if text:
        return text[:max_chars] if len(text) > max_chars else text
    if not isinstance(meta, dict):
        return ""
    for key in ("reasoning_content", "reasoning"):
        rc = meta.get(key)
        if isinstance(rc, str) and rc.strip():
            text = rc.strip()
            if len(text) > max_chars:
                head = max_chars // 2
                tail = max_chars - head
                marker = "\n\n...[reasoning truncated for compact summary]...\n\n"
                text = text[:head] + marker + text[-tail:]
            return text
    return ""


def _summarizer_llm(fallback: LLMAPIExecutor) -> LLMAPIExecutor:
    """If ``SEED_CONTEXT_COMPACT_SUMMARIZER_*`` (alias ``CODEAGENT_*``) are set, create a
    dedicated summarizer executor; otherwise return the fallback (main LLM).

    Inherits API key and auth scheme from the main LLM (fallback) so the
    summarizer can authenticate even when ``SEED_LLM_API_KEY`` is not set
    as an environment variable (e.g. credentials come from a preset)."""
    url = _ea.pick_nonempty(*_ea.CONTEXT_COMPACT_SUMMARIZER_BASEURL)
    mod = _ea.pick_nonempty(*_ea.CONTEXT_COMPACT_SUMMARIZER_MODEL)
    max_tok = _get_compact_summarizer_max_tokens()
    if url and mod:
        from seed.core.llm_exec import get_llm_executor

        return get_llm_executor(
            baseURL=url,
            model=mod,
            api_key=fallback.api_key,
            auth_scheme=fallback.auth_scheme,
            max_tokens=max_tok,
        )
    return fallback




def default_system_prompt() -> str:
    """Explicit env override, else config plane (if resolvable), else built-in default."""
    explicit = _ea.pick_nonempty(*_ea.SYSTEM_PROMPT)
    if explicit.strip():
        return explicit.strip()
    try:
        from seed.core.config_plane import build_system_prompt

        base = build_system_prompt()
        # Multi-agent core memory (persona/memory.md) — best-effort append.
        try:
            from seed.core.paths import agent_id_default, agent_persona_memory_path

            aid = agent_id_default()
            p = agent_persona_memory_path(aid)
            if p.is_file():
                try:
                    text = p.read_text(encoding="utf-8").strip()
                except OSError:
                    text = ""
                if text:
                    max_chars = int(
                        _ea.pick_nonempty(*_ea.PERSONA_MEMORY_MAX_CHARS) or "4000"
                    )
                    max_chars = max(200, min(max_chars, 50_000))
                    if len(text) > max_chars:
                        text = text[: max_chars - 20].rstrip() + "\n…[已截断]"
                    base = (
                        base.rstrip()
                        + "\n\n---\n"
                        + f"## Persona core memory (`agents/{aid}/persona/memory.md`)\n\n"
                        + text
                        + "\n"
                    )
        except Exception:
            pass
        return base
    except Exception:
        return DEFAULT_SYSTEM


def maybe_compact_context_messages(
    messages: List[Dict[str, Any]],
    llm: LLMAPIExecutor,
    *,
    api_prompt_tokens: Optional[int] = None,
    persisted_context_usage: Optional[Dict[str, Any]] = None,
    loop_meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    If enabled and the message tail (excluding system) exceeds a token threshold,
    summarize older turns into the system prompt and drop older raw messages —
    keeps the last ``KEEP_USER_ROUNDS`` (default 3) verbatim.

    **链式摘要**：将压缩结果写入 ``old[-1][\"_compact_summary\"]``（边界消息），
    下一轮压缩时检测已有摘要并叠加（而非全量重算）。
    返回压缩信息字典（供调用者写回持久化消息），或 ``None``（未触发压缩）。

    Args:
        api_prompt_tokens: Explicit trigger token count (e.g. current segment peak).
        persisted_context_usage: Session ``metadata.context_usage`` dict.
        loop_meta: In-flight tool-loop meta (``peak_prompt_tokens``).

        Compact triggers when ``resolve_compact_trigger_tokens(...)`` exceeds
        ``SEED_CONTEXT_COMPACT_MIN_TOKENS``. No local message estimation.

    Env (``CODEAGENT_*`` aliases still honored):
      SEED_CONTEXT_COMPACT=1
      SEED_CONTEXT_COMPACT_KEEP_USER_ROUNDS (default 3) — full turns to preserve
      SEED_CONTEXT_COMPACT_RECENT_MAX_CHARS (default 120000) — recent tail projection budget
      SEED_CONTEXT_COMPACT_SUMMARIZER_BASEURL — dedicated summarizer URL (optional)
      SEED_CONTEXT_COMPACT_SUMMARIZER_MODEL   — dedicated summarizer model (optional)
      SEED_CONTEXT_SUMMARIZER_MAX_INPUT     (default 120000) — cap for summarizer input chars
    """
    if not _context_compact_enabled():
        return None
    if not messages:
        return None
    has_system = messages[0].get("role") == "system"
    body_start = 1 if has_system else 0
    if body_start and len(messages) < 3:
        return None

    min_tokens = _get_compact_min_tokens()
    if min_tokens <= 0:
        return None
    keep_rounds = int(_ea.pick_default("3", *_ea.CONTEXT_COMPACT_KEEP_USER_ROUNDS))
    recent_max_chars = _get_compact_recent_max_chars()
    max_in = int(_ea.pick_default("120000", *_ea.CONTEXT_SUMMARIZER_MAX_INPUT))
    if keep_rounds < 1:
        return None

    cur_tokens = resolve_compact_trigger_tokens(
        persisted=persisted_context_usage,
        loop_meta=loop_meta,
        api_prompt_tokens=api_prompt_tokens,
    )
    if cur_tokens <= 0:
        return None
    try:
        warn_ratio = float(_ea.pick_default("0.85", *_ea.CONTEXT_COMPACT_WARN_RATIO) or 0.85)
    except Exception:
        warn_ratio = 0.85
    warn_ratio = max(0.1, min(warn_ratio, 0.99))
    if cur_tokens >= int(min_tokens * warn_ratio):
        _warn_model = getattr(llm, "model", None)
        emit_chat_event(
            {
                "type": "context_usage",
                "prompt_tokens": int(cur_tokens),
                "compact_min_tokens": int(min_tokens),
                "context_limit": int(_resolve_context_limit(_warn_model)),
                "warn_ratio": warn_ratio,
                "message_count": int(len(messages)),
            }
        )

    body = messages[body_start:]
    # 包括所有 role=user 的消息作为轮次边界（含 _auto_continue_nudge），
    # 让自主模式的分块渐进压缩能按 chunk 边界切分。
    user_idx = [
        i for i, m in enumerate(body)
        if isinstance(m, dict) and m.get("role") == "user"
    ]

    if cur_tokens < min_tokens:
        return None

    if len(user_idx) <= keep_rounds:
        base_keep = max(1, len(user_idx) - 1)
    else:
        base_keep = keep_rounds

    recent_preview = body[user_idx[len(user_idx) - base_keep] :] if user_idx else body
    tool_ratio = _recent_tool_char_ratio(recent_preview)
    effective_keep, adaptive_reason = effective_keep_user_rounds(
        base_keep,
        trigger_tokens=cur_tokens,
        min_tokens=min_tokens,
        recent_tool_char_ratio=tool_ratio,
    )

    cut = user_idx[len(user_idx) - effective_keep]
    old = body[:cut]
    recent = body[cut:]
    if not old:
        recent_stats = _compact_recent_tail_for_projection(
            recent,
            max_chars=recent_max_chars,
        )
        if recent_stats["count"] > 0:
            messages[:] = ([messages[0]] if has_system else []) + recent
            emit_chat_event(
                {
                    "type": "context_recent_tail_compact",
                    "compacted_messages": int(recent_stats["count"]),
                    "recent_chars_before": int(recent_stats["before"]),
                    "recent_chars_after": int(recent_stats["after"]),
                    "recent_max_chars": int(recent_max_chars),
                    "prompt_tokens_before": int(cur_tokens),
                }
            )
        return None

    # ── 增量摘要检测 ──
    # 检查 old 中的消息是否已有之前写的 _compact_summary，取最晚的一条
    _prior_summary: str | None = None
    for m in old:
        cs = m.get("_compact_summary")
        if isinstance(cs, str) and cs.strip():
            _prior_summary = cs.strip()

    # 构建 transcript：如有旧摘要，拼在前面
    transcript = _format_transcript_for_summary(old, max_in)
    if _prior_summary:
        transcript = (
            "[Previous compact summary]\n"
            + _prior_summary
            + "\n\n[New messages since last compact]\n"
            + transcript
        )

    sum_max_tok = _get_compact_summarizer_max_tokens()

    sum_messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": _compact_summarizer_system_prompt(sum_max_tok),
        },
        {
            "role": "user",
            "content": "Transcript to compress:\n\n" + transcript,
        },
    ]
    if is_chat_cancelled():
        return None

    summarizer = _summarizer_llm(llm)
    try:
        from seed.core.projection_audit import persist_llm_projection_audit

        persist_llm_projection_audit(
            sum_messages,
            kind="compact_summarizer",
            round_index=0,
            tools=[],
            extra={"trigger": "maybe_compact_context_messages"},
        )
    except Exception:
        logger.debug("compact summarizer projection audit failed", exc_info=True)
    try:
        summary, _meta = summarizer.generate(
            sum_messages,
            tools=None,
            enable_thinking=False,
            max_tokens=sum_max_tok,
        )
    except LLMError as e:
        logger.warning("Context compact skipped (summarizer LLM error): %s", e)
        return None

    summary = _coalesce_llm_visible_text(summary or "", _meta)
    if not summary:
        logger.warning(
            "Context compact skipped (empty summary; cur_tokens=%s min_tokens=%s "
            "transcript_chars=%s max_tokens=%s model=%s)",
            cur_tokens,
            min_tokens,
            len(transcript),
            sum_max_tok,
            getattr(summarizer, "model", "?"),
        )
        return None

    # ── 链式：与旧摘要叠加（如有） ──
    combined = summary
    if _prior_summary:
        combined = _prior_summary + "\n\n[continued]\n\n" + summary
    # 记录压缩发生的时间，后续只读不改
    _compact_ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    combined = f"### 📅 摘要生成时间: {_compact_ts}\n\n" + combined

    boundary_message = old[-1]
    boundary_source_idx = boundary_message.get("_source_idx")
    if boundary_source_idx is None:
        boundary_source_idx = body_start + cut - 1
    else:
        try:
            boundary_source_idx = int(boundary_source_idx)
        except (TypeError, ValueError):
            boundary_source_idx = body_start + cut - 1

    # 写入 _compact_summary 到边界消息（old 中最后一条，投影副本）
    boundary_message["_compact_summary"] = combined

    sys_msg = messages[0]
    base = strip_compact_block_from_system(str(sys_msg.get("content") or ""))
    block = (
        "\n\n<<<SEED_COMPACT>>>\n"
        "## Earlier conversation (compressed)\n"
        f"{combined}\n"
        "<<<END_SEED_COMPACT>>>\n"
    )
    sys_msg["content"] = base + block
    recent_stats = _compact_recent_tail_for_projection(
        recent,
        max_chars=recent_max_chars,
    )
    messages[:] = [sys_msg] + recent
    emit_chat_event(
        {
            "type": "context_compact",
            "dropped_messages": int(len(old)),
            "kept_user_rounds": int(effective_keep),
            "effective_keep_user_rounds": int(effective_keep),
            "adaptive_keep_reason": adaptive_reason,
            "summary_chars": int(len(combined)),
            "recent_compacted_messages": int(recent_stats["count"]),
            "recent_chars_before": int(recent_stats["before"]),
            "recent_chars_after": int(recent_stats["after"]),
            "recent_max_chars": int(recent_max_chars),
            "compact_min_tokens": int(min_tokens),
            "prompt_tokens_before": int(cur_tokens),
            "update_agent_state_hint": True,
        }
    )
    logger.info(
        "Context compact: dropped %s messages, kept %s user rounds verbatim",
        len(old),
        effective_keep,
    )

    # ── 返回压缩信息供调用者写回 chat_sess.messages ──
    return {
        "boundary_idx": body_start + cut - 1,
        "boundary_source_idx": boundary_source_idx,
        "compact_summary": combined,
        "dropped_count": len(old),
        "effective_keep_user_rounds": int(effective_keep),
        "adaptive_keep_reason": adaptive_reason,
    }


def try_mid_loop_compact_if_needed(
    messages: List[Dict[str, Any]],
    llm: LLMAPIExecutor,
    *,
    loop_meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Compact during a tool loop when peak prompt tokens exceed the threshold."""
    if not _context_compact_mid_loop_enabled():
        return None
    min_tokens = _get_compact_min_tokens()
    if min_tokens <= 0:
        return None
    peak = int(loop_meta.get("peak_prompt_tokens") or 0)
    if peak < min_tokens:
        return None
    result = maybe_compact_context_messages(
        messages,
        llm,
        api_prompt_tokens=peak,
        loop_meta=loop_meta,
    )
    if result is not None:
        loop_meta["peak_prompt_tokens"] = 0
        loop_meta["compact_count"] = int(loop_meta.get("compact_count") or 0) + 1
    return result


def _truncate_tool_output(text: str, *, tool_name: str = "tool") -> str:
    from seed.core.tool_output_cap import cap_tool_output_for_context

    return cap_tool_output_for_context(text, tool_name=tool_name)


"""OpenAI-style chat completions loop with tool execution."""


def _stream_llm_round(
    llm: LLMAPIExecutor,
    messages: List[Dict[str, Any]],
    tool_schema: Optional[List[Dict[str, Any]]],
    on_text_delta: Optional[Callable[[str], None]],
    on_reasoning_delta: Optional[Callable[[str], None]],
    *,
    enable_thinking: Optional[bool] = None,
    reasoning_effort: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    """Run one LLM round with per-token streaming (called in a thread).

    ``on_text_delta`` receives the **cumulative** text (full round text so far)
    so the frontend can compute the delta internally.
    """
    full_text = ""
    tool_calls: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}

    for event in llm.generate_stream(
        messages,
        tools=tool_schema,
        enable_thinking=enable_thinking,
        reasoning_effort=reasoning_effort,
    ):
        if is_chat_cancelled():
            break
        et = event.get("type")
        if et == "delta":
            text = event.get("text", "")
            full_text += text
            if on_text_delta:
                try:
                    on_text_delta(full_text)
                except Exception:
                    pass
        elif et == "reasoning_delta":
            text = event.get("text", "")
            if on_reasoning_delta:
                try:
                    on_reasoning_delta(text)
                except Exception:
                    pass
        elif et == "done":
            tool_calls = event.get("tool_calls") or []
            metadata = event.get("metadata", {})
            break

    try:
        from seed.core.usage_accumulator import record_round_usage

        record_round_usage(metadata)
    except Exception:
        pass

    return full_text, tool_calls, metadata


def _emit_context_usage_snapshot(
    messages: List[Dict[str, Any]],
    meta: Optional[Dict[str, Any]] = None,
    model_name: Optional[str] = None,
    *,
    loop_meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Push in-flight context size to Web UI (tool-loop rounds)."""
    try:
        snap = build_context_usage_from_run(
            messages,
            loop_meta=loop_meta,
            last_meta=meta,
            model_name=model_name,
        )
        emit_chat_event({"type": "context_usage", **snap})
    except Exception:
        logger.debug("context_usage snapshot emit failed", exc_info=True)


async def _execute_tool_with_cancel(
    executor: ToolExecutor,
    tool_name: str,
    args_obj: Dict[str, Any],
) -> str:
    """Run one tool, polling cancel so stop requests are not blocked until tool returns."""
    if is_chat_cancelled():
        return f"Error executing tool {tool_name!r}: cancelled by user"

    exec_task = asyncio.create_task(
        executor.execute_with_validation_async(tool_name, args_obj)
    )
    try:
        while not exec_task.done():
            if is_chat_cancelled():
                exec_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await exec_task
                return f"Error executing tool {tool_name!r}: cancelled by user"
            await asyncio.sleep(0.2)
        result = await exec_task
    except asyncio.CancelledError:
        return f"Error executing tool {tool_name!r}: cancelled by user"
    except Exception as e:
        logger.exception("tool %s failed", tool_name)
        return f"Error executing tool {tool_name!r}: {e}"

    out = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return out


async def run_llm_tool_loop(
    llm: LLMAPIExecutor,
    executor: ToolExecutor,
    *,
    messages: List[Dict[str, Any]],
    registry: ToolRegistry,
    max_tool_rounds: int = 16,
    on_round_persist: Optional[Callable[[List[Dict[str, str]], List[str]], None]] = None,
    on_text_delta: Optional[Callable[[str], None]] = None,
    on_reasoning_delta: Optional[Callable[[str], None]] = None,
    on_check_pending_messages: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    on_compact: Optional[Callable[[Optional[Dict[str, Any]]], None]] = None,
    enable_thinking: Optional[bool] = None,
    reasoning_effort: Optional[str] = None,
) -> Tuple[str, Dict[str, Any], List[str], List[Dict[str, str]], Dict[str, Any]]:
    tools_used: List[str] = []
    tool_trace: List[Dict[str, str]] = []
    loop_meta: Dict[str, Any] = {"rounds": 0, "stopped_reason": None}
    _model_name: Optional[str] = getattr(llm, "model", None)

    oai_tools = registry_to_openai_tools(registry)
    tool_schema = oai_tools if oai_tools else None

    last_meta: Dict[str, Any] = {}

    try:
        for round_i in range(max(1, int(max_tool_rounds))):
            loop_meta["rounds"] = round_i + 1

            # ── 停止信号检查 ──
            if is_chat_cancelled():
                loop_meta["stopped_reason"] = "cancelled"
                reply = ""
                for m in reversed(messages):
                    if isinstance(m, dict) and m.get("role") == "assistant":
                        c = m.get("content")
                        reply = c if isinstance(c, str) else str(c or "")
                        break
                return reply, last_meta, tools_used, tool_trace, loop_meta

            # ── 运行时消息注入：第二个用户消息在工具链中间到达 ──
            if on_check_pending_messages:
                try:
                    pending = on_check_pending_messages()
                except Exception:
                    logger.exception("check_pending_messages failed")
                    pending = []
                if pending:
                    messages.extend(pending)
                    # 不直接 return，而是 continue 让下一轮循环重新调用 LLM
                    # 处理新注入的用户消息，避免 pending 消息被忽略
                    continue

            _audit_path = None
            try:
                from seed.core.projection_audit import persist_llm_projection_audit

                _audit_path = persist_llm_projection_audit(
                    messages,
                    kind="chat",
                    round_index=round_i + 1,
                    tools=tool_schema,
                    extra={"max_tool_rounds": int(max_tool_rounds)},
                )
            except Exception:
                logger.debug("chat projection audit failed", exc_info=True)
            try:
                from seed.core.trace_audit import append_trace_event

                append_trace_event(
                    "llm_request",
                    round=round_i + 1,
                    audit_file=_audit_path.name if _audit_path else "",
                    message_count=len(messages),
                    tools_count=len(tool_schema or []),
                    max_tool_rounds=int(max_tool_rounds),
                )
            except Exception:
                logger.debug("trace llm_request failed", exc_info=True)

            # --- Streaming LLM round (per-token) ---
            content, tool_calls, meta = await asyncio.to_thread(
                _stream_llm_round,
                llm,
                messages,
                tool_schema,
                on_text_delta,
                on_reasoning_delta,
                enable_thinking=enable_thinking,
                reasoning_effort=reasoning_effort,
            )
            last_meta = meta or {}
            _record_peak_prompt_tokens(loop_meta, last_meta)
            try:
                from seed.core.projection_audit import append_projection_audit_usage

                _usage = last_meta.get("usage") if isinstance(last_meta, dict) else None
                append_projection_audit_usage(
                    _audit_path,
                    _usage if isinstance(_usage, dict) else None,
                    meta={
                        "tool_calls": int(len(tool_calls or [])),
                        "finish_reason": str(last_meta.get("finish_reason") or ""),
                    },
                )
            except Exception:
                logger.debug("chat projection audit usage append failed", exc_info=True)
            try:
                from seed.core.trace_audit import append_trace_event

                _usage_for_trace = last_meta.get("usage") if isinstance(last_meta, dict) else None
                append_trace_event(
                    "llm_response",
                    round=round_i + 1,
                    audit_file=_audit_path.name if _audit_path else "",
                    usage=_usage_for_trace if isinstance(_usage_for_trace, dict) else {},
                    tool_calls=int(len(tool_calls or [])),
                    finish_reason=str(last_meta.get("finish_reason") or ""),
                )
            except Exception:
                logger.debug("trace llm_response failed", exc_info=True)

            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": content}
            rc = last_meta.get("reasoning_content")
            if rc is not None:
                assistant_msg["reasoning_content"] = rc
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            _compact_result = await asyncio.to_thread(
                try_mid_loop_compact_if_needed,
                messages,
                llm,
                loop_meta=loop_meta,
            )
            if _compact_result is not None and on_compact:
                try:
                    on_compact(_compact_result)
                except Exception:
                    logger.exception("on_compact callback failed")

            if not tool_calls:
                _emit_context_usage_snapshot(
                    messages, last_meta, model_name=_model_name, loop_meta=loop_meta
                )
                if on_round_persist:
                    try:
                        on_round_persist(list(tool_trace), list(tools_used))
                    except Exception:
                        pass
                loop_meta["stopped_reason"] = "no_tool_calls"
                return content, last_meta, tools_used, tool_trace, loop_meta

            _emit_context_usage_snapshot(
                messages, last_meta, model_name=_model_name, loop_meta=loop_meta
            )

            for tc in tool_calls:
                if is_chat_cancelled():
                    loop_meta["stopped_reason"] = "cancelled"
                    reply = ""
                    for m in reversed(messages):
                        if isinstance(m, dict) and m.get("role") == "assistant":
                            c = m.get("content")
                            reply = c if isinstance(c, str) else str(c or "")
                            break
                    return reply, last_meta, tools_used, tool_trace, loop_meta

                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                name = (fn.get("name") or "").strip()
                raw_args = fn.get("arguments") if isinstance(fn.get("arguments"), str) else "{}"
                if not isinstance(raw_args, str):
                    raw_args = json.dumps(raw_args or {}, ensure_ascii=False)
                tid = str(tc.get("id") or "")
                tools_used.append(name)
                row: Dict[str, str] = {"name": name, "arguments": raw_args}
                tool_trace.append(row)

                event_id = str(uuid.uuid4())
                emit_chat_event(
                    {
                        "type": "tool_start",
                        "event_id": event_id,
                        "tool": name,
                        "arguments": raw_args,
                    }
                )
                try:
                    from seed.core.trace_audit import append_trace_event

                    append_trace_event(
                        "tool_start",
                        round=round_i + 1,
                        event_id=event_id,
                        tool_call_id=tid,
                        tool=name,
                        arguments=raw_args,
                    )
                except Exception:
                    logger.debug("trace tool_start failed", exc_info=True)
                try:
                    try:
                        args_obj = json.loads(raw_args) if raw_args.strip() else {}
                    except json.JSONDecodeError:
                        args_obj = {}
                    out = await _execute_tool_with_cancel(executor, name, args_obj)
                except Exception as e:
                    logger.exception("tool %s failed", name)
                    out = f"Error executing tool {name!r}: {e}"

                if is_chat_cancelled() and "cancelled by user" in out:
                    loop_meta["stopped_reason"] = "cancelled"

                out = _truncate_tool_output(out, tool_name=name)

                snippet = out if len(out) <= 4000 else out[:4000] + "…"
                row["result"] = snippet

                if out:
                    chunk = 8000
                    for i in range(0, len(out), chunk):
                        emit_chat_event(
                            {
                                "type": "tool_output",
                                "event_id": event_id,
                                "tool": name,
                                "text": out[i : i + chunk],
                            }
                        )
                emit_chat_event(
                    {
                        "type": "tool_end",
                        "event_id": event_id,
                        "tool": name,
                        "arguments": raw_args,
                        "result": snippet,
                    }
                )
                try:
                    from seed.core.trace_audit import append_trace_event

                    append_trace_event(
                        "tool_end",
                        round=round_i + 1,
                        event_id=event_id,
                        tool_call_id=tid,
                        tool=name,
                        result_chars=len(out or ""),
                        result_preview=snippet,
                        cancelled=bool(loop_meta.get("stopped_reason") == "cancelled"),
                    )
                except Exception:
                    logger.debug("trace tool_end failed", exc_info=True)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tid,
                        "name": name,
                        "content": out,
                    }
                )

                if on_round_persist:
                    try:
                        on_round_persist(list(tool_trace), list(tools_used))
                    except Exception:
                        pass

                if loop_meta.get("stopped_reason") == "cancelled":
                    reply = ""
                    for m in reversed(messages):
                        if isinstance(m, dict) and m.get("role") == "assistant":
                            c = m.get("content")
                            reply = c if isinstance(c, str) else str(c or "")
                            break
                    return reply, last_meta, tools_used, tool_trace, loop_meta

        loop_meta["stopped_reason"] = "max_tool_rounds"
        reply = ""
        if on_round_persist:
            try:
                on_round_persist(list(tool_trace), list(tools_used))
            except Exception:
                pass
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "assistant":
                c = m.get("content")
                reply = c if isinstance(c, str) else str(c or "")
                break
        return reply, last_meta, tools_used, tool_trace, loop_meta
    finally:
        try:
            from seed.integrations.hooks import dispatch_hooks

            dispatch_hooks(
                "turn_end",
                {
                    "stopped_reason": loop_meta.get("stopped_reason"),
                    "tools_used": list(tools_used),
                    "rounds": loop_meta.get("rounds"),
                },
            )
        except Exception:
            pass


_TOOL_CALL_WRAPPER_RE = re.compile(
    r"<tool_call>\s*[\s\S]*?</tool_call>", re.IGNORECASE
)
_FUNCTION_BLOCK_RE = re.compile(
    r"<\s*function\s*=\s*(\w+)>\s*([\s\S]*?)</\s*function\s*>",
    re.IGNORECASE,
)
_PARAMETER_RE = re.compile(
    r"<\s*parameter\s*=\s*(\w+)>\s*([\s\S]*?)\s*</\s*parameter\s*>",
    re.IGNORECASE,
)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_ORPHAN_TOOL_CLOSE_RE = re.compile(
    r"</\s*(?:parameter|function|tool_call)\s*>",
    re.IGNORECASE,
)
_LOOSE_TOOL_OPEN_RE = re.compile(
    r"<\s*tool_call\s*>",
    re.IGNORECASE,
)
_LOOSE_FUNCTION_OPEN_RE = re.compile(
    r"<\s*function\s*=\s*\w+\s*>",
    re.IGNORECASE,
)
_LOOSE_PARAMETER_OPEN_RE = re.compile(
    r"<\s*parameter\s*=\s*\w+\s*>",
    re.IGNORECASE,
)


def strip_inline_tool_markup_from_assistant_text(text: str) -> str:
    """Remove Qwen-style pseudo-tool XML from assistant content before storing.

    Also strips orphan closing tags (e.g. ``</tool_call>``) left when HTML/Markdown
    sanitizers remove unknown opening tags, and stray opening tags without pairs.
    Additionally strips ``<think>...</think>`` chain-of-thought so it is not
    replayed into the next turn and cause self-reinforcing repetition.
    """
    if not text:
        return ""
    t = _strip_think_markup(text)
    t = _TOOL_CALL_WRAPPER_RE.sub("", t)
    t = _FUNCTION_BLOCK_RE.sub("", t)
    t = _PARAMETER_RE.sub("", t)
    t = _ORPHAN_TOOL_CLOSE_RE.sub("", t)
    t = _LOOSE_TOOL_OPEN_RE.sub("", t)
    t = _LOOSE_FUNCTION_OPEN_RE.sub("", t)
    t = _LOOSE_PARAMETER_OPEN_RE.sub("", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    # Final pass: redact bare chain-of-thought (no <think> markup at all,
    # just prose like "Here's a thinking process..." or "用户纠正了我...").
    # Required after markup strip because the markup pass is a no-op on these
    # leaks yet they still pollute history and re-trigger the model.
    t = scrub_bare_cot_from_assistant_text(t)
    return t


def parse_inline_qwen_tool_calls(text: str) -> List[Dict[str, Any]]:
    """
    Parse tool calls that some models emit inside plain text, e.g.:

        <tool_call>
        <function=example_tool_name>
        <parameter=body>...</parameter>
        </function>
        </tool_call>

    Returns OpenAI-shaped tool_call dicts (ids are synthetic).
    """
    if not text or not re.search(r"<\s*function\s*=", text, re.IGNORECASE):
        return []
    out: List[Dict[str, Any]] = []
    for m in _FUNCTION_BLOCK_RE.finditer(text):
        name, inner = m.group(1), m.group(2)
        args: Dict[str, str] = {}
        for pm in _PARAMETER_RE.finditer(inner):
            k, v = pm.group(1), pm.group(2).strip()
            args[k] = v
        if not name:
            continue
        out.append(
            {
                "id": f"call_inline_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        )
    return out


def parse_inline_json_tool_calls(text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Parse ```json ... ``` blocks that look like
    {"name": "tool", "arguments": {...}} or a list of such dicts.
    Returns (openai-shaped tool_calls, raw fence strings to strip from transcript).
    """
    out: List[Dict[str, Any]] = []
    consumed_fences: List[str] = []
    if not text or "```" not in text:
        return out, consumed_fences
    for m in _JSON_FENCE_RE.finditer(text):
        raw_inner = (m.group(1) or "").strip()
        if not raw_inner or '"' not in raw_inner:
            continue
        try:
            obj = json.loads(raw_inner)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        items: List[Dict[str, Any]] = []
        if isinstance(obj, dict):
            items = [obj]
        elif isinstance(obj, list):
            items = [x for x in obj if isinstance(x, dict)]
        batch: List[Dict[str, Any]] = []
        for item in items:
            name = item.get("name") or item.get("tool")
            if not name or not isinstance(name, str):
                continue
            args = item.get("arguments")
            if args is None:
                args = item.get("args") or item.get("parameters")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError, ValueError):
                    args = {}
            if not isinstance(args, dict):
                args = {}
            if not args:
                args = {
                    k: v
                    for k, v in item.items()
                    if k
                    not in ("name", "tool", "type", "id", "arguments", "args", "parameters")
                }
            batch.append(
                {
                    "id": f"call_inline_{uuid.uuid4().hex[:16]}",
                    "type": "function",
                    "function": {
                        "name": name.strip(),
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                }
            )
        if batch:
            out.extend(batch)
            consumed_fences.append(m.group(0))
    return out, consumed_fences


def _strip_json_fences_once_each(text: str, fences: List[str]) -> str:
    t = text
    for f in fences:
        if f in t:
            t = t.replace(f, "", 1)
    return t.strip()


def _inline_tool_parse_enabled() -> bool:
    return _ea.pick_default("1", *_ea.INLINE_TOOL_PARSE).lower() not in (
        "0",
        "false",
        "no",
    )


def _allowed_tool_names_for_loop(
    registry: ToolRegistry,
    exclude_prefixes: Optional[Sequence[str]],
) -> set:
    names = {t.name for t in registry.list_all()}
    if exclude_prefixes:
        names = {
            n
            for n in names
            if not any(n.startswith(p) for p in exclude_prefixes if p)
        }
    return names


