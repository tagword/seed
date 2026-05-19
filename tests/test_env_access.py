import os

import pytest

from seed.core import env_access as ea


def test_pick_nonempty_seed_only(monkeypatch):
    monkeypatch.delenv("SEED_LLM_MODEL", raising=False)
    monkeypatch.setenv("CODEAGENT_LLM_MODEL", "legacy")
    assert ea.pick_nonempty(*ea.LLM_MODEL) == ""


def test_pick_default_uses_seed(monkeypatch):
    monkeypatch.setenv("SEED_AGENT_ID", "kernel-agent")
    assert ea.pick_default("default", *ea.AGENT_ID) == "kernel-agent"


def test_env_truthy(monkeypatch):
    monkeypatch.setenv("SEED_CRON", "1")
    assert ea.env_truthy(*ea.CRON)


def test_pick_int(monkeypatch):
    monkeypatch.setenv("SEED_CHAT_USER_ROUNDS", "8")
    assert ea.pick_int(12, *ea.CHAT_USER_ROUNDS) == 8
