"""LLM tool_call arguments validation + historical self-heal."""

from __future__ import annotations

import json

from seed.core.agent_runtime import (
    _clean_invalid_tool_call_arguments,
    _sweep_empty_invalid_tc_turns,
    build_api_projection_messages,
)
from seed.core.llm_exec import _extract_tool_calls


# ── _extract_tool_calls: drop malformed args ───────────────────────────


def test_extract_tool_calls_drops_empty_arguments() -> None:
    """Streaming truncation can leave function.arguments="" — must be dropped."""
    msg = {
        "tool_calls": [
            {
                "id": "call_a",
                "type": "function",
                "function": {"name": "file_edit_tool", "arguments": ""},
            }
        ]
    }
    assert _extract_tool_calls(msg) == []


def test_extract_tool_calls_drops_partial_json_arguments() -> None:
    msg = {
        "tool_calls": [
            {
                "id": "call_b",
                "type": "function",
                "function": {"name": "bash_exec", "arguments": '{"command": "ls'},
            }
        ]
    }
    assert _extract_tool_calls(msg) == []


def test_extract_tool_calls_keeps_valid_arguments() -> None:
    msg = {
        "tool_calls": [
            {
                "id": "call_c",
                "type": "function",
                "function": {
                    "name": "file_read",
                    "arguments": '{"filepath": "/tmp/x"}',
                },
            }
        ]
    }
    out = _extract_tool_calls(msg)
    assert len(out) == 1
    assert out[0]["id"] == "call_c"
    assert out[0]["function"]["name"] == "file_read"
    assert json.loads(out[0]["function"]["arguments"]) == {"filepath": "/tmp/x"}


def test_extract_tool_calls_keeps_valid_drops_invalid_in_batch() -> None:
    msg = {
        "tool_calls": [
            {
                "id": "ok",
                "type": "function",
                "function": {"name": "echo", "arguments": '{"message": "hi"}'},
            },
            {
                "id": "bad_empty",
                "type": "function",
                "function": {"name": "echo", "arguments": ""},
            },
            {
                "id": "bad_partial",
                "type": "function",
                "function": {"name": "echo", "arguments": "{not json"},
            },
        ]
    }
    out = _extract_tool_calls(msg)
    assert [tc["id"] for tc in out] == ["ok"]


def test_extract_tool_calls_non_string_arguments_normalized_kept_if_valid() -> None:
    """If a provider gives us a dict for arguments, it gets json.dumps'd — still valid."""
    msg = {
        "tool_calls": [
            {
                "id": "call_d",
                "type": "function",
                "function": {"name": "echo", "arguments": {"message": "hi"}},
            }
        ]
    }
    out = _extract_tool_calls(msg)
    assert len(out) == 1
    assert json.loads(out[0]["function"]["arguments"]) == {"message": "hi"}


# ── _clean_invalid_tool_call_arguments: historical self-heal ──────────


def test_clean_invalid_tc_strips_malformed_keeps_message_with_text() -> None:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do x"},
        {
            "role": "assistant",
            "content": "thinking about it",
            "tool_calls": [
                {
                    "id": "bad",
                    "type": "function",
                    "function": {"name": "file_edit_tool", "arguments": ""},
                },
                {
                    "id": "ok",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"m":"hi"}'},
                },
            ],
        },
    ]
    _clean_invalid_tool_call_arguments(msgs)
    asst = msgs[2]
    assert [tc["id"] for tc in asst["tool_calls"]] == ["ok"]
    assert asst["content"] == "thinking about it"


def test_clean_invalid_tc_marks_empty_turn_for_removal() -> None:
    """A poisoned assistant turn with whitespace-only content gets removed
    entirely — its real text content is gone, only the broken tool_call
    existed."""
    msgs = [
        {
            "role": "assistant",
            "content": "   \n  ",
            "tool_calls": [
                {
                    "id": "bad",
                    "type": "function",
                    "function": {"name": "x", "arguments": ""},
                },
            ],
        },
    ]
    _clean_invalid_tool_call_arguments(msgs)
    assert "tool_calls" not in msgs[0]  # dropped
    assert msgs[0].get("_invalid_tc_drop_turn") is True
    _sweep_empty_invalid_tc_turns(msgs)
    assert msgs == []


def test_clean_invalid_tc_keeps_think_only_content() -> None:
    """An assistant turn with <think>...</think> text is *not* empty —
    we keep the message (the thinking is the only signal the user has
    that the model was mid-thought) and just drop the bad tool_call."""
    msgs = [
        {
            "role": "assistant",
            "content": "<think>...</think>",
            "tool_calls": [
                {
                    "id": "bad",
                    "type": "function",
                    "function": {"name": "x", "arguments": ""},
                },
            ],
        },
    ]
    _clean_invalid_tool_call_arguments(msgs)
    assert "tool_calls" not in msgs[0]
    assert msgs[0].get("_invalid_tc_drop_turn") is not True
    _sweep_empty_invalid_tc_turns(msgs)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "<think>...</think>"


def test_clean_invalid_tc_preserves_real_text_assistant_turn() -> None:
    """An assistant message with both real content AND a malformed tool_call
    must NOT be removed by the sweeper — its text is the only artifact left."""
    msgs = [
        {
            "role": "assistant",
            "content": "I'll do this now.",
            "tool_calls": [
                {
                    "id": "bad",
                    "type": "function",
                    "function": {"name": "x", "arguments": ""},
                },
            ],
        },
    ]
    _clean_invalid_tool_call_arguments(msgs)
    # Real text → not marked for removal, even though tool_calls got cleared.
    assert msgs[0].get("_invalid_tc_drop_turn") is not True
    _sweep_empty_invalid_tc_turns(msgs)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "I'll do this now."


def test_build_api_projection_messages_heals_real_broken_session() -> None:
    """End-to-end: a session shaped like the dc5dfa1b1be0/a081ef07 failure
    must be clean after projection (no invalid-args tool_calls survive)."""
    full_messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "ok",
            "tool_calls": [
                {
                    "id": "call_function_xingp9d2jw1t_1",
                    "type": "function",
                    "function": {
                        "name": "file_edit_tool",
                        "arguments": '{"filepath":"/a","old_text":"x","new_text":"y"}',
                    },
                }
            ],
        },
        {"role": "tool", "content": "ok", "tool_call_id": "call_function_xingp9d2jw1t_1"},
        # The poisoned turn from streaming truncation:
        {
            "role": "assistant",
            "content": "<think>...</think>",
            "tool_calls": [
                {
                    "id": "call_function_efq2r9fxz1qp_1",
                    "type": "function",
                    "function": {"name": "file_edit_tool", "arguments": ""},
                },
            ],
        },
        # The error response that *did* make it out:
        {
            "role": "tool",
            "content": "Missing required parameter: filepath",
            "tool_call_id": "call_function_efq2r9fxz1qp_1",
        },
    ]
    api = build_api_projection_messages(full_messages, max_user_rounds=10)
    # No surviving tool_call with empty/non-JSON arguments.
    for m in api:
        for tc in m.get("tool_calls") or []:
            args = tc["function"]["arguments"]
            assert isinstance(args, str) and args.strip(), f"empty args: {tc}"
            json.loads(args)  # must be valid JSON
    # The original "bad" call_id should not appear anywhere as a tool_call.id
    # in an invalid shape (it was a tool response and that's fine; the assistant
    # turn that owned it got dropped as an empty turn).
    asst_calls = []
    for m in api:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                asst_calls.append(tc["id"])
    assert "call_function_efq2r9fxz1qp_1" not in asst_calls
    # Orphan tool response must not survive — MiniMax returns HTTP 400 otherwise.
    tool_ids = [
        m.get("tool_call_id")
        for m in api
        if m.get("role") == "tool"
    ]
    assert "call_function_efq2r9fxz1qp_1" not in tool_ids
