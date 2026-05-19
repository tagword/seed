"""Symbol index build and search."""

from __future__ import annotations

from pathlib import Path

from seed.integrations.symbol_index import build_symbol_index, search_symbols


def test_build_and_search_python(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    f.write_text(
        "def hello():\n    pass\n\nclass Foo:\n    def bar(self):\n        return 1\n",
        encoding="utf-8",
    )
    index = build_symbol_index(tmp_path, use_ctags=False)
    hits = search_symbols(index, "hello", kind="function")
    assert any(h.name == "hello" for h in hits)
    hits2 = search_symbols(index, "Foo", kind="class")
    assert any(h.name == "Foo" for h in hits2)
