"""Tests for #59: V5/V6 legacy file cleanup and V7 base clarification.

Verifies that legacy files are properly isolated from the V7 codebase.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# Use the same Python that is running the tests (handles venv version mismatches)
VENV_PYTHON = sys.executable


# ─── Small Tests ───


@pytest.mark.small
class TestLegacyIsolation:
    """Verify V5/V6 code is isolated from V7 codebase."""

    def test_no_bugfix_agent_import_in_dao_harness(self) -> None:
        """dao_harness/ must not import bugfix_agent."""
        dao_dir = ROOT / "dao_harness"
        violations: list[str] = []
        for py_file in dao_dir.rglob("*.py"):
            source = py_file.read_text()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("bugfix_agent"):
                            violations.append(f"{py_file}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("bugfix_agent"):
                        violations.append(f"{py_file}:{node.lineno}")
        assert violations == [], f"bugfix_agent imports found in dao_harness/: {violations}"

    def test_no_bugfix_agent_import_in_v7_tests(self) -> None:
        """tests/ (V7) must not import bugfix_agent."""
        tests_dir = ROOT / "tests"
        violations: list[str] = []
        for py_file in tests_dir.rglob("*.py"):
            source = py_file.read_text()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("bugfix_agent"):
                            violations.append(f"{py_file}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("bugfix_agent"):
                        violations.append(f"{py_file}:{node.lineno}")
        assert violations == [], f"bugfix_agent imports found in tests/: {violations}"

    def test_legacy_directory_exists(self) -> None:
        """legacy/ directory must exist after cleanup."""
        assert (ROOT / "legacy").is_dir(), "legacy/ directory does not exist"

    def test_legacy_contains_bugfix_agent(self) -> None:
        """legacy/ must contain the bugfix_agent package."""
        assert (ROOT / "legacy" / "bugfix_agent").is_dir()

    def test_legacy_contains_orchestrator(self) -> None:
        """legacy/ must contain the V5 orchestrator."""
        assert (ROOT / "legacy" / "bugfix_agent_orchestrator.py").is_file()

    def test_legacy_contains_config(self) -> None:
        """legacy/ must contain V5 config."""
        assert (ROOT / "legacy" / "config.toml").is_file()

    def test_legacy_contains_agent_md(self) -> None:
        """legacy/ must contain V5 agent instructions."""
        assert (ROOT / "legacy" / "AGENT.md").is_file()

    def test_legacy_contains_prompts(self) -> None:
        """legacy/ must contain V6 prompts."""
        assert (ROOT / "legacy" / "prompts").is_dir()

    def test_legacy_contains_v5_tests(self) -> None:
        """legacy/ must contain V5 test files."""
        legacy_tests = ROOT / "legacy" / "tests"
        assert legacy_tests.is_dir()
        assert (legacy_tests / "conftest.py").is_file()
        assert (legacy_tests / "test_prompts.py").is_file()
        assert (legacy_tests / "test_handlers.py").is_file()
        assert (legacy_tests / "test_issue_provider.py").is_file()

    def test_legacy_contains_v5_test_utils(self) -> None:
        """legacy/ must contain V5 test utilities."""
        utils_dir = ROOT / "legacy" / "tests" / "utils"
        assert utils_dir.is_dir()
        assert (utils_dir / "__init__.py").is_file()
        assert (utils_dir / "context.py").is_file()
        assert (utils_dir / "providers.py").is_file()

    def test_legacy_contains_v5_docs(self) -> None:
        """legacy/ must contain V5 documentation."""
        legacy_docs = ROOT / "legacy" / "docs"
        assert legacy_docs.is_dir()
        assert (legacy_docs / "ARCHITECTURE.ja.md").is_file()
        assert (legacy_docs / "E2E_TEST_FINDINGS.md").is_file()
        assert (legacy_docs / "TEST_DESIGN.md").is_file()

    def test_root_does_not_contain_moved_files(self) -> None:
        """Root directory must not contain files that were moved to legacy/."""
        assert not (ROOT / "bugfix_agent").exists(), "bugfix_agent/ still at root"
        assert not (ROOT / "bugfix_agent_orchestrator.py").exists()
        assert not (ROOT / "test_bugfix_agent_orchestrator.py").exists()
        assert not (ROOT / "config.toml").exists()
        assert not (ROOT / "AGENT.md").exists()
        assert not (ROOT / "prompts").exists()

    def test_v5_tests_not_in_tests_dir(self) -> None:
        """V5 test files must not remain in tests/ directory."""
        tests_dir = ROOT / "tests"
        assert not (tests_dir / "test_prompts.py").exists()
        assert not (tests_dir / "test_handlers.py").exists()
        assert not (tests_dir / "test_issue_provider.py").exists()
        # conftest.py may be recreated for V7, but V5 one should be moved
        # utils/ should be moved
        assert not (tests_dir / "utils").exists()


@pytest.mark.small
class TestPyprojectToml:
    """Verify pyproject.toml is updated for V7."""

    def test_no_bugfix_agent_in_packages(self) -> None:
        """pyproject.toml must not include bugfix_agent in packages."""
        content = (ROOT / "pyproject.toml").read_text()
        assert "bugfix_agent" not in content

    def test_dao_harness_in_packages(self) -> None:
        """pyproject.toml must include dao_harness in packages."""
        content = (ROOT / "pyproject.toml").read_text()
        assert "dao_harness" in content


@pytest.mark.small
class TestDocumentation:
    """Verify documentation reflects V7 state."""

    def test_architecture_md_updated(self) -> None:
        """ARCHITECTURE.md must reference legacy/ instead of bugfix_agent/."""
        content = (ROOT / "docs" / "ARCHITECTURE.md").read_text()
        # Should mention legacy/ move
        assert "legacy/" in content
        # Should not say "削除予定" (planned for deletion) anymore
        assert "削除予定" not in content

    def test_readme_mentions_dao_harness(self) -> None:
        """README.md must describe dao_harness (V7)."""
        content = (ROOT / "README.md").read_text()
        assert "dao_harness" in content

    def test_readme_mentions_legacy(self) -> None:
        """README.md must mention legacy/ directory."""
        content = (ROOT / "README.md").read_text()
        assert "legacy/" in content or "legacy" in content


# ─── Medium Tests ───


@pytest.mark.medium
class TestPackageDiscovery:
    """Verify setuptools package discovery excludes legacy."""

    def test_find_packages_excludes_legacy(self) -> None:
        """setuptools find_packages should not find bugfix_agent."""
        result = subprocess.run(
            [
                VENV_PYTHON,
                "-c",
                (
                    "from setuptools import find_packages; "
                    f"pkgs = find_packages(where='{ROOT}', include=['dao_harness*']); "
                    "ba = [p for p in pkgs if p.startswith('bugfix_agent')]; "
                    "assert ba == [], f'bugfix_agent packages found: {ba}'"
                ),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Assertion failed: {result.stderr}"

    def test_find_packages_includes_dao_harness(self) -> None:
        """setuptools find_packages should find dao_harness."""
        result = subprocess.run(
            [
                VENV_PYTHON,
                "-c",
                (
                    "from setuptools import find_packages; "
                    f"pkgs = find_packages(where='{ROOT}', include=['dao_harness*']); "
                    "assert any(p.startswith('dao_harness') for p in pkgs), "
                    "f'dao_harness not found in: {pkgs}'"
                ),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Assertion failed: {result.stderr}"


@pytest.mark.medium
class TestV7TestsIntact:
    """Verify V7 tests remain functional after cleanup."""

    def test_v7_test_files_exist(self) -> None:
        """All V7 test files must still exist in tests/."""
        v7_tests = [
            "test_adapters.py",
            "test_cli_args.py",
            "test_cli_streaming_integration.py",
            "test_cycle_limit.py",
            "test_e2e_cli.py",
            "test_logging_integration.py",
            "test_prompt_builder.py",
            "test_run_logger.py",
            "test_session_state.py",
            "test_skill_validation.py",
            "test_start_logic.py",
            "test_state_persistence.py",
            "test_verdict_parser.py",
            "test_workflow_execution.py",
            "test_workflow_parser.py",
            "test_workflow_validator.py",
        ]
        tests_dir = ROOT / "tests"
        for test_file in v7_tests:
            assert (tests_dir / test_file).is_file(), f"V7 test missing: {test_file}"


# ─── Large Tests ───


@pytest.mark.large
class TestPackageInstallation:
    """E2E verification of package installation boundary."""

    def test_bugfix_agent_not_importable_after_install(self) -> None:
        """pip install -e '.[dev]' then import bugfix_agent raises ModuleNotFoundError.

        E2E verification: reinstall the package from the current worktree,
        then confirm that bugfix_agent is excluded from the installed packages.
        """
        # Reinstall package to ensure pyproject.toml changes take effect
        install_result = subprocess.run(
            [VENV_PYTHON, "-m", "pip", "install", "-e", ".[dev]", "-q"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert install_result.returncode == 0, (
            f"pip install failed:\nstderr: {install_result.stderr}"
        )

        # Verify bugfix_agent is not importable
        result = subprocess.run(
            [VENV_PYTHON, "-c", "import bugfix_agent"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode != 0, "bugfix_agent should not be importable after install"
        assert "ModuleNotFoundError" in result.stderr

    def test_pytest_collection_no_errors(self) -> None:
        """E2E: subprocess pytest --collect-only tests/ has 0 collection errors.

        Verifies that after V5 test removal, pytest can discover and collect
        all remaining V7 tests without ImportError or other collection failures.
        """
        result = subprocess.run(
            [VENV_PYTHON, "-m", "pytest", "--collect-only", "-q", "tests/"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert result.returncode == 0, (
            f"pytest collection failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # Verify no "ERROR" lines in stderr (collection errors appear there)
        error_lines = [
            line for line in result.stderr.splitlines() if line.strip().startswith("ERROR")
        ]
        assert error_lines == [], f"Collection errors found: {error_lines}"
