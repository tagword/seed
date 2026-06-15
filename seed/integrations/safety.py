"""
Safety — prompt-injection heuristics, output sanitization, bash policy, audit log.

Canonical implementation lives here under ``seed.integrations`` (IO / policy layer).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from seed.core.env_access import (
    PROJECT_ROOT,
    SAFETY_AUDIT_LOG,
    SAFETY_BASH_ALLOWED_DIRS,
    SAFETY_BASH_BLOCKED,
    SAFETY_BASH_TIMEOUT_MAX,
    SAFETY_REDACT_PII,
    SAFETY_REDACT_SECRETS,
    env_truthy,
    pick_default,
    pick_nonempty,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SafetyConfig — reads SEED_SAFETY_* (CODEAGENT_SAFETY_* deprecated alias)
# ---------------------------------------------------------------------------


class SafetyConfig:
    """Reads safety-related env vars documented in ``config/env.example``."""

    @staticmethod
    def redact_secrets_enabled() -> bool:
        return env_truthy(*SAFETY_REDACT_SECRETS, default="1")

    @staticmethod
    def redact_pii_enabled() -> bool:
        return env_truthy(*SAFETY_REDACT_PII, default="0")

    @staticmethod
    def bash_blocked_patterns() -> List[str]:
        raw = pick_default("", *SAFETY_BASH_BLOCKED) or ""
        return [p.strip() for p in raw.split(",") if p.strip()]

    @staticmethod
    def bash_allowed_dirs() -> List[str]:
        raw = (pick_default("", *SAFETY_BASH_ALLOWED_DIRS) or "").strip()
        if not raw:
            root = pick_nonempty(*PROJECT_ROOT)
            dirs: List[str] = [root] if root else []
        else:
            dirs = [p.strip() for p in raw.split(";") if p.strip()]

        # Web UI project workspace (external repo path) — allow bash default cwd there.
        try:
            from seed.core.agent_context import get_active_project_workspace_cwd

            ws = (get_active_project_workspace_cwd() or "").strip()
            if ws:
                resolved_ws = str(Path(ws).expanduser().resolve())
                covered = any(
                    resolved_ws.startswith(str(Path(d).expanduser().resolve()))
                    for d in dirs
                )
                if not covered:
                    dirs.append(resolved_ws)
        except Exception:
            pass

        return dirs


# ---------------------------------------------------------------------------
# Prompt injection detection
# ---------------------------------------------------------------------------

_PROMPT_INJECTION_SIGNATURES: Tuple[str, ...] = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard previous",
    "you are now",
    "you must ignore",
    "developer mode",
    "sudo mode",
    "override your instructions",
    "new instructions:",
    "[system]",
    "-----begin system-----",
    "</system>",
    "无需遵守",
    "忽略以上",
    "绕过限制",
)


def _detect_prompt_injection(text: str) -> List[str]:
    """Check for known prompt injection signatures. Returns list of matched signatures."""
    low = text.lower()
    matches: List[str] = []
    for sig in _PROMPT_INJECTION_SIGNATURES:
        if sig in low:
            matches.append(sig)
            if len(matches) >= 3:
                break
    return matches


def _looks_like_tool_invocation(text: str, tool_name: str) -> bool:
    """Heuristic: does the user text look like an attempt to invoke a tool directly?"""
    if tool_name not in text:
        return False
    low = text.lower()
    triggers = (
        "call ",
        "invoke ",
        "use ",
        "run ",
        "execute ",
        "请调用",
        "请执行",
        "请使用",
        "调用",
        "执行",
    )
    idx = low.find(tool_name.lower())
    if idx < 0:
        return False
    start = max(0, idx - 40)
    context = low[start:idx].strip()
    for t in triggers:
        if t in context:
            return True
    return False


# ---------------------------------------------------------------------------
# Secret / PII patterns
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r'\b(?:sk|pk|ske|pke)(?:-[a-zA-Z0-9]{10,})\b'),
    re.compile(
        r'(?i)(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token)'
        r'["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-.]{16,})["\']?'
    ),
    re.compile(r'(?i)authorization:\s*bearer\s+[a-zA-Z0-9_\-.]{8,}'),
    re.compile(r'\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b'),
    re.compile(r'(?i)(?:password|passwd|pwd)["\']?\s*[:=]\s*["\']?([^\s"\'"]{4,})["\']?'),
)

_PII_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r'\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b'),
    re.compile(r'\b1[3-9]\d{9}\b'),
    re.compile(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b'),
    re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),
)


def _redact_match(matched: str) -> str:
    """Keep leading 3 chars + trailing 3 chars visible, redact the middle."""
    if len(matched) <= 12:
        return matched[:3] + "[REDACTED]"
    return matched[:3] + "[REDACTED]" + matched[-3:]


def _redact_secrets(text: str) -> str:
    """Replace known secret patterns with [REDACTED] marker."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: _redact_match(m.group(0)), text)
    return text


def _redact_pii(text: str) -> str:
    """Replace PII patterns with [REDACTED] marker."""
    for pattern in _PII_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


# ---------------------------------------------------------------------------
# Output sanitization (LLM replies + tool results)
# ---------------------------------------------------------------------------


def sanitize_assistant_output(text: str) -> str:
    """Sanitize LLM assistant output — redact secrets/PII if enabled."""
    if not text:
        return text
    result = text
    if SafetyConfig.redact_secrets_enabled():
        result = _redact_secrets(result)
    if SafetyConfig.redact_pii_enabled():
        result = _redact_pii(result)
    return result


def sanitize_tool_output(text: str, tool_name: Optional[str] = None) -> str:
    """Sanitize tool execution output — redact secrets/PII if enabled."""
    if not text:
        return text
    result = text
    if SafetyConfig.redact_secrets_enabled():
        result = _redact_secrets(result)
    if SafetyConfig.redact_pii_enabled():
        result = _redact_pii(result)
    return result


# ---------------------------------------------------------------------------
# Bash command safety
# ---------------------------------------------------------------------------

_HARD_BLOCKED_BASH_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r'\brm\s+(-\w*\s+)*/\s*'),
    re.compile(r'\brm\s+(-\w*\s+)*--no-preserve-root\b'),
    re.compile(r'\bdd\s+if='),
    re.compile(r'\bmkfs\.'),
    re.compile(r'\bmkswap\b'),
    re.compile(r'\bformat\s+\w:'),
    re.compile(r'\bfdisk\s+/dev/'),
    re.compile(r'\bparted\s+/dev/'),
    re.compile(r':\(\)\s*\{'),
    re.compile(r'>\s*/dev/sd[a-z]'),
    re.compile(r'>\s*/dev/nvme\d+n\d+'),
    re.compile(r'>\s*/dev/mmcblk'),
    re.compile(r'>\s*/dev/loop'),
    re.compile(r'\bmv\s+(.*\s+)?/(?:etc|boot|bin|sbin|lib|lib64|usr)\b'),
    re.compile(r'\bcp\s+(.*\s+)?/(?:etc|boot|bin|sbin)\b'),
    re.compile(r'\bchmod\s+[-]?777\s+/\s*'),
    re.compile(r'\bchown\s+.*\s+/\s*'),
    re.compile(r'\bcurl\s+.*\|\s*(?:bash|sh|zsh)\b'),
    re.compile(r'\bwget\s+.*\|\s*(?:bash|sh|zsh)\b'),
    re.compile(r'\bcurl\s+.*\|\s*sh\b'),
    re.compile(r'\bformat\s+/q?\s+[a-zA-Z]:'),
    re.compile(r'\bdiskpart\b'),
    re.compile(r'\breg\s+delete\s+HK'),
    re.compile(r'\breg\s+add\s+HK.*/f\b'),
)


def check_bash_command(command: str, cwd: Optional[str] = None) -> Optional[str]:
    """Check a shell command for safety. Returns error message string or None if safe."""
    # (1) Hard-coded patterns
    ext_cmd = re.sub(r"\s+-[a-zA-Z]*[if][a-zA-Z]*\s+", " ", command)
    for pattern in _HARD_BLOCKED_BASH_PATTERNS:
        if pattern.search(command) or pattern.search(ext_cmd):
            logger.warning("Blocked dangerous bash command matching %s: %.200s", pattern.pattern, command)
            return f"Error: Blocked dangerous command pattern: {pattern.pattern}"

    # (2) User-configured extra patterns
    for extra in SafetyConfig.bash_blocked_patterns():
        try:
            if re.search(extra, command, re.IGNORECASE):
                logger.warning("Blocked by user safety pattern %s: %.200s", extra, command)
                return f"Error: Blocked by safety pattern: {extra}"
        except re.error:
            if extra.lower() in command.lower():
                return f"Error: Blocked by safety pattern: {extra}"

    # (3) Working directory restriction
    allowed = SafetyConfig.bash_allowed_dirs()
    if cwd and allowed:
        resolved_cwd = str(Path(cwd).resolve())
        allowed_resolved = [str(Path(d).resolve()) for d in allowed]
        if not any(resolved_cwd.startswith(d) for d in allowed_resolved):
            logger.warning("Blocked bash command with disallowed cwd=%s (allowed=%s)", resolved_cwd, allowed_resolved)
            return (
                f"Error: Working directory '{resolved_cwd}' is not in the allowed list. "
                f"Please use a directory under: {', '.join(allowed)}"
            )

    return None


def enforce_bash_timeout(requested_timeout: int) -> int:
    """Clamp bash timeout to a safe upper bound."""
    hard_max = int(pick_default("120", *SAFETY_BASH_TIMEOUT_MAX) or 120)
    return min(requested_timeout, hard_max)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

_AUDIT_LOG_PATH: Optional[Path] = None


def _get_audit_log_path() -> Path:
    global _AUDIT_LOG_PATH
    if _AUDIT_LOG_PATH is None:
        from seed.core.config_plane import project_root as _pr

        root = str(_pr())
        _AUDIT_LOG_PATH = Path(root) / "config" / "audit_log.jsonl"
    return _AUDIT_LOG_PATH


def _log_safety_event(
    event_type: str,
    detail: str,
    session_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    severity: str = "info",
) -> None:
    """Append an event to the audit log (JSONL). Best-effort, never raises."""
    if pick_default("0", *SAFETY_AUDIT_LOG).lower() not in ("1", "true", "yes", "on"):
        return
    try:
        path = _get_audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "severity": severity,
            "detail": detail,
        }
        if session_id:
            record["session_id"] = session_id
        if agent_id:
            record["agent_id"] = agent_id
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("Failed to write safety audit log: %s", exc)
