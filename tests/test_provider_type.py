"""Phase 4 commit 1: ``actual_provider_type`` helper + ``kaji config provider-type``.

Small/Medium テスト:

- ``actual_provider_type(config)`` の挙動（provider 確定後 / None ガード）
- ``kaji config provider-type`` の stdout / stderr / exit code
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from kaji_harness.commands.main import main as cli_main
from kaji_harness.config import KajiConfig
from kaji_harness.providers import actual_provider_type

_BASE_CONFIG = """
[paths]
artifacts_dir = ".kaji/artifacts"
skill_dir = ".claude/skills"

[execution]
default_timeout = 600
""".lstrip()


def _write_config(repo_root: Path, *, body: str = "", overlay: str | None = None) -> None:
    import subprocess as _sp

    (repo_root / ".kaji").mkdir(exist_ok=True)
    (repo_root / ".kaji" / "config.toml").write_text(_BASE_CONFIG + body)
    if overlay is not None:
        (repo_root / ".kaji" / "config.local.toml").write_text(overlay)
    # gl:21: provider.type='local' resolution requires a git repo.
    if not (repo_root / ".git").exists():
        _sp.run(["git", "init", "-q", "--initial-branch=main", str(repo_root)], check=True)


@pytest.mark.small
def test_actual_provider_type_returns_github(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        body='[provider]\ntype = "github"\n[provider.github]\nrepo = "x/y"\n',
    )
    config = KajiConfig.discover(start_dir=tmp_path)
    assert actual_provider_type(config) == "github"


@pytest.mark.small
def test_actual_provider_type_returns_local(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        body='[provider]\ntype = "local"\n[provider.local]\nmachine_id = "pc1"\n',
    )
    config = KajiConfig.discover(start_dir=tmp_path)
    assert actual_provider_type(config) == "local"


@pytest.mark.small
def test_actual_provider_type_raises_when_provider_is_none(tmp_path: Path) -> None:
    _write_config(tmp_path)
    config = KajiConfig.discover(start_dir=tmp_path)
    assert config.provider is None
    with pytest.raises(ValueError, match="actual_provider_type"):
        actual_provider_type(config)


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Invoke ``cli_main`` and capture stdout/stderr/exit code."""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli_main(argv)
        except SystemExit as e:  # argparse may raise SystemExit
            rc = int(e.code) if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


@pytest.mark.medium
def test_kaji_config_provider_type_github(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        body='[provider]\ntype = "github"\n[provider.github]\nrepo = "owner/name"\n',
    )
    rc, stdout, stderr = _run_cli(["config", "provider-type", "--workdir", str(tmp_path)])
    assert rc == 0, stderr
    assert stdout == "github\n"
    assert stderr == ""


@pytest.mark.medium
def test_kaji_config_provider_type_local(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        body='[provider]\ntype = "local"\n[provider.local]\nmachine_id = "pc1"\n',
    )
    rc, stdout, stderr = _run_cli(["config", "provider-type", "--workdir", str(tmp_path)])
    assert rc == 0, stderr
    assert stdout == "local\n"
    assert stderr == ""


@pytest.mark.medium
def test_kaji_config_provider_type_missing_provider_section(tmp_path: Path) -> None:
    _write_config(tmp_path)
    rc, stdout, stderr = _run_cli(["config", "provider-type", "--workdir", str(tmp_path)])
    assert rc == 2
    assert stdout == ""
    assert "[provider]" in stderr


@pytest.mark.medium
def test_kaji_config_provider_type_missing_config(tmp_path: Path) -> None:
    rc, stdout, stderr = _run_cli(["config", "provider-type", "--workdir", str(tmp_path)])
    assert rc == 2
    assert stdout == ""
    assert "Error:" in stderr


@pytest.mark.medium
def test_kaji_config_provider_type_overlay_takes_precedence(tmp_path: Path) -> None:
    """``.kaji/config.local.toml`` overlay が tracked config を上書きする経路。"""
    _write_config(
        tmp_path,
        body='[provider]\ntype = "github"\n[provider.github]\nrepo = "owner/name"\n',
        overlay='[provider]\ntype = "local"\n[provider.local]\nmachine_id = "pc1"\n',
    )
    rc, stdout, _ = _run_cli(["config", "provider-type", "--workdir", str(tmp_path)])
    assert rc == 0
    assert stdout == "local\n"


@pytest.mark.medium
def test_kaji_config_provider_type_invalid_workdir(tmp_path: Path) -> None:
    bogus = tmp_path / "does-not-exist"
    rc, stdout, stderr = _run_cli(["config", "provider-type", "--workdir", str(bogus)])
    assert rc == 2
    assert stdout == ""
    assert "is not a valid directory" in stderr
