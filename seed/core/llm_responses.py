"""OpenAI Responses API protocol adapter.

Reference (OpenAI official docs, 2026):
  https://developers.openai.com/api/docs/guides/reasoning
  https://platform.openai.com/docs/api-reference/responses

Why this module exists
----------------------
``seed`` speaks the **Chat Completions** message shape internally
(``role`` / ``content`` / ``tool_calls`` / ``tool_call_id``) because every
downstream consumer — the tool loop, session history, reasoning extraction,
usage accounting and the Web UI — is built on it.

The Responses protocol (``POST {base_url}/responses``) is *item*-shaped:
a request carries ``input[]`` and a response returns ``output[]``.  Rather
than re-shaping the whole system, we convert **at the protocol boundary**:

    OpenAI shape  --(_to_openai_responses_request)-->  Responses request
    Responses     --(_parse_openai_responses_response)->  OpenAI shape
    Responses SSE --(_iter_openai_responses_stream)-->  OpenAI-shaped deltas

This mirrors the existing ``minimax_anthropic`` adapter, so it is a proven
pattern in this codebase rather than a new invention.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, Iterator, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "RESPONSES_PROTOCOL",
    "build_responses_input",
    "to_responses_request",
    "parse_responses_response",
    "parse_responses_tools",
    "iter_responses_stream",
    "responses_usage_to_openai",
]

RESPONSES_PROTOCOL = "openai_responses"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _text_of(content: Any) -> str:
    """Flatten a Chat-Completions ``content`` value into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                txt = p.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
        return "".join(parts)
    if isinstance(content, dict):
        txt = content.get("text")
        if isinstance(txt, str):
            return txt
    return ""


def _as_json_str(value: Any) -> str:
    """Tool-call arguments must be a JSON *string* in the Responses shape."""
    if isinstance(value, str):
        return value
    if value is None:
        return "{}"
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


# ---------------------------------------------------------------------------
# request: Chat Completions shape -> Responses request body
# ---------------------------------------------------------------------------

def build_responses_input(
    messages: Iterable[Dict[str, Any]],
    *,
    include_reasoning: bool = True,
) -> tuple[List[Dict[str, Any]], str]:
    """Convert OpenAI-shaped messages into Responses ``input`` items.

    Returns ``(items, instructions)``.

    ``instructions`` carries the system prompt (Responses has a dedicated
    top-level field instead of a ``system`` message).

    ``include_reasoning`` controls whether assistant reasoning summaries are
    replayed as ``reasoning`` items.  OpenAI requires a reasoning item that
    immediately preceded a function call to be present in the next turn's
    input, otherwise the request is rejected; when ``store=false`` the raw
    (encrypted) reasoning is not available, so we replay the *summary* text
    instead.  Callers that hit a rejection can retry with
    ``include_reasoning=False``.
    """
    items: List[Dict[str, Any]] = []
    instructions_parts: List[str] = []

    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")

        # --- system -> instructions -----------------------------------------
        if role == "system":
            text = _text_of(m.get("content"))
            if text.strip():
                instructions_parts.append(text)
            continue
        if role in ("developer",):
            # The Responses API has no ``developer`` role; map it to the
            # instructions field as well.
            text = _text_of(m.get("content"))
            if text.strip():
                instructions_parts.append(text)
            continue

        # --- tool result -> function_call_output ----------------------------
        if role == "tool":
            call_id = m.get("tool_call_id") or ""
            if not call_id:
                continue
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": _text_of(m.get("content")),
                }
            )
            continue

        if role not in ("user", "assistant"):
            continue

        # --- reasoning summary -> reasoning item ----------------------------
        if role == "assistant" and include_reasoning:
            reasoning_text = ""
            for key in ("reasoning_content", "reasoning", "thinking"):
                val = m.get(key)
                if isinstance(val, str) and val.strip():
                    reasoning_text = val
                    break
            if reasoning_text.strip():
                items.append(
                    {
                        "type": "reasoning",
                        "summary": [
                            {"type": "summary_text", "text": reasoning_text},
                        ],
                    }
                )

        # --- assistant tool_calls -> function_call items ---------------------
        tool_calls = m.get("tool_calls") or []
        if role == "assistant" and tool_calls:
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function")
                if isinstance(fn, str):
                    try:
                        fn = json.loads(fn)
                    except (json.JSONDecodeError, TypeError):
                        fn = {}
                if not isinstance(fn, dict):
                    fn = {}
                name = str(fn.get("name") or "")
                if not name:
                    continue
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(tc.get("id") or ""),
                        "name": name,
                        "arguments": _as_json_str(fn.get("arguments")),
                    }
                )

        # --- regular message ------------------------------------------------
        content = m.get("content")
        if content is None and not tool_calls:
            continue
        parts = _to_responses_content(content, role=role)
        if parts:
            items.append(
                {
                    "type": "message",
                    "role": role,
                    "content": parts,
                }
            )

    instructions = "\n\n".join(instructions_parts).strip()
    return items, instructions


def _to_responses_content(content: Any, *, role: str) -> List[Dict[str, Any]]:
    """Map a Chat-Completions ``content`` value to Responses content parts."""
    text_type = "output_text" if role == "assistant" else "input_text"

    if content is None:
        return []
    if isinstance(content, str):
        if not content:
            return []
        return [{"type": text_type, "text": content}]

    if isinstance(content, dict):
        content = [content]

    if not isinstance(content, list):
        return []

    parts: List[Dict[str, Any]] = []
    for p in content:
        if isinstance(p, str):
            if p:
                parts.append({"type": text_type, "text": p})
            continue
        if not isinstance(p, dict):
            continue
        ptype = str(p.get("type") or "")
        text = p.get("text")

        if ptype in ("text", "input_text", "output_text"):
            if isinstance(text, str) and text:
                parts.append({"type": text_type, "text": text})
            continue

        if ptype in ("image_url", "input_image"):
            src = p.get("image_url") or p.get("image")
            url = src.get("url") if isinstance(src, dict) else (src if isinstance(src, str) else None)
            if url:
                part: Dict[str, Any] = {"type": "input_image", "image_url": url}
                detail = src.get("detail") if isinstance(src, dict) else None
                if detail:
                    part["detail"] = detail
                parts.append(part)
            continue

        if ptype in ("file", "input_file"):
            file_id = p.get("file_id")
            filename = p.get("filename") or p.get("name")
            file_data = p.get("file_data")
            if file_id or file_data:
                part = {"type": "input_file"}
                if file_id:
                    part["file_id"] = file_id
                if file_data:
                    part["file_data"] = file_data
                if filename:
                    part["filename"] = filename
                parts.append(part)
            continue

        if ptype == "input_audio":
            audio = p.get("input_audio")
            if isinstance(audio, dict) and audio.get("data"):
                parts.append(
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": audio.get("data"),
                            "format": audio.get("format") or "wav",
                        },
                    }
                )
            continue

        # Unknown block: keep the text if there is any so information is lost.
        if isinstance(text, str) and text:
            parts.append({"type": text_type, "text": text})

    return parts


def parse_responses_tools(
    tools: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Convert Chat-Completions tool definitions to the flat Responses shape.

    Chat Completions nests the function under ``function``; Responses keeps
    ``name`` / ``description`` / ``parameters`` at the top level.
    """
    out: List[Dict[str, Any]] = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function")
        if isinstance(fn, str):
            try:
                fn = json.loads(fn)
            except (json.JSONDecodeError, TypeError):
                fn = {}
        if not isinstance(fn, dict):
            # Already in the flat Responses shape?
            if t.get("type") == "function" and t.get("name"):
                out.append(
                    {
                        "type": "function",
                        "name": t.get("name"),
                        "description": t.get("description") or "",
                        "parameters": t.get("parameters") or {"type": "object", "properties": {}},
                    }
                )
            continue
        name = str(fn.get("name") or "")
        if not name:
            continue
        entry: Dict[str, Any] = {
            "type": "function",
            "name": name,
            "description": fn.get("description") or "",
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        }
        if fn.get("strict") is not None:
            entry["strict"] = fn["strict"]
        out.append(entry)
    return out


def to_responses_request(
    messages: Iterable[Dict[str, Any]],
    *,
    model: str,
    max_output_tokens: int = 0,
    tools: Optional[List[Dict[str, Any]]] = None,
    reasoning: Optional[Dict[str, Any]] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    tool_choice: Optional[Any] = None,
    stream: bool = False,
    extra_body: Optional[Dict[str, Any]] = None,
    include_reasoning: bool = True,
    store: bool = False,
) -> Dict[str, Any]:
    """Build a full Responses API request body."""
    items, instructions = build_responses_input(
        messages, include_reasoning=include_reasoning
    )

    params: Dict[str, Any] = {"model": model, "input": items}
    if instructions:
        params["instructions"] = instructions
    if max_output_tokens and int(max_output_tokens) > 0:
        params["max_output_tokens"] = int(max_output_tokens)
    if stream:
        params["stream"] = True
    # ``store`` defaults to true on the server, which retains the response
    # (and its content) in the provider's storage.  We keep the conversation
    # local instead.
    params["store"] = bool(store)

    if reasoning:
        params["reasoning"] = dict(reasoning)
    if temperature is not None:
        params["temperature"] = temperature
    if top_p is not None:
        params["top_p"] = top_p

    rtools = parse_responses_tools(tools)
    if rtools:
        params["tools"] = rtools
        params["tool_choice"] = tool_choice if tool_choice is not None else "auto"

    if extra_body:
        for k, v in extra_body.items():
            params.setdefault(k, v)

    return params


# ---------------------------------------------------------------------------
# response: Responses -> Chat Completions shape
# ---------------------------------------------------------------------------

def responses_usage_to_openai(usage: Any) -> Dict[str, Any]:
    """Map Responses ``usage`` onto the Chat Completions usage shape."""
    if not isinstance(usage, dict):
        return {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total = usage.get("total_tokens")
    out: Dict[str, Any] = {}
    if input_tokens is not None:
        out["prompt_tokens"] = input_tokens
    if output_tokens is not None:
        out["completion_tokens"] = output_tokens
    if total is not None:
        out["total_tokens"] = total
    else:
        try:
            out["total_tokens"] = int(input_tokens or 0) + int(output_tokens or 0)
        except (TypeError, ValueError):
            pass
    # Some gateways mirror Chat Completions keys; preserve them.
    for key in ("prompt_tokens", "completion_tokens"):
        if key not in out and usage.get(key) is not None:
            out[key] = usage[key]
    return out


def parse_responses_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a Responses API payload into the Chat Completions shape.

    The result mimics ``{"choices": [{"message": {...}}], "usage": {...}}``
    so every existing consumer (tool loop, reasoning extraction, usage
    accounting) keeps working unchanged.
    """
    if not isinstance(data, dict):
        raise ValueError("Responses payload is not an object")

    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        itype = str(item.get("type") or "")

        if itype == "message":
            for c in item.get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") in ("output_text", "text"):
                    txt = c.get("text")
                    if isinstance(txt, str) and txt:
                        content_parts.append(txt)

        elif itype == "reasoning":
            # Prefer the human-readable summary; raw CoT is usually gated.
            for s in item.get("summary") or []:
                if isinstance(s, dict) and isinstance(s.get("text"), str) and s["text"]:
                    reasoning_parts.append(s["text"])
            if not reasoning_parts:
                for c in item.get("content") or []:
                    if isinstance(c, dict) and isinstance(c.get("text"), str) and c["text"]:
                        reasoning_parts.append(c["text"])

        elif itype == "function_call":
            name = str(item.get("name") or "")
            if not name:
                continue
            call_id = str(item.get("call_id") or item.get("id") or "")
            args = item.get("arguments")
            if not isinstance(args, str):
                args = _as_json_str(args)
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                }
            )

    message: Dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts),
    }
    if reasoning_parts:
        message["reasoning_content"] = "\n\n".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = tool_calls

    usage = responses_usage_to_openai(data.get("usage"))

    out: Dict[str, Any] = {
        "id": data.get("id") or "",
        "object": "response",
        "model": data.get("model") or "",
        "status": data.get("status") or "completed",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }
    if usage:
        out["usage"] = usage

    # Surface truncation / failure details the caller may want to act on.
    incomplete = data.get("incomplete_details")
    if isinstance(incomplete, dict) and incomplete.get("reason"):
        out["incomplete_details"] = incomplete
    if data.get("error"):
        out["error"] = data["error"]
    return out


# ---------------------------------------------------------------------------
# streaming
# ---------------------------------------------------------------------------

def iter_responses_stream(
    lines: Iterable[str],
) -> Iterator[Dict[str, Any]]:
    """Parse a Responses SSE stream into Chat-Completions-shaped deltas.

    Yields the same dict shapes the existing ``stream()`` generator produces:

      - ``{"type": "delta", "text": ...}``
      - ``{"type": "reasoning_delta", "text": ...}``
      - ``{"type": "tool_calls", "tool_calls": [...]}``
      - ``{"type": "done", "content": ..., "metadata": {...}}``

    Raises ``RuntimeError`` on ``response.failed``.
    """
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    usage: Dict[str, Any] = {}
    status = "completed"

    for raw_line in lines:
        if not raw_line:
            continue
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            if payload == "[DONE]":
                break
            continue
        try:
            event = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue

        etype = str(event.get("type") or "")

        if etype == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                content_parts.append(delta)
                yield {"type": "delta", "text": delta}
            continue

        if etype == "response.reasoning_summary_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                reasoning_parts.append(delta)
                yield {"type": "reasoning_delta", "text": delta}
            continue

        if etype == "response.reasoning_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                reasoning_parts.append(delta)
                yield {"type": "reasoning_delta", "text": delta}
            continue

        if etype == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                idx = len(tool_calls)
                call_id = str(item.get("call_id") or item.get("id") or "")
                name = str(item.get("name") or "")
                slot: Dict[str, Any] = {
                    "index": idx,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": ""},
                }
                tool_calls.append(slot)
                yield {
                    "type": "tool_calls",
                    "tool_calls": [
                        {
                            "index": idx,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": ""},
                        }
                    ],
                }
            continue

        if etype == "response.function_call_arguments.delta":
            item = event.get("item_id")
            delta = event.get("delta")
            if not isinstance(delta, str) or not delta:
                continue
            # The stream carries item ids, not indexes; map by matching the
            # call id we recorded when the item was added.
            target = _find_tool_slot(tool_calls, item, event)
            if target is None:
                target = _ensure_last_slot(tool_calls)
            target["function"]["arguments"] += delta
            continue

        if etype == "response.function_call_arguments.done":
            continue

        if etype == "response.completed":
            resp = event.get("response")
            if isinstance(resp, dict):
                usage = responses_usage_to_openai(resp.get("usage")) or usage
                status = str(resp.get("status") or "completed")
                # Some servers omit the text deltas but fill the final
                # response; prefer the accumulated deltas, fall back to it.
                if not content_parts:
                    content_parts = _content_from_response(resp)
            continue

        if etype == "response.incomplete":
            resp = event.get("response")
            if isinstance(resp, dict):
                usage = responses_usage_to_openai(resp.get("usage")) or usage
                details = resp.get("incomplete_details")
                status = "incomplete"
            continue

        if etype == "response.failed":
            resp = event.get("response")
            err = None
            if isinstance(resp, dict):
                err = resp.get("error")
            raise RuntimeError(f"Responses stream failed: {json.dumps(err)[:1200]}")

        if etype == "error":
            raise RuntimeError(
                f"Responses stream error: {json.dumps(event.get('message') or event)[:1200]}"
            )

    metadata: Dict[str, Any] = {"status": status}
    if usage:
        metadata["usage"] = usage
    if reasoning_parts:
        metadata["reasoning"] = "\n\n".join(reasoning_parts)
    if tool_calls:
        metadata["tool_calls"] = [
            {
                "id": tc.get("id") or "",
                "type": "function",
                "function": {
                    "name": (tc.get("function") or {}).get("name") or "",
                    "arguments": (tc.get("function") or {}).get("arguments") or "",
                },
            }
            for tc in tool_calls
        ]
    yield {
        "type": "done",
        "content": "".join(content_parts),
        "metadata": metadata,
    }


def _content_from_response(resp: Dict[str, Any]) -> List[str]:
    parts: List[str] = []
    for item in resp.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for c in item.get("content") or []:
            if isinstance(c, dict) and isinstance(c.get("text"), str) and c["text"]:
                parts.append(c["text"])
    return parts


def _find_tool_slot(
    tool_calls: List[Dict[str, Any]],
    item_id: Any,
    event: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Locate the tool-call slot an arguments delta belongs to."""
    if not tool_calls:
        return None
    # The event may carry the output index directly.
    out_idx = event.get("output_index")
    if isinstance(out_idx, int) and 0 <= out_idx < len(tool_calls):
        return tool_calls[out_idx]
    if isinstance(item_id, str):
        for tc in tool_calls:
            if tc.get("id") == item_id:
                return tc
    return None


def _ensure_last_slot(tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not tool_calls:
        slot: Dict[str, Any] = {
            "index": 0,
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        }
        tool_calls.append(slot)
        return slot
    return tool_calls[-1]
