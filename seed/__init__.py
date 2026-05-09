"""
Seed — unified meta-package.

Install this package and it brings in:
  - seed-engine   → core engine, session, memory, execution, safety
  - seed-services → browser, safety, webhook, bridge services
  - seed-tools    → tool implementations

Re-exports key symbols for backward compatibility with ``from seed import ...``.
"""

# ── seed-engine ─────────────────────────────────────────────────
from seed_engine import __version__ as engine_version

from seed_engine.agent_runtime import (
    registry_to_openai_tools,
    format_tool_segment_summary,
)
from seed_engine.config_plane import (
    build_system_prompt,
    ensure_default_config_files,
    project_root,
    config_dir,
)
from seed_engine.engine import EngineConfig, QueryEngine
from seed_engine.execution import ToolExecution, ExecutionContext, ToolRegistry
from seed_engine.llm_exec import LLMAPIExecutor, LLMError
from seed_engine.llm_sess import (
    llm_sessions_dir,
    list_stored_llm_sessions_meta,
    list_stored_llm_session_ids,
    load_chat_session_from_disk,
    load_or_create_chat_session,
    persist_chat_session,
)
from seed_engine.mem_sys import MemorySystem, MemorySystemError
from seed_engine.safety import (
    sanitize_assistant_output,
    sanitize_tool_output,
    check_bash_command,
    enforce_bash_timeout,
)
from seed_engine.sess_store import SessionManager, SessionStore, SessionNotFoundError
from seed_engine.turn_loop import TurnLoopEngine, TurnLoopConfig, AutonomousAgent

# ── seed-services ────────────────────────────────────────────────
from seed_services import (
    BROWSER,
    ensure_browser_running,
    BrowserError,
    check_bash_command as services_check_bash,
    enforce_bash_timeout as services_enforce_timeout,
    sanitize_assistant_output as services_sanitize_output,
    sanitize_tool_output as services_sanitize_tool,
    compute_webhook_dedup_key,
    dedup_enabled,
    reset_webhook_dedup_cache,
    try_acquire,
    try_acquire_report,
    to_openai_chat_payload,
    llm_generate_display_title,
    maybe_llm_refresh_session_title,
    transcript_jsonl_path,
    append_transcript_entries,
)

# ── seed-tools ───────────────────────────────────────────────────
from seed_tools import (
    ToolRegistry as SeedToolRegistry,
    ToolExecutor,
    ToolExecutionError,
    setup_builtin_tools,
)

__all__ = [
    # engine
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
    "llm_sessions_dir",
    "list_stored_llm_sessions_meta",
    "list_stored_llm_session_ids",
    "load_chat_session_from_disk",
    "load_or_create_chat_session",
    "persist_chat_session",
    # services
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
    "transcript_jsonl_path",
    "append_transcript_entries",
    # tools
    "SeedToolRegistry",
    "ToolExecutor",
    "ToolExecutionError",
    "setup_builtin_tools",
]

__version__ = "1.0.0"
