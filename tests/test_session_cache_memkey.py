"""内存会话缓存键 ``_memkey`` 的项目隔离测试。

历史 bug：``_memkey`` 只拼 ``agent::session``，缺少项目维度。
``session_id`` 只在项目内唯一——两个项目都用 ``web-chat`` 时会命中同一份
内存 Session 对象，消息互相追加、``project_id`` 被覆盖，即"跨项目上下文串台"。
磁盘持久化本身按项目目录隔离，只有内存缓存这一层会串。
"""

from __future__ import annotations

from seed.core._session_cache import SESSIONS, _memkey


def test_memkey_includes_project():
    a = _memkey("default", "web-chat", "proj-A")
    b = _memkey("default", "web-chat", "proj-B")
    assert a != b, "跨项目同名 session 必须产生不同缓存键"
    assert "proj-A" in a and "proj-B" in b


def test_memkey_same_project_same_key():
    assert _memkey("default", "s1", "p1") == _memkey("default", "s1", "p1")


def test_memkey_without_project_is_backward_compatible():
    """未传 project_id 时退化为旧格式，保证未接入项目维度的调用点不炸。"""
    assert _memkey("default", "web-chat") == "default::web-chat"
    assert _memkey("default", "web-chat", "") == "default::web-chat"


def test_memkey_normalizes_agent_and_whitespace():
    assert _memkey("", "s1", "p1") == _memkey("default", "s1", "p1")
    assert _memkey(" default ", "s1", "p1") == _memkey("default", "s1", "p1")
    assert _memkey("default", " s1 ", " p1 ") == _memkey("default", "s1", "p1")


def test_cross_project_sessions_do_not_share_memory():
    """端到端：两个项目同名 session 各自持有独立消息列表。"""
    SESSIONS.clear()
    try:
        ka = _memkey("default", "web-chat", "proj-A")
        kb = _memkey("default", "web-chat", "proj-B")

        SESSIONS[ka] = {"project_id": "proj-A", "messages": []}
        SESSIONS[ka]["messages"].append("项目A的问题")

        # 项目 B 走同样的 key 计算 → 必须未命中项目 A 的缓存
        assert kb not in SESSIONS, "项目B 不应命中项目A 的缓存"
        SESSIONS[kb] = {"project_id": "proj-B", "messages": []}
        SESSIONS[kb]["messages"].append("项目B的问题")

        assert SESSIONS[ka]["messages"] == ["项目A的问题"]
        assert SESSIONS[kb]["messages"] == ["项目B的问题"]
        assert SESSIONS[ka] is not SESSIONS[kb]
    finally:
        SESSIONS.clear()


def test_cron_sched_passes_project_id_to_memkey():
    """回归守卫：cron 调度器的 _memkey 调用必须带 project_id。"""
    import inspect

    from seed.integrations import cron_sched

    src = inspect.getsource(cron_sched)
    assert "_memkey(agent_id, sid)" not in src, (
        "cron_sched 存在未带 project_id 的 _memkey 调用，会导致跨项目串台"
    )
