"""Inbound webhook HMAC verification (shared by taskagent and other hosts)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

logger = logging.getLogger(__name__)


def webhook_secret() -> str:
    return (
        os.environ.get("TASKAGENT_WEBHOOK_SECRET", "").strip()
        or os.environ.get("CODEAGENT_WEBHOOK_SECRET", "").strip()
        or os.environ.get("SEED_WEBHOOK_SECRET", "").strip()
    )


def verify_webhook_signature(body: bytes, sig: str | None) -> bool:
    secret = webhook_secret()
    if not secret:
        logger.warning("webhook secret unset — rejecting webhook request")
        return False
    if not sig or not sig.startswith("sha256="):
        logger.warning("webhook missing or malformed signature header")
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig[7:], expected):
        logger.warning("webhook signature mismatch")
        return False
    return True
