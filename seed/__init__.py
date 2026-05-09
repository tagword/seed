"""
Seed — unified meta-package.

Install this package and it brings in:
  - seed-engine   → core engine, session, memory, execution, safety
  - seed-services → browser, safety, webhook, bridge services
  - seed-tools    → tool implementations

Re-exports key symbols for backward compatibility with ``from seed import ...``.
"""

# ── seed-engine re-exports ──────────────────────────────────────
from seed_engine import (
    AgentRuntime,
    SessionManager,
    TurnLoop,
    SafetyChecker,
    MemorySystem,
    ConfigPlane,
    ProjectRegistry,
    LLMExecutor,
)
from seed_engine import __version__ as engine_version

# ── seed-services re-exports ────────────────────────────────────
from seed_services import (
    BrowserManager,
    WebHookDedup,
    BridgeService,
)

# ── seed-tools re-exports ───────────────────────────────────────
from seed_tools import (
    ToolRegistry,
    ToolBase,
    register_tool,
    get_tool,
    list_tools,
)

__all__ = [
    # engine
    "AgentRuntime",
    "SessionManager",
    "TurnLoop",
    "SafetyChecker",
    "MemorySystem",
    "ConfigPlane",
    "ProjectRegistry",
    "LLMExecutor",
    # services
    "BrowserManager",
    "WebHookDedup",
    "BridgeService",
    # tools
    "ToolRegistry",
    "ToolBase",
    "register_tool",
    "get_tool",
    "list_tools",
    # version
    "engine_version",
]

__version__ = "1.0.0"
