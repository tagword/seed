"""Multi-agent filesystem layout under project root (kernel — no product imports).

Environment:
  - ``SEED_PROJECT_ROOT`` / ``CODEAGENT_PROJECT_ROOT`` — data root (see ``config_plane.project_root``).
  - ``SEED_AGENT_ID`` / ``CODEAGENT_AGENT_ID`` — default logical agent id (default ``default``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from seed.core.env_access import AGENT_ID, pick_nonempty


def agent_id_default() -> str:
    raw = pick_nonempty(*AGENT_ID)
    return raw or "default"


def _root(base: Optional[Path]) -> Path:
    if base is not None:
        return Path(base).resolve()
    from seed.core.config_plane import project_root

    return project_root()


def agent_home(agent_id: str, base: Optional[Path] = None) -> Path:
    aid = (agent_id or "").strip() or agent_id_default()
    return _root(base) / "agents" / aid


def ensure_agent_dirs(agent_id: str, base: Optional[Path] = None) -> Path:
    """Create ``agents/<id>/{persona,skills,sessions}``; returns agent home."""
    home = agent_home(agent_id, base)
    for sub in ("persona", "skills", "sessions"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    return home


def agent_persona_dir(agent_id: str, base: Optional[Path] = None) -> Path:
    return ensure_agent_dirs(agent_id, base) / "persona"


def agent_skills_dir(agent_id: str, base: Optional[Path] = None) -> Path:
    return ensure_agent_dirs(agent_id, base) / "skills"


def agent_memory_dir(agent_id: str, base: Optional[Path] = None) -> Path:
    p = agent_home(agent_id, base) / "memory"
    p.mkdir(parents=True, exist_ok=True)
    return p


def agent_persona_memory_path(agent_id: str, base: Optional[Path] = None) -> Path:
    """Optional long-form persona memory file: ``persona/memory.md``."""
    return agent_persona_dir(agent_id, base) / "memory.md"


def agent_projects_registry_dir(agent_id: str, base: Optional[Path] = None) -> Path:
    p = agent_home(agent_id, base) / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


def agent_projects_data_dir(agent_id: str, base: Optional[Path] = None) -> Path:
    p = agent_home(agent_id, base) / "projects-data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def agent_project_data_dir(agent_id: str, project_id: str, base: Optional[Path] = None) -> Path:
    pid = (project_id or "").strip()
    d = agent_projects_data_dir(agent_id, base) / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


def agent_project_data_subdir(
    agent_id: str, project_id: str, sub: str, base: Optional[Path] = None
) -> Path:
    d = agent_project_data_dir(agent_id, project_id, base) / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def agent_daily_dir(agent_id: str, base: Optional[Path] = None) -> Path:
    p = agent_memory_dir(agent_id, base) / "daily"
    p.mkdir(parents=True, exist_ok=True)
    return p


def agent_archive_dir(agent_id: str, base: Optional[Path] = None) -> Path:
    p = agent_memory_dir(agent_id, base) / "archive"
    p.mkdir(parents=True, exist_ok=True)
    return p


def agent_project_daily_dir(agent_id: str, project_id: str, base: Optional[Path] = None) -> Path:
    p = agent_project_data_subdir(agent_id, project_id, "memory", base) / "daily"
    p.mkdir(parents=True, exist_ok=True)
    return p


def agent_project_archive_dir(agent_id: str, project_id: str, base: Optional[Path] = None) -> Path:
    p = agent_project_data_subdir(agent_id, project_id, "memory", base) / "archive"
    p.mkdir(parents=True, exist_ok=True)
    return p
