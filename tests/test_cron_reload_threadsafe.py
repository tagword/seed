"""Cron reload 线程安全测试。

背景：`reload_cron_scheduler()` 内部调用 `AsyncIOScheduler().start()`，
需要当前线程有 running event loop。agent 工具（seed_cron_apply / seed_cron_reload）
在无 loop 的 worker 线程执行，若直接 shutdown 再 start 会因
``no running event loop`` 失败，把运行中的调度器打成停摆。

修复后的行为：
1. 无 running loop 且未注册主循环 → 安全失败，绝不 shutdown 现有调度器
2. 已注册主循环 → 通过 run_coroutine_threadsafe 调度到主循环执行，任意线程可热更新
3. 有 running loop（服务内调用）→ 直接 reload
"""

import asyncio
import threading

import pytest

import seed.integrations.cron_sched as cs


def _run_in_worker(fn, *args):
    """在无 running loop 的 worker 线程执行 fn，返回其结果或抛异常。"""
    result: dict = {}

    def target():
        try:
            result["ret"] = fn(*args)
        except Exception as e:  # pragma: no cover
            result["err"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join(timeout=15)
    if "err" in result:
        raise result["err"]  # pragma: no cover
    return result.get("ret")


def _fake_config():
    return {
        "enabled": True,
        "jobs": [
            {
                "id": "test-job",
                "enabled": True,
                "cron": "0 4 * * *",
                "timezone": "UTC",
                "agent_id": "default",
                "session_id": "cron-test",
                "prompt": "test",
            }
        ],
    }


def test_reload_no_loop_safe_fail(monkeypatch):
    """无 running loop 且未注册主循环：reload 安全失败，_scheduler 保持 None。"""
    monkeypatch.setattr(cs, "_main_loop", None)
    monkeypatch.setattr(cs, "_scheduler", None)
    ret = _run_in_worker(cs.reload_cron_scheduler)
    assert ret == "error: no running event loop available"
    assert cs._scheduler is None


def test_reload_no_loop_preserves_running_scheduler(monkeypatch):
    """关键：无 loop 且无主循环时，已运行的调度器必须原样保留（不得被 shutdown）。"""
    monkeypatch.setattr(cs, "_main_loop", None)
    fake_sched = object()
    monkeypatch.setattr(cs, "_scheduler", fake_sched)
    ret = _run_in_worker(cs.reload_cron_scheduler)
    assert ret == "error: no running event loop available"
    assert cs._scheduler is fake_sched  # 原调度器未被触碰


def test_reload_in_loop_direct(monkeypatch):
    """服务内（有 running loop）：直接 reload，返回 'reloaded'。"""
    monkeypatch.setattr(cs, "load_cron_config", _fake_config)

    async def scenario():
        cs.register_main_loop()
        ret = cs.reload_cron_scheduler()
        assert ret == "reloaded"
        assert cs._scheduler is not None
        cs.shutdown_cron_scheduler()
        cs._main_loop = None

    asyncio.run(scenario())


def test_reload_via_main_loop_from_worker(monkeypatch):
    """注册主循环后，worker 线程 reload 调度到主循环执行成功。"""
    monkeypatch.setattr(cs, "load_cron_config", _fake_config)

    async def scenario():
        cs.register_main_loop()
        cs.start_cron_scheduler()
        assert cs._scheduler is not None
        # to_thread 的线程池线程没有 running loop → 走 main loop 分支
        ret = await asyncio.to_thread(cs.reload_cron_scheduler)
        assert ret == "reloaded (via main loop)"
        assert cs._scheduler is not None  # 新调度器已建立
        cs.shutdown_cron_scheduler()
        cs._main_loop = None

    asyncio.run(scenario())


def test_reload_main_loop_closed_safe_fail(monkeypatch):
    """主循环已关闭：安全失败，不破坏现有调度器。"""
    loop = asyncio.new_event_loop()

    async def scenario():
        cs.register_main_loop(loop)
        fake_sched = object()
        monkeypatch.setattr(cs, "_scheduler", fake_sched)
        ret = _run_in_worker(cs.reload_cron_scheduler)
        assert ret == "error: main loop not running"
        assert cs._scheduler is fake_sched
        cs._main_loop = None

    asyncio.run(scenario())
    loop.close()
