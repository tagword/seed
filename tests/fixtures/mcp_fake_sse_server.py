#!/usr/bin/env python3
"""Minimal MCP SSE server for tests.

Usage: python mcp_fake_sse_server.py [port]
  1. SSE endpoint at GET /sse
  2. Message endpoint at POST /message (sent via SSE endpoint event)
  3. Supports: initialize, tools/list (echo, add), tools/call
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")
logger = logging.getLogger("mcp-fake-sse")


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server for SSE testing."""
    daemon_threads = True


class MCPSseTestHandler(BaseHTTPRequestHandler):
    """HTTP handler for MCP SSE test server."""

    # Shared state - server-level attributes set in run_server()
    # server._sse_handler = None
    # server._sse_ready = threading.Event()
    # server._sse_pending = []
    # server._shutdown = False

    def log_message(self, fmt, *args):
        logger.debug(fmt, *args)

    def _send_sse(self, event: str, data: str) -> None:
        """Send an SSE event on the streaming connection."""
        self.wfile.write(f"event: {event}\n".encode())
        self.wfile.write(f"data: {data}\n\n".encode())
        self.wfile.flush()

    def _handle_jsonrpc(self, body: dict) -> dict:
        """Process a JSON-RPC request and return response."""
        method = body.get("method")
        params = body.get("params") or {}
        req_id = body.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-sse-mcp", "version": "0.1"},
                },
            }
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo text",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                            },
                        },
                        {
                            "name": "add",
                            "description": "Add two numbers",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "a": {"type": "number"},
                                    "b": {"type": "number"},
                                },
                                "required": ["a", "b"],
                            },
                        },
                    ]
                },
            }
        elif method == "tools/call":
            args = params.get("arguments") or {}
            name = params.get("name", "")
            if name == "echo":
                text = str(args.get("text") or "")
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"echo:{text}"}],
                        "isError": False,
                    },
                }
            elif name == "add":
                a = float(args.get("a", 0))
                b = float(args.get("b", 0))
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": str(a + b)}],
                        "isError": False,
                    },
                }
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {name}"},
                }
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

    def do_GET(self):
        if self.path == "/sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            # Send endpoint event — tell client where to POST messages
            host = self.server.server_address[0]
            port = self.server.server_address[1]
            post_url = f"http://{host}:{port}/message"
            self._send_sse("endpoint", post_url)
            logger.info("SSE connected, endpoint=%s", post_url)

            self.server._sse_handler = self

            # Keep connection alive, send pending responses
            try:
                while not getattr(self.server, "_shutdown", False):
                    self.server._sse_ready.wait(timeout=0.5)
                    self.server._sse_ready.clear()
                    pending = getattr(self.server, "_sse_pending", [])
                    while pending:
                        resp = pending.pop(0)
                        self._send_sse("message", json.dumps(resp))
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            logger.info("SSE connection closed")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/message":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                req = json.loads(body)
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"bad json"}')
                return

            resp = self._handle_jsonrpc(req)

            # Queue the response for SSE delivery
            if not hasattr(self.server, "_sse_pending"):
                self.server._sse_pending = []
            self.server._sse_pending.append(resp)
            self.server._sse_ready.set()

            # Return 202 Accepted (response comes via SSE)
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"accepted":true}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def run_server(host: str = "127.0.0.1", port: int = 0) -> HTTPServer:
    """Start SSE test server and return (server, port)."""
    server = ThreadingHTTPServer((host, port), MCPSseTestHandler)
    server._sse_handler = None
    server._sse_ready = threading.Event()
    server._sse_pending = []
    server._shutdown = False

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    actual_port = server.server_address[1]
    logger.info("MCP SSE test server on http://%s:%d", host, actual_port)
    return server


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    server = run_server(port=port)
    print(f"http://127.0.0.1:{server.server_address[1]}/sse")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server._shutdown = True
        server.shutdown()


if __name__ == "__main__":
    main()
