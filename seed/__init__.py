"""
Seed — kernel + integrations (``pip install seed``).

- ``seed.core`` — orchestrator, sessions, memory, LLM, paths, tool runtime contracts, etc.
- ``seed.integrations`` — browser, safety bridge, webhooks, cron, env file, …

Builtin tool **implementations** live in the separate ``seed-tools`` distribution (``import seed_tools``).
``ToolRegistry`` / ``ToolExecutor`` **contracts** are always available from ``seed.core.tool_runtime``
(and re-exported below as ``SeedToolRegistry`` / ``ToolExecutor``).
"""

from seed.core import __version__ as engine_version
from seed.core.agent_runtime import (
    format_tool_segment_summary,
    registry_to_openai_tools,
)
from seed.core.config_plane import (
    build_system_prompt,
    config_dir,
    ensure_default_config_files,
    project_root,
)
from seed.core.engine import EngineConfig, QueryEngine
from seed.core.execution import ExecutionContext, ToolExecution, ToolRegistry
from seed.core.llm_exec import LLMAPIExecutor, LLMError
from seed.core.llm_sess import (
    agent_sessions_dir,
    list_stored_session_ids,
    list_stored_sessions_meta,
    load_chat_session_from_disk,
    load_or_create_chat_session,
    load_session_messages,
    migrate_legacy_agent_sessions,
    persist_chat_session,
    save_session_messages,
)
from seed.core.mem_sys import MemorySystem, MemorySystemError
from seed.integrations.safety import (
    check_bash_command,
    enforce_bash_timeout,
    sanitize_assistant_output,
    sanitize_tool_output,
)
from seed.core.sess_store import SessionManager, SessionNotFoundError, SessionStore
from seed.core.tool_runtime import ToolExecutionError, ToolExecutor, ToolRegistry as SeedToolRegistry
from seed.core.turn_loop import AutonomousAgent, TurnLoopConfig, TurnLoopEngine
from seed.integrations import (
    BROWSER,
    BrowserError,
    compute_webhook_dedup_key,
    dedup_enabled,
    ensure_browser_running,
    llm_generate_display_title,
    maybe_llm_refresh_session_title,
    reset_webhook_dedup_cache,
    to_openai_chat_payload,
    try_acquire,
    try_acquire_report,
    check_bash_command as services_check_bash,
    enforce_bash_timeout as services_enforce_timeout,
    sanitize_assistant_output as services_sanitize_output,
    sanitize_tool_output as services_sanitize_tool,
)

try:
    from seed_tools import setup_builtin_tools
except ImportError:

    def setup_builtin_tools(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError(
            "Builtin tools require the seed-tools package: pip install seed-tools",
        ) from None


__all__ = [
    "engine_version",
    "EngineConfig",
    "QueryEngine",
    "TurnLoopEngine",
    "TurnLoopConfig",
    "AutonomousAgent",
    "SessionManager",
    "SessionStore",
    "SessionNotFoundError",
    "MemorySystem",
    "MemorySystemError",
    "LLMAPIExecutor",
    "LLMError",
    "ToolExecution",
    "ExecutionContext",
    "ToolRegistry",
    "build_system_prompt",
    "ensure_default_config_files",
    "project_root",
    "config_dir",
    "registry_to_openai_tools",
    "format_tool_segment_summary",
    "sanitize_assistant_output",
    "sanitize_tool_output",
    "check_bash_command",
    "enforce_bash_timeout",
    "agent_sessions_dir",
    "list_stored_sessions_meta",
    "list_stored_session_ids",
    "load_chat_session_from_disk",
    "load_or_create_chat_session",
    "load_session_messages",
    "migrate_legacy_agent_sessions",
    "persist_chat_session",
    "save_session_messages",
    "BROWSER",
    "ensure_browser_running",
    "BrowserError",
    "compute_webhook_dedup_key",
    "dedup_enabled",
    "reset_webhook_dedup_cache",
    "try_acquire",
    "try_acquire_report",
    "to_openai_chat_payload",
    "llm_generate_display_title",
    "maybe_llm_refresh_session_title",
    "SeedToolRegistry",
    "ToolExecutor",
    "ToolExecutionError",
    "setup_builtin_tools",
]

__version__ = "1.0.4"
