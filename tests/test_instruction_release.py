"""Tests for instruction release bundles."""

from __future__ import annotations

from pathlib import Path

import pytest

from seed.integrations.instruction_release import (
    list_releases,
    publish_release,
    read_section_text,
    resolve_bootstrap,
)


def test_publish_and_bootstrap(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    md = """# Deploy skill

## Prerequisites

Check kubectl context.

## Deploy steps

Run helm upgrade.
"""
    publish_release("deploy", "v1.0", md, base=tmp_path)
    assert "deploy@v1.0" in list_releases(tmp_path)
    boot = resolve_bootstrap("deploy@v1.0", mode="bootstrap", base=tmp_path)
    assert "Prerequisites" in boot
    assert "deploy" in boot.lower()
    body = read_section_text("deploy@v1.0", section="deploy-steps", base=tmp_path)
    assert "helm" in body.lower()


def test_publish_duplicate_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    publish_release("x", "v1", "hello", base=tmp_path)
    with pytest.raises(FileExistsError):
        publish_release("x", "v1", "other", base=tmp_path)
