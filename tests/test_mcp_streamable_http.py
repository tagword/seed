"""Tests for MCPStreamableHttpSession (Streamable HTTP transport, Spec 2025-06-18)."""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from seed.integrations.mcp_client import MCPError, MCPStreamableHttpSession
from seed.integrations.mcp_config import MCPServerConfig


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def cfg() -> MCPServerConfig:
    return MCPServerConfig(
        server_id="test-server",
        transport="streamable-http",
        url="http://localhost:9999/mcp",
        headers={"Authorization": "Bearer test-token"},
    )


@pytest.fixture
def cfg_no_auth() -> MCPServerConfig:
    return MCPServerConfig(
        server_id="test-server-noauth",
        transport="streamable-http",
        url="http://localhost:9999/mcp",
    )


# ------------------------------------------------------------------ #
#  Construction / validation
# ------------------------------------------------------------------ #


class TestConstruction:
    def test_create(self, cfg: MCPServerConfig) -> None:
        """Create with valid config."""
        sess = MCPStreamableHttpSession(cfg)
        assert sess.cfg.server_id == "test-server"
        assert sess._url == "http://localhost:9999/mcp"
        assert sess._session_id is None
        assert not sess._ready

    def test_wrong_transport(self) -> None:
        """Reject non-streamable-http transport."""
        cfg_bad = MCPServerConfig(server_id="bad", transport="stdio", command="echo")
        with pytest.raises(MCPError, match="Unsupported transport"):
            MCPStreamableHttpSession(cfg_bad)

    def test_missing_url(self) -> None:
        """Reject empty URL."""
        cfg_bad = MCPServerConfig(server_id="bad", transport="streamable-http", url="")
        with pytest.raises(MCPError, match="url is required"):
            MCPStreamableHttpSession(cfg_bad)


# ------------------------------------------------------------------ #
#  HTTP client (httpx) — mock-based
# ------------------------------------------------------------------ #


def _mock_response(
    data: Any,
    status: int = 200,
    content_type: str = "application/json",
) -> MagicMock:
    """Create a mock httpx Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = json.dumps(data) if isinstance(data, dict) else str(data)
    resp.headers = {"content-type": content_type}

    def _json() -> Any:
        return data

    resp.json = _json
    resp.iter_lines = MagicMock(return_value=[])
    return resp


def _mock_sse_response(
    events: list[tuple[str, str]],
    status: int = 200,
) -> MagicMock:
    """Create a mock httpx Response that streams SSE events."""
    lines: list[str] = []
    for event, data in events:
        lines.append(f"event:{event}")
        lines.append(f"data:{data}")
        lines.append("")  # blank line = event boundary

    resp = MagicMock()
    resp.status_code = status
    resp.text = "\n".join(lines)
    resp.headers = {"content-type": "text/event-stream"}
    resp.iter_lines = MagicMock(return_value=lines)
    return resp


class TestHttpClient:
    def test_client_has_protocol_version_header(self, cfg: MCPServerConfig) -> None:
        """HTTP client has MCP-Protocol-Version header."""
        sess = MCPStreamableHttpSession(cfg)
        client = sess._http_client()
        headers = client.headers
        assert headers.get("MCP-Protocol-Version") == "2025-11-25"

    def test_client_has_custom_headers(self, cfg: MCPServerConfig) -> None:
        """Custom headers from config are included."""
        sess = MCPStreamableHttpSession(cfg)
        client = sess._http_client()
        assert client.headers.get("Authorization") == "Bearer test-token"

    def test_client_no_auth(self, cfg_no_auth: MCPServerConfig) -> None:
        """Client works without custom headers."""
        sess = MCPStreamableHttpSession(cfg_no_auth)
        client = sess._http_client()
        assert "Authorization" not in client.headers


# ------------------------------------------------------------------ #
#  initialize
# ------------------------------------------------------------------ #


class TestInitialize:
    @patch("httpx.Client")
    def test_initialize_success(self, mock_client_cls: MagicMock, cfg: MCPServerConfig) -> None:
        """Successful initialize extracts sessionId from _meta."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        sess = MCPStreamableHttpSession(cfg)

        # Mock initialize response
        init_result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-mcp", "version": "1.0"},
                "_meta": {"sessionId": "sess-abc-123"},
            },
        }
        mock_client.post.return_value = _mock_response(init_result)

        # Trigger initialize via _start
        sess._start()

        assert sess._ready
        assert sess._session_id == "sess-abc-123"

        # Verify POST was called with correct payload (first call = initialize)
        assert mock_client.post.call_count >= 2
        first_call = mock_client.post.call_args_list[0]
        url = first_call[0][0]
        assert url == "http://localhost:9999/mcp"

        payload = first_call[1].get("json", {})
        assert payload["method"] == "initialize"
        assert payload["params"]["protocolVersion"] == "2025-11-25"

        # Verify initialized notification was sent
        second_call = mock_client.post.call_args_list[1]
        payload2 = second_call[1].get("json", {})
        assert payload2["method"] == "notifications/initialized"

    @patch("httpx.Client")
    def test_initialize_no_session_id(
        self, mock_client_cls: MagicMock, cfg: MCPServerConfig
    ) -> None:
        """Initialize works even without _meta.sessionId."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        sess = MCPStreamableHttpSession(cfg)

        init_result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "serverInfo": {"name": "test-mcp", "version": "1.0"},
            },
        }
        mock_client.post.return_value = _mock_response(init_result)

        sess._start()
        assert sess._ready
        assert sess._session_id is None  # No sessionId — not a failure

    @patch("httpx.Client")
    def test_initialize_error_response(
        self, mock_client_cls: MagicMock, cfg: MCPServerConfig
    ) -> None:
        """Initialize error is raised."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        sess = MCPStreamableHttpSession(cfg)

        error_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32603, "message": "Internal error"},
        }
        mock_client.post.return_value = _mock_response(error_response)

        with pytest.raises(MCPError, match="MCP error"):
            sess._start()


# ------------------------------------------------------------------ #
#  list_tools
# ------------------------------------------------------------------ #


class TestListTools:
    @patch("httpx.Client")
    def test_list_tools_success(self, mock_client_cls: MagicMock, cfg: MCPServerConfig) -> None:
        """list_tools returns tool list from direct JSON response."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        sess = MCPStreamableHttpSession(cfg)

        # First call = initialize, second = initialized notification, third = tools/list
        init_result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "serverInfo": {"name": "test", "version": "1.0"},
                "_meta": {"sessionId": "sess-1"},
            },
        }
        tools_result = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {"name": "echo", "description": "Echo input", "inputSchema": {}},
                    {
                        "name": "add",
                        "description": "Add two numbers",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                        },
                    },
                ]
            },
        }

        # Return initialize on first post, tools on third post
        responses = [
            _mock_response(init_result),
            _mock_response({"jsonrpc": "2.0"}),
            _mock_response(tools_result),
        ]
        mock_client.post.side_effect = responses

        tools = sess.list_tools()
        assert len(tools) == 2
        assert tools[0].name == "echo"
        assert tools[1].name == "add"
        assert tools[1].input_schema["properties"]["a"]["type"] == "number"

    @patch("httpx.Client")
    def test_list_tools_sse_response(
        self, mock_client_cls: MagicMock, cfg: MCPServerConfig
    ) -> None:
        """list_tools works when server responds with SSE stream."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        sess = MCPStreamableHttpSession(cfg)

        init_result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "serverInfo": {"name": "test", "version": "1.0"},
            },
        }
        tools_result_sse = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"tools": [{"name": "sse-tool", "description": "Via SSE"}]},
        }

        responses = [
            _mock_response(init_result),
            _mock_response({"jsonrpc": "2.0"}),
            _mock_sse_response([("message", json.dumps(tools_result_sse))]),
        ]
        mock_client.post.side_effect = responses

        tools = sess.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "sse-tool"

    @patch("httpx.Client")
    def test_list_tools_http_error(
        self, mock_client_cls: MagicMock, cfg: MCPServerConfig
    ) -> None:
        """HTTP error is raised as MCPError."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        sess = MCPStreamableHttpSession(cfg)

        init_result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "serverInfo": {"name": "test", "version": "1.0"},
            },
        }

        error_resp = MagicMock()
        error_resp.status_code = 401
        error_resp.text = '{"error":"unauthorized"}'
        error_resp.headers = {"content-type": "application/json"}

        responses = [
            _mock_response(init_result),
            _mock_response({"jsonrpc": "2.0"}),
            error_resp,
        ]
        mock_client.post.side_effect = responses

        with pytest.raises(MCPError, match="401"):
            sess.list_tools()


# ------------------------------------------------------------------ #
#  close
# ------------------------------------------------------------------ #


class TestClose:
    @patch("httpx.Client")
    def test_close_cleans_up(self, mock_client_cls: MagicMock, cfg: MCPServerConfig) -> None:
        """Close cleans up client and resets state."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        sess = MCPStreamableHttpSession(cfg)
        init_result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "serverInfo": {"name": "test", "version": "1.0"},
            },
        }
        mock_client.post.return_value = _mock_response(init_result)

        sess._start()
        assert sess._ready

        sess.close()
        assert not sess._ready
        assert sess._client is None
        mock_client.close.assert_called_once()

    @patch("httpx.Client")
    def test_close_idempotent(self, mock_client_cls: MagicMock, cfg: MCPServerConfig) -> None:
        """Close can be called multiple times without error."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        sess = MCPStreamableHttpSession(cfg)
        sess.close()
        sess.close()  # second call should not raise
        assert True


# ------------------------------------------------------------------ #
#  SSE reader (background thread)
# ------------------------------------------------------------------ #


class TestSseReader:
    @patch("httpx.Client")
    def test_sse_reader_dispatches_message(
        self, mock_client_cls: MagicMock, cfg: MCPServerConfig
    ) -> None:
        """Background SSE reader dispatches message events."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        sess = MCPStreamableHttpSession(cfg)

        # Mock the SSE streaming response
        msg_data = json.dumps({"jsonrpc": "2.0", "id": 99, "result": {"ok": True}})
        sse_lines = [
            "event:message",
            f"data:{msg_data}",
            "",  # blank line
        ]
        sse_resp = MagicMock()
        sse_resp.status_code = 200
        sse_resp.headers = {"content-type": "text/event-stream"}
        sse_resp.iter_lines = MagicMock(return_value=sse_lines)

        mock_client.stream.return_value.__enter__.return_value = sse_resp

        # Setup a pending event to verify dispatch
        ev = threading.Event()
        sess._pending[99] = ev

        # Manually run _sse_reader
        sess._sse_reader()

        # The event should have been set
        assert ev.is_set()
        assert sess._results.get(99) is not None
        assert sess._results[99]["result"]["ok"] is True
