"""MCP stdio client (JSON-RPC newline-delimited) — minimal implementation."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from seed.core.env_access import MCP_ENABLED, MCP_INIT_TIMEOUT, env_truthy, pick_default
from seed.integrations.mcp_config import MCPServerConfig, get_server_config, list_server_configs

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_NAME = "seed"
_CLIENT_VERSION = "1.0.4"


class MCPError(Exception):
    pass


def mcp_globally_enabled() -> bool:
    return env_truthy(*MCP_ENABLED, default="1")


@dataclass
class MCPToolInfo:
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPSkillInfo:
    name: str
    description: str = ""
    arguments: List[Dict[str, Any]] = field(default_factory=list)


class MCPStdioSession:
    """One persistent stdio session to an MCP server subprocess."""

    def __init__(self, cfg: MCPServerConfig):
        if cfg.transport != "stdio":
            raise MCPError(f"Unsupported transport {cfg.transport!r} for {cfg.server_id}")
        self.cfg = cfg
        self._proc: Optional[subprocess.Popen[str]] = None
        self._lock = threading.Lock()
        self._req_id = 0
        self._ready = False
        self._stderr_thread: Optional[threading.Thread] = None

    def _attach_stderr_drain(self) -> None:
        """Prevent subprocess blocking when uvx/MCP writes progress to stderr."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        if self._stderr_thread is not None and self._stderr_thread.is_alive():
            return
        sid = self.cfg.server_id

        def _drain() -> None:
            try:
                assert proc.stderr is not None
                for line in proc.stderr:
                    s = line.rstrip()
                    if s:
                        logger.info("MCP %s stderr: %s", sid, s[:800])
            except Exception:
                logger.debug("MCP %s stderr drain ended", sid, exc_info=True)

        self._stderr_thread = threading.Thread(target=_drain, daemon=True)
        self._stderr_thread.start()

    def _start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        argv = self.cfg.argv()
        env = {**os.environ, **self.cfg.env}
        cwd = self.cfg.cwd or None
        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=cwd,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            raise MCPError(f"Failed to start MCP server {self.cfg.server_id}: {e}") from e
        self._attach_stderr_drain()
        self._ready = False
        self._initialize()

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        except OSError:
            pass
        finally:
            self._proc = None
            self._ready = False

    def _readline(self, timeout: float) -> str:
        if self._proc is None or self._proc.stdout is None:
            raise MCPError("MCP process not running")
        stream = self._proc.stdout
        box: list[Optional[str]] = [None]

        def _reader() -> None:
            box[0] = stream.readline()

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            hint = (
                f"MCP read timeout ({timeout}s) on {self.cfg.server_id}. "
                "首次用 uvx 拉包可能要 1–3 分钟，请增大 SEED_MCP_INIT_TIMEOUT 或先在终端执行："
                f" {' '.join(self.cfg.argv())}"
            )
            raise MCPError(hint)
        line = box[0]
        if line is None or line == "":
            raise MCPError(f"MCP server {self.cfg.server_id} closed stdout")
        return line

    def _write(self, msg: Dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError("MCP process not running")
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

    def _request(self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = 60.0) -> Any:
        self._req_id += 1
        rid = self._req_id
        self._write(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "method": method,
                "params": params if params is not None else {},
            }
        )
        deadline = timeout
        while True:
            raw = self._readline(deadline)
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                msg = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning("MCP non-JSON line from %s: %s", self.cfg.server_id, stripped[:200])
                continue
            if msg.get("id") == rid:
                if "error" in msg and msg["error"] is not None:
                    err = msg["error"]
                    raise MCPError(f"MCP error: {err}")
                return msg.get("result")
            # notifications / other responses — ignore for sync client

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _initialize(self) -> None:
        init_timeout = float(pick_default("180", *MCP_INIT_TIMEOUT) or "180")
        init_timeout = max(30.0, min(init_timeout, 600.0))
        result = self._request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": _CLIENT_NAME, "version": _CLIENT_VERSION},
            },
            timeout=init_timeout,
        )
        if not isinstance(result, dict):
            raise MCPError("initialize returned invalid result")
        self._notify("notifications/initialized")
        self._ready = True
        logger.info(
            "MCP server %s ready: %s",
            self.cfg.server_id,
            (result.get("serverInfo") or {}).get("name", "?"),
        )

    def list_tools(self) -> List[MCPToolInfo]:
        with self._lock:
            self._start()
            result = self._request("tools/list", {}, timeout=30.0)
        tools_raw = []
        if isinstance(result, dict):
            tools_raw = result.get("tools") or []
        out: List[MCPToolInfo] = []
        for t in tools_raw:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").strip()
            if not name:
                continue
            out.append(
                MCPToolInfo(
                    name=name,
                    description=str(t.get("description") or ""),
                    input_schema=t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {},
                )
            )
        return out

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, *, timeout: float = 120.0) -> str:
        with self._lock:
            self._start()
            result = self._request(
                "tools/call",
                {"name": name, "arguments": arguments or {}},
                timeout=timeout,
            )
        return _format_tool_result(result)

    def list_skills(self) -> List[MCPSkillInfo]:
        with self._lock:
            self._start()
            return _list_skills_via_request(self._request)

    def call_skill(self, name: str, arguments: Optional[Dict[str, Any]] = None, *, timeout: float = 120.0) -> str:
        with self._lock:
            self._start()
            return _call_skill_via_request(self._request, name, arguments or {}, timeout=timeout)


class MCPSseSession:
    """MCP client session over HTTP SSE transport.

    Connects to a remote MCP server via SSE (Server-Sent Events).
    Uses httpx for HTTP streaming.
    """

    def __init__(self, cfg: MCPServerConfig):
        if cfg.transport != "sse":
            raise MCPError(f"Unsupported transport {cfg.transport!r} for SSE session")
        if not cfg.url.strip():
            raise MCPError(f"MCP SSE server {cfg.server_id!r}: url is required")
        self.cfg = cfg
        self._sse_url = cfg.url.strip().rstrip("/")
        self._post_url: Optional[str] = None
        self._lock = threading.Lock()
        self._response_lock = threading.Lock()
        self._req_id = 0
        self._pending: Dict[int, threading.Event] = {}
        self._results: Dict[int, Any] = {}
        self._ready = False
        self._client: Any = None  # httpx.Client
        self._sse_thread: Optional[threading.Thread] = None
        self._close_flag = threading.Event()

    def _http_client(self) -> Any:
        """Lazy import httpx and create client with custom headers."""
        if self._client is None:
            import httpx

            hdrs = {}
            if self.cfg.headers:
                hdrs.update(self.cfg.headers)
            # Bypass any system/env proxy for loopback MCP servers; a proxy
            # cannot reach 127.0.0.1 and would otherwise fail the connection.
            try:
                from seed_model_providers import httpx_trust_env_for

                trust_env = httpx_trust_env_for(self._sse_url)
            except Exception:
                trust_env = True
            self._client = httpx.Client(
                timeout=httpx.Timeout(300.0, connect=30.0),
                headers=hdrs,
                trust_env=trust_env,
            )
        return self._client

    def _parse_sse_event(self, line: str) -> tuple[Optional[str], Optional[str]]:
        """Parse a single SSE line. Returns (event_type, data) or (None, None)."""
        line = line.strip()
        if not line:
            return None, None
        if line.startswith("event:"):
            return line[len("event:"):].strip(), None
        if line.startswith("data:"):
            return None, line[len("data:"):].strip()
        # Also handle "event:xxx" without colon-space
        if line.startswith("event "):
            return line[len("event "):].strip(), None
        if line.startswith("data "):
            return None, line[len("data "):].strip()
        return None, None

    def _sse_reader(self) -> None:
        """Background thread: read SSE stream and dispatch messages."""
        try:
            client = self._http_client()
            with client.stream("GET", self._sse_url) as response:
                if response.status_code != 200:
                    logger.error(
                        "MCP SSE %s: GET %s returned %d",
                        self.cfg.server_id, self._sse_url, response.status_code,
                    )
                    return
                current_event: Optional[str] = None
                data_lines: list[str] = []

                for chunk in response.iter_lines():
                    if self._close_flag.is_set():
                        break
                    line = chunk.strip()
                    if not line:
                        # Empty line = event boundary
                        if current_event is not None and data_lines:
                            self._handle_sse_event(current_event, "\n".join(data_lines))
                        current_event = None
                        data_lines = []
                        continue

                    if line.startswith("event:"):
                        current_event = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:"):].strip())
                    # SSE comments (starting with :) are ignored

                # Handle last event if no trailing blank line
                if current_event is not None and data_lines:
                    self._handle_sse_event(current_event, "\n".join(data_lines))

        except Exception as e:
            if not self._close_flag.is_set():
                logger.error("MCP SSE %s reader error: %s", self.cfg.server_id, e)

    def _handle_sse_event(self, event: str, data: str) -> None:
        if event == "endpoint":
            raw = data.strip().rstrip("/")
            # Resolve relative endpoint URL against the SSE base URL
            if raw.startswith("http://") or raw.startswith("https://"):
                self._post_url = raw
            else:
                from urllib.parse import urljoin
                self._post_url = urljoin(self._sse_url + "/", raw.lstrip("/"))
            logger.info(
                "MCP SSE %s: endpoint received -> %s", self.cfg.server_id, self._post_url
            )
        elif event == "message":
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                logger.warning("MCP SSE %s: bad JSON in message", self.cfg.server_id)
                return
            rid = msg.get("id")
            if rid is not None:
                with self._response_lock:
                    self._results[rid] = msg
                    ev = self._pending.get(rid)
                if ev:
                    ev.set()
        # Other event types are ignored

    def _wait_for_endpoint(self, timeout: float = 30.0) -> None:
        """Wait until the SSE endpoint event is received."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._post_url is not None:
                return
            if self._close_flag.is_set():
                raise MCPError("MCP SSE session closed before receiving endpoint")
            time.sleep(0.05)
        raise MCPError(
            f"MCP SSE {self.cfg.server_id}: timeout waiting for endpoint event "
            f"from {self._sse_url}"
        )

    def _start(self) -> None:
        if self._ready:
            return
        if self._close_flag.is_set():
            raise MCPError("MCP SSE session already closed")

        self._sse_thread = threading.Thread(
            target=self._sse_reader, daemon=True, name=f"mcp-sse-{self.cfg.server_id}"
        )
        self._sse_thread.start()

        # Wait for endpoint event
        init_timeout = float(pick_default("30", *MCP_INIT_TIMEOUT) or "30")
        self._wait_for_endpoint(timeout=init_timeout)

        # Send initialize handshake
        result = self._request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": _CLIENT_NAME, "version": _CLIENT_VERSION},
            },
            timeout=init_timeout,
        )
        if not isinstance(result, dict):
            raise MCPError("initialize returned invalid result")
        # Send initialized notification (fire-and-forget)
        try:
            self._send_notification("notifications/initialized")
        except Exception:
            pass
        self._ready = True
        logger.info(
            "MCP SSE server %s ready: %s",
            self.cfg.server_id,
            (result.get("serverInfo") or {}).get("name", "?"),
        )

    def _send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC notification (no id)."""
        if self._post_url is None:
            raise MCPError("MCP SSE session not initialized (no endpoint)")
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params if params is not None else {},
        }
        client = self._http_client()
        resp = client.post(
            self._post_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()

    def _request(self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = 60.0) -> Any:
        """Send a JSON-RPC request and wait for response."""
        if self._post_url is None:
            raise MCPError("MCP SSE session not initialized (no endpoint)")

        self._req_id += 1
        rid = self._req_id
        payload = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params if params is not None else {},
        }

        ev = threading.Event()
        with self._response_lock:
            self._pending[rid] = ev

        try:
            client = self._http_client()
            resp = client.post(
                self._post_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                raise MCPError(
                    f"MCP SSE {self.cfg.server_id}: POST {self._post_url} "
                    f"returned {resp.status_code}: {resp.text[:200]}"
                )

            # Wait for response via SSE
            ok = ev.wait(timeout=timeout)
            if not ok:
                raise MCPError(
                    f"MCP SSE {self.cfg.server_id}: timeout ({timeout}s) waiting for "
                    f"response to {method!r} (id={rid})"
                )

            with self._response_lock:
                msg = self._results.pop(rid, None)
                self._pending.pop(rid, None)

            if msg is None:
                raise MCPError(f"MCP SSE: no response for id={rid}")

            if "error" in msg and msg["error"] is not None:
                err = msg["error"]
                raise MCPError(f"MCP error: {err}")
            return msg.get("result")

        except MCPError:
            raise
        except Exception as e:
            with self._response_lock:
                self._pending.pop(rid, None)
                self._results.pop(rid, None)
            raise MCPError(f"MCP SSE request failed: {e}") from e

    def list_tools(self) -> List[MCPToolInfo]:
        with self._lock:
            self._start()
            result = self._request("tools/list", {}, timeout=30.0)
        tools_raw = []
        if isinstance(result, dict):
            tools_raw = result.get("tools") or []
        out: List[MCPToolInfo] = []
        for t in tools_raw:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").strip()
            if not name:
                continue
            out.append(
                MCPToolInfo(
                    name=name,
                    description=str(t.get("description") or ""),
                    input_schema=t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {},
                )
            )
        return out

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, *, timeout: float = 120.0) -> str:
        with self._lock:
            self._start()
            result = self._request(
                "tools/call",
                {"name": name, "arguments": arguments or {}},
                timeout=timeout,
            )
        return _format_tool_result(result)

    def list_skills(self) -> List[MCPSkillInfo]:
        with self._lock:
            self._start()
            return _list_skills_via_request(self._request)

    def call_skill(self, name: str, arguments: Optional[Dict[str, Any]] = None, *, timeout: float = 120.0) -> str:
        with self._lock:
            self._start()
            return _call_skill_via_request(self._request, name, arguments or {}, timeout=timeout)

    def close(self) -> None:
        self._close_flag.set()
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._ready = False
        # Wake up any pending waiters
        with self._response_lock:
            for ev in self._pending.values():
                ev.set()
            self._pending.clear()
            self._results.clear()


def _format_tool_result(result: Any) -> str:
    if result is None:
        return "(empty result)"
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, indent=2)
    if result.get("isError"):
        content = result.get("content")
        text = _extract_content_text(content)
        return f"[MCP tool error]\n{text or json.dumps(result, ensure_ascii=False)}"
    content = result.get("content")
    text = _extract_content_text(content)
    if text:
        return text
    return json.dumps(result, ensure_ascii=False, indent=2)


def _extract_content_text(content: Any) -> str:
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(p for p in parts if p)


def _is_method_not_found_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "-32601" in text or "method not found" in text or "not found" in text


def _skill_info_from_entry(entry: Any) -> Optional[MCPSkillInfo]:
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or "").strip()
    if not name:
        return None
    raw_args = entry.get("arguments")
    args: List[Dict[str, Any]] = []
    if isinstance(raw_args, list):
        args = [a for a in raw_args if isinstance(a, dict)]
    return MCPSkillInfo(
        name=name,
        description=str(entry.get("description") or ""),
        arguments=args,
    )


def _parse_skills_result(result: Any) -> List[MCPSkillInfo]:
    if isinstance(result, dict):
        raw = result.get("skills")
        if raw is None:
            raw = result.get("prompts")
        if raw is None:
            raw = result.get("tools")
    elif isinstance(result, list):
        raw = result
    else:
        raw = []
    if not isinstance(raw, list):
        return []
    out: List[MCPSkillInfo] = []
    for item in raw:
        info = _skill_info_from_entry(item)
        if info is not None:
            out.append(info)
    return out


def _list_skills_via_request(request_fn: Any) -> List[MCPSkillInfo]:
    try:
        return _parse_skills_result(request_fn("skills/list", {}, timeout=30.0))
    except MCPError as e:
        if not _is_method_not_found_error(e):
            raise
    try:
        return _parse_skills_result(request_fn("prompts/list", {}, timeout=30.0))
    except MCPError as e:
        if not _is_method_not_found_error(e):
            raise
    return _parse_skills_result(request_fn("tools/list", {}, timeout=30.0))


def _format_prompt_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        ctype = str(content.get("type") or "").strip()
        if ctype == "text":
            return str(content.get("text") or "")
        if "text" in content:
            return str(content.get("text") or "")
        return json.dumps(content, ensure_ascii=False, indent=2)
    return json.dumps(content, ensure_ascii=False, indent=2)


def _format_prompt_result(result: Any) -> str:
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, indent=2)
    messages = result.get("messages")
    if not isinstance(messages, list):
        return _format_tool_result(result)
    parts: List[str] = []
    desc = str(result.get("description") or "").strip()
    if desc:
        parts.append(desc)
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").strip() or "user"
        text = _format_prompt_content(msg.get("content"))
        if text:
            parts.append(f"{role}: {text}")
    return "\n\n".join(parts).strip() or json.dumps(result, ensure_ascii=False, indent=2)


def _call_skill_via_request(
    request_fn: Any,
    name: str,
    arguments: Dict[str, Any],
    *,
    timeout: float,
) -> str:
    try:
        result = request_fn(
            "skills/call",
            {"name": name, "arguments": arguments},
            timeout=timeout,
        )
        return _format_tool_result(result)
    except MCPError as e:
        if not _is_method_not_found_error(e):
            raise
    try:
        result = request_fn(
            "prompts/get",
            {"name": name, "arguments": arguments},
            timeout=timeout,
        )
        return _format_prompt_result(result)
    except MCPError as e:
        if not _is_method_not_found_error(e):
            raise
    result = request_fn(
        "tools/call",
        {"name": name, "arguments": arguments},
        timeout=timeout,
    )
    return _format_tool_result(result)


class MCPStreamableHttpSession:
    """MCP client session over Streamable HTTP transport (Spec 2025-06-18).

    Uses a single MCP endpoint for both sending requests and receiving responses.
    Synchronous responses arrive directly in the HTTP POST response body.
    Optional SSE streaming for server-push notifications.

    Key differences from the deprecated HTTP+SSE (``MCPSseSession``):
    - Single endpoint URL (no separate SSE/POST URLs)
    - ``MCP-Protocol-Version`` header on all requests
    - sessionId extracted from ``initialize`` response ``_meta.sessionId``
    - SSE connection is optional (only for streaming subscriptions)
    """

    # Latest protocol version for Streamable HTTP transport
    _PROTOCOL_VERSION_HTTP = "2025-11-25"

    def __init__(self, cfg: MCPServerConfig):
        if cfg.transport != "streamable-http":
            raise MCPError(
                f"Unsupported transport {cfg.transport!r} for Streamable HTTP session"
            )
        if not cfg.url.strip():
            raise MCPError(
                f"MCP Streamable HTTP server {cfg.server_id!r}: url is required"
            )
        self.cfg = cfg
        self._url = cfg.url.strip().rstrip("/")
        self._session_id: Optional[str] = None
        self._lock = threading.Lock()
        self._response_lock = threading.Lock()
        self._req_id = 0
        self._pending: Dict[int, threading.Event] = {}
        self._results: Dict[int, Any] = {}
        self._ready = False
        self._client: Any = None  # httpx.Client
        self._sse_thread: Optional[threading.Thread] = None
        self._close_flag = threading.Event()

    # ------------------------------------------------------------------ #
    #  HTTP client helpers
    # ------------------------------------------------------------------ #

    def _http_client(self) -> Any:
        """Lazy import httpx and create client with custom headers."""
        if self._client is None:
            import httpx

            hdrs = {}
            # MCP-Protocol-Version header (required by Streamable HTTP spec)
            hdrs["MCP-Protocol-Version"] = self._PROTOCOL_VERSION_HTTP
            if self.cfg.headers:
                # User-defined headers (e.g. Authorization) override defaults
                hdrs.update(self.cfg.headers)
            try:
                from seed_model_providers import httpx_trust_env_for

                trust_env = httpx_trust_env_for(self._url)
            except Exception:
                trust_env = True
            self._client = httpx.Client(
                timeout=httpx.Timeout(300.0, connect=30.0),
                headers=hdrs,
                trust_env=trust_env,
            )
        return self._client

    def _headers(self) -> Dict[str, str]:
        """Return per-request extra headers (content-type + optional session)."""
        h: Dict[str, str] = {"Content-Type": "application/json"}
        if self._session_id:
            h["MCP-Session-Id"] = self._session_id
        return h

    # ------------------------------------------------------------------ #
    #  SSE reader (optional — for server-push notifications)
    # ------------------------------------------------------------------ #

    def _parse_sse_event(self, line: str) -> tuple[Optional[str], Optional[str]]:
        """Parse a single SSE line. Returns (event_type, data) or (None, None)."""
        line = line.strip()
        if not line:
            return None, None
        if line.startswith("event:"):
            return line[len("event:") :].strip(), None
        if line.startswith("data:"):
            return None, line[len("data:") :].strip()
        if line.startswith("event "):
            return line[len("event ") :].strip(), None
        if line.startswith("data "):
            return None, line[len("data ") :].strip()
        return None, None

    def _handle_sse_event(self, event: str, data: str) -> None:
        """Handle a single SSE event from the stream."""
        if event == "message":
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                logger.warning(
                    "MCP Streamable HTTP %s: bad JSON in SSE message",
                    self.cfg.server_id,
                )
                return
            rid = msg.get("id")
            if rid is not None:
                with self._response_lock:
                    self._results[rid] = msg
                    ev = self._pending.get(rid)
                if ev:
                    ev.set()
        # Other event types are logged for debugging
        elif event:
            logger.debug(
                "MCP Streamable HTTP %s: unhandled SSE event %r",
                self.cfg.server_id,
                event,
            )

    def _sse_reader(self) -> None:
        """Background thread: read SSE stream and dispatch messages."""
        try:
            client = self._http_client()
            with client.stream("GET", self._url) as response:
                if response.status_code != 200:
                    logger.error(
                        "MCP Streamable HTTP %s: SSE GET %s returned %d",
                        self.cfg.server_id,
                        self._url,
                        response.status_code,
                    )
                    return
                current_event: Optional[str] = None
                data_lines: list[str] = []

                for chunk in response.iter_lines():
                    if self._close_flag.is_set():
                        break
                    line = chunk.strip()
                    if not line:
                        # Empty line = event boundary
                        if current_event is not None and data_lines:
                            self._handle_sse_event(
                                current_event, "\n".join(data_lines)
                            )
                        current_event = None
                        data_lines = []
                        continue

                    if line.startswith("event:"):
                        current_event = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:") :].strip())
                    # SSE comments (starting with :) are ignored

                # Handle last event if no trailing blank line
                if current_event is not None and data_lines:
                    self._handle_sse_event(current_event, "\n".join(data_lines))

        except Exception as e:
            if not self._close_flag.is_set():
                logger.error(
                    "MCP Streamable HTTP %s SSE reader error: %s",
                    self.cfg.server_id,
                    e,
                )

    # ------------------------------------------------------------------ #
    #  Initialization
    # ------------------------------------------------------------------ #

    def _start(self) -> None:
        """Connect and initialize the session (lazy, called on first use)."""
        if self._ready:
            return
        if self._close_flag.is_set():
            raise MCPError("MCP Streamable HTTP session already closed")

        init_timeout = float(pick_default("30", *MCP_INIT_TIMEOUT) or "30")

        # Send initialize — response comes directly in HTTP body
        result = self._request(
            "initialize",
            {
                "protocolVersion": self._PROTOCOL_VERSION_HTTP,
                "capabilities": {},
                "clientInfo": {"name": _CLIENT_NAME, "version": _CLIENT_VERSION},
            },
            timeout=init_timeout,
        )
        if not isinstance(result, dict):
            raise MCPError("initialize returned invalid result")

        # Extract sessionId from _meta (Streamable HTTP spec)
        meta = result.get("_meta") or {}
        sid = meta.get("sessionId")
        if sid:
            self._session_id = str(sid).strip()
            logger.info(
                "MCP Streamable HTTP %s: session %s established",
                self.cfg.server_id,
                self._session_id,
            )

        # Send initialized notification (fire-and-forget)
        try:
            self._send_notification("notifications/initialized")
        except Exception:
            pass

        self._ready = True
        logger.info(
            "MCP Streamable HTTP server %s ready: %s",
            self.cfg.server_id,
            (result.get("serverInfo") or {}).get("name", "?"),
        )

    # ------------------------------------------------------------------ #
    #  JSON-RPC message helpers
    # ------------------------------------------------------------------ #

    def _send_notification(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> None:
        """Send a JSON-RPC notification (no id)."""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params if params is not None else {},
        }
        client = self._http_client()
        resp = client.post(self._url, json=payload, headers=self._headers())
        resp.raise_for_status()

    def _request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout: float = 60.0,
    ) -> Any:
        """Send a JSON-RPC request and return the response.

        In Streamable HTTP, sync responses arrive directly in the HTTP POST
        response body (no need to wait for SSE for simple operations).
        """
        self._req_id += 1
        rid = self._req_id
        payload = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params if params is not None else {},
        }

        client = self._http_client()
        resp = client.post(self._url, json=payload, headers=self._headers())

        if resp.status_code >= 400:
            raise MCPError(
                f"MCP Streamable HTTP {self.cfg.server_id}: "
                f"POST {self._url} returned {resp.status_code}: {resp.text[:200]}"
            )

        # Check if response is SSE stream or direct JSON
        ct = (resp.headers.get("content-type") or "").lower()

        if "text/event-stream" in ct:
            # Server chose to respond with SSE — use event-based dispatch
            ev = threading.Event()
            with self._response_lock:
                self._pending[rid] = ev

            try:
                # Parse SSE events from the response body
                current_event: Optional[str] = None
                data_lines: list[str] = []

                for line in resp.iter_lines():
                    line = line.strip()
                    if not line:
                        if current_event is not None and data_lines:
                            data = "\n".join(data_lines)
                            self._handle_sse_event_inline(
                                current_event, data, rid, ev
                            )
                        current_event = None
                        data_lines = []
                        continue
                    if line.startswith("event:"):
                        current_event = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:") :].strip())

                if current_event is not None and data_lines:
                    data = "\n".join(data_lines)
                    self._handle_sse_event_inline(current_event, data, rid, ev)

                # Wait for the response to arrive via SSE
                ok = ev.wait(timeout=timeout)
                if not ok:
                    raise MCPError(
                        f"MCP Streamable HTTP {self.cfg.server_id}: "
                        f"timeout ({timeout}s) waiting for SSE response "
                        f"to {method!r} (id={rid})"
                    )

                with self._response_lock:
                    msg = self._results.pop(rid, None)
                    self._pending.pop(rid, None)

                if msg is None:
                    raise MCPError(
                        f"MCP Streamable HTTP: no response for id={rid}"
                    )
                if "error" in msg and msg["error"] is not None:
                    err = msg["error"]
                    raise MCPError(f"MCP error: {err}")
                return msg.get("result")

            except MCPError:
                raise
            except Exception as e:
                with self._response_lock:
                    self._pending.pop(rid, None)
                    self._results.pop(rid, None)
                raise MCPError(
                    f"MCP Streamable HTTP SSE request failed: {e}"
                ) from e
            finally:
                with self._response_lock:
                    self._pending.pop(rid, None)

        else:
            # Direct JSON response
            try:
                msg = resp.json()
            except Exception as e:
                raise MCPError(
                    f"MCP Streamable HTTP {self.cfg.server_id}: "
                    f"invalid JSON response: {e}"
                ) from e

            if isinstance(msg, list):
                # May receive a batch response
                for item in msg:
                    if isinstance(item, dict) and item.get("id") == rid:
                        msg = item
                        break
                else:
                    raise MCPError(
                        f"MCP Streamable HTTP: no matching response for id={rid}"
                    )

            if "error" in msg and msg["error"] is not None:
                err = msg["error"]
                raise MCPError(f"MCP error: {err}")
            return msg.get("result")

    def _handle_sse_event_inline(
        self,
        event: str,
        data: str,
        expected_rid: int,
        ev: threading.Event,
    ) -> None:
        """Handle SSE event parsed from an inline streaming response."""
        if event == "message":
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                logger.warning(
                    "MCP Streamable HTTP %s: bad JSON in SSE message",
                    self.cfg.server_id,
                )
                return
            rid = msg.get("id")
            if rid == expected_rid:
                with self._response_lock:
                    self._results[rid] = msg
                ev.set()

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def list_tools(self) -> List[MCPToolInfo]:
        with self._lock:
            self._start()
            result = self._request("tools/list", {}, timeout=30.0)
        tools_raw = []
        if isinstance(result, dict):
            tools_raw = result.get("tools") or []
        out: List[MCPToolInfo] = []
        for t in tools_raw:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").strip()
            if not name:
                continue
            out.append(
                MCPToolInfo(
                    name=name,
                    description=str(t.get("description") or ""),
                    input_schema=(
                        t.get("inputSchema")
                        if isinstance(t.get("inputSchema"), dict)
                        else {}
                    ),
                )
            )
        return out

    def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        timeout: float = 120.0,
    ) -> str:
        with self._lock:
            self._start()
            result = self._request(
                "tools/call",
                {"name": name, "arguments": arguments or {}},
                timeout=timeout,
            )
        return _format_tool_result(result)

    def list_skills(self) -> List[MCPSkillInfo]:
        with self._lock:
            self._start()
            return _list_skills_via_request(self._request)

    def call_skill(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        timeout: float = 120.0,
    ) -> str:
        with self._lock:
            self._start()
            return _call_skill_via_request(
                self._request, name, arguments or {}, timeout=timeout
            )

    def close(self) -> None:
        self._close_flag.set()
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._ready = False
        with self._response_lock:
            for ev in self._pending.values():
                ev.set()
            self._pending.clear()
            self._results.clear()


class MCPClientManager:
    """Process-wide session cache."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def _create_session(self, cfg: MCPServerConfig) -> Any:
        """Create session based on transport type."""
        transport = cfg.transport
        if transport == "streamable-http":
            return MCPStreamableHttpSession(cfg)
        elif transport == "sse":
            logger.warning(
                "MCP transport 'sse' is deprecated (Spec 2024-11-05). "
                "Migrate to 'streamable-http' (Spec 2025-06-18). "
                "See: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports"
            )
            return MCPSseSession(cfg)
        elif transport == "stdio":
            return MCPStdioSession(cfg)
        else:
            raise MCPError(f"Unsupported MCP transport: {transport!r}")

    def get_session(self, server_id: str) -> Any:
        if not mcp_globally_enabled():
            raise MCPError("MCP is disabled (set SEED_MCP_ENABLED=1)")
        cfg = get_server_config(server_id)
        if cfg is None:
            raise MCPError(f"Unknown MCP server id: {server_id!r}")
        if not cfg.enabled:
            raise MCPError(f"MCP server {server_id!r} is disabled in config")
        with self._lock:
            sess = self._sessions.get(server_id)
            if sess is None:
                sess = self._create_session(cfg)
                self._sessions[server_id] = sess
            return sess

    def close_server(self, server_id: str) -> None:
        with self._lock:
            sess = self._sessions.pop(server_id, None)
        if sess:
            sess.close()

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            s.close()

    def list_servers_status(self, *, probe: bool = False) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for cfg in list_server_configs():
            row: Dict[str, Any] = {
                "id": cfg.server_id,
                "enabled": cfg.enabled,
                "transport": cfg.transport,
                "command": cfg.command,
                "args": cfg.args,
                "url": cfg.url,
                "connected": False,
            }
            with self._lock:
                sess = self._sessions.get(cfg.server_id)
            if sess:
                if cfg.transport in ("sse", "streamable-http"):
                    row["connected"] = bool(sess._ready and not sess._close_flag.is_set())
                else:
                    row["connected"] = bool(
                        sess._proc is not None and sess._proc.poll() is None and sess._ready
                    )
            if probe and cfg.enabled and mcp_globally_enabled():
                try:
                    sess = self.get_session(cfg.server_id)
                    tool_error = ""
                    skill_error = ""
                    try:
                        tools = sess.list_tools()
                    except Exception as e:
                        tools = []
                        tool_error = str(e)
                    try:
                        skills = sess.list_skills()
                    except Exception as e:
                        skills = []
                        skill_error = str(e)
                    if not tools and not skills and tool_error:
                        raise MCPError(tool_error)
                    row["connected"] = True
                    row["tool_count"] = len(tools)
                    row["tools"] = [t.name for t in tools]
                    row["skill_count"] = len(skills)
                    row["skills"] = [s.name for s in skills]
                    if skill_error:
                        row["skill_error"] = skill_error
                except Exception as e:
                    row["connected"] = False
                    row["last_error"] = str(e)
            out.append(row)
        return out


_manager: Optional[MCPClientManager] = None
_manager_lock = threading.Lock()


def get_mcp_manager() -> MCPClientManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = MCPClientManager()
        return _manager


def reset_mcp_manager() -> None:
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.close_all()
            _manager = None


def probe_mcp_server_config(cfg: MCPServerConfig) -> Dict[str, Any]:
    """Start a one-off session, list tools, then close (for Web UI test)."""
    if cfg.transport == "streamable-http":
        sess: Any = MCPStreamableHttpSession(cfg)
    elif cfg.transport == "sse":
        sess = MCPSseSession(cfg)
    else:
        sess = MCPStdioSession(cfg)
    try:
        tool_error = ""
        skill_error = ""
        try:
            tools = sess.list_tools()
        except Exception as e:
            tools = []
            tool_error = str(e)
        try:
            skills = sess.list_skills()
        except Exception as e:
            skills = []
            skill_error = str(e)
        if not tools and not skills and tool_error:
            raise MCPError(tool_error)
        return {
            "ok": True,
            "tool_count": len(tools),
            "tools": [t.name for t in tools],
            "skill_count": len(skills),
            "skills": [s.name for s in skills],
            "skill_error": skill_error,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        sess.close()
