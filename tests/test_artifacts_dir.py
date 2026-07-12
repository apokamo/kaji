"""Tests for ``kaji_harness.artifacts.resolve_artifacts_dir`` (Issue #177).

`KajiConfig.artifacts_dir` の相対パス解決を main worktree 基準に固定する。
feature worktree 配下から ``kaji run`` しても artifacts/log が
``<main_worktree>/.kaji-artifacts/`` に集約され、``git worktree remove`` で
ログが消えないことを担保する。
"""

from __future__ import annotations

import io
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness import cli_main
from kaji_harness.artifacts import _try_resolve_main_worktree, resolve_artifacts_dir
from kaji_harness.config import (
    ExecutionConfig,
    GitHubProviderConfig,
    KajiConfig,
    LocalProviderConfig,
    PathsConfig,
    ProviderConfig,
)
from kaji_harness.models import Verdict
from kaji_harness.providers.local import LocalProviderError
from kaji_harness.state import SessionState


def _make_config(
    *,
    repo_root: Path,
    artifacts_dir: str = ".kaji-artifacts",
    provider: ProviderConfig | None = None,
) -> KajiConfig:
    """Lightweight KajiConfig factory for resolve_artifacts_dir branch tests."""
    return KajiConfig(
        repo_root=repo_root,
        paths=PathsConfig(skill_dir=".claude/skills", artifacts_dir=artifacts_dir),
        execution=ExecutionConfig(default_timeout=1800),
        provider=provider,
    )


def _local_provider(default_branch: str = "main") -> ProviderConfig:
    return ProviderConfig(
        type="local",
        local=LocalProviderConfig(machine_id="pc1", default_branch=default_branch),
        github=GitHubProviderConfig(),
    )


# ============================================================
# Small: resolve_artifacts_dir 分岐網羅
# ============================================================


@pytest.mark.small
class TestResolveArtifactsDirSmall:
    def test_absolute_path_returned_as_is(self, tmp_path: Path) -> None:
        cfg = _make_config(repo_root=tmp_path, artifacts_dir=str(tmp_path / "abs"))
        with patch("kaji_harness.artifacts._try_resolve_main_worktree") as m:
            result = resolve_artifacts_dir(cfg)
        assert result == tmp_path / "abs"
        m.assert_not_called()

    def test_tilde_expanded_to_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = _make_config(repo_root=tmp_path, artifacts_dir="~/artifacts")
        with patch("kaji_harness.artifacts._try_resolve_main_worktree") as m:
            result = resolve_artifacts_dir(cfg)
        assert result == tmp_path / "artifacts"
        m.assert_not_called()

    def test_relative_with_main_worktree_resolved(self, tmp_path: Path) -> None:
        cfg = _make_config(repo_root=tmp_path / "feat")
        main_root = tmp_path / "main"
        with patch(
            "kaji_harness.artifacts._try_resolve_main_worktree",
            return_value=main_root,
        ):
            result = resolve_artifacts_dir(cfg)
        assert result == main_root / ".kaji-artifacts"

    def test_relative_fallback_when_main_worktree_none(self, tmp_path: Path) -> None:
        cfg = _make_config(repo_root=tmp_path / "feat")
        with patch(
            "kaji_harness.artifacts._try_resolve_main_worktree",
            return_value=None,
        ):
            result = resolve_artifacts_dir(cfg)
        assert result == tmp_path / "feat" / ".kaji-artifacts"


@pytest.mark.small
class TestTryResolveMainWorktreeSmall:
    def test_provider_none_returns_none(self, tmp_path: Path) -> None:
        cfg = _make_config(repo_root=tmp_path, provider=None)
        assert _try_resolve_main_worktree(cfg) is None

    def test_local_provider_error_swallowed(self, tmp_path: Path) -> None:
        cfg = _make_config(repo_root=tmp_path, provider=_local_provider())
        with patch(
            "kaji_harness.providers._worktree.resolve_main_worktree",
            side_effect=LocalProviderError("no git"),
        ):
            assert _try_resolve_main_worktree(cfg) is None

    def test_normal_returns_resolved_path(self, tmp_path: Path) -> None:
        cfg = _make_config(repo_root=tmp_path, provider=_local_provider())
        resolved = tmp_path / "main"
        with patch(
            "kaji_harness.providers._worktree.resolve_main_worktree",
            return_value=resolved,
        ):
            assert _try_resolve_main_worktree(cfg) == resolved


# ============================================================
# Medium: bare + main + feature worktree fixture を用いた統合検証
# ============================================================


_CONFIG_TOML = (
    "[paths]\n"
    'skill_dir = ".claude/skills"\n'
    'artifacts_dir = ".kaji-artifacts"\n\n'
    "[execution]\ndefault_timeout = 1800\n\n"
    '[provider]\ntype = "local"\n\n'
    "[provider.local]\n"
    'machine_id = "pc1"\n'
    'default_branch = "main"\n'
)


def _seed_kaji_config(wt: Path) -> None:
    """Write ``.kaji/config.toml`` into a worktree (provider.type=local)."""
    cfg_dir = wt / ".kaji"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(_CONFIG_TOML)


@pytest.mark.medium
class TestResolveArtifactsDirIntegration:
    """再現テスト1: feature worktree から discover しても main 基準で解決される。"""

    def test_feature_worktree_resolves_to_main(
        self, bare_with_two_worktrees: tuple[Path, Path, Path]
    ) -> None:
        _bare, main_wt, feat_wt = bare_with_two_worktrees
        _seed_kaji_config(main_wt)
        _seed_kaji_config(feat_wt)

        cfg = KajiConfig.discover(start_dir=feat_wt)
        result = resolve_artifacts_dir(cfg)
        assert result == main_wt.resolve() / ".kaji-artifacts"


_MINIMAL_WORKFLOW_YAML = """\
name: test
description: test workflow
execution_policy: auto
steps:
  - id: step1
    skill: test-skill
    agent: claude
    on:
      PASS: end
      ABORT: end
"""


@pytest.mark.medium
class TestCmdRunWiring:
    """再現テスト2-A: cli_main._cmd_run の差し替え漏れを検出する配線テスト。

    ``cli_main.main(["run", ...])`` を実際に駆動し、``WorkflowRunner.__init__`` に
    渡される ``artifacts_dir`` kwarg が ``main_wt / ".kaji-artifacts"`` で
    あることを assert する。``config.artifacts_dir`` のまま (差し替え忘れ) では
    ``feat_wt / ".kaji-artifacts"`` が渡り FAIL する。
    """

    def test_cmd_run_passes_main_worktree_artifacts_dir(
        self,
        bare_with_two_worktrees: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _bare, main_wt, feat_wt = bare_with_two_worktrees
        _seed_kaji_config(main_wt)
        _seed_kaji_config(feat_wt)

        workflow_path = feat_wt / "wf.yaml"
        workflow_path.write_text(_MINIMAL_WORKFLOW_YAML)

        captured: dict[str, Path] = {}

        class _StubRunner:
            canonical_issue_ref = "local-pc1-1"

            def __init__(self, *, artifacts_dir: Path, **kwargs: object) -> None:
                captured["artifacts_dir"] = artifacts_dir

            def run(self) -> object:
                state = MagicMock()
                state.last_transition_verdict = Verdict("PASS", "ok", "", "")
                return state

        monkeypatch.setattr("kaji_harness.commands.run.WorkflowRunner", _StubRunner)
        # provider=local は skill 存在検証を行うため、test-skill を validate 段階で
        # 通過させるため validate_workflow_provider_match のみ最小限通過する設計に
        # しているが、念のため skill validation はそのまま走らせる前にここで弾く。
        monkeypatch.setattr(
            "kaji_harness.commands.validate.validate_skill_exists", lambda *a, **kw: None
        )

        rc = cli_main.main(
            [
                "run",
                str(workflow_path),
                "local-pc1-1",
                "--workdir",
                str(feat_wt),
            ]
        )
        assert rc == 0
        assert captured["artifacts_dir"] == main_wt.resolve() / ".kaji-artifacts"


@pytest.mark.medium
class TestLogsSurviveWorktreeRemoval:
    """再現テスト2-B: worktree 削除後も main 配下のログが残ることを確認する。"""

    def test_session_state_persists_in_main_and_survives_remove(
        self, bare_with_two_worktrees: tuple[Path, Path, Path]
    ) -> None:
        bare, main_wt, feat_wt = bare_with_two_worktrees
        _seed_kaji_config(main_wt)
        _seed_kaji_config(feat_wt)

        cfg = KajiConfig.discover(start_dir=feat_wt)
        artifacts_path = resolve_artifacts_dir(cfg)
        assert artifacts_path == main_wt.resolve() / ".kaji-artifacts"

        state = SessionState.load_or_create("test-issue", artifacts_path)
        state.save_session_id("step1", "session-abc")

        target = main_wt.resolve() / ".kaji-artifacts" / "test-issue" / "session-state.json"
        assert target.exists()
        assert not (feat_wt / ".kaji-artifacts").exists()

        subprocess.run(
            ["git", "-C", str(bare), "worktree", "remove", "--force", str(feat_wt)],
            check=True,
        )
        assert target.exists()


@pytest.mark.medium
class TestFallbackPaths:
    """再現テスト2-C / 2-D: fallback 経路 (非 git / provider=None) の検証。"""

    def test_non_git_dir_falls_back_to_repo_root(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "plain"
        repo_root.mkdir()
        cfg = _make_config(repo_root=repo_root, provider=_local_provider())
        result = resolve_artifacts_dir(cfg)
        assert result == repo_root / ".kaji-artifacts"

    def test_provider_none_falls_back_to_repo_root(self, tmp_path: Path) -> None:
        repo_root = tmp_path / "plain"
        repo_root.mkdir()
        cfg = _make_config(repo_root=repo_root, provider=None)
        result = resolve_artifacts_dir(cfg)
        assert result == repo_root / ".kaji-artifacts"


# ============================================================
# Issue #305: `kaji config artifacts-dir` — incident-* skill 群が
# feature worktree の cwd に依存せず main 集約の artifact root を取得する経路を固定する。
# ============================================================


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Invoke ``cli_main.main`` and capture stdout/stderr/exit code."""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli_main.main(argv)
        except SystemExit as e:  # argparse may raise SystemExit
            rc = int(e.code) if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


@pytest.mark.medium
class TestConfigArtifactsDirCommand:
    """`kaji config artifacts-dir` の read-only 契約を固定する回帰テスト。"""

    def test_feature_worktree_prints_main_artifacts_dir(
        self, bare_with_two_worktrees: tuple[Path, Path, Path]
    ) -> None:
        """回帰: feature worktree から呼んでも main 集約の絶対 root を stdout に返す。

        incident-* skill は feature worktree（例 `kaji-feat-305`）の cwd から起動され得る。
        そこには `.kaji-artifacts` が存在しないため、cwd 相対参照では source run / 台帳 /
        調査 report に到達できない。本コマンドが `kaji run` と同一の `resolve_artifacts_dir()`
        を経由して main worktree 基準の絶対パスを返すことで、この不整合を解消する。
        """
        _bare, main_wt, feat_wt = bare_with_two_worktrees
        _seed_kaji_config(main_wt)
        _seed_kaji_config(feat_wt)

        rc, stdout, stderr = _run_cli(["config", "artifacts-dir", "--workdir", str(feat_wt)])
        assert rc == 0, stderr
        assert stdout == f"{main_wt.resolve() / '.kaji-artifacts'}\n"
        assert stderr == ""
        # cwd 相対では feature worktree 配下を指してしまうことの明示（回帰の核心）。
        assert stdout.strip() != str(feat_wt / ".kaji-artifacts")

    def test_absolute_artifacts_dir_returned_as_is(self, tmp_path: Path) -> None:
        """絶対 `artifacts_dir` は worktree 解決を経ずそのまま出力される。"""
        abs_dir = tmp_path / "abs-artifacts"
        cfg_dir = tmp_path / ".kaji"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.toml").write_text(
            "[paths]\n"
            'skill_dir = ".claude/skills"\n'
            f'artifacts_dir = "{abs_dir}"\n\n'
            "[execution]\ndefault_timeout = 1800\n\n"
            '[provider]\ntype = "github"\n\n'
            "[provider.github]\n"
            'repo = "owner/name"\n'
        )
        rc, stdout, stderr = _run_cli(["config", "artifacts-dir", "--workdir", str(tmp_path)])
        assert rc == 0, stderr
        assert stdout == f"{abs_dir}\n"

    def test_missing_config_exits_2(self, tmp_path: Path) -> None:
        rc, stdout, stderr = _run_cli(["config", "artifacts-dir", "--workdir", str(tmp_path)])
        assert rc == 2
        assert stdout == ""
        assert "Error:" in stderr

    def test_invalid_workdir_exits_2(self, tmp_path: Path) -> None:
        bogus = tmp_path / "does-not-exist"
        rc, stdout, stderr = _run_cli(["config", "artifacts-dir", "--workdir", str(bogus)])
        assert rc == 2
        assert stdout == ""
        assert "is not a valid directory" in stderr
