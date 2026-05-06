"""Tests for ``kaji local init``（phase3d-design.md § 3 / § 6）。"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.local_init import (
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_OVERLAY_EXISTS,
    cmd_local_init,
    register_subcommand,
)


def _run_init(
    repo_root: Path,
    *,
    machine_id: str | None = None,
    default_branch: str = "main",
    non_interactive: bool = True,
) -> int:
    args = argparse.Namespace(
        local_command="init",
        machine_id=machine_id,
        default_branch=default_branch,
        non_interactive=non_interactive,
        repo_root=repo_root,
    )
    return cmd_local_init(args)


@pytest.mark.medium
def test_local_init_creates_overlay_and_gitignore(tmp_path: Path) -> None:
    rc = _run_init(tmp_path, machine_id="pc1")
    assert rc == EXIT_OK
    overlay = tmp_path / ".kaji" / "config.local.toml"
    assert overlay.is_file()
    text = overlay.read_text(encoding="utf-8")
    assert "[provider]" in text
    assert 'type = "local"' in text
    assert 'machine_id = "pc1"' in text
    assert 'default_branch = "main"' in text

    gitignore = tmp_path / ".gitignore"
    assert gitignore.is_file()
    assert ".kaji/config.local.toml" in gitignore.read_text(encoding="utf-8")


@pytest.mark.medium
def test_local_init_does_not_touch_tracked_config_toml(tmp_path: Path) -> None:
    """phase3d-design.md § 3: tracked .kaji/config.toml は touch しない。"""
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir()
    cfg = kaji_dir / "config.toml"
    original = '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n'
    cfg.write_text(original, encoding="utf-8")

    rc = _run_init(tmp_path, machine_id="pc1")
    assert rc == EXIT_OK
    assert cfg.read_text(encoding="utf-8") == original


@pytest.mark.medium
def test_local_init_machine_id_uppercase_rejected(tmp_path: Path) -> None:
    rc = _run_init(tmp_path, machine_id="PC1")
    assert rc == EXIT_INVALID_INPUT
    assert not (tmp_path / ".kaji" / "config.local.toml").exists()


@pytest.mark.medium
def test_local_init_machine_id_hyphen_rejected(tmp_path: Path) -> None:
    rc = _run_init(tmp_path, machine_id="pc-1")
    assert rc == EXIT_INVALID_INPUT
    assert not (tmp_path / ".kaji" / "config.local.toml").exists()


@pytest.mark.medium
def test_local_init_existing_overlay_aborts(tmp_path: Path) -> None:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir()
    overlay = kaji_dir / "config.local.toml"
    overlay.write_text('[provider]\ntype = "local"\n', encoding="utf-8")
    original = overlay.read_text(encoding="utf-8")

    rc = _run_init(tmp_path, machine_id="pc2")
    assert rc == EXIT_OVERLAY_EXISTS
    # overlay の内容は変更されない
    assert overlay.read_text(encoding="utf-8") == original


@pytest.mark.medium
def test_local_init_default_branch_explicit(tmp_path: Path) -> None:
    rc = _run_init(tmp_path, machine_id="pc1", default_branch="develop")
    assert rc == EXIT_OK
    overlay = tmp_path / ".kaji" / "config.local.toml"
    assert 'default_branch = "develop"' in overlay.read_text(encoding="utf-8")


@pytest.mark.medium
def test_local_init_warns_on_duplicate_machine_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    issues_dir = tmp_path / ".kaji" / "issues"
    issues_dir.mkdir(parents=True)
    (issues_dir / "local-pc1-3-foo").mkdir()

    rc = _run_init(tmp_path, machine_id="pc1")
    assert rc == EXIT_OK
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "pc1" in err
    assert "local-pc1-3" in err


@pytest.mark.medium
def test_local_init_uses_hostname_when_machine_id_omitted(tmp_path: Path) -> None:
    with patch("kaji_harness.local_init.socket.gethostname", return_value="MyDesktop42"):
        rc = _run_init(tmp_path, machine_id=None)
    assert rc == EXIT_OK
    overlay = (tmp_path / ".kaji" / "config.local.toml").read_text(encoding="utf-8")
    # lowercase + alphanumeric only + truncated to 16
    assert 'machine_id = "mydesktop42"' in overlay


@pytest.mark.medium
def test_local_init_falls_back_to_pcN_when_hostname_empty(tmp_path: Path) -> None:
    with patch("kaji_harness.local_init.socket.gethostname", return_value="!!!"):
        rc = _run_init(tmp_path, machine_id=None)
    assert rc == EXIT_OK
    overlay = (tmp_path / ".kaji" / "config.local.toml").read_text(encoding="utf-8")
    assert 'machine_id = "pc1"' in overlay


@pytest.mark.medium
def test_local_init_falls_back_to_pcN_avoiding_duplicates(tmp_path: Path) -> None:
    issues_dir = tmp_path / ".kaji" / "issues"
    issues_dir.mkdir(parents=True)
    (issues_dir / "local-pc1-1-foo").mkdir()
    (issues_dir / "local-pc2-1-bar").mkdir()
    with patch("kaji_harness.local_init.socket.gethostname", return_value="!!!"):
        rc = _run_init(tmp_path, machine_id=None)
    assert rc == EXIT_OK
    overlay = (tmp_path / ".kaji" / "config.local.toml").read_text(encoding="utf-8")
    assert 'machine_id = "pc3"' in overlay


@pytest.mark.medium
def test_local_init_existing_gitignore_appended(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("__pycache__/\n", encoding="utf-8")

    rc = _run_init(tmp_path, machine_id="pc1")
    assert rc == EXIT_OK
    text = gitignore.read_text(encoding="utf-8")
    assert "__pycache__/" in text
    assert ".kaji/config.local.toml" in text


@pytest.mark.medium
def test_local_init_existing_gitignore_no_duplicate(tmp_path: Path) -> None:
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".kaji/config.local.toml\n", encoding="utf-8")

    rc = _run_init(tmp_path, machine_id="pc1")
    assert rc == EXIT_OK
    text = gitignore.read_text(encoding="utf-8")
    # only one occurrence
    assert text.count(".kaji/config.local.toml") == 1


@pytest.mark.medium
def test_register_subcommand_attaches_local_init() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    register_subcommand(sub)
    ns = parser.parse_args(["local", "init", "--machine-id", "pc1"])
    assert ns.command == "local"
    assert ns.local_command == "init"
    assert ns.machine_id == "pc1"
