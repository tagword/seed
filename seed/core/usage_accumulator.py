"""Accumulate LLM usage across multiple tool-loop rounds (contextvar-scoped)."""

from __future__ import annotations

import contextvars
import uuid
from typing import Any, Dict, Optional

_USAGE_CTX: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "seed_usage_acc_token",
    default=None,
)
_REGISTRY: Dict[str, Dict[str, Any]] = {}

USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)


def begin_usage_accumulation() -> str:
    """Start accumulating usage; returns opaque token for :func:`end_usage_accumulation`."""
    token = str(uuid.uuid4())
    _REGISTRY[token] = {}
    _USAGE_CTX.set(token)
    return token


def end_usage_accumulation(token: Optional[str] = None) -> Dict[str, int]:
    """Stop accumulation and return summed ``usage_summary`` (may be empty)."""
    tok = token or _USAGE_CTX.get()
    _USAGE_CTX.set(None)
    if not tok:
        return {}
    bucket = _REGISTRY.pop(tok, {})
    raw = bucket.get("usage_summary") if isinstance(bucket, dict) else {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, int] = {}
    for k in USAGE_KEYS:
        v = raw.get(k, 0)
        if isinstance(v, (int, float)):
            out[k] = int(v)
    return out


def reset_usage_accumulation(token: Optional[str] = None) -> None:
    """Clear accumulator without returning totals (e.g. on error)."""
    tok = token or _USAGE_CTX.get()
    _USAGE_CTX.set(None)
    if tok:
        _REGISTRY.pop(tok, None)


def record_round_usage(meta: Optional[Dict[str, Any]]) -> None:
    """Add one LLM round's ``meta['usage']`` into the active accumulator."""
    tok = _USAGE_CTX.get()
    if not tok or tok not in _REGISTRY:
        return
    if not isinstance(meta, dict):
        return
    round_usage = meta.get("usage")
    if not isinstance(round_usage, dict) or not round_usage:
        return
    us = _REGISTRY[tok].setdefault("usage_summary", {})
    for k in USAGE_KEYS:
        v = round_usage.get(k, 0)
        if isinstance(v, (int, float)):
            us[k] = int(us.get(k, 0)) + int(v)
