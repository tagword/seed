"""
Shell execution backends: host (local) or Docker-isolated.

Env (``CODEAGENT_*`` aliases honored):
  SEED_EXEC_BACKEND=auto|local|docker   (default auto: Docker if available else local)
  SEED_EXEC_DOCKER_IMAGE              (default python:3.12-slim)
  SEED_EXEC_DOCKER_WORKDIR            (default /workspace)
  SEED_EXEC_DOCKER_NETWORK            (optional, e.g. bridge; omit for Docker default)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from seed.core.env_access import (
    EXEC_BACKEND,
    EXEC_DOCKER_IMAGE,
    EXEC_DOCKER_NETWORK,
    EXEC_DOCKER_WORKDIR,
    pick_default,
)

logger = logging.getLogger(__name__)

# ── Detached task management ──────────────────────────────────────────
_DETACH_TASKS_DIR = ".scripts/tasks"


def _tasks_dir(cwd: Path) -> Path:
    return cwd / _DETACH_TASKS_DIR


def _task_dir(task_id: str, cwd: Path) -> Path:
    return _tasks_dir(cwd) / task_id


def _task_meta_path(task_id: str, cwd: Path) -> Path:
    return _task_dir(task_id, cwd) / "meta.json"


def _task_combined_path(task_id: str, cwd: Path) -> Path:
    return _task_dir(task_id, cwd) / "combined.log"


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive (Unix only; Windows falls back)."""
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _load_task_meta(task_id: str, cwd: Path) -> Optional[dict]:
    p = _task_meta_path(task_id, cwd)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_task_meta(meta: dict, task_id: str, cwd: Path) -> None:
    p = _task_meta_path(task_id, cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def _compute_task_status(meta: dict) -> str:
    """Compute current status of a task based on meta + process liveness."""
    if meta.get("status") in ("killed", "failed"):
        return meta["status"]
    pid = meta.get("pid")
    if pid and _pid_alive(pid):
        return "running"
    exit_code = meta.get("exit_code")
    if exit_code is not None:
        return "completed" if exit_code == 0 else "failed"
    return "unknown"


def detach_task_status(task_id: str, cwd: Optional[str] = None) -> str:
    """Return human-readable status of a detached task."""
    work = _resolve_cwd(cwd)
    meta = _load_task_meta(task_id, work)
    if meta is None:
        return f"Task {task_id}: not found"
    status = _compute_task_status(meta)
    parts = [f"Task {task_id}: {status}"]
    if meta.get("command"):
        parts.append(f"  command: {meta['command']}")
    if meta.get("pid"):
        parts.append(f"  pid: {meta['pid']}")
    if meta.get("start_time"):
        parts.append(f"  started: {meta['start_time']}")
    exit_code = meta.get("exit_code")
    if exit_code is not None:
        parts.append(f"  exit code: {exit_code}")
    return "\n".join(parts)


def detach_task_log(
    task_id: str, tail: int = 20, cwd: Optional[str] = None
) -> str:
    """Return tail of combined log for a detached task."""
    work = _resolve_cwd(cwd)
    log_path = _task_combined_path(task_id, work)
    if not log_path.is_file():
        meta = _load_task_meta(task_id, work)
        if meta is None:
            return f"Task {task_id}: not found"
        return f"Task {task_id}: no log file yet"
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if tail <= 0:
            tail = len(lines)
        chunk = lines[-tail:]
        header = f"Task {task_id} (last {len(chunk)} of {len(lines)} lines):\n"
        return header + "\n".join(chunk)
    except OSError as e:
        return f"Task {task_id}: error reading log: {e}"


def detach_task_stop(task_id: str, cwd: Optional[str] = None) -> str:
    """Stop (kill) a detached task."""
    work = _resolve_cwd(cwd)
    meta = _load_task_meta(task_id, work)
    if meta is None:
        return f"Task {task_id}: not found"
    status = _compute_task_status(meta)
    if status != "running":
        return f"Task {task_id}: not running (status: {status})"
    pid = meta.get("pid")
    if not pid:
        return f"Task {task_id}: no pid recorded"
    try:
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)
        else:
            # Send SIGTERM to the process group
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            # Give it 5 seconds, then SIGKILL
            for _ in range(5):
                if not _pid_alive(pid):
                    break
                time.sleep(1)
            else:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        meta["status"] = "killed"
        _save_task_meta(meta, task_id, work)
        return f"Task {task_id} (PID {pid}): stopped"
    except ProcessLookupError:
        meta["status"] = "completed"
        _save_task_meta(meta, task_id, work)
        return f"Task {task_id} (PID {pid}): already exited"
    except Exception as e:
        return f"Task {task_id}: error stopping: {e}"


def detach_task_list(cwd: Optional[str] = None) -> str:
    """List all tracked detached tasks."""
    work = _resolve_cwd(cwd)
    td = _tasks_dir(work)
    if not td.is_dir():
        return "No tasks found."
    lines = []
    for d in sorted(td.iterdir()):
        if not d.is_dir():
            continue
        meta = _load_task_meta(d.name, work)
        if meta is None:
            continue
        status = _compute_task_status(meta)
        cmd = (meta.get("command") or "?")[:60]
        lines.append(f"  {d.name:14s} {status:10s} {cmd}")
    if not lines:
        return "No tasks found."
    return "Tasks:\n" + "\n".join(lines)


def _resolve_cwd(cwd: Optional[str]) -> Path:
    if cwd and str(cwd).strip():
        return Path(cwd).expanduser().resolve()
    return Path.cwd().resolve()

_FALLBACK_NOTE = "[seed] Docker unavailable — command ran on host. Set SEED_EXEC_BACKEND=local to hide.\n"


def docker_available() -> bool:
    """True if ``docker info`` succeeds within a short timeout."""
    try:
        r = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def resolve_exec_backend() -> str:
    """Return ``local`` or ``docker`` after resolving ``auto``."""
    raw = (pick_default("auto", *EXEC_BACKEND) or "auto").strip().lower()
    if raw == "docker":
        return "docker" if docker_available() else "local"
    if raw == "auto":
        return "docker" if docker_available() else "local"
    return "local"


def exec_backend_label() -> str:
    """Human-readable backend for tool output / setup UI."""
    b = resolve_exec_backend()
    if b == "docker":
        img = pick_default("python:3.12-slim", *EXEC_DOCKER_IMAGE)
        return f"docker ({img})"
    return "local (host)"


def run_shell(
    command: str,
    *,
    timeout: int = 30,
    cwd: Optional[str] = None,
    detach: bool = False,
) -> Tuple[int, str]:
    """
    Run a shell command. Returns ``(returncode, combined_output)``.

    Safety checks (``check_bash_command``) must be applied by the caller before this.
    When ``detach=True``, the command runs in the background and the call returns
    immediately with the process PID (or an error).
    """
    backend = resolve_exec_backend()
    work = _resolve_cwd(cwd)
    if backend == "docker":
        code, out = _run_docker(command, timeout=timeout, cwd=work, detach=detach)
        if code == -2:
            # docker missing at runtime — fall back
            code, out = _run_local(command, timeout=timeout, cwd=work, detach=detach)
            out = _FALLBACK_NOTE + out
        return code, out
    return _run_local(command, timeout=timeout, cwd=work, detach=detach)


def _windows_no_window_kwargs() -> dict:
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    si.wShowWindow = 0
    return {"startupinfo": si, "creationflags": 0x08000000}


def _run_local(command: str, timeout: int, cwd: Path, detach: bool = False) -> Tuple[int, str]:
    if detach:
        # Detached background execution: spawn with log capture
        task_id = uuid.uuid4().hex[:12]
        tdir = _task_dir(task_id, cwd)
        tdir.mkdir(parents=True, exist_ok=True)

        combined_path = _task_combined_path(task_id, cwd)

        meta = {
            "task_id": task_id,
            "command": command,
            "cwd": str(cwd),
            "start_time": datetime.now(timezone.utc).isoformat(),
            "pid": None,
            "status": "running",
            "exit_code": None,
        }
        _save_task_meta(meta, task_id, cwd)

        # Open combined log as a real file (Popen needs fileno())
        combined_f = open(combined_path, "w", buffering=1, encoding="utf-8")

        kwargs: dict = {
            "stdout": combined_f,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["startupinfo"] = _windows_no_window_kwargs()["startupinfo"]
            kwargs["creationflags"] = 0x08000000 | 0x00000200
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(command, shell=True, cwd=str(cwd), **kwargs)

        # Update meta with pid BEFORE starting monitor thread (avoid race)
        meta["pid"] = proc.pid
        _save_task_meta(meta, task_id, cwd)

        # Close our handle (child process has its own copy via fork)
        combined_f.close()

        # Spawn a daemon thread to record exit code when process finishes
        def _monitor(p: subprocess.Popen, tid: str, wd: Path) -> None:
            p.wait()
            m = _load_task_meta(tid, wd)
            if m and m.get("status") == "running":
                m["exit_code"] = p.returncode
                m["status"] = "completed" if p.returncode == 0 else "failed"
                _save_task_meta(m, tid, wd)

        threading.Thread(target=_monitor, args=(proc, task_id, cwd), daemon=True).start()

        return 0, (
            f"Task {task_id} started (PID: {proc.pid}).\n"
            f"  logs: .scripts/tasks/{task_id}/combined.log\n"
            f"  manage: task status {task_id} | task log {task_id} | task stop {task_id}"
        )

    try:
        # Windows: 显式指定 UTF-8 + errors='replace' 防止 GBK 解码崩溃
        # （npx / pip 等子进程输出 UTF-8 内容时，GBK 解码会抛 UnicodeDecodeError）
        sub_kw = {}
        if os.name == "nt":
            sub_kw["encoding"] = "utf-8"
            sub_kw["errors"] = "replace"
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            **_windows_no_window_kwargs(),
            **sub_kw,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return -1, f"Command timed out after {timeout} seconds"
    except Exception as e:
        return -1, f"Error executing command: {e}"


def _run_docker(command: str, timeout: int, cwd: Path, detach: bool = False) -> Tuple[int, str]:
    if not docker_available():
        return -2, "Docker is not available"

    image = (pick_default("python:3.12-slim", *EXEC_DOCKER_IMAGE) or "python:3.12-slim").strip()
    container_wd = (
        pick_default("/workspace", *EXEC_DOCKER_WORKDIR) or "/workspace"
    ).strip()
    network = (pick_default("", *EXEC_DOCKER_NETWORK) or "").strip()

    mount_src = str(cwd)
    # Docker Desktop on Windows accepts native paths; WSL paths need no extra conversion here.
    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{mount_src}:{container_wd}",
        "-w",
        container_wd,
    ]
    if network:
        docker_cmd.extend(["--network", network])
    if detach:
        # Background: detach mode — no timeout enforcement, return container ID
        docker_cmd.insert(2, "-d")  # docker run -d
        docker_cmd.extend(["image", "sh", "-lc", command])
        try:
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=15,
            )
            output = result.stdout.strip() if result.stdout else ""
            if result.returncode != 0:
                return result.returncode, f"docker run -d failed: {result.stderr or 'unknown error'}"
            return 0, f"Detached container: {output}"
        except subprocess.TimeoutExpired:
            return -1, "Docker detach command timed out"
        except FileNotFoundError:
            return -2, "docker binary not found"
        except Exception as e:
            return -1, f"Docker detach error: {e}"
    else:
        docker_cmd.extend([image, "sh", "-lc", command])

    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 15,
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 and not output.strip():
            output = f"docker exit {result.returncode}"
        prefix = f"[docker:{image}] cwd={container_wd}\n"
        return result.returncode, prefix + output
    except subprocess.TimeoutExpired:
        return -1, f"Docker command timed out after {timeout} seconds"
    except FileNotFoundError:
        return -2, "docker binary not found"
    except Exception as e:
        return -1, f"Docker execution error: {e}"
