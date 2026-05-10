"""
Memory System for CodeAgent — manages identity, soul, experiences, and capabilities.

The memory directory layout under a project or agent root:

    <base>/
      config/
        identity.md         agent self-concept
        soul.md             values / ethics
      memory/
        learning/
        skills/
        patterns/
        experiences/        episodic markdown files
      capabilities.md       tool/skill proficiency tracking
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class MemorySystemError(Exception):
    """Exception raised for memory system errors."""

    def __init__(self, operation: str, path: str, error: str):
        self.operation = operation
        self.path = path
        self.error = error
        super().__init__(f"Memory {operation} error at '{path}': {error}")


class MemorySystem:
    """
    Manages agent memory including identity, values, experiences, and capabilities.
    Loads automatically when Agent starts with --session flag.
    """

    _LEVEL_ORDER = [
        "novice", "learning", "skilled", "competent", "proficient", "expert", "master"
    ]

    def __init__(self, base_path: Optional[Union[str, Path]] = None, auto_load: bool = False):
        """
        Initialize the memory system.

        Args:
            base_path: Base directory for memory storage.
                       If path ends with 'memory', use it directly as memory_path.
                       Otherwise, base_path/config and base_path/memory.
            auto_load: If True, automatically load memory components on init.
        """
        if base_path is None:
            base_path = Path(__file__).resolve().parent.parent
        elif isinstance(base_path, str):
            base_path = Path(base_path)

        if not base_path.is_absolute():
            base_path = (Path(__file__).resolve().parent.parent / base_path).resolve()

        if base_path.name == "memory":
            self.memory_path = base_path
            self.base_path: Path = base_path.parent if base_path.parent else base_path
        else:
            self.base_path = base_path
            self.memory_path = self.base_path / "memory"

        # Fix double "memory/memory" nesting
        if self.memory_path.name == "memory" and self.memory_path.parent.name == "memory":
            self.memory_path = self.base_path / "memory"

        self.config_path: Path = self.base_path / "config"
        self.capabilities_path: Path = self.base_path / "capabilities.md"

        # Ensure directories
        self._ensure_directories()

        # In-memory caches
        self.identity_cache: Optional[Dict[str, str]] = None
        self.soul_cache: Optional[Dict[str, str]] = None
        self.capabilities: Dict[str, Dict[str, Any]] = {}
        self._experiences: List[Dict[str, Any]] = []

        if auto_load:
            try:
                self.load_all()
            except Exception:
                logger.warning("Auto-load of MemorySystem failed during initialization.")

    # ------------------------------------------------------------------
    # Directory management
    # ------------------------------------------------------------------

    def _ensure_directories(self) -> None:
        """Create required directories if they don't exist."""
        try:
            self.config_path.mkdir(parents=True, exist_ok=True)
            self.memory_path.mkdir(parents=True, exist_ok=True)
            for sub in ("learning", "skills", "patterns", "experiences"):
                (self.memory_path / sub).mkdir(exist_ok=True)
        except OSError as e:
            raise MemorySystemError("create", str(self.memory_path), str(e))

    # ------------------------------------------------------------------
    # Identity management (config/identity.md)
    # ------------------------------------------------------------------

    def load_identity(self) -> Dict[str, str]:
        """Load agent identity from config/identity.md."""
        identity_file = self.config_path / "identity.md"
        if not identity_file.exists():
            logger.warning("Identity file not found: %s", identity_file)
            return self._create_default_identity(identity_file)
        try:
            content = identity_file.read_text(encoding="utf-8")
            identity_dict = self._parse_markdown_to_dict(content)
            self.identity_cache = identity_dict
            return identity_dict
        except OSError as e:
            raise MemorySystemError("load", str(identity_file), str(e))

    def save_identity(self, identity_data: Dict[str, str]) -> None:
        """Save agent identity to config/identity.md."""
        identity_file = self.config_path / "identity.md"
        try:
            content = self._dict_to_markdown(identity_data)
            identity_file.write_text(content, encoding="utf-8")
            self.identity_cache = identity_data
        except OSError as e:
            raise MemorySystemError("save", str(identity_file), str(e))

    def _create_default_identity(self, file_path: Path) -> Dict[str, str]:
        """Create default identity file if it doesn't exist."""
        default = {
            "Self-Concept": "I am an autonomous AI agent designed to learn, grow, and adapt through experience.",
            "Capabilities": "- Tool usage and discovery\n- Task execution and planning\n- Self-reflection and memory management",
            "Learning Goals": "- Improve tool composition skills\n- Develop strategic decision-making\n- Build efficiency heuristics",
            "Current State": "- Session: Active\n- Memory: Persistent (file-based)\n- Tools Available: echo, calculate, counter, whoami",
        }
        try:
            file_path.write_text(self._dict_to_markdown(default), encoding="utf-8")
        except OSError as e:
            raise MemorySystemError("create", str(file_path), str(e))
        return default

    # ------------------------------------------------------------------
    # Soul / values management (config/soul.md)
    # ------------------------------------------------------------------

    def load_soul(self) -> Dict[str, str]:
        """Load agent values and ethics from config/soul.md."""
        soul_file = self.config_path / "soul.md"
        if not soul_file.exists():
            logger.warning("Soul file not found: %s", soul_file)
            return self._create_default_soul(soul_file)
        try:
            content = soul_file.read_text(encoding="utf-8")
            soul_dict = self._parse_markdown_to_dict(content)
            self.soul_cache = soul_dict
            return soul_dict
        except OSError as e:
            raise MemorySystemError("load", str(soul_file), str(e))

    def _create_default_soul(self, file_path: Path) -> Dict[str, str]:
        """Create default soul/values file."""
        default = {
            "Core Values": "- Reliability: Deliver what you promise\n- Growth: Continuously learn and adapt\n- Honesty: Be truthful about capabilities and limitations",
            "Behavioral Guidelines": "- Always verify before executing destructive operations\n- Seek clarification when instructions are ambiguous\n- Log and reflect on failures for improvement",
        }
        try:
            file_path.write_text(self._dict_to_markdown(default), encoding="utf-8")
        except OSError as e:
            raise MemorySystemError("create", str(file_path), str(e))
        return default

    def save_soul(self, soul_data: Dict[str, str]) -> None:
        """Save agent soul/values to config/soul.md."""
        soul_file = self.config_path / "soul.md"
        try:
            soul_file.write_text(self._dict_to_markdown(soul_data), encoding="utf-8")
            self.soul_cache = soul_data
        except OSError as e:
            raise MemorySystemError("save", str(soul_file), str(e))

    # ------------------------------------------------------------------
    # Capability tracking (capabilities.md)
    # ------------------------------------------------------------------

    def track_capability(self, capability: str, level: str) -> None:
        """Track agent capability level in capabilities.md."""
        capabilities = self.load_capabilities()
        if capability in capabilities:
            capabilities[capability]["total_usage"] = capabilities[capability].get("total_usage", 0) + 1
        else:
            capabilities[capability] = {"level": level, "total_usage": 1}
        current_usage = capabilities[capability]["total_usage"]
        auto_level = self._get_auto_upgrade_level(current_usage)
        provided_rank = self._get_level_rank(level)
        auto_rank = self._get_level_rank(auto_level)
        if auto_rank > provided_rank:
            capabilities[capability]["level"] = auto_level
        else:
            capabilities[capability]["level"] = level
        self._save_capabilities(capabilities)
        self.capabilities = capabilities

    def load_capabilities(self) -> Dict[str, Dict[str, Any]]:
        """Load capabilities from capabilities.md."""
        if not self.capabilities_path.exists():
            return {}
        try:
            content = self.capabilities_path.read_text(encoding="utf-8")
            caps: Dict[str, Dict[str, Any]] = {}
            current_cap: Optional[str] = None
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("## "):
                    current_cap = stripped[3:].strip()
                    caps[current_cap] = {"level": "novice", "total_usage": 0}
                elif current_cap and ":" in stripped:
                    k, _, v = stripped.partition(":")
                    k = k.strip().lower()
                    v = v.strip()
                    if k == "level":
                        caps[current_cap]["level"] = v
                    elif k == "total_usage":
                        try:
                            caps[current_cap]["total_usage"] = int(v)
                        except (ValueError, TypeError):
                            caps[current_cap]["total_usage"] = 0
            return caps
        except OSError as e:
            raise MemorySystemError("load", str(self.capabilities_path), str(e))

    def _save_capabilities(self, caps: Dict[str, Dict[str, Any]]) -> None:
        """Save capabilities to capabilities.md."""
        lines: List[str] = ["# Agent Capabilities\n"]
        for cap, data in sorted(caps.items()):
            lines.append(f"\n## {cap}\n")
            lines.append(f"- Level: {data.get('level', 'novice')}\n")
            lines.append(f"- Total Usage: {data.get('total_usage', 0)}\n")
        try:
            self.capabilities_path.write_text("".join(lines), encoding="utf-8")
        except OSError as e:
            raise MemorySystemError("save", str(self.capabilities_path), str(e))

    # ------------------------------------------------------------------
    # Experience management (memory/experiences/*.md)
    # ------------------------------------------------------------------

    def log_experience(
        self,
        task_id: str,
        outcome: str,
        tools_used: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        """
        Log a task experience to memory/experiences/<task_id>.md.
        Returns the file path.
        """
        exp_dir = self.memory_path / "experiences"
        exp_dir.mkdir(parents=True, exist_ok=True)
        lines: List[str] = [f"# Experience: {task_id}\n"]
        if session_id:
            lines.append(f"\n## Session\n{session_id}\n")
        if project_id:
            lines.append(f"\n## Project\n{project_id}\n")
        if tools_used:
            lines.append(f"\n## Tools Used\n{', '.join(tools_used)}\n")
        lines.append(f"\n## Outcome\n{outcome}\n")
        if ttl_seconds is not None:
            lines.append(f"\n## TTL\n{ttl_seconds}\n")
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", task_id)
        file_path = exp_dir / f"{safe_name}.md"
        file_path.write_text("".join(lines), encoding="utf-8")
        return str(file_path)

    def load_all(self) -> None:
        """Load all memory components (identity, soul, capabilities)."""
        try:
            self.load_identity()
        except Exception as e:
            logger.warning("Failed to load identity: %s", e)
        try:
            self.load_soul()
        except Exception as e:
            logger.warning("Failed to load soul: %s", e)
        try:
            self.capabilities = self.load_capabilities()
        except Exception as e:
            logger.warning("Failed to load capabilities: %s", e)

    def get_performance_summary(self) -> Dict[str, Any]:
        """Aggregate a performance summary from experiences and capabilities."""
        exp_dir = self.memory_path / "experiences"
        tool_counts: Dict[str, int] = {}
        successful = 0
        failed = 0
        tool_success_rates: Dict[str, Any] = {}

        if exp_dir.is_dir():
            for f in sorted(exp_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                # Extract tools used
                m = re.search(r"## Tools Used\n(.+)", content)
                if m:
                    for t in m.group(1).split(","):
                        tn = t.strip()
                        if tn:
                            tool_counts[tn] = tool_counts.get(tn, 0) + 1
                # Determine success/failure from outcome section
                om = re.search(r"## Outcome\n(.+?)(?:\n##|\Z)", content, re.DOTALL)
                if om:
                    outcome_text = om.group(1).strip().lower()
                    if any(w in outcome_text for w in ("completed", "success")):
                        successful += 1
                    else:
                        failed += 1
                else:
                    if "success: true" in content.lower() or "status: completed" in content.lower():
                        successful += 1
                    else:
                        failed += 1

        total = successful + failed
        success_rate = (successful / total * 100) if total > 0 else 0

        capabilities = self.load_capabilities()
        for cap, data in capabilities.items():
            lvl = data.get("level")
            if lvl in ("competent", "proficient", "master"):
                tool_success_rates[cap] = lvl

        suggestions = []
        if success_rate < 50:
            suggestions.append("High failure rate detected. Review recent failed tasks and consider simplifying task approach.")
        for tool_name, level in tool_success_rates.items():
            if level == "novice":
                suggestions.append(f"Consider practicing '{tool_name}' tool to improve proficiency from {level} to competent.")

        return {
            "total_experiences": total,
            "successful": successful,
            "failed": failed,
            "success_rate": round(success_rate, 1),
            "tool_usage_count": tool_counts,
            "improvement_areas": suggestions,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_markdown_to_dict(self, content: str) -> Dict[str, str]:
        """Parse markdown sections (## heading) into a dict."""
        result: Dict[str, str] = {}
        current_section: Optional[str] = None
        current_lines: List[str] = []
        for line in content.splitlines():
            if line.startswith("## "):
                if current_section:
                    result[current_section] = "\n".join(current_lines).strip()
                current_section = line[3:].strip()
                current_lines = []
            elif current_section is not None:
                current_lines.append(line)
        if current_section:
            result[current_section] = "\n".join(current_lines).strip()
        return result

    @staticmethod
    def _dict_to_markdown(data: Dict[str, str]) -> str:
        """Convert a dict to markdown sections."""
        lines: List[str] = []
        for key, value in data.items():
            lines.append(f"## {key}\n")
            lines.append(f"{value}\n")
        return "\n".join(lines)

    def _get_level_rank(self, level: str) -> int:
        try:
            return self._LEVEL_ORDER.index(level.lower())
        except (ValueError, AttributeError):
            return 0

    def _get_auto_upgrade_level(self, total_usage: int) -> str:
        if total_usage >= 10:
            return "master"
        elif total_usage >= 7:
            return "expert"
        elif total_usage >= 5:
            return "proficient"
        elif total_usage >= 3:
            return "competent"
        elif total_usage >= 2:
            return "skilled"
        else:
            return "learning"
