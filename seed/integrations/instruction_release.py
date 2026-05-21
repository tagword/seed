"""Versioned instruction bundles (releases/) for fixed tasks."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from seed.core.config_plane import project_root

_BUNDLE_RE = re.compile(r"^([^@]+)@(.+)$")
_SECTION_RE = re.compile(r"^##\s+(.+)$")


@dataclass
class SectionInfo:
    id: str
    title: str
    start_line: int
    end_line: int
    summary: str


def releases_root(base: Optional[Path] = None) -> Path:
    root = (base or project_root()) / "releases"
    root.mkdir(parents=True, exist_ok=True)
    return root


def parse_bundle_ref(ref: str) -> tuple[str, str]:
    m = _BUNDLE_RE.match((ref or "").strip())
    if not m:
        raise ValueError(f"invalid bundle ref (expected name@version): {ref!r}")
    return m.group(1).strip(), m.group(2).strip()


def bundle_dir(name: str, version: str, base: Optional[Path] = None) -> Path:
    return releases_root(base) / f"{name}@{version}"


def _slug_section(title: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "").strip().lower()).strip("-")
    return s or "section"


def _summarize(text: str, max_chars: int = 120) -> str:
    line = (text or "").strip().replace("\n", " ")
    if len(line) <= max_chars:
        return line
    return line[: max_chars - 1].rstrip() + "…"


def _parse_sections(full_text: str) -> list[SectionInfo]:
    lines = full_text.splitlines()
    headers: list[tuple[int, str]] = []
    for i, ln in enumerate(lines, start=1):
        m = _SECTION_RE.match(ln.strip())
        if m:
            headers.append((i, m.group(1).strip()))
    if not headers:
        body = "\n".join(lines).strip()
        return [
            SectionInfo(
                id="full",
                title="Full",
                start_line=1,
                end_line=len(lines) or 1,
                summary=_summarize(body),
            )
        ]
    out: list[SectionInfo] = []
    for idx, (start, title) in enumerate(headers):
        end = (headers[idx + 1][0] - 1) if idx + 1 < len(headers) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        sid = _slug_section(title)
        out.append(
            SectionInfo(
                id=sid,
                title=title,
                start_line=start,
                end_line=end,
                summary=_summarize(body),
            )
        )
    return out


def publish_release(
    name: str,
    version: str,
    content: str,
    *,
    base: Optional[Path] = None,
    bootstrap_max_chars: int = 4000,
) -> Path:
    """Write immutable release directory with manifest + full.md."""
    bdir = bundle_dir(name, version, base)
    if bdir.exists():
        raise FileExistsError(f"release already exists: {bdir}")
    bdir.mkdir(parents=True)
    text = (content or "").strip() + "\n"
    full_path = bdir / "full.md"
    full_path.write_text(text, encoding="utf-8")
    sections = _parse_sections(text)
    sec_dir = bdir / "sections"
    sec_dir.mkdir(exist_ok=True)
    lines = text.splitlines()
    manifest_sections: list[dict[str, Any]] = []
    for sec in sections:
        chunk = "\n".join(lines[sec.start_line - 1 : sec.end_line]).strip() + "\n"
        (sec_dir / f"{sec.id}.md").write_text(chunk, encoding="utf-8")
        manifest_sections.append(
            {
                "id": sec.id,
                "title": sec.title,
                "start_line": sec.start_line,
                "end_line": sec.end_line,
                "summary": sec.summary,
            }
        )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    manifest = {
        "bundle": f"{name}@{version}",
        "name": name,
        "version": version,
        "hash": digest,
        "bootstrap_max_chars": bootstrap_max_chars,
        "sections": manifest_sections,
    }
    (bdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return bdir


def load_manifest(bundle_ref: str, base: Optional[Path] = None) -> dict[str, Any]:
    name, version = parse_bundle_ref(bundle_ref)
    path = bundle_dir(name, version, base) / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"release not found: {bundle_ref}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("invalid manifest")
    return raw


def list_releases(base: Optional[Path] = None) -> list[str]:
    root = releases_root(base)
    out: list[str] = []
    for p in sorted(root.iterdir()):
        if p.is_dir() and (p / "manifest.json").is_file():
            out.append(p.name)
    return out


def read_section_text(
    bundle_ref: str,
    *,
    section: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    pattern: str | None = None,
    max_chars: int = 12000,
    base: Optional[Path] = None,
) -> str:
    """Read release content by section id or line range (for instruction_read tool)."""
    name, version = parse_bundle_ref(bundle_ref)
    bdir = bundle_dir(name, version, base)
    full_path = bdir / "full.md"
    if not full_path.is_file():
        raise FileNotFoundError(f"release not found: {bundle_ref}")
    text = full_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    needle = (pattern or "").strip()
    if needle:
        hits: list[str] = []
        for i, ln in enumerate(lines, start=1):
            if needle in ln:
                hits.append(f"{i}|{ln}")
        out = "\n".join(hits).strip() or "(no matches)"
    elif section:
        sec_path = bdir / "sections" / f"{section.strip()}.md"
        if sec_path.is_file():
            out = sec_path.read_text(encoding="utf-8").strip()
        else:
            manifest = load_manifest(bundle_ref, base=base)
            for s in manifest.get("sections") or []:
                if isinstance(s, dict) and str(s.get("id")) == section.strip():
                    a = int(s.get("start_line") or 1)
                    b = int(s.get("end_line") or len(lines))
                    out = "\n".join(lines[a - 1 : b]).strip()
                    break
            else:
                raise KeyError(f"section not found: {section}")
    else:
        a = max(1, int(start_line or 1))
        b = int(end_line or 0)
        if b <= 0:
            b = len(lines)
        out = "\n".join(f"{i}|{lines[i-1]}" for i in range(a, min(b, len(lines)) + 1))

    max_c = max(500, min(int(max_chars or 12000), 200_000))
    if len(out) > max_c:
        out = out[: max_c - 24].rstrip() + "\n…[instruction 已截断]"
    return out


def resolve_bootstrap(
    bundle_ref: str,
    *,
    mode: str = "bootstrap",
    sections: list[str] | None = None,
    base: Optional[Path] = None,
) -> str:
    """Build markdown appendix for system prompt (TOC + optional section bodies)."""
    manifest = load_manifest(bundle_ref, base=base)
    name = manifest.get("name") or parse_bundle_ref(bundle_ref)[0]
    version = manifest.get("version") or parse_bundle_ref(bundle_ref)[1]
    sec_list = manifest.get("sections") or []
    max_chars = int(manifest.get("bootstrap_max_chars") or 4000)

    parts = [
        "\n\n---\n",
        f"## Instruction: {name}@{version}\n",
        "Follow this instruction bundle. Use `instruction_read` for sections not loaded below.\n",
        "\n### Table of contents\n",
        "| id | title | summary |\n",
        "|----|-------|--------|\n",
    ]
    for s in sec_list:
        if not isinstance(s, dict):
            continue
        parts.append(
            f"| {s.get('id', '')} | {s.get('title', '')} | {s.get('summary', '')} |\n"
        )

    mode_l = (mode or "bootstrap").strip().lower()
    if mode_l in ("sections", "full"):
        want = set(sections or [])
        if mode_l == "full":
            want = {str(s.get("id")) for s in sec_list if isinstance(s, dict)}
        for sid in want:
            try:
                body = read_section_text(bundle_ref, section=sid, base=base, max_chars=8000)
            except (KeyError, FileNotFoundError):
                continue
            parts.append(f"\n### Section: {sid}\n\n{body}\n")

    out = "".join(parts)
    if len(out) > max_chars:
        out = out[: max_chars - 40].rstrip() + "\n…[bootstrap 已截断，请 instruction_read]\n"
    return out
