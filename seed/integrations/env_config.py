"""Optional repo-local env files under ``<project>/config/``.

Loads ``env`` (kernel ``SEED_*``), then ``seed.env`` (legacy), then ``codeagent.env``
(older legacy). Does not override keys already present in ``os.environ``
(shell/export wins).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from seed.core.config_plane import project_root

ENV_FILENAME = "env"
LEGACY_ENV_FILENAME_SEED = "seed.env"
LEGACY_ENV_FILENAME = "codeagent.env"


def _parse_line(line: str) -> Optional[tuple[str, str]]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "=" not in line:
        return None
    key, _, val = line.partition("=")
    key = key.strip()
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        val = val[1:-1]
    if not key:
        return None
    return key, val


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        pair = _parse_line(line)
        if not pair:
            continue
        k, v = pair
        if k not in os.environ:
            os.environ[k] = v


def apply_seed_env_from_config(base: Optional[Path] = None) -> None:
    """
    Load KEY=VALUE lines from ``config/env`` (new name), falling back to
    ``config/seed.env`` (legacy), then ``config/codeagent.env`` (older legacy).

    Skips any key already present in ``os.environ`` so the shell/export wins.
    """
    root = project_root() if base is None else base.resolve()
    cfg = root / "config"
    primary = cfg / ENV_FILENAME
    old_seed = cfg / LEGACY_ENV_FILENAME_SEED
    leg = cfg / LEGACY_ENV_FILENAME
    if primary.is_file():
        _load_env_file(primary)
        _load_env_file(old_seed)
        _load_env_file(leg)
    elif old_seed.is_file():
        _load_env_file(old_seed)
        _load_env_file(leg)
    elif leg.is_file():
        _load_env_file(leg)


def apply_codeagent_env_from_config(base: Optional[Path] = None) -> None:
    """Deprecated alias for :func:`apply_seed_env_from_config`."""
    apply_seed_env_from_config(base)
