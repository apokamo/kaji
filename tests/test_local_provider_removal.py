"""Medium: ``provider.type = "local"``（削除済み provider）に対する CLI の終了コード（#448）。

``main([...])`` を in-process で呼び、tmp repo の設定に対する終了コードと stderr を検証する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.commands.main import main

pytestmark = pytest.mark.medium

_MIGRATION_HINT = 'Set provider.type = "github"'
_REMOVED = "local provider was removed"

_BASE_CONFIG = (
    "[paths]\n"
    'artifacts_dir = ".kaji-artifacts"\n'
    'skill_dir = ".claude/skills"\n'
    "\n"
    "[execution]\n"
    "default_timeout = 1800\n"
)
_GITHUB_PROVIDER = '\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "owner/name"\n'
_LOCAL_PROVIDER = '\n[provider]\ntype = "local"\n'

_VALID_WORKFLOW = (
    "name: ok\n"
    'description: ""\n'
    "execution_policy: auto\n"
    "steps:\n"
    "  - id: only\n"
    "    skill: noop\n"
    "    agent: claude\n"
    "    on:\n"
    "      PASS: end\n"
)
_INVALID_WORKFLOW = "name: bad\nsteps: []\n"


def _make_repo(tmp_path: Path, *, tracked_provider: str, overlay_provider: str | None) -> Path:
    repo = tmp_path / "repo"
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir(parents=True)
    (kaji_dir / "config.toml").write_text(_BASE_CONFIG + tracked_provider)
    if overlay_provider is not None:
        (kaji_dir / "config.local.toml").write_text(overlay_provider)
    return repo


@pytest.fixture
def tracked_local_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = _make_repo(tmp_path, tracked_provider=_LOCAL_PROVIDER, overlay_provider=None)
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture
def overlay_local_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = _make_repo(tmp_path, tracked_provider=_GITHUB_PROVIDER, overlay_provider=_LOCAL_PROVIDER)
    monkeypatch.chdir(repo)
    return repo


@pytest.mark.parametrize(
    "argv",
    [
        ["issue", "view", "1"],
        ["pr", "list"],
        ["config", "provider-type"],
        ["run", "wf.yaml", "1"],
    ],
)
def test_tracked_local_provider_exits_2(
    tracked_local_repo: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    (tracked_local_repo / "wf.yaml").write_text(_VALID_WORKFLOW)
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert _REMOVED in err
    assert _MIGRATION_HINT in err


def test_overlay_local_provider_exits_2_with_overlay_path(
    overlay_local_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["issue", "view", "1"]) == 2
    err = capsys.readouterr().err
    assert str(overlay_local_repo / ".kaji" / "config.local.toml") in err
    assert _MIGRATION_HINT in err


@pytest.mark.parametrize("repo_fixture", ["tracked_local_repo", "overlay_local_repo"])
def test_validate_under_local_provider_exits_2(
    repo_fixture: str, request: pytest.FixtureRequest, capsys: pytest.CaptureFixture[str]
) -> None:
    repo: Path = request.getfixturevalue(repo_fixture)
    wf = repo / "wf.yaml"
    wf.write_text(_VALID_WORKFLOW)
    assert main(["validate", str(wf)]) == 2
    err = capsys.readouterr().err
    assert f"✗ {wf}" in err
    assert _MIGRATION_HINT in err


def test_validate_multiple_files_prefers_exit_2(
    tracked_local_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ok = tracked_local_repo / "ok.yaml"
    ok.write_text(_VALID_WORKFLOW)
    bad = tracked_local_repo / "bad.yaml"
    bad.write_text(_INVALID_WORKFLOW)
    assert main(["validate", str(ok), str(bad)]) == 2
    err = capsys.readouterr().err
    assert f"✗ {ok}" in err
    assert f"✗ {bad}" in err
    assert "Validation failed: 2 of 2 files had errors." in err


@pytest.mark.parametrize("filename", ["missing.yaml", "malformed.yaml"])
def test_validate_exits_2_when_workflow_fails_before_config_resolution(
    filename: str, tracked_local_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """workflow が存在しない / YAML として読めない場合も local provider 診断と exit 2 を優先する。"""
    (tracked_local_repo / "malformed.yaml").write_text("name: [unclosed\n")
    wf = tracked_local_repo / filename
    assert main(["validate", str(wf)]) == 2
    err = capsys.readouterr().err
    assert f"✗ {wf}" in err
    assert _MIGRATION_HINT in err


def test_validate_requires_provider_local_under_github_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _make_repo(tmp_path, tracked_provider=_GITHUB_PROVIDER, overlay_provider=None)
    monkeypatch.chdir(repo)
    wf = repo / "wf.yaml"
    wf.write_text(_VALID_WORKFLOW + "requires_provider: local\n")
    assert main(["validate", str(wf)]) == 1
    assert "'requires_provider' must be one of ['any', 'github'], got 'local'" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize("argv", [["local", "init"], ["sync", "status"]])
def test_removed_subcommands_are_argument_errors(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
