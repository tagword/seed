"""Integration tests for team capability Phase 1.

Tests AgentRegistry + team tools (call_agent, dispatch) 
in same-process mode, verifying the wiring and data flow.
"""
from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from seed.core.agent_registry import AgentHandle, AgentRegistry


# ── Fixtures ──


class _MockAgent:
    """Minimal mock that mimics AutonomousAgent.run_task()."""

    def __init__(self, agent_id: str, response_prefix: str = "ok"):
        self.agent_id = agent_id
        self._prefix = response_prefix

    def run_task(self, task: str) -> Dict[str, Any]:
        return {"content": f"{self._prefix}: {task}", "success": True}


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure AgentRegistry is clean before and after each test."""
    AgentRegistry.clear()
    yield
    AgentRegistry.clear()


@pytest.fixture
def registered_agents():
    """Register three agents and return their ids."""
    backend = _MockAgent("backend", "backend-ok")
    frontend = _MockAgent("frontend", "frontend-ok")
    db = _MockAgent("db", "db-ok")

    AgentRegistry.register("backend", AgentHandle(agent=backend))
    AgentRegistry.register("frontend", AgentHandle(agent=frontend))
    AgentRegistry.register("db", AgentHandle(agent=db))

    return ["backend", "frontend", "db"]


# ── AgentRegistry Tests ──


class TestAgentRegistry:
    def test_register_and_get(self):
        agent = _MockAgent("worker1")
        AgentRegistry.register("worker1", AgentHandle(agent=agent))
        handle = AgentRegistry.get("worker1")
        assert handle is not None
        assert handle.agent_id == "worker1"

    def test_get_nonexistent(self):
        assert AgentRegistry.get("nobody") is None

    def test_list_returns_copy(self):
        a = _MockAgent("a")
        b = _MockAgent("b")
        AgentRegistry.register("a", AgentHandle(agent=a))
        AgentRegistry.register("b", AgentHandle(agent=b))
        all_agents = AgentRegistry.list()
        assert set(all_agents.keys()) == {"a", "b"}
        # Modifying the returned dict does not affect registry
        all_agents.pop("a")
        assert AgentRegistry.get("a") is not None

    def test_unregister(self):
        agent = _MockAgent("temp")
        AgentRegistry.register("temp", AgentHandle(agent=agent))
        assert AgentRegistry.unregister("temp") is True
        assert AgentRegistry.get("temp") is None

    def test_unregister_nonexistent(self):
        assert AgentRegistry.unregister("nobody") is False

    def test_clear(self):
        AgentRegistry.register("x", AgentHandle(agent=_MockAgent("x")))
        AgentRegistry.register("y", AgentHandle(agent=_MockAgent("y")))
        AgentRegistry.clear()
        assert AgentRegistry.list() == {}


# ── AgentHandle Tests ──


class TestAgentHandle:
    def test_run_task_returns_content(self):
        agent = _MockAgent("test", "hello")
        handle = AgentHandle(agent=agent)
        result = handle.run_task("do something")
        assert result == "hello: do something"

    def test_run_task_error_handling(self):
        class _FailingAgent:
            agent_id = "failing"

            def run_task(self, task: str) -> Dict[str, Any]:
                return {"content": "", "error": "something went wrong"}

        handle = AgentHandle(agent=_FailingAgent())
        result = handle.run_task("will fail")
        assert "Error" in result
        assert "something went wrong" in result

    def test_run_task_exception(self):
        class _CrashAgent:
            agent_id = "crash"

            def run_task(self, task: str) -> Dict[str, Any]:
                raise RuntimeError("kaboom")

        handle = AgentHandle(agent=_CrashAgent())
        result = handle.run_task("crash it")
        assert "Error" in result
        assert "kaboom" in result

    def test_cross_process_not_implemented(self):
        handle = AgentHandle(url="http://example.com/agent")
        result = handle.run_task("cross process task")
        assert "not implemented" in result.lower()

    def test_requires_agent_or_url(self):
        with pytest.raises(ValueError, match="Either agent"):
            AgentHandle()

    def test_is_same_process(self):
        assert AgentHandle(agent=_MockAgent("x")).is_same_process is True
        assert AgentHandle(url="http://x").is_same_process is False


# ── Team Tools Integration (via direct function calls) ──


class TestCallAgent:
    """Tests the call_agent tool logic directly."""

    @pytest.mark.asyncio
    async def test_call_agent_success(self, registered_agents):
        from seed_tools.team_tools import call_agent

        result = await call_agent("backend", "implement login API")
        assert result == "backend-ok: implement login API"

    @pytest.mark.asyncio
    async def test_call_agent_not_found(self, registered_agents):
        from seed_tools.team_tools import call_agent

        result = await call_agent("nonexistent", "any task")
        assert "Error" in result
        assert "not found" in result


class TestDispatch:
    """Tests the dispatch tool logic directly."""

    @pytest.mark.asyncio
    async def test_dispatch_sequential(self, registered_agents):
        from seed_tools.team_tools import dispatch

        tasks = [
            {"agent_id": "backend", "task": "build API"},
            {"agent_id": "frontend", "task": "build UI"},
        ]
        raw = await dispatch(tasks, mode="sequential")
        results = json.loads(raw)

        assert len(results) == 2
        assert results[0]["agent_id"] == "backend"
        assert results[0]["result"] == "backend-ok: build API"
        assert results[1]["agent_id"] == "frontend"
        assert results[1]["result"] == "frontend-ok: build UI"

    @pytest.mark.asyncio
    async def test_dispatch_parallel(self, registered_agents):
        from seed_tools.team_tools import dispatch

        tasks = [
            {"agent_id": "backend", "task": "build API"},
            {"agent_id": "frontend", "task": "build UI"},
            {"agent_id": "db", "task": "design schema"},
        ]
        raw = await dispatch(tasks, mode="parallel")
        results = json.loads(raw)

        assert len(results) == 3
        agent_ids = {r["agent_id"] for r in results}
        assert agent_ids == {"backend", "frontend", "db"}

        result_map = {r["agent_id"]: r["result"] for r in results}
        assert result_map["backend"] == "backend-ok: build API"
        assert result_map["frontend"] == "frontend-ok: build UI"
        assert result_map["db"] == "db-ok: design schema"

    @pytest.mark.asyncio
    async def test_dispatch_empty(self, registered_agents):
        from seed_tools.team_tools import dispatch

        raw = await dispatch([], mode="sequential")
        assert json.loads(raw) == []

    @pytest.mark.asyncio
    async def test_dispatch_stop_on_first_error(self, registered_agents):
        from seed_tools.team_tools import dispatch

        tasks = [
            {"agent_id": "backend", "task": "ok"},
            {"agent_id": "nonexistent", "task": "will fail"},
            {"agent_id": "frontend", "task": "never reached"},
        ]
        raw = await dispatch(tasks, mode="sequential")
        results = json.loads(raw)

        assert len(results) == 2  # stops after first error
        assert "result" in results[0]  # first succeeded
        assert "error" in results[1]  # second failed

    @pytest.mark.asyncio
    async def test_dispatch_unknown_mode(self, registered_agents):
        from seed_tools.team_tools import dispatch

        raw = await dispatch([{"agent_id": "backend", "task": "x"}], mode="invalid")
        result = json.loads(raw)
        assert "error" in result


class TestParallel:
    @pytest.mark.asyncio
    async def test_parallel_shortcut(self, registered_agents):
        from seed_tools.team_tools import parallel

        tasks = [
            {"agent_id": "backend", "task": "task 1"},
            {"agent_id": "frontend", "task": "task 2"},
        ]
        raw = await parallel(tasks)
        results = json.loads(raw)

        assert len(results) == 2
        agent_ids = {r["agent_id"] for r in results}
        assert agent_ids == {"backend", "frontend"}
