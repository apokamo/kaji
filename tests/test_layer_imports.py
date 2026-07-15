"""Fitness tests for runtime import direction between architectural layers."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

LAYER_RANK = {
    "foundation": 0,
    "provider": 1,
    "application": 2,
    "command": 3,
    "shim": 4,
}

MODULE_LAYERS: dict[str, str] = {
    "kaji_harness.__init__": "foundation",
    "kaji_harness.errors": "foundation",
    "kaji_harness.fsio": "foundation",
    "kaji_harness.providers": "provider",
    "kaji_harness.adapters": "application",
    "kaji_harness.artifacts": "application",
    "kaji_harness.cli": "application",
    "kaji_harness.config": "application",
    "kaji_harness.console_log": "application",
    "kaji_harness.interactive_terminal": "application",
    "kaji_harness.local_init": "application",
    "kaji_harness.logger": "application",
    "kaji_harness.models": "application",
    "kaji_harness.prompt": "application",
    "kaji_harness.recovery": "application",
    "kaji_harness.result": "application",
    "kaji_harness.runner": "application",
    "kaji_harness.script_exec": "application",
    "kaji_harness.series": "application",
    "kaji_harness.scripts": "application",
    "kaji_harness.skill": "application",
    "kaji_harness.state": "application",
    "kaji_harness.starter_release": "application",
    "kaji_harness.sync": "application",
    "kaji_harness.verdict": "application",
    "kaji_harness.workflow": "application",
    "kaji_harness.worktree_discovery": "application",
    "kaji_harness.commands": "command",
    "kaji_harness.cli_main": "shim",
}


@dataclass(frozen=True)
class ImportEdge:
    """One import dependency discovered in a module's AST."""

    source: str
    target: str
    runtime: bool
    lineno: int


@dataclass(frozen=True)
class LayerViolation:
    """A runtime import from a lower layer to a higher layer."""

    source: str
    target: str
    source_layer: str
    target_layer: str
    lineno: int


def layer_of(module: str) -> str:
    """Return the architectural layer for ``module`` using longest-prefix matching."""
    matches = [
        (prefix, layer)
        for prefix, layer in MODULE_LAYERS.items()
        if module == prefix or module.startswith(f"{prefix}.")
    ]
    if not matches:
        raise ValueError(f"unclassified module: {module}")
    return max(matches, key=lambda item: len(item[0]))[1]


def module_name_for_path(path: Path, package_root: Path) -> str:
    """Convert a Python file below ``package_root`` to its dotted module name."""
    relative = path.relative_to(package_root).with_suffix("")
    return ".".join((package_root.name, *relative.parts))


def discover_modules(package_root: Path) -> frozenset[str]:
    """Return every Python module below ``package_root``, including initializers."""
    return frozenset(
        module_name_for_path(path, package_root) for path in package_root.rglob("*.py")
    )


def _is_type_checking_guard(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    return isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING"


def _resolve_import_from(node: ast.ImportFrom, module_name: str) -> str:
    if node.level == 0:
        return node.module or ""
    parts = module_name.split(".")
    if node.level >= len(parts):
        raise ValueError(f"relative import escapes package in {module_name}:{node.lineno}")
    base_parts = parts[: -node.level]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def iter_runtime_imports(
    source_text: str,
    module_name: str,
    modules: frozenset[str],
) -> list[ImportEdge]:
    """Collect imports and distinguish ``TYPE_CHECKING``-only edges from runtime edges."""
    edges: list[ImportEdge] = []

    def walk(node: ast.AST, *, runtime: bool) -> None:
        if isinstance(node, ast.Import):
            edges.extend(
                ImportEdge(module_name, alias.name, runtime, node.lineno) for alias in node.names
            )
            return
        if isinstance(node, ast.ImportFrom):
            base = _resolve_import_from(node, module_name)
            for alias in node.names:
                child = f"{base}.{alias.name}" if base else alias.name
                target = child if child in modules else base
                edges.append(ImportEdge(module_name, target, runtime, node.lineno))
            return
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            for child in node.body:
                walk(child, runtime=False)
            for child in node.orelse:
                walk(child, runtime=runtime)
            return
        for child in ast.iter_child_nodes(node):
            walk(child, runtime=runtime)

    walk(ast.parse(source_text), runtime=True)
    return edges


def classify_layer_source(
    source_text: str,
    module_name: str,
    modules: frozenset[str],
) -> list[LayerViolation]:
    """Return prohibited runtime import edges from ``source_text``."""
    source_layer = layer_of(module_name)
    violations: list[LayerViolation] = []
    for edge in iter_runtime_imports(source_text, module_name, modules):
        if not edge.runtime or not (
            edge.target == "kaji_harness" or edge.target.startswith("kaji_harness.")
        ):
            continue
        target_layer = layer_of(edge.target)
        violates_foundation = source_layer == "foundation"
        violates_rank = LAYER_RANK[source_layer] < LAYER_RANK[target_layer]
        if violates_foundation or violates_rank:
            violations.append(
                LayerViolation(
                    source=edge.source,
                    target=edge.target,
                    source_layer=source_layer,
                    target_layer=target_layer,
                    lineno=edge.lineno,
                )
            )
    return violations


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "kaji_harness"

SYNTHETIC_MODULES = frozenset(
    {
        "kaji_harness.__init__",
        "kaji_harness.commands.issue",
        "kaji_harness.errors",
        "kaji_harness.fsio",
        "kaji_harness.models",
        "kaji_harness.providers.local",
        "kaji_harness.sync",
    }
)


@pytest.mark.small
class TestLayerClassification:
    def test_longest_prefix_classifies_provider_module(self) -> None:
        assert layer_of("kaji_harness.providers.local") == "provider"

    def test_unclassified_module_fails_loud(self) -> None:
        with pytest.raises(ValueError, match="unclassified module"):
            layer_of("kaji_harness.unknown")


@pytest.mark.small
class TestRuntimeImportClassification:
    def test_provider_to_application_symbol_import_is_violation(self) -> None:
        violations = classify_layer_source(
            "from ..sync import sync_from_github\n",
            "kaji_harness.providers.local",
            SYNTHETIC_MODULES,
        )
        assert [violation.target for violation in violations] == ["kaji_harness.sync"]

    @pytest.mark.parametrize(
        "guard",
        ["TYPE_CHECKING", "typing.TYPE_CHECKING"],
    )
    def test_type_checking_import_is_not_runtime_violation(self, guard: str) -> None:
        source = f"if {guard}:\n    from ..sync import sync_from_github\n"
        assert (
            classify_layer_source(source, "kaji_harness.providers.local", SYNTHETIC_MODULES) == []
        )
        edges = iter_runtime_imports(
            source,
            "kaji_harness.providers.local",
            SYNTHETIC_MODULES,
        )
        assert len(edges) == 1
        assert edges[0].runtime is False

    @pytest.mark.parametrize(
        ("source", "module_name"),
        [
            ("from .providers import IssueProvider\n", "kaji_harness.sync"),
            ("from ..sync import sync_from_github\n", "kaji_harness.commands.issue"),
            ("from .commands import issue\n", "kaji_harness.cli_main"),
        ],
    )
    def test_downward_imports_are_allowed(self, source: str, module_name: str) -> None:
        assert classify_layer_source(source, module_name, SYNTHETIC_MODULES) == []

    def test_child_module_import_is_resolved_before_layer_check(self) -> None:
        violations = classify_layer_source(
            "from .. import sync\n",
            "kaji_harness.providers.local",
            SYNTHETIC_MODULES,
        )
        assert [violation.target for violation in violations] == ["kaji_harness.sync"]

    def test_foundation_child_module_import_is_allowed_from_provider(self) -> None:
        assert (
            classify_layer_source(
                "from .. import errors\n",
                "kaji_harness.providers.local",
                SYNTHETIC_MODULES,
            )
            == []
        )

    def test_foundation_symbol_import_is_allowed_from_provider(self) -> None:
        assert (
            classify_layer_source(
                "from ..errors import SyncError\n",
                "kaji_harness.providers.local",
                SYNTHETIC_MODULES,
            )
            == []
        )

    @pytest.mark.parametrize("module_name", ["kaji_harness.fsio", "kaji_harness.__init__"])
    def test_foundation_cannot_import_internal_module(self, module_name: str) -> None:
        violations = classify_layer_source(
            "from .models import Step\n",
            module_name,
            SYNTHETIC_MODULES,
        )
        assert [violation.target for violation in violations] == ["kaji_harness.models"]

    def test_plain_import_reverse_dependency_is_detected(self) -> None:
        violations = classify_layer_source(
            "import kaji_harness.sync\n",
            "kaji_harness.providers.local",
            SYNTHETIC_MODULES,
        )
        assert [violation.target for violation in violations] == ["kaji_harness.sync"]

    def test_function_local_import_remains_runtime_dependency(self) -> None:
        source = "def load():\n    from ..sync import sync_from_github\n"
        violations = classify_layer_source(
            source,
            "kaji_harness.providers.local",
            SYNTHETIC_MODULES,
        )
        assert [violation.target for violation in violations] == ["kaji_harness.sync"]


@pytest.mark.medium
class TestRealTreeLayerDirection:
    def test_all_modules_are_classified(self) -> None:
        modules = discover_modules(PACKAGE_ROOT)
        assert modules
        assert {module for module in modules if layer_of(module) is None} == set()

    def test_mapping_entries_are_not_stale(self) -> None:
        modules = discover_modules(PACKAGE_ROOT)
        stale = {
            prefix
            for prefix in MODULE_LAYERS
            if not any(module == prefix or module.startswith(f"{prefix}.") for module in modules)
        }
        assert stale == set()

    def test_runtime_imports_follow_layer_direction(self) -> None:
        modules = discover_modules(PACKAGE_ROOT)
        violations = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            module_name = module_name_for_path(path, PACKAGE_ROOT)
            violations.extend(classify_layer_source(path.read_text(), module_name, modules))
        assert violations == []
