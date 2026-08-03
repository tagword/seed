"""Tests for MCPStreamableHttpSession (Streamable HTTP transport, Spec 2025-06-18)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from seed.integrations.mcp_client import MCPClientManager, MCPError, MCPStreamableHttpSession
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
    def test_client_has_accept_header(self, cfg: MCPServerConfig) -> None:
        """HTTP client has Accept header listing both content types."""
        sess = MCPStreamableHttpSession(cfg)
        client = sess._http_client()
        assert client.headers.get("Accept") == "application/json, text/event-stream"

    def test_per_request_headers_include_protocol_version(self, cfg: MCPServerConfig) -> None:
        """Per-request _headers() includes MCP-Protocol-Version and Accept."""
        sess = MCPStreamableHttpSession(cfg)
        h = sess._headers()
        assert h.get("MCP-Protocol-Version") == "2025-11-25"
        assert h.get("Accept") == "application/json, text/event-stream"

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
        """Successful initialize extracts sessionId from MCP-Session-Id HTTP header."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        sess = MCPStreamableHttpSession(cfg)

        # Mock initialize response — sessionId goes in HTTP header per spec
        init_result = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-mcp", "version": "1.0"},
            },
        }
        init_resp = _mock_response(init_result)
        init_resp.headers["MCP-Session-Id"] = "sess-abc-123"
        mock_client.post.return_value = init_resp

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

        # Verify per-request headers include Accept and MCP-Protocol-Version
        call_headers = first_call[1].get("headers", {})
        assert call_headers.get("Accept") == "application/json, text/event-stream"
        assert call_headers.get("MCP-Protocol-Version") == "2025-11-25"

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


class TestSkills:
    def test_list_skills_falls_back_to_prompts(self, cfg: MCPServerConfig) -> None:
        sess = MCPStreamableHttpSession(cfg)

        def fake_request(method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = 60.0) -> Any:
            assert method == "prompts/list"
            return {
                "prompts": [
                    {
                        "name": "summarize",
                        "description": "Summarize text",
                        "arguments": [{"name": "text", "required": True}],
                    }
                ]
            }

        sess._ready = True
        sess._start = lambda: None  # type: ignore[method-assign]
        sess._request = fake_request  # type: ignore[method-assign]

        skills = sess.list_skills()
        assert len(skills) == 1
        assert skills[0].name == "summarize"
        assert skills[0].arguments[0]["name"] == "text"

    def test_call_skill_falls_back_to_prompt_get(self, cfg: MCPServerConfig) -> None:
        sess = MCPStreamableHttpSession(cfg)

        def fake_request(method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = 60.0) -> Any:
            assert method == "prompts/get"
            assert params == {"name": "summarize", "arguments": {"text": "hello"}}
            return {
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": "Summarize: hello"}}
                ]
            }

        sess._ready = True
        sess._start = lambda: None  # type: ignore[method-assign]
        sess._request = fake_request  # type: ignore[method-assign]

        out = sess.call_skill("summarize", {"text": "hello"})
        assert "user: Summarize: hello" in out

    def test_list_skills_falls_back_to_tools(self, cfg: MCPServerConfig) -> None:
        sess = MCPStreamableHttpSession(cfg)

        def fake_request(method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = 60.0) -> Any:
            if method == "prompts/list":
                raise MCPError("MCP error: {'code': -32601, 'message': 'Method not found'}")
            assert method == "tools/list"
            return {"tools": [{"name": "web_search", "description": "Search web"}]}

        sess._ready = True
        sess._start = lambda: None  # type: ignore[method-assign]
        sess._request = fake_request  # type: ignore[method-assign]

        skills = sess.list_skills()
        assert skills[0].name == "web_search"

    def test_call_skill_falls_back_to_tool_call(self, cfg: MCPServerConfig) -> None:
        sess = MCPStreamableHttpSession(cfg)

        def fake_request(method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = 60.0) -> Any:
            if method == "prompts/get":
                raise MCPError("MCP error: {'code': -32601, 'message': 'Method not found'}")
            assert method == "tools/call"
            assert params == {"name": "web_search", "arguments": {"query": "hello"}}
            return {"content": [{"type": "text", "text": "search result"}]}

        sess._ready = True
        sess._start = lambda: None  # type: ignore[method-assign]
        sess._request = fake_request  # type: ignore[method-assign]

        assert sess.call_skill("web_search", {"query": "hello"}) == "search result"


class TestStatus:
    def test_streamable_http_ready_session_is_connected(self, cfg: MCPServerConfig) -> None:
        manager = MCPClientManager()
        sess = MCPStreamableHttpSession(cfg)
        sess._ready = True
        manager._sessions[cfg.server_id] = sess

        with patch("seed.integrations.mcp_client.list_server_configs", return_value=[cfg]):
            rows = manager.list_servers_status(probe=False)

        assert rows[0]["connected"] is True


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
#  close — DELETE session termination
# ------------------------------------------------------------------ #


class TestSessionTermination:
    """Test session termination via HTTP DELETE (spec §Session Management)."""

    @patch("httpx.Client")
    def test_close_sends_delete_when_session_active(
        self, mock_client_cls: MagicMock, cfg: MCPServerConfig
    ) -> None:
        """close() sends HTTP DELETE with MCP-Session-Id when session is active."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        sess = MCPStreamableHttpSession(cfg)
        # Trigger httpx client creation + set session state
        sess._http_client()
        sess._session_id = "sess-abc"
        sess._ready = True

        mock_delete = MagicMock()
        mock_client.delete = mock_delete

        sess.close()

        # Verify DELETE was sent
        mock_delete.assert_called_once()
        call_url = mock_delete.call_args[0][0]
        call_headers = mock_delete.call_args[1].get("headers", {})
        assert call_url == "http://localhost:9999/mcp"
        assert call_headers.get("MCP-Session-Id") == "sess-abc"
        assert not sess._ready
        assert sess._client is None

    @patch("httpx.Client")
    def test_close_skips_delete_without_session(
        self, mock_client_cls: MagicMock, cfg: MCPServerConfig
    ) -> None:
        """close() does not send DELETE when no session ID (stateless server)."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        sess = MCPStreamableHttpSession(cfg)
        sess._ready = True
        sess._session_id = None

        sess.close()
        mock_client.delete.assert_not_called()


class TestReconnect:
    """Auto-reconnect when the MCP HTTP server restarts / invalidates the session."""

    _INIT_RESULT = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "test-mcp", "version": "1.0"},
        },
    }

    def _init_resp(self) -> MagicMock:
        r = _mock_response(self._INIT_RESULT)
        r.headers["MCP-Session-Id"] = "sess-abc-123"
        return r

    @patch("httpx.Client")
    def test_reconnect_after_connection_failure(
        self, mock_client_cls: MagicMock, cfg: MCPServerConfig
    ) -> None:
        """Server restarted (connection refused) → auto re-initialize + retry succeeds."""
        import httpx

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        sess = MCPStreamableHttpSession(cfg)

        tools_resp = _mock_response(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "result": {
                    "tools": [{"name": "t1", "description": "d", "inputSchema": {}}]
                },
            }
        )

        # initialize 连接被拒（服务器重启）→ 重连 initialize → initialized → tools/list
        mock_client.post.side_effect = [
            httpx.ConnectError("connection refused"),
            self._init_resp(),
            _mock_response({}),  # notifications/initialized
            tools_resp,
        ]

        tools = sess.list_tools()

        assert sess._ready
        assert sess._session_id == "sess-abc-123"
        assert [t.name for t in tools] == ["t1"]
        assert mock_client.post.call_count == 4

    @patch("httpx.Client")
    def test_reconnect_on_404_session_invalid(
        self, mock_client_cls: MagicMock, cfg: MCPServerConfig
    ) -> None:
        """Server invalidated session (404 without MCP-Session-Id) → re-initialize + retry."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        sess = MCPStreamableHttpSession(cfg)

        not_found = _mock_response({"jsonrpc": "2.0", "id": 2}, status=404)
        # 无 MCP-Session-Id 响应头 = 会话失效信号

        tools_resp = _mock_response(
            {"jsonrpc": "2.0", "id": 4, "result": {"tools": []}}
        )

        mock_client.post.side_effect = [
            self._init_resp(),      # 首次 initialize
            _mock_response({}),     # notifications/initialized
            not_found,              # tools/list → 404 会话失效
            self._init_resp(),      # 重连 initialize
            _mock_response({}),     # notifications/initialized
            tools_resp,             # tools/list 重试成功
        ]

        tools = sess.list_tools()

        assert sess._ready
        assert sess._session_id == "sess-abc-123"
        assert tools == []
        assert mock_client.post.call_count == 6

    @patch("httpx.Client")
    def test_no_reconnect_on_generic_http_error(
        self, mock_client_cls: MagicMock, cfg: MCPServerConfig
    ) -> None:
        """Generic HTTP error (401) is NOT treated as stale → no reconnect, raises."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        sess = MCPStreamableHttpSession(cfg)

        mock_client.post.side_effect = [
            self._init_resp(),
            _mock_response({}),  # notifications/initialized
            _mock_response({"error": "unauthorized"}, status=401),
        ]

        with pytest.raises(MCPError, match="401"):
            sess.list_tools()
        # 无重试：仅 3 次 POST
        assert mock_client.post.call_count == 3

    @patch("httpx.Client")
    def test_stale_resets_session_state(
        self, mock_client_cls: MagicMock, cfg: MCPServerConfig
    ) -> None:
        """Stale detection clears session id / ready flag so next call re-initializes."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        sess = MCPStreamableHttpSession(cfg)

        not_found = _mock_response({"jsonrpc": "2.0", "id": 2}, status=404)

        # 先成功 initialize，再触发 404
        mock_client.post.side_effect = [
            self._init_resp(),
            _mock_response({}),
            not_found,
        ]

        with pytest.raises(MCPError):
            sess.list_tools()

        assert not sess._ready
        assert sess._session_id is None
