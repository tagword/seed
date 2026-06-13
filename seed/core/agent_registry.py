"""Agent registry — singleton for registering and looking up agent handles.

Seed 是库（内核引擎），不能单独运行。宿主（如 CodeAgent、自定义 CLI 等）
负责创建 AutonomousAgent 实例并注册到 AgentRegistry，供团队工具使用。

同进程模式：call_agent = 函数调用（零网络开销）
跨进程模式：call_agent = HTTP POST（Phase 1 仅保留接口，Phase 1+ 实现）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from seed.core.turn_loop import AutonomousAgent

logger = logging.getLogger(__name__)


class AgentHandle:
    """Handle to an agent — abstracts same-process vs cross-process access.

    Usage::
        handle = AgentHandle(agent=some_agent_instance)
        result = handle.run_task("do something")

    Phase 1 只支持同进程（agent 参数传入 AutonomousAgent 实例）。
    跨进程（url 参数）仅保留接口，暂不实现。
    """

    def __init__(
        self,
        agent: Optional[AutonomousAgent] = None,
        url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        if agent is None and url is None:
            raise ValueError("Either agent (same-process) or url (cross-process) must be provided")

        self._agent = agent
        self._url = url
        self.metadata = metadata or {}

    @property
    def is_same_process(self) -> bool:
        """True if this handle uses same-process function call transport."""
        return self._agent is not None

    @property
    def agent_id(self) -> str:
        """Return the agent's id string if available."""
        if self._agent and hasattr(self._agent, "agent_id"):
            return str(self._agent.agent_id)
        if self._url:
            return self._url
        return "unknown"

    def run_task(self, task: str) -> str:
        """Execute a task on the agent and return the result text.

        Same-process: calls agent.run_task() directly (zero-latency function call).
        Cross-process: would POST to agent's HTTP endpoint (Phase 1 not supported).

        Args:
            task: Task description to send to the agent.

        Returns:
            Agent's response text.
        """
        if self._agent is not None:
            return self._run_same_process(task)
        else:
            return self._run_cross_process(task)

    def _run_same_process(self, task: str) -> str:
        """Same-process: function call, zero network overhead."""
        logger.info(f"AgentHandle.run_task (same-process): {task[:80]}...")
        try:
            result = self._agent.run_task(task)
            content = result.get("content", "")
            if content:
                return content
            error = result.get("error", "")
            if error:
                return f"Error: {error}"
            return "Agent returned no response"
        except Exception as e:
            logger.exception(f"AgentHandle.run_task failed: {e}")
            return f"Error: {e}"

    def _run_cross_process(self, task: str) -> str:
        """Cross-process placeholder — not implemented in Phase 1."""
        logger.warning(
            f"AgentHandle.run_task (cross-process) not implemented yet. "
            f"Would POST to {self._url}"
        )
        return (
            f"Error: cross-process agent call not implemented. "
            f"Agent URL configured as {self._url}, but Phase 1 only supports same-process calls."
        )


class AgentRegistry:
    """Singleton registry for agent handles.

    All agents in the same process share this registry.
    Team tools (call_agent, dispatch, parallel) access the registry
    to look up target agents.

    Usage::

        AgentRegistry.register("backend", AgentHandle(agent=backend_agent))
        handle = AgentRegistry.get("backend")
        result = handle.run_task("implement login API")
    """

    _instance: Optional["AgentRegistry"] = None

    def __new__(cls) -> "AgentRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._handles: Dict[str, AgentHandle] = {}
        return cls._instance

    @classmethod
    def register(cls, agent_id: str, handle: AgentHandle) -> None:
        """Register an agent handle under the given id.

        Args:
            agent_id: Unique identifier for the agent.
            handle: AgentHandle wrapping the agent instance.
        """
        registry = cls()
        existing = registry._handles.get(agent_id)
        if existing:
            logger.warning(f"Overwriting existing agent '{agent_id}' (was: {existing.agent_id})")
        registry._handles[agent_id] = handle
        logger.info(f"Registered agent '{agent_id}'")

    @classmethod
    def get(cls, agent_id: str) -> Optional[AgentHandle]:
        """Look up an agent handle by id.

        Args:
            agent_id: The agent identifier.

        Returns:
            AgentHandle if found, None otherwise.
        """
        return cls()._handles.get(agent_id)

    @classmethod
    def list(cls) -> Dict[str, AgentHandle]:
        """Return a copy of all registered agent handles."""
        return dict(cls()._handles)

    @classmethod
    def unregister(cls, agent_id: str) -> bool:
        """Remove an agent from the registry.

        Args:
            agent_id: The agent identifier to remove.

        Returns:
            True if removed, False if not found.
        """
        registry = cls()
        if agent_id in registry._handles:
            del registry._handles[agent_id]
            logger.info(f"Unregistered agent '{agent_id}'")
            return True
        logger.warning(f"Agent '{agent_id}' not found for unregister")
        return False

    @classmethod
    def clear(cls) -> None:
        """Remove all registered agents (mainly for testing)."""
        cls()._handles.clear()

    def __repr__(self) -> str:
        return f"AgentRegistry(agents={list(self._handles.keys())})"
