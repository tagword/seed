"""Integrations: safety bridge, webhooks, messaging — depend on ``seed.core``."""

from seed.integrations.safety import (
    check_bash_command,
    enforce_bash_timeout,
    sanitize_assistant_output,
    sanitize_tool_output,
)
from seed.integrations.webhook_dedup import (
    compute_webhook_dedup_key,
    dedup_enabled,
    reset_webhook_dedup_cache,
    try_acquire,
    try_acquire_report,
)

# HTTP / UI helpers (depend on seed.core for sessions and LLM types)
from seed.integrations.message_api import to_openai_chat_payload
from seed.integrations.session_title import llm_generate_display_title, maybe_llm_refresh_session_title
__all__ = (
    "check_bash_command",
    "enforce_bash_timeout",
    "sanitize_assistant_output",
    "sanitize_tool_output",
    "compute_webhook_dedup_key",
    "dedup_enabled",
    "reset_webhook_dedup_cache",
    "try_acquire",
    "try_acquire_report",
    "to_openai_chat_payload",
    "llm_generate_display_title",
    "maybe_llm_refresh_session_title",
)
