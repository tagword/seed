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
            except json.JSONDecodeError as e:
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


class MCPClientManager:
    """Process-wide session cache."""

    def __init__(self) -> None:
        self._sessions: Dict[str, MCPStdioSession] = {}
        self._lock = threading.Lock()

    def get_session(self, server_id: str) -> MCPStdioSession:
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
                sess = MCPStdioSession(cfg)
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
                "connected": False,
            }
            with self._lock:
                sess = self._sessions.get(cfg.server_id)
            if sess and sess._proc is not None and sess._proc.poll() is None:
                row["connected"] = bool(sess._ready)
            if probe and cfg.enabled and mcp_globally_enabled():
                try:
                    tools = self.get_session(cfg.server_id).list_tools()
                    row["connected"] = True
                    row["tool_count"] = len(tools)
                    row["tools"] = [t.name for t in tools]
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
    sess = MCPStdioSession(cfg)
    try:
        tools = sess.list_tools()
        return {
            "ok": True,
            "tool_count": len(tools),
            "tools": [t.name for t in tools],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        sess.close()
