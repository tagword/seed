"""Minimal LSP stdio client (definition + pull diagnostics when supported)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from seed.core.env_access import LSP_ENABLED, env_truthy
from seed.integrations.lsp_config import LSPServerConfig

logger = logging.getLogger(__name__)


class LSPError(Exception):
    pass


def lsp_enabled() -> bool:
    return env_truthy(*LSP_ENABLED, default="1")


class LSPStdioSession:
    def __init__(self, cfg: LSPServerConfig, root_uri: str):
        self.cfg = cfg
        self.root_uri = root_uri
        self._proc: Optional[subprocess.Popen[str]] = None
        self._lock = threading.Lock()
        self._req_id = 0
        self._version: Dict[str, int] = {}
        self._ready = False

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            with contextlib.suppress(Exception):
                self._proc.terminate()
        self._proc = None
        self._ready = False

    def _readline(self, timeout: float = 30.0) -> str:
        if not self._proc or not self._proc.stdout:
            raise LSPError("LSP not running")
        stream = self._proc.stdout
        box: list[Optional[str]] = [None]

        def _read() -> None:
            box[0] = stream.readline()

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise LSPError("LSP read timeout")
        if not box[0]:
            raise LSPError("LSP stdout closed")
        return box[0]

    def _write(self, msg: Dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise LSPError("LSP not running")
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params: Dict[str, Any], *, timeout: float = 45.0) -> Any:
        self._req_id += 1
        rid = self._req_id
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            line = self._readline(timeout).strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("id") == rid:
                if msg.get("error"):
                    raise LSPError(str(msg["error"]))
                return msg.get("result")

    def _notify(self, method: str, params: Dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _ensure_started(self) -> None:
        if self._proc and self._proc.poll() is None and self._ready:
            return
        argv = self.cfg.argv()
        env = {**os.environ, **self.cfg.env}
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._ready = False
        self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": self.root_uri,
                "capabilities": {
                    "textDocument": {
                        "definition": {"dynamicRegistration": False},
                        "diagnostic": {"dynamicRegistration": False},
                    }
                },
            },
            timeout=30.0,
        )
        self._notify("initialized", {})
        self._ready = True

    def _file_uri(self, path: Path) -> str:
        return path.resolve().as_uri()

    def _did_open(self, path: Path, text: str) -> None:
        uri = self._file_uri(path)
        self._version[uri] = self._version.get(uri, 0) + 1
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": self.cfg.language_id,
                    "version": self._version[uri],
                    "text": text,
                }
            },
        )

    def definition(self, filepath: Path, line: int, character: int) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_started()
            text = filepath.read_text(encoding="utf-8", errors="replace")
            self._did_open(filepath, text)
            result = self._request(
                "textDocument/definition",
                {
                    "textDocument": {"uri": self._file_uri(filepath)},
                    "position": {"line": max(0, line - 1), "character": max(0, character)},
                },
            )
        if not result:
            return []
        if isinstance(result, dict):
            return [result]
        if isinstance(result, list):
            return [r for r in result if isinstance(r, dict)]
        return []

    def diagnostics_pull(self, filepath: Path) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_started()
            text = filepath.read_text(encoding="utf-8", errors="replace")
            self._did_open(filepath, text)
            try:
                result = self._request(
                    "textDocument/diagnostic",
                    {"textDocument": {"uri": self._file_uri(filepath)}},
                    timeout=60.0,
                )
            except LSPError:
                return []
        if not isinstance(result, dict):
            return []
        items = result.get("items") or result.get("relatedDocuments")
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
        kind = result.get("kind")
        if kind == "full" and isinstance(result.get("items"), list):
            return result["items"]
        return []


_sessions: Dict[str, LSPStdioSession] = {}
_sessions_lock = threading.Lock()


def get_lsp_session(cfg: LSPServerConfig, project_root: Path) -> LSPStdioSession:
    key = cfg.language_id
    with _sessions_lock:
        sess = _sessions.get(key)
        if sess is None:
            sess = LSPStdioSession(cfg, project_root.resolve().as_uri())
            _sessions[key] = sess
        return sess


def reset_lsp_sessions() -> None:
    with _sessions_lock:
        for s in list(_sessions.values()):
            s.close()
        _sessions.clear()


def format_location(loc: Dict[str, Any]) -> str:
    uri = str(loc.get("uri") or "")
    rng = loc.get("range") or {}
    start = rng.get("start") or {}
    line = int(start.get("line", 0)) + 1
    col = int(start.get("character", 0)) + 1
    path = uri.replace("file://", "") if uri.startswith("file://") else uri
    return f"{path}:{line}:{col}"


def pyright_cli_diagnostics(filepath: Path, *, cwd: Optional[Path] = None) -> Tuple[bool, str]:
    """Fallback diagnostics via ``pyright --outputjson`` if on PATH."""
    import shlex

    from seed.integrations.exec_backend import run_shell

    cmd = f"pyright {shlex.quote(str(filepath))} --outputjson"
    code, out = run_shell(cmd, timeout=120, cwd=str(cwd or filepath.parent))
    return code == 0, out
