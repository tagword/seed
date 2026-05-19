"""Project symbol index (Python AST + optional universal-ctags)."""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    ".seed",
    ".codeagent",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

SymbolKind = Literal["function", "class", "method", "variable", "unknown"]


@dataclass
class SymbolEntry:
    name: str
    kind: SymbolKind
    path: str
    line: int
    end_line: int = 0
    container: str = ""
    signature: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SymbolIndex:
    root: str
    symbols: List[SymbolEntry] = field(default_factory=list)
    built_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "built_at": self.built_at,
            "symbols": [s.to_dict() for s in self.symbols],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SymbolIndex:
        syms = []
        for raw in data.get("symbols") or []:
            if not isinstance(raw, dict):
                continue
            syms.append(
                SymbolEntry(
                    name=str(raw.get("name") or ""),
                    kind=raw.get("kind") or "unknown",
                    path=str(raw.get("path") or ""),
                    line=int(raw.get("line") or 0),
                    end_line=int(raw.get("end_line") or 0),
                    container=str(raw.get("container") or ""),
                    signature=str(raw.get("signature") or ""),
                )
            )
        return cls(
            root=str(data.get("root") or ""),
            symbols=syms,
            built_at=str(data.get("built_at") or ""),
        )


def _should_skip_dir(name: str) -> bool:
    return name.startswith(".") and name not in (".",) or name in _SKIP_DIRS


def iter_source_files(root: Path, *, max_files: int = 500) -> List[Path]:
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for fn in filenames:
            if fn.endswith((".py", ".js", ".ts", ".tsx", ".go", ".rs")):
                out.append(Path(dirpath) / fn)
                if len(out) >= max_files:
                    return out
    return out


def _index_python_file(path: Path, root: Path) -> List[SymbolEntry]:
    rel = str(path.relative_to(root))
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.entries: List[SymbolEntry] = []
            self._class_stack: List[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.entries.append(
                SymbolEntry(
                    name=node.name,
                    kind="class",
                    path=rel,
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    signature=f"class {node.name}",
                )
            )
            self._class_stack.append(node.name)
            self.generic_visit(node)
            self._class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            parent = self._class_stack[-1] if self._class_stack else ""
            kind: SymbolKind = "method" if parent else "function"
            params = ", ".join(a.arg for a in node.args.args[:8])
            self.entries.append(
                SymbolEntry(
                    name=node.name,
                    kind=kind,
                    path=rel,
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
                    container=parent,
                    signature=f"{node.name}({params})",
                )
            )

    v = _Visitor()
    v.visit(tree)
    return v.entries


def _index_via_ctags(root: Path, *, max_files: int = 500) -> List[SymbolEntry]:
    try:
        proc = subprocess.run(
            [
                "ctags",
                "-x",
                "--fields=+n",
                "-R",
                "--languages=Python,JavaScript,TypeScript,Go,Rust",
                str(root),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(root),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode not in (0, 1):
        return []
    out: List[SymbolEntry] = []
    for line in (proc.stdout or "").splitlines()[: max_files * 3]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        name, _typ, rest = parts[0], parts[1], parts[2]
        if " " not in rest:
            continue
        path_part, _extras = rest.split(" ", 1)[0], rest
        line_no = 0
        if "(" in _extras:
            try:
                line_no = int(_extras.split("(", 1)[1].split(")", 1)[0])
            except ValueError:
                pass
        kind: SymbolKind = "unknown"
        low = _typ.lower()
        if "function" in low or "method" in low:
            kind = "method" if "method" in low else "function"
        elif "class" in low:
            kind = "class"
        try:
            rel = str(Path(path_part).resolve().relative_to(root.resolve()))
        except ValueError:
            rel = path_part
        out.append(SymbolEntry(name=name, kind=kind, path=rel, line=line_no or 1))
    return out


def build_symbol_index(root: str | Path, *, use_ctags: bool = True) -> SymbolIndex:
    from datetime import datetime, timezone

    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"Not a directory: {base}")

    symbols: List[SymbolEntry] = []
    if use_ctags:
        symbols.extend(_index_via_ctags(base))

    seen = {(s.path, s.name, s.line) for s in symbols}
    for fp in iter_source_files(base):
        if fp.suffix != ".py":
            continue
        for ent in _index_python_file(fp, base):
            key = (ent.path, ent.name, ent.line)
            if key not in seen:
                symbols.append(ent)
                seen.add(key)

    return SymbolIndex(
        root=str(base),
        symbols=symbols,
        built_at=datetime.now(timezone.utc).isoformat(),
    )


def index_cache_path(root: Path) -> Path:
    return root / ".seed" / "symbol_index.json"


def load_cached_index(root: str | Path) -> Optional[SymbolIndex]:
    p = index_cache_path(Path(root).resolve())
    if not p.is_file():
        return None
    try:
        return SymbolIndex.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return None


def save_index_cache(index: SymbolIndex) -> Path:
    p = index_cache_path(Path(index.root))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(index.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def search_symbols(
    index: SymbolIndex,
    query: str,
    *,
    kind: str = "all",
    path_prefix: str = "",
    limit: int = 30,
) -> List[SymbolEntry]:
    q = (query or "").strip().lower()
    if not q:
        return []
    pref = (path_prefix or "").strip().replace("\\", "/")
    out: List[SymbolEntry] = []
    for s in index.symbols:
        if kind != "all" and s.kind != kind:
            continue
        if pref and not s.path.replace("\\", "/").startswith(pref):
            continue
        if q in s.name.lower() or q in s.path.lower():
            out.append(s)
            if len(out) >= limit:
                break
    return out


def python_definition_at(filepath: Path, line: int, column: int = 0) -> Optional[SymbolEntry]:
    """Naive go-to-definition using AST (same file / import names)."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return None

    lines = source.splitlines()
    if line < 1 or line > len(lines):
        return None
    row = lines[line - 1]
    col = max(0, min(column, len(row) - 1)) if column else len(row) - 1
    # token under cursor
    import re

    m = re.search(r"[A-Za-z_][A-Za-z0-9_]*", row[: col + 1])
    if not m:
        return None
    name = m.group(0)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return SymbolEntry(
                name=name,
                kind="function",
                path=str(filepath),
                line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            )
        if isinstance(node, ast.ClassDef) and node.name == name:
            return SymbolEntry(
                name=name,
                kind="class",
                path=str(filepath),
                line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            )
    return None
