"""Phase 3-c: commands の dispatcher / config parsing 検証。

PR-3c のスコープのうち、``kaji issue`` / ``kaji pr`` の dispatch 経路に
対応するテスト。``kaji run`` 経由の IssueContext 解決と prompt 注入は
``tests/test_runner.py`` を参照。

カバー範囲:

- ``KajiConfig`` が ``[provider]`` セクションを optional に parse できる
- ``providers.get_provider`` の routing（github / 未設定 fail-fast）
- ``commands.issue._handle_issue`` の dispatch（gh passthrough + ``--commit`` 剥離）
- ``commands.pr._forward_to_gh`` の ``--repo`` 強制注入

phase3-design.md § 4 ロールアウト戦略 PR-3c に対応。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.commands.issue import _handle_issue
from kaji_harness.commands.pr import _user_specified_repo
from kaji_harness.config import KajiConfig
from kaji_harness.providers import (
    GitHubProvider,
    get_provider,
)

# ============================================================
# Helpers
# ============================================================


def _write_repo(tmp_path: Path, *, provider_section: str = "") -> Path:
    """``.kaji/config.toml`` を持つ最小 repo を tmp_path 下に作る。"""
    repo = tmp_path / "repo"
    (repo / ".kaji").mkdir(parents=True)
    (repo / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n" + provider_section
    )
    return repo


# ============================================================
# Config: [provider] section parsing
# ============================================================


@pytest.mark.medium
class TestProviderConfigParsing:
    def test_no_provider_section_yields_none(self, tmp_path: Path) -> None:
        repo = _write_repo(tmp_path)
        cfg = KajiConfig.discover(start_dir=repo)
        assert cfg.provider is None

    def test_github_provider_parsed(self, tmp_path: Path) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "kamo/kaji"\n'
            ),
        )
        cfg = KajiConfig.discover(start_dir=repo)
        assert cfg.provider is not None
        assert cfg.provider.type == "github"
        assert cfg.provider.github.repo == "kamo/kaji"

    def test_invalid_provider_type_rejected(self, tmp_path: Path) -> None:
        from kaji_harness.errors import ConfigLoadError

        repo = _write_repo(
            tmp_path,
            provider_section='\n[provider]\ntype = "bitbucket"\n',
        )
        with pytest.raises(ConfigLoadError):
            KajiConfig.discover(start_dir=repo)

    def test_removed_local_provider_type_rejected(self, tmp_path: Path) -> None:
        """#448: 削除済み ``provider.type = "local"`` は ``ProviderTypeRemovedError``。"""
        from kaji_harness.errors import ProviderTypeRemovedError

        repo = _write_repo(
            tmp_path,
            provider_section='\n[provider]\ntype = "local"\n',
        )
        with pytest.raises(ProviderTypeRemovedError):
            KajiConfig.discover(start_dir=repo)


# ============================================================
# get_provider: routing
# ============================================================


@pytest.mark.medium
class TestGetProviderRouting:
    def test_no_provider_raises_value_error(self, tmp_path: Path) -> None:
        """Phase 3-e: `[provider]` 不在は WARN ではなく fail-fast (ValueError)。"""
        repo = _write_repo(tmp_path)
        cfg = KajiConfig.discover(start_dir=repo)
        with pytest.raises(ValueError, match=r"\[provider\] section is required"):
            get_provider(cfg)

    def test_github_provider_routing(self, tmp_path: Path) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=('\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'),
        )
        cfg = KajiConfig.discover(start_dir=repo)
        provider = get_provider(cfg)
        assert isinstance(provider, GitHubProvider)
        assert provider.repo == "o/r"

    def test_github_without_repo_raises(self, tmp_path: Path) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section='\n[provider]\ntype = "github"\n',
        )
        cfg = KajiConfig.discover(start_dir=repo)
        with pytest.raises(ValueError, match="repo"):
            get_provider(cfg)


# ============================================================
# cli_main: _handle_issue dispatch
# ============================================================


@pytest.mark.medium
class TestHandleIssueDispatch:
    def test_no_provider_section_fails_fast_exit_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Phase 3-e: `[provider]` 不在で `kaji issue` は exit 2 で stop し、gh は呼ばない。"""
        repo = _write_repo(tmp_path)
        monkeypatch.chdir(repo)
        with patch("subprocess.run") as mock_run:
            rc = _handle_issue(["view", "42"])
        assert rc == 2
        mock_run.assert_not_called()
        captured = capsys.readouterr()
        assert "[provider]" in captured.err

    def test_github_provider_routes_to_passthrough(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=('\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'),
        )
        monkeypatch.chdir(repo)
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["view", "42", "--json", "title"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["gh", "issue", "view"]
        assert "--json" in cmd  # passthrough は引数を保持する

    def test_github_provider_strips_noop_commit_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """github mode forwards to gh without `--commit`.

        `--commit` は既存 skill 呼び出しとの互換のため受理する no-op で、
        silent に剥がして gh に forward する（gh CLI に渡ると unknown flag で fail する）。
        """
        repo = _write_repo(
            tmp_path,
            provider_section=('\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'),
        )
        monkeypatch.chdir(repo)
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["comment", "42", "--body", "x", "--commit"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert "--commit" not in cmd
        assert cmd[:3] == ["gh", "issue", "comment"]


@pytest.mark.medium
class TestDispatcherFailFastOnConfig:
    """``ConfigLoadError`` / ``get_provider`` の ValueError は fail-fast。"""

    def test_invalid_provider_type_yields_exit_2_not_silent_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``type=gitlab`` のような撤去済 / 未知 type は gh fallback せず exit 2。

        bridging test (Issue #191 撤去後): GitLab forge 撤去後、
        ``provider.type='gitlab'`` 設定値は ConfigLoadError として fail-fast
        する。silent な GitHub fallback は踏まない。
        """
        repo = _write_repo(tmp_path, provider_section='\n[provider]\ntype = "gitlab"\n')
        monkeypatch.chdir(repo)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["view", "1"])
        assert rc == 2
        mock_run.assert_not_called()  # gh fallback を踏まないことを構造で検証
        captured = capsys.readouterr()
        assert "provider.type" in captured.err

    def test_broken_toml_yields_exit_2(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """壊れた TOML は gh fallback せず exit 2。"""
        repo = tmp_path / "repo"
        (repo / ".kaji").mkdir(parents=True)
        (repo / ".kaji" / "config.toml").write_text("not = a [valid TOML\n")
        monkeypatch.chdir(repo)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["view", "1"])
        assert rc == 2
        mock_run.assert_not_called()

    def test_removed_local_type_yields_exit_2(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """#448: 削除済み ``type=local`` は traceback / gh fallback ではなく exit 2。"""
        repo = _write_repo(
            tmp_path,
            provider_section='\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n',
        )
        monkeypatch.chdir(repo)
        with patch("subprocess.run") as mock_run:
            rc = _handle_issue(["view", "1"])
        assert rc == 2
        mock_run.assert_not_called()
        captured = capsys.readouterr()
        assert "provider.type" in captured.err

    def test_github_missing_repo_yields_exit_2(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``type=github`` で repo 不在は traceback ではなく exit 2。"""
        repo = _write_repo(tmp_path, provider_section='\n[provider]\ntype = "github"\n')
        monkeypatch.chdir(repo)
        rc = _handle_issue(["view", "1"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "repo" in captured.err.lower()


@pytest.mark.medium
class TestConfigLocalOverlayProviderType:
    """`config.local.toml` の ``[provider]`` 全体 overlay（review #4）。"""

    def test_overlay_switching_type_to_local_is_rejected(self, tmp_path: Path) -> None:
        """#448: tracked が ``type=github`` でも overlay の ``type=local`` は拒否される。"""
        from kaji_harness.errors import ProviderTypeRemovedError

        repo = _write_repo(
            tmp_path,
            provider_section=('\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'),
        )
        (repo / ".kaji" / "config.local.toml").write_text(
            '[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
        )
        with pytest.raises(ProviderTypeRemovedError):
            KajiConfig.discover(start_dir=repo)

    def test_overlay_only_introduces_provider_section(self, tmp_path: Path) -> None:
        """tracked に ``[provider]`` が無くても overlay だけで section を導入できる。"""
        repo = _write_repo(tmp_path)  # tracked は [provider] 無し
        (repo / ".kaji" / "config.local.toml").write_text(
            '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'
        )
        cfg = KajiConfig.discover(start_dir=repo)
        assert cfg.provider is not None
        assert cfg.provider.type == "github"
        assert cfg.provider.github.repo == "o/r"


# ============================================================
# rev #3: GitHubProvider 経路で --repo を強制注入
# ============================================================


@pytest.mark.medium
class TestForwardToGhRepoInjection:
    """review #3: ``[provider.github] repo`` を ``--repo`` で gh に伝搬する。"""

    def test_github_provider_passthrough_injects_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "kamo/kaji"\n'
            ),
        )
        monkeypatch.chdir(repo)
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["view", "42"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        # --repo が必ず注入される（位置は末尾追加）
        assert "--repo" in cmd
        assert cmd[cmd.index("--repo") + 1] == "kamo/kaji"

    def test_user_repo_flag_takes_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """user が明示 ``--repo`` を渡した場合は config を上書きしない。"""
        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "kamo/kaji"\n'
            ),
        )
        monkeypatch.chdir(repo)
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _handle_issue(["view", "42", "--repo", "user/explicit"])
        cmd = mock_run.call_args[0][0]
        # config 由来の二重注入をしない
        assert cmd.count("--repo") == 1
        assert cmd[cmd.index("--repo") + 1] == "user/explicit"

    def test_no_provider_section_does_not_invoke_gh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase 3-e: `[provider]` 未設定では fail-fast し、gh subprocess を呼ばない。"""
        repo = _write_repo(tmp_path)  # provider なし
        monkeypatch.chdir(repo)
        with patch("subprocess.run") as mock_run:
            rc = _handle_issue(["view", "42"])
        assert rc == 2
        mock_run.assert_not_called()

    def test_pr_passthrough_injects_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from kaji_harness.commands.pr import _handle_pr

        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "kamo/kaji"\n'
            ),
        )
        monkeypatch.chdir(repo)
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_pr(["view", "153"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert "--repo" in cmd
        assert cmd[cmd.index("--repo") + 1] == "kamo/kaji"

    def test_pr_review_comments_uses_config_repo_not_detect_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """builtin (review-comments) も config repo を尊重し、cwd 推論を使わない。"""
        from kaji_harness.commands.pr import _handle_pr

        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "kamo/kaji"\n'
            ),
        )
        monkeypatch.chdir(repo)
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_run,
            # _detect_repo の auto-detect 経路（subprocess gh repo view）が
            # 呼ばれてはならない。override が機能していれば fallback しない
            patch(
                "kaji_harness.commands.pr._detect_repo",
                wraps=__import__(
                    "kaji_harness.commands.pr", fromlist=["_detect_repo"]
                )._detect_repo,
            ) as spy_detect,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _handle_pr(["review-comments", "153"])
        # _detect_repo が呼ばれたことは構わないが、override 経由で auto-detect
        # subprocess を踏まないことを構造的に確認: 呼ばれた gh subprocess は
        # `gh api repos/kamo/kaji/pulls/...` のみ
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["gh", "api", "repos/kamo/kaji/pulls/153/comments"]
        # spy には override="kamo/kaji" で呼ばれたはず
        spy_detect.assert_called_once_with(override="kamo/kaji")


# ============================================================
# Issue #172: _user_specified_repo helper — pflag 5 形式の検出
# ============================================================


@pytest.mark.small
class TestUserSpecifiedRepo:
    """pflag が受理する `--repo` / `-R` の全形式を検出することを確認する。"""

    def test_standalone_long(self) -> None:
        assert _user_specified_repo(["pr", "list", "--repo", "o/n"]) is True

    def test_standalone_short(self) -> None:
        assert _user_specified_repo(["pr", "list", "-R", "o/n"]) is True

    def test_inline_long(self) -> None:
        assert _user_specified_repo(["pr", "list", "--repo=o/n"]) is True

    def test_inline_short_with_equals(self) -> None:
        assert _user_specified_repo(["pr", "list", "-R=o/n"]) is True

    def test_short_concatenated(self) -> None:
        assert _user_specified_repo(["pr", "list", "-Ro/n"]) is True

    def test_unspecified(self) -> None:
        assert _user_specified_repo(["pr", "list"]) is False

    def test_repository_is_not_matched(self) -> None:
        """`--repository` は `gh` が受理しない別フラグ。誤検出しない。"""
        assert _user_specified_repo(["--repository", "o/n"]) is False


# ============================================================
# Issue #172: gh インライン形式の二重 --repo 注入回帰テスト
# ============================================================


def _count_repo_tokens(cmd: list[str]) -> tuple[int, int]:
    """(--repo 系の token 総数, -R 系の token 総数) を prefix 集計で返す。

    standalone `--repo` / inline `--repo=...` を区別せず prefix で数え、
    config 由来注入 (= `--repo` 単独 token 追加) があれば総数が 2 になる。
    """
    repo_tokens = sum(1 for c in cmd if c.startswith("--repo"))
    r_tokens = sum(1 for c in cmd if c.startswith("-R") and not c.startswith("--repo"))
    return repo_tokens, r_tokens


@pytest.mark.medium
class TestForwardToGhRepoInjectionInline:
    """Issue #172: pflag インライン形式の user `--repo` を尊重する（gh）。"""

    @pytest.fixture()
    def repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        r = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "kamo/kaji"\n'
            ),
        )
        monkeypatch.chdir(r)
        return r

    def test_inline_long_repo_not_double_injected(self, repo: Path) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _handle_issue(["view", "42", "--repo=user/explicit"])
        cmd = mock_run.call_args[0][0]
        repo_tokens, r_tokens = _count_repo_tokens(cmd)
        # user の `--repo=user/explicit` のみ。config 由来 standalone `--repo` は追加されない
        assert repo_tokens == 1
        assert r_tokens == 0
        assert "--repo=user/explicit" in cmd
        assert "--repo" not in cmd  # standalone token は無い

    def test_inline_short_with_equals_not_double_injected(self, repo: Path) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _handle_issue(["view", "42", "-R=user/explicit"])
        cmd = mock_run.call_args[0][0]
        repo_tokens, r_tokens = _count_repo_tokens(cmd)
        assert repo_tokens == 0
        assert r_tokens == 1
        assert "-R=user/explicit" in cmd
        assert "-R" not in cmd
        assert "--repo" not in cmd

    def test_short_concatenated_not_double_injected(self, repo: Path) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _handle_issue(["view", "42", "-Ruser/explicit"])
        cmd = mock_run.call_args[0][0]
        repo_tokens, r_tokens = _count_repo_tokens(cmd)
        assert repo_tokens == 0
        assert r_tokens == 1
        assert "-Ruser/explicit" in cmd
        assert "-R" not in cmd
        assert "--repo" not in cmd
