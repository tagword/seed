"""OpenAI Responses protocol adapter: request/response/stream conversion."""

from __future__ import annotations

import json

from seed_model_providers.model_providers import (
    _RESPONSES_REASONING_EFFORTS as _RESPONSES_EFFORTS,
)

from seed.core.llm_responses import (
    build_responses_input,
    iter_responses_stream,
    parse_responses_response,
    parse_responses_tools,
    responses_usage_to_openai,
    to_responses_request,
)

# ---------------------------------------------------------------------------
# request: messages -> input items
# ---------------------------------------------------------------------------

def test_system_message_becomes_instructions() -> None:
    items, instructions = build_responses_input(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
    )
    assert instructions == "You are helpful."
    assert len(items) == 1
    assert items[0]["type"] == "message"
    assert items[0]["role"] == "user"
    assert items[0]["content"] == [{"type": "input_text", "text": "Hi"}]


def test_assistant_text_uses_output_text_part() -> None:
    items, _ = build_responses_input([{"role": "assistant", "content": "Hello"}])
    assert items[0]["content"] == [{"type": "output_text", "text": "Hello"}]


def test_tool_message_becomes_function_call_output() -> None:
    items, _ = build_responses_input(
        [{"role": "tool", "tool_call_id": "call_1", "content": "42"}]
    )
    assert items == [
        {"type": "function_call_output", "call_id": "call_1", "output": "42"}
    ]


def test_assistant_tool_calls_become_function_call_items() -> None:
    items, _ = build_responses_input(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city":"SF"}',
                        },
                    }
                ],
            }
        ]
    )
    assert items == [
        {
            "type": "function_call",
            "call_id": "call_abc",
            "name": "get_weather",
            "arguments": '{"city":"SF"}',
        }
    ]


def test_reasoning_summary_replayed_as_reasoning_item() -> None:
    items, _ = build_responses_input(
        [
            {
                "role": "assistant",
                "content": "answer",
                "reasoning_content": "thought process",
            }
        ]
    )
    assert items[0]["type"] == "reasoning"
    assert items[0]["summary"] == [
        {"type": "summary_text", "text": "thought process"}
    ]
    assert items[1]["type"] == "message"


def test_reasoning_can_be_excluded() -> None:
    items, _ = build_responses_input(
        [{"role": "assistant", "content": "a", "reasoning_content": "r"}],
        include_reasoning=False,
    )
    assert all(i["type"] != "reasoning" for i in items)


def test_image_url_becomes_input_image() -> None:
    items, _ = build_responses_input(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://x/y.png"},
                    },
                ],
            }
        ]
    )
    parts = items[0]["content"]
    assert parts[0] == {"type": "input_text", "text": "look"}
    assert parts[1] == {"type": "input_image", "image_url": "https://x/y.png"}


def test_empty_assistant_with_tool_calls_kept() -> None:
    items, _ = build_responses_input(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "f", "arguments": "{}"},
                    }
                ],
            }
        ]
    )
    assert len(items) == 1
    assert items[0]["type"] == "function_call"


# ---------------------------------------------------------------------------
# request: tools
# ---------------------------------------------------------------------------

def test_tools_flattened_to_responses_shape() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    out = parse_responses_tools(tools)
    assert out == [
        {
            "type": "function",
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {"type": "object", "properties": {}},
        }
    ]


def test_tools_already_flat_pass_through() -> None:
    out = parse_responses_tools(
        [{"type": "function", "name": "f", "description": "d", "parameters": {}}]
    )
    assert out[0]["name"] == "f"


# ---------------------------------------------------------------------------
# request: full body
# ---------------------------------------------------------------------------

def test_to_responses_request_body() -> None:
    params = to_responses_request(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
        model="gpt-5.1",
        max_output_tokens=512,
        reasoning={"effort": "high", "summary": "auto"},
        temperature=0.5,
        stream=True,
    )
    assert params["model"] == "gpt-5.1"
    assert params["instructions"] == "sys"
    assert params["max_output_tokens"] == 512
    assert params["reasoning"]["effort"] == "high"
    assert params["stream"] is True
    # No Chat Completions leakage
    assert "messages" not in params
    assert "max_tokens" not in params
    assert "max_completion_tokens" not in params
    assert "stream_options" not in params


def test_to_responses_request_defaults_store_false() -> None:
    """We keep conversations local; the server must not retain them."""
    params = to_responses_request(
        [{"role": "user", "content": "hi"}], model="gpt-5.1"
    )
    assert params["store"] is False


# ---------------------------------------------------------------------------
# response: Responses -> Chat Completions shape
# ---------------------------------------------------------------------------

def test_parse_response_extracts_text() -> None:
    data = {
        "id": "resp_1",
        "model": "gpt-5.1",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hi there"}],
            }
        ],
    }
    out = parse_responses_response(data)
    msg = out["choices"][0]["message"]
    assert msg["role"] == "assistant"
    assert msg["content"] == "Hi there"


def test_parse_response_extracts_reasoning_summary() -> None:
    data = {
        "output": [
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "step 1"}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "done"}],
            },
        ]
    }
    out = parse_responses_response(data)
    assert out["choices"][0]["message"]["reasoning_content"] == "step 1"


def test_parse_response_extracts_tool_calls() -> None:
    data = {
        "output": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "get_weather",
                "arguments": '{"city":"SF"}',
            }
        ]
    }
    out = parse_responses_response(data)
    tcs = out["choices"][0]["message"]["tool_calls"]
    assert tcs == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"SF"}'},
        }
    ]


def test_parse_response_maps_usage() -> None:
    data = {
        "output": [],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }
    out = parse_responses_response(data)
    assert out["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_parse_response_keeps_incomplete_details() -> None:
    data = {
        "output": [],
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
    }
    out = parse_responses_response(data)
    assert out["incomplete_details"]["reason"] == "max_output_tokens"
    assert out["status"] == "incomplete"


def test_usage_helper_tolerates_garbage() -> None:
    assert responses_usage_to_openai(None) == {}
    assert responses_usage_to_openai("nope") == {}


# ---------------------------------------------------------------------------
# streaming
# ---------------------------------------------------------------------------

def _sse(*events: dict) -> list[str]:
    return [f"data: {json.dumps(e)}" for e in events]


def test_stream_text_deltas() -> None:
    lines = _sse(
        {"type": "response.created"},
        {"type": "response.output_text.delta", "delta": "Hel"},
        {"type": "response.output_text.delta", "delta": "lo"},
        {"type": "response.completed", "response": {"status": "completed"}},
    )
    events = list(iter_responses_stream(lines))
    deltas = [e for e in events if e["type"] == "delta"]
    assert [e["text"] for e in deltas] == ["Hel", "lo"]
    done = events[-1]
    assert done["type"] == "done"
    assert done["content"] == "Hello"


def test_stream_reasoning_deltas() -> None:
    lines = _sse(
        {"type": "response.reasoning_summary_text.delta", "delta": "think"},
        {"type": "response.completed", "response": {"status": "completed"}},
    )
    events = list(iter_responses_stream(lines))
    rd = [e for e in events if e["type"] == "reasoning_delta"]
    assert [e["text"] for e in rd] == ["think"]
    assert events[-1]["metadata"]["reasoning"] == "think"


def test_stream_tool_call_arguments() -> None:
    lines = _sse(
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "call_id": "call_9",
                "name": "get_weather",
            },
            "output_index": 0,
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "output_index": 0,
            "delta": '{"city":',
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_1",
            "output_index": 0,
            "delta": '"SF"}',
        },
        {"type": "response.completed", "response": {"status": "completed"}},
    )
    events = list(iter_responses_stream(lines))
    done = events[-1]
    tcs = done["metadata"]["tool_calls"]
    assert len(tcs) == 1
    assert tcs[0]["function"]["name"] == "get_weather"
    assert tcs[0]["function"]["arguments"] == '{"city":"SF"}'
    assert tcs[0]["id"] == "call_9"


def test_stream_usage_from_completed_event() -> None:
    lines = _sse(
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 4,
                    "total_tokens": 7,
                },
            },
        }
    )
    events = list(iter_responses_stream(lines))
    assert events[-1]["metadata"]["usage"]["total_tokens"] == 7


def test_stream_failed_raises() -> None:
    lines = _sse(
        {"type": "response.failed", "response": {"error": {"message": "boom"}}}
    )
    try:
        list(iter_responses_stream(lines))
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_stream_ignores_malformed_json() -> None:
    lines = [
        "data: {not json}",
        'data: {"type":"response.output_text.delta","delta":"x"}',
    ]
    events = list(iter_responses_stream(lines))
    assert any(e["type"] == "delta" for e in events)


def test_stream_done_marker_stops() -> None:
    lines = ['data: {"type":"response.output_text.delta","delta":"x"}', "data: [DONE]"]
    events = list(iter_responses_stream(lines))
    assert events[-1]["type"] == "done"


# ---------------------------------------------------------------------------
# conformance with the official OpenAI SDK contract
# ---------------------------------------------------------------------------

def test_reasoning_effort_values_match_official_spec() -> None:
    """OpenAI Responses ``reasoning.effort`` accepts exactly these values."""
    assert set(_RESPONSES_EFFORTS) == {
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    }


def test_request_keys_are_all_legal_for_responses_api() -> None:
    """The request body must not contain Chat Completions-only keys."""
    params = to_responses_request(
        [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ],
        model="gpt-5.1",
        max_output_tokens=100,
        tools=[
            {
                "type": "function",
                "function": {"name": "f", "parameters": {"type": "object"}},
            }
        ],
        reasoning={"effort": "medium", "summary": "auto"},
        temperature=0.2,
        top_p=0.9,
        stream=False,
    )
    legal = {
        "model",
        "input",
        "instructions",
        "max_output_tokens",
        "tools",
        "tool_choice",
        "reasoning",
        "temperature",
        "top_p",
        "stream",
        "store",
        "metadata",
        "text",
        "parallel_tool_calls",
        "previous_response_id",
        "include",
        "truncation",
    }
    illegal = set(params) - legal
    assert not illegal, f"illegal Responses keys: {illegal}"


def test_response_output_item_types_are_known() -> None:
    """Only the item types the adapter understands may appear."""
    data = {
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": []},
            {"type": "function_call", "call_id": "c", "name": "f", "arguments": "{}"},
        ]
    }
    out = parse_responses_response(data)
    # All three items are handled without raising.
    assert out["choices"][0]["message"]["tool_calls"][0]["id"] == "c"
