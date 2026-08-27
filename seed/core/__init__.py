"""Kernel: turn loop, LLM, sessions, memory, config, paths, execution protocol."""

__version__ = "1.0.18"
__description__ = "Seed agent engine"

from seed.core.agent_registry import AgentHandle, AgentRegistry

__all__ = ["AgentHandle", "AgentRegistry"]
