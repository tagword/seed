"""
Shell execution backends: host (local) or Docker-isolated.

Env (``CODEAGENT_*`` aliases honored):
  SEED_EXEC_BACKEND=auto|local|docker   (default auto: Docker if available else local)
  SEED_EXEC_DOCKER_IMAGE              (default python:3.12-slim)
  SEED_EXEC_DOCKER_WORKDIR            (default /workspace)
  SEED_EXEC_DOCKER_NETWORK            (optional, e.g. bridge; omit for Docker default)
"""

from __future__ import annotations

import logging
import os
import subprocess
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


def _resolve_cwd(cwd: Optional[str]) -> Path:
    if cwd and str(cwd).strip():
        return Path(cwd).expanduser().resolve()
    return Path.cwd().resolve()


def _windows_no_window_kwargs() -> dict:
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    si.wShowWindow = 0
    return {"startupinfo": si, "creationflags": 0x08000000}


def _run_local(command: str, timeout: int, cwd: Path, detach: bool = False) -> Tuple[int, str]:
    if detach:
        # Detached background execution: spawn and forget
        kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP = silent background
            kwargs["startupinfo"] = _windows_no_window_kwargs()["startupinfo"]
            kwargs["creationflags"] = 0x08000000 | 0x00000200
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(command, shell=True, cwd=str(cwd), **kwargs)
        return 0, f"Detached PID: {proc.pid}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            **_windows_no_window_kwargs(),
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
