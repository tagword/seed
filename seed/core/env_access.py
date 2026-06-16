"""
Central environment variable reads for the Seed kernel.

Canonical names use the ``SEED_*`` prefix only. Code Agent maps ``CODEAGENT_*`` to
``SEED_*`` in the product layer (``codeagent.core.seed_bridge``) before calling Seed.
"""

from __future__ import annotations

import os
from typing import Tuple


def pick_nonempty(*keys: str) -> str:
    """First defined env var whose value is non-empty after strip."""
    for k in keys:
        raw = os.environ.get(k)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return ""


def pick_default(default: str, *keys: str) -> str:
    """First key present in ``os.environ`` (value may be empty); else ``default``."""
    for k in keys:
        if k in os.environ:
            return os.environ[k]
    return default


def any_nonempty(*keys: str) -> bool:
    """True if any listed key has a non-empty stripped value."""
    return any((os.environ.get(k) or "").strip() for k in keys)


def env_truthy(*keys: str, default: str = "0") -> bool:
    """True if the first present key among ``keys`` is truthy (1/true/on/yes)."""
    val = pick_default(default, *keys).strip().lower()
    return val in ("1", "true", "yes", "on")


def env_falsy(*keys: str, default: str = "1") -> bool:
    """True if the first present key is falsy (0/false/no/off)."""
    val = pick_default(default, *keys).strip().lower()
    return val in ("0", "false", "no", "off")


def pick_int(default: int, *keys: str) -> int:
    """Parse first present env key as int; fall back to ``default`` on error."""
    raw = pick_default(str(default), *keys).strip()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


# --- Kernel / integration (``SEED_*`` only) ---

PROJECT_ROOT: Tuple[str, ...] = ('SEED_PROJECT_ROOT',)
LLM_API_KEY: Tuple[str, ...] = ('SEED_LLM_API_KEY',)
LLM_AUTH_SCHEME: Tuple[str, ...] = ('SEED_LLM_AUTH_SCHEME',)
LLM_MAX_TOKENS: Tuple[str, ...] = ('SEED_LLM_MAX_TOKENS',)
LLM_NO_TOPK: Tuple[str, ...] = ('SEED_LLM_NO_TOPK',)
LLM_ENABLE_THINKING: Tuple[str, ...] = ('SEED_LLM_ENABLE_THINKING',)
LLM_REASONING_EFFORT: Tuple[str, ...] = ('SEED_LLM_REASONING_EFFORT',)
LLM_SEPARATE_REASONING: Tuple[str, ...] = ('SEED_LLM_SEPARATE_REASONING',)
LLM_CHAT_TEMPLATE_KWARGS: Tuple[str, ...] = ('SEED_LLM_CHAT_TEMPLATE_KWARGS',)
LLM_EXTRA_BODY: Tuple[str, ...] = ('SEED_LLM_EXTRA_BODY',)
LLM_CONTEXT_SIZE: Tuple[str, ...] = ('SEED_LLM_CONTEXT_SIZE',)
LLM_INPUT_TOKEN_EST_DIVISOR: Tuple[str, ...] = ('SEED_LLM_INPUT_TOKEN_EST_DIVISOR',)
LLM_CONTEXT_MARGIN: Tuple[str, ...] = ('SEED_LLM_CONTEXT_MARGIN',)
LLM_BASEURL: Tuple[str, ...] = ('SEED_LLM_BASEURL',)
LLM_MODEL: Tuple[str, ...] = ('SEED_LLM_MODEL',)
LLM_SEND_REASONING_CONTENT: Tuple[str, ...] = ('SEED_LLM_SEND_REASONING_CONTENT',)
ASSISTANT_TOOLCALL_PLACEHOLDER_DISABLE: Tuple[str, ...] = ('SEED_ASSISTANT_TOOLCALL_PLACEHOLDER_DISABLE',)
ASSISTANT_TOOLCALL_PLACEHOLDER: Tuple[str, ...] = ('SEED_ASSISTANT_TOOLCALL_PLACEHOLDER',)
LLM_MAX_REQUEST_BODY_BYTES: Tuple[str, ...] = ('SEED_LLM_MAX_REQUEST_BODY_BYTES',)
TOOL_OUTPUT_MAX_CHARS: Tuple[str, ...] = ('SEED_TOOL_OUTPUT_MAX_CHARS',)
SESSION_DIR: Tuple[str, ...] = ('SEED_SESSION_DIR',)
AGENT_CHAT_SESSIONS_DIR: Tuple[str, ...] = (
    'SEED_AGENT_SESSIONS_DIR',
    'SEED_LLM_SESSIONS_DIR',
)
AGENT_ID: Tuple[str, ...] = ('SEED_AGENT_ID',)
SAFETY_BASH_BLOCKED: Tuple[str, ...] = ('SEED_SAFETY_BASH_BLOCKED',)
SAFETY_BASH_ALLOWED_DIRS: Tuple[str, ...] = ('SEED_SAFETY_BASH_ALLOWED_DIRS',)
SAFETY_BASH_TIMEOUT_MAX: Tuple[str, ...] = ('SEED_SAFETY_BASH_TIMEOUT_MAX',)
SAFETY_AUDIT_LOG: Tuple[str, ...] = ('SEED_SAFETY_AUDIT_LOG',)
LLM_PROJECTION_AUDIT: Tuple[str, ...] = ('SEED_LLM_PROJECTION_AUDIT',)
LLM_PROJECTION_AUDIT_DIR: Tuple[str, ...] = ('SEED_LLM_PROJECTION_AUDIT_DIR',)
SAFETY_REDACT_SECRETS: Tuple[str, ...] = ('SEED_SAFETY_REDACT_SECRETS',)
SAFETY_REDACT_PII: Tuple[str, ...] = ('SEED_SAFETY_REDACT_PII',)
CRON: Tuple[str, ...] = ('SEED_CRON',)
CRON_TZ: Tuple[str, ...] = ('SEED_CRON_TZ',)
CHAT_USER_ROUNDS: Tuple[str, ...] = ('SEED_CHAT_USER_ROUNDS',)
MAX_TOOL_ROUNDS: Tuple[str, ...] = ('SEED_MAX_TOOL_ROUNDS',)
CHAT_AUTO_CONTINUE_ON_LIMIT: Tuple[str, ...] = ('SEED_CHAT_AUTO_CONTINUE_ON_LIMIT',)
CHAT_AUTO_CONTINUE_MAX_SEGMENTS: Tuple[str, ...] = ('SEED_CHAT_AUTO_CONTINUE_MAX_SEGMENTS',)
MEMORY_LOG: Tuple[str, ...] = ('SEED_MEMORY_LOG',)
CRON_EXPERIENCE_SKIP_DUPLICATE: Tuple[str, ...] = ('SEED_CRON_EXPERIENCE_SKIP_DUPLICATE',)
CRON_EXPERIENCE_TTL_SECONDS: Tuple[str, ...] = ('SEED_CRON_EXPERIENCE_TTL_SECONDS',)
MEMORY_INJECT: Tuple[str, ...] = ('SEED_MEMORY_INJECT',)
MEMORY_INJECT_MAX_CHARS: Tuple[str, ...] = ('SEED_MEMORY_INJECT_MAX_CHARS',)
MEMORY_INJECT_SESSION_ONLY: Tuple[str, ...] = ('SEED_MEMORY_INJECT_SESSION_ONLY',)
WEBHOOK_DEDUP: Tuple[str, ...] = ('SEED_WEBHOOK_DEDUP',)
WEBHOOK_DEDUP_TTL_SEC: Tuple[str, ...] = ('SEED_WEBHOOK_DEDUP_TTL_SEC',)
WEBHOOK_DEDUP_MAX_KEYS: Tuple[str, ...] = ('SEED_WEBHOOK_DEDUP_MAX_KEYS',)
MAX_TOOL_ROUNDS_PER_CHUNK: Tuple[str, ...] = ('SEED_MAX_TOOL_ROUNDS_PER_CHUNK',)
CONTEXT_COMPACT: Tuple[str, ...] = ('SEED_CONTEXT_COMPACT',)
CONTEXT_COMPACT_SUMMARIZER_BASEURL: Tuple[str, ...] = ('SEED_CONTEXT_COMPACT_SUMMARIZER_BASEURL',)
CONTEXT_COMPACT_SUMMARIZER_MODEL: Tuple[str, ...] = ('SEED_CONTEXT_COMPACT_SUMMARIZER_MODEL',)
CONTEXT_COMPACT_SUMMARIZER_MAX_TOKENS: Tuple[str, ...] = ('SEED_CONTEXT_COMPACT_SUMMARIZER_MAX_TOKENS',)
CONTEXT_COMPACT_MIN_TOKENS: Tuple[str, ...] = ('SEED_CONTEXT_COMPACT_MIN_TOKENS',)
CONTEXT_COMPACT_KEEP_USER_ROUNDS: Tuple[str, ...] = ('SEED_CONTEXT_COMPACT_KEEP_USER_ROUNDS',)
CONTEXT_SUMMARIZER_MAX_INPUT: Tuple[str, ...] = ('SEED_CONTEXT_SUMMARIZER_MAX_INPUT',)
CONTEXT_COMPACT_WARN_RATIO: Tuple[str, ...] = ('SEED_CONTEXT_COMPACT_WARN_RATIO',)
INLINE_TOOL_PARSE: Tuple[str, ...] = ('SEED_INLINE_TOOL_PARSE',)
SYSTEM_PROMPT: Tuple[str, ...] = ('SEED_SYSTEM_PROMPT',)
PERSONA_MEMORY_MAX_CHARS: Tuple[str, ...] = ('SEED_PERSONA_MEMORY_MAX_CHARS',)
SESSION_TITLE_MAX_CHARS: Tuple[str, ...] = ('SEED_SESSION_TITLE_MAX_CHARS',)
SESSION_TITLE_MAX_TOKENS: Tuple[str, ...] = ('SEED_SESSION_TITLE_MAX_TOKENS',)
SESSION_TITLE_LLM: Tuple[str, ...] = ('SEED_SESSION_TITLE_LLM',)
SESSION_TITLE_MODE: Tuple[str, ...] = ('SEED_SESSION_TITLE_MODE',)
BROWSER_UNHEALTHY_THRESHOLD: Tuple[str, ...] = ('SEED_BROWSER_UNHEALTHY_THRESHOLD',)
BROWSER_CDP_UNHEALTHY_THRESHOLD: Tuple[str, ...] = ('SEED_BROWSER_CDP_UNHEALTHY_THRESHOLD',)
BROWSER_ALLOW_REMOTE_DEBUG: Tuple[str, ...] = ('SEED_BROWSER_ALLOW_REMOTE_DEBUG',)
BROWSER_ALLOW_PRIVATE_URLS: Tuple[str, ...] = ('SEED_BROWSER_ALLOW_PRIVATE_URLS',)

EXEC_BACKEND: Tuple[str, ...] = ('SEED_EXEC_BACKEND',)
EXEC_DOCKER_IMAGE: Tuple[str, ...] = ('SEED_EXEC_DOCKER_IMAGE',)
EXEC_DOCKER_WORKDIR: Tuple[str, ...] = ('SEED_EXEC_DOCKER_WORKDIR',)
EXEC_DOCKER_NETWORK: Tuple[str, ...] = ('SEED_EXEC_DOCKER_NETWORK',)

MCP_ENABLED: Tuple[str, ...] = ('SEED_MCP_ENABLED',)
MCP_CALL_TIMEOUT: Tuple[str, ...] = ('SEED_MCP_CALL_TIMEOUT',)
MCP_INIT_TIMEOUT: Tuple[str, ...] = ('SEED_MCP_INIT_TIMEOUT',)
MCP_REGISTER_TOOLS: Tuple[str, ...] = ('SEED_MCP_REGISTER_TOOLS',)

LSP_ENABLED: Tuple[str, ...] = ('SEED_LSP_ENABLED',)

# --- seed-tools built-in tool env vars ---

VISION_ANALYZE_MAX_IMAGES: Tuple[str, ...] = ('SEED_VISION_ANALYZE_MAX_IMAGES',)
VISION_MAX_TOKENS: Tuple[str, ...] = ('SEED_VISION_MAX_TOKENS',)
VISION_RESULT_MAX_CHARS: Tuple[str, ...] = ('SEED_VISION_RESULT_MAX_CHARS',)
AUDIO_TRANSCRIBE_TIMEOUT_SEC: Tuple[str, ...] = ('SEED_AUDIO_TRANSCRIBE_TIMEOUT_SEC',)
VIDEO_MAX_FRAMES: Tuple[str, ...] = ('SEED_VIDEO_MAX_FRAMES',)
VIDEO_FRAME_INTERVAL_SEC: Tuple[str, ...] = ('SEED_VIDEO_FRAME_INTERVAL_SEC',)
MEDIA_RESULT_MAX_CHARS: Tuple[str, ...] = ('SEED_MEDIA_RESULT_MAX_CHARS',)
IMAGE_GEN_MAX_COUNT: Tuple[str, ...] = ('SEED_IMAGE_GEN_MAX_COUNT',)
IMAGE_GEN_DEFAULT_SIZE: Tuple[str, ...] = ('SEED_IMAGE_GEN_DEFAULT_SIZE',)
BUNDLED_TOOLS: Tuple[str, ...] = ('SEED_BUNDLED_TOOLS',)
PUBLIC_BASE_URL: Tuple[str, ...] = ('SEED_PUBLIC_BASE_URL',)

HOOKS_ENABLED: Tuple[str, ...] = ('SEED_HOOKS_ENABLED',)

ORCHESTRATOR_AUTO_SPLIT: Tuple[str, ...] = ('SEED_ORCHESTRATOR_AUTO_SPLIT',)


def project_root_env_raw() -> str:
    return pick_nonempty(*PROJECT_ROOT)
