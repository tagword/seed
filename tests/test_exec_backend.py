"""Execution backend resolution."""

from __future__ import annotations

from seed.integrations import exec_backend as eb


def test_resolve_local_when_forced(monkeypatch) -> None:
    monkeypatch.setenv("SEED_EXEC_BACKEND", "local")
    assert eb.resolve_exec_backend() == "local"


def test_run_shell_local_echo(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SEED_EXEC_BACKEND", "local")
    code, out = eb.run_shell("echo hello-seed", timeout=10, cwd=str(tmp_path))
    assert code == 0
    assert "hello-seed" in out


def test_docker_available_false_when_no_binary(monkeypatch) -> None:
    monkeypatch.setattr(eb.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert eb.docker_available() is False
