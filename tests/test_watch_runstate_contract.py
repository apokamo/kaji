"""Small tests: ``kaji_harness.watch`` uses only ``runstate.query`` (Issue #543 M2・S1).

run と series の内部事実を取得する境界だけを検査する。自分の永続化（``_watch/``）の
I/O は許可し、``open`` / ``read_text`` を一律には禁止しない。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.small

WATCH_DIR = Path(__file__).resolve().parent.parent / "kaji_harness" / "watch"
FORBIDDEN_MODULES = {
    "kaji_harness.runstate",
    "kaji_harness.runstate.facts",
    "kaji_harness.runstate.runlog",
    "kaji_harness.runstate.process",
    "kaji_harness.runstate.launch",
    "kaji_harness.runstate.schema",
    "kaji_harness.series.state",
}
FORBIDDEN_LITERALS = (
    "run.log",
    "session-state.json",
    "process.json",
    "pane-metadata.json",
)
# ``_series`` は path segment としてだけ禁止する（``list_series`` 等の識別子名は許す）
SERIES_DIR = re.compile(r"(^|[/\\\s\"'`])_series($|[/\\\s\"'`])")


def _absolute(module: str | None, level: int, path: Path) -> str:
    if level == 0:
        return module or ""
    package = ["kaji_harness", *path.relative_to(WATCH_DIR.parent).parent.parts]
    base = package[: len(package) - (level - 1)]
    return ".".join([*base, module] if module else base)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute(node.module, node.level, path)
            found.append(base)
            found.extend(f"{base}.{alias.name}" for alias in node.names)
    return found


def _modules() -> list[Path]:
    return sorted(WATCH_DIR.rglob("*.py"))


def test_watch_package_exists() -> None:
    assert _modules()


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_runstate_access_only_via_query(path: Path) -> None:
    for name in _imports(path):
        if name.startswith("kaji_harness.runstate"):
            assert name.startswith("kaji_harness.runstate.query"), f"{path.name}: {name}"
        assert name not in FORBIDDEN_MODULES, f"{path.name}: {name}"


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_internal_file_literals(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for literal in FORBIDDEN_LITERALS:
                assert literal not in node.value, f"{path.name}: {node.value!r}"
            assert not SERIES_DIR.search(node.value), f"{path.name}: {node.value!r}"
