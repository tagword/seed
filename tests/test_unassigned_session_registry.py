"""无项目（随手聊）会话应注册到虚拟项目 __unassigned__ 并可见。

覆盖两个历史缺陷：
1. persist_chat_session 对空 project_id 直接跳过注册 → 随手聊永久不可见
2. __unassigned__ 项目行仅在 JSON→SQLite 迁移时创建 → 全新环境无"未分类"容器
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seed.core import proj_reg
from seed.core.llm_sess import persist_chat_session
from seed.core.models import Session
from seed.core.paths import agent_id_default
from seed.core.proj_reg import (
    UNASSIGNED_PROJECT_ID,
    _close_conn,
    list_project_session_ids,
    list_projects,
)


@pytest.fixture
def seed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _cleanup_conns():
    yield
    # 清理连接缓存，避免跨测试污染
    try:
        _close_conn(agent_id_default())
    except Exception:
        pass


def _make_session(sid: str, *, project_id=None, channel=None, source=None) -> Session:
    md = {}
    if project_id is not None:
        md["project_id"] = project_id
    if channel is not None:
        md["channel"] = channel
    if source is not None:
        md["source"] = source
    s = Session(id=sid, name=sid, messages=[{"role": "user", "content": "随手一问"}])
    s.metadata = md
    return s


# ── 缺陷 2：全新环境必须有 __unassigned__ 行 ──────────────────────

def test_fresh_env_has_unassigned_project(seed_home) -> None:
    """全新环境（无 registry.json）也应自动创建 __unassigned__。"""
    aid = agent_id_default()
    projects = list_projects(aid)
    ids = [p["id"] for p in projects]
    assert UNASSIGNED_PROJECT_ID in ids, f"未分类容器缺失: {ids}"


def test_ensure_unassigned_is_idempotent(seed_home) -> None:
    """重复获取连接不应产生重复行。"""
    aid = agent_id_default()
    list_projects(aid)
    list_projects(aid)
    conn = proj_reg._get_conn(aid)
    n = list(conn.execute(
        "SELECT COUNT(*) FROM projects WHERE id = ?", (UNASSIGNED_PROJECT_ID,)
    ))[0][0]
    assert n == 1


# ── 缺陷 1：随手聊应注册并可见 ────────────────────────────────────

def test_unassigned_chat_session_is_registered(seed_home) -> None:
    """无 project_id 的用户会话应注册到 __unassigned__。"""
    aid = agent_id_default()
    persist_chat_session(_make_session("casual-1"), aid)
    sids = list_project_session_ids(aid, UNASSIGNED_PROJECT_ID)
    assert "casual-1" in sids


def test_unassigned_session_not_registered_under_other_project(seed_home) -> None:
    """随手聊不应混入真实项目。"""
    aid = agent_id_default()
    persist_chat_session(_make_session("casual-2"), aid)
    projects = list_projects(aid, include_virtual=False)
    for p in projects:
        assert "casual-2" not in list_project_session_ids(aid, p["id"])


def test_project_session_still_routes_to_its_project(seed_home) -> None:
    """有 project_id 的会话仍归属该项目，不受本次改动影响。"""
    aid = agent_id_default()
    proj = proj_reg.create_project(aid, "真实项目")
    persist_chat_session(_make_session("proj-sess", project_id=proj["id"]), aid)
    assert "proj-sess" in list_project_session_ids(aid, proj["id"])
    assert "proj-sess" not in list_project_session_ids(aid, UNASSIGNED_PROJECT_ID)


# ── 系统会话排除 ──────────────────────────────────────────────────

def test_cron_session_not_in_unassigned(seed_home) -> None:
    """Cron 系统会话不应污染用户的未分类列表。"""
    aid = agent_id_default()
    persist_chat_session(
        _make_session("cron-x", channel="Cron", source="cron:daily"), aid
    )
    sids = list_project_session_ids(aid, UNASSIGNED_PROJECT_ID)
    assert "cron-x" not in sids


def test_webhook_session_not_in_unassigned(seed_home) -> None:
    """Webhook 系统会话同样排除。"""
    aid = agent_id_default()
    persist_chat_session(
        _make_session("hook-x", channel="Webhook"), aid
    )
    sids = list_project_session_ids(aid, UNASSIGNED_PROJECT_ID)
    assert "hook-x" not in sids


def test_normal_web_chat_is_included(seed_home) -> None:
    """普通 Web 聊天（channel="Web 聊天"）应纳入未分类。"""
    aid = agent_id_default()
    persist_chat_session(
        _make_session("web-x", channel="Web 聊天"), aid
    )
    sids = list_project_session_ids(aid, UNASSIGNED_PROJECT_ID)
    assert "web-x" in sids


# ── 迁移兼容：老环境已有 __unassigned__ 不重复插入 ────────────────

def test_existing_unassigned_not_duplicated(seed_home) -> None:
    """已存在 __unassigned__ 行时，兜底逻辑不应重复插入或覆盖。"""
    aid = agent_id_default()
    conn = proj_reg._get_conn(aid)
    # 人为改名为自定义，验证不被覆盖
    conn.execute(
        "UPDATE projects SET name = '我的未分类' WHERE id = ?", (UNASSIGNED_PROJECT_ID,)
    )
    conn.commit()
    proj_reg._ensure_unassigned_project(conn)
    row = list(conn.execute(
        "SELECT name FROM projects WHERE id = ?", (UNASSIGNED_PROJECT_ID,)
    ))[0]
    assert row[0] == "我的未分类"
