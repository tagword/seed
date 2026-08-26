"""Tests for host-injected cron job handler (codeagent → seed, one-way).

Covers: handler is invoked instead of the built-in path, exceptions from the
handler still release the per-job reentrancy guard, and unregistering falls
back to the built-in implementation.
"""
import asyncio

import pytest

from seed.integrations import cron_sched


@pytest.fixture(autouse=True)
def _reset_handler():
    prev = cron_sched._job_handler
    cron_sched._job_handler = None
    yield
    cron_sched._job_handler = prev


async def test_handler_invoked_instead_of_builtin(monkeypatch):
    """Registered handler receives the job and no built-in logic runs."""
    called = []
    builtin_touched = []

    async def fake_handler(job):
        called.append(job)
        return {"reply": "ok", "tools_used": ["a"]}

    cron_sched.register_cron_job_handler(fake_handler)

    # 内置路径的 ensure_agent_dirs 不应被调用（handler 分支在它之前返回）
    monkeypatch.setattr(cron_sched.asyncio, "to_thread", lambda *a, **k: asyncio.sleep(0))
    # 通过 monkeypatch 记录内置路径是否执行：把 _tools_for_agent 换成一个哨兵
    monkeypatch.setattr(
        cron_sched, "_tools_for_agent", lambda aid: builtin_touched.append(aid) or (None, None)
    )

    job = {
        "enabled": True,
        "id": "t-handler",
        "agent_id": "default",
        "session_id": "s-handler",
        "prompt": "hello",
        "max_tool_rounds": 2,
    }
    await cron_sched._run_cron_job_async(job)

    assert called == [job]
    assert builtin_touched == [], "built-in path must not run when handler registered"
    assert "t-handler" not in cron_sched._active_jobs, "reentrancy guard must be released"


async def test_handler_exception_releases_guard():
    """Handler crash must not leave the per-job guard set."""
    calls = []

    async def broken_handler(job):
        calls.append(job["id"])
        raise RuntimeError("boom")

    cron_sched.register_cron_job_handler(broken_handler)
    job = {
        "enabled": True,
        "id": "t-crash",
        "agent_id": "default",
        "session_id": "s-crash",
        "prompt": "hello",
    }
    await cron_sched._run_cron_job_async(job)

    assert calls == ["t-crash"]
    assert "t-crash" not in cron_sched._active_jobs


async def test_handler_returns_skipped_no_experience(monkeypatch):
    """skipped result must not attempt experience logging."""
    logged = []

    async def skip_handler(job):
        return {"skipped": True, "reply": ""}

    monkeypatch.setattr(
        cron_sched,
        "_log_cron_experience_sync",
        lambda **kw: logged.append(kw),
    )
    cron_sched.register_cron_job_handler(skip_handler)
    job = {
        "enabled": True,
        "id": "t-skip",
        "agent_id": "default",
        "session_id": "s-skip",
        "prompt": "hello",
    }
    await cron_sched._run_cron_job_async(job)

    assert logged == [], "skipped runs must not write experience logs"


async def test_unregister_clears_handler():
    """register_cron_job_handler(None) 取消注入 → 回退内置实现。"""
    async def fake_handler(job):
        return {"reply": "x"}

    cron_sched.register_cron_job_handler(fake_handler)
    assert cron_sched._job_handler is fake_handler

    cron_sched.register_cron_job_handler(None)
    assert cron_sched._job_handler is None

    # 未注册时 handler 分支条件为假 → 走内置路径（由既有 162 个测试覆盖）
    assert cron_sched._job_handler is None
