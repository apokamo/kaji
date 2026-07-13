"""Network-free subprocess coverage for sequential series CLI commands."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_local]


@pytest.fixture
def series_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal GitHub-configured repo without external provider access."""
    kaji_dir = tmp_path / ".kaji"
    workflow_dir = kaji_dir / "wf"
    series_dir = kaji_dir / "series"
    workflow_dir.mkdir(parents=True)
    series_dir.mkdir()
    (kaji_dir / "config.toml").write_text(
        "[paths]\n"
        'artifacts_dir = "artifacts"\n'
        'skill_dir = ".claude/skills"\n'
        "[execution]\n"
        "default_timeout = 60\n"
        "[provider]\n"
        'type = "github"\n'
        "[provider.github]\n"
        'repo = "owner/name"\n',
        encoding="utf-8",
    )
    (workflow_dir / "dev.yaml").write_text(
        "name: dev\n"
        "description: test\n"
        "requires_provider: github\n"
        "execution_policy: auto\n"
        "steps:\n"
        "  - id: done\n"
        '    exec: ["true"]\n'
        "    on:\n"
        "      PASS: end\n",
        encoding="utf-8",
    )
    series = series_dir / "acceptance.yaml"
    series.write_text(
        "id: acceptance\n"
        "parent_issue: 291\n"
        "strategy: sequential\n"
        "members:\n"
        "  - issue: 282\n"
        "    workflow: .kaji/wf/dev.yaml\n"
        "on_failure: stop\n",
        encoding="utf-8",
    )
    return tmp_path, series


def test_validate_series_real_cli(series_repo: tuple[Path, Path]) -> None:
    repo, series = series_repo
    result = subprocess.run(
        ["kaji", "validate-series", str(series)],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "1 members" in result.stdout


def test_validate_series_aggregates_multiple_files(
    series_repo: tuple[Path, Path],
) -> None:
    repo, series = series_repo
    second = series.with_name("second.yaml")
    second.write_text(
        series.read_text(encoding="utf-8").replace("id: acceptance", "id: second"),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["kaji", "validate-series", str(series), str(second)],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert str(series) in result.stdout
    assert str(second) in result.stdout

    second.write_text("id: Bad_ID\nmembers: []\n", encoding="utf-8")
    mixed = subprocess.run(
        ["kaji", "validate-series", str(series), str(second)],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert mixed.returncode == 1
    assert str(series) in mixed.stdout
    assert str(second) in mixed.stderr


def test_run_series_dry_run_has_no_side_effects(series_repo: tuple[Path, Path]) -> None:
    repo, series = series_repo
    result = subprocess.run(
        ["kaji", "run-series", str(series), "--dry-run"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "issue #282" in result.stdout
    assert "sha256:" in result.stdout
    assert not (repo / "artifacts" / "_series").exists()


def test_validate_series_real_cli_reports_schema_error(
    series_repo: tuple[Path, Path],
) -> None:
    repo, series = series_repo
    series.write_text("id: Bad_ID\nmembers: []\n", encoding="utf-8")
    result = subprocess.run(
        ["kaji", "validate-series", str(series)],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "id" in result.stderr
    assert "members" in result.stderr
