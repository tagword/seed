"""Regression: tool arguments whose name collides with the executor dispatch
parameter must not raise "got multiple values for argument 'tool_name'".

Real-world case: `mcp_call` takes a parameter literally named `tool_name`
(remote tool name), while ToolExecutor's dispatch param used to be named
`tool_name` too -> execute_async("mcp_call", tool_name=..., ...) crashed
before reaching the MCP server.
"""

from __future__ import annotations

import asyncio

from seed.core.models import Tool
from seed.core.tool_runtime import ToolExecutor, ToolRegistry


def _fake_mcp_call_handler(server_id: str, tool_name: str, arguments: str = "{}") -> str:
    # Mirror seed_tools.mcp.mcp_call_handler shape without the MCP dependency.
    return f"call:{server_id}:{tool_name}:{arguments}"


def _make_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="mcp_call",
            description="Invoke a tool on an MCP server (mirror)",
            parameters={
                "server_id": {"type": "string", "required": True},
                "tool_name": {"type": "string", "required": True},
                "arguments": {"type": "string", "required": False, "default": "{}"},
            },
            returns="string",
            category="mcp",
        ),
        _fake_mcp_call_handler,
    )
    return reg


def test_execute_async_param_name_collision() -> None:
    executor = ToolExecutor(_make_registry())
    result = asyncio.run(
        executor.execute_async(
            "mcp_call",
            server_id="aidata-mcp",
            tool_name="news_list_articles",
            arguments='{"page":1}',
        )
    )
    assert result == 'call:aidata-mcp:news_list_articles:{"page":1}'


def test_execute_with_validation_async_param_name_collision() -> None:
    """The exact path used by agent_runtime._execute_tool_with_cancel."""
    executor = ToolExecutor(_make_registry())
    result = asyncio.run(
        executor.execute_with_validation_async(
            "mcp_call",
            {"server_id": "aidata-mcp", "tool_name": "news_list_articles", "arguments": "{}"},
        )
    )
    assert result == "call:aidata-mcp:news_list_articles:{}"


def test_execute_sync_param_name_collision() -> None:
    executor = ToolExecutor(_make_registry())
    result = executor.execute(
        "mcp_call",
        server_id="aidata-mcp",
        tool_name="news_list_articles",
        arguments="{}",
    )
    assert result == "call:aidata-mcp:news_list_articles:{}"


def test_execute_with_validation_sync_param_name_collision() -> None:
    executor = ToolExecutor(_make_registry())
    result = executor.execute_with_validation(
        "mcp_call",
        {"server_id": "aidata-mcp", "tool_name": "news_list_articles", "arguments": "{}"},
    )
    assert result == "call:aidata-mcp:news_list_articles:{}"
