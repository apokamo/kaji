"""Phase 3-c: cli_main の dispatcher / config parsing 検証。

PR-3c のスコープのうち、``kaji issue`` / ``kaji pr`` の dispatch 経路に
対応するテスト。``kaji run`` 経由の IssueContext 解決と prompt 注入は
``tests/test_phase3c_runner.py`` を参照。

カバー範囲:

- ``KajiConfig`` が ``[provider]`` セクションを optional に parse できる
- ``providers.get_provider`` の routing（github / local / 未設定 fallback）
- ``cli_main._handle_issue`` の dispatch（local provider 経路 + フラグ）
- ``cli_main._forward_to_gh`` の ``--repo`` 強制注入

phase3-design.md § 4 ロールアウト戦略 PR-3c に対応。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.cli_main import _handle_issue
from kaji_harness.config import KajiConfig
from kaji_harness.providers import (
    GitHubProvider,
    LocalProvider,
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

    def test_local_provider_with_overlay(self, tmp_path: Path) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\ndefault_branch = "main"\n'
            ),
        )
        # config.local.toml で machine_id を上書き注入する
        (repo / ".kaji" / "config.local.toml").write_text('[provider.local]\nmachine_id = "pc1"\n')
        cfg = KajiConfig.discover(start_dir=repo)
        assert cfg.provider is not None
        assert cfg.provider.type == "local"
        assert cfg.provider.local.machine_id == "pc1"
        assert cfg.provider.local.default_branch == "main"

    def test_invalid_provider_type_rejected(self, tmp_path: Path) -> None:
        from kaji_harness.errors import ConfigLoadError

        repo = _write_repo(
            tmp_path,
            provider_section='\n[provider]\ntype = "gitlab"\n',
        )
        with pytest.raises(ConfigLoadError):
            KajiConfig.discover(start_dir=repo)


# ============================================================
# get_provider: routing
# ============================================================


@pytest.mark.medium
class TestGetProviderRouting:
    def test_no_provider_returns_none_with_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # WARN は process-wide flag で 1 度しか出ないため、テスト前に reset
        import kaji_harness.providers as providers_pkg

        providers_pkg._PROVIDER_FALLBACK_WARNED = False
        repo = _write_repo(tmp_path)
        cfg = KajiConfig.discover(start_dir=repo)
        provider = get_provider(cfg)
        assert provider is None
        captured = capsys.readouterr()
        assert "[provider]" in captured.err
        assert "fallback" in captured.err.lower()

    def test_github_provider_routing(self, tmp_path: Path) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=('\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'),
        )
        cfg = KajiConfig.discover(start_dir=repo)
        provider = get_provider(cfg)
        assert isinstance(provider, GitHubProvider)
        assert provider.repo == "o/r"

    def test_local_provider_routing(self, tmp_path: Path) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n'
                '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
            ),
        )
        cfg = KajiConfig.discover(start_dir=repo)
        provider = get_provider(cfg)
        assert isinstance(provider, LocalProvider)
        assert provider.machine_id == "pc1"

    def test_local_without_machine_id_raises(self, tmp_path: Path) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section='\n[provider]\ntype = "local"\n',
        )
        cfg = KajiConfig.discover(start_dir=repo)
        with pytest.raises(ValueError, match="machine_id"):
            get_provider(cfg)

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
    def test_no_provider_falls_back_to_gh_passthrough(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_repo(tmp_path)
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["view", "42"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["gh", "issue", "view"]

    def test_github_provider_routes_to_passthrough(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=('\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'),
        )
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["view", "42", "--json", "title"])
        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["gh", "issue", "view"]
        assert "--json" in cmd  # passthrough は引数を保持する

    def test_local_provider_view_dispatches_to_local_handler(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
            ),
        )
        monkeypatch.chdir(repo)
        # local issue を 1 件作る
        from kaji_harness.providers import LocalProvider as _LP

        provider = _LP(repo_root=repo, machine_id="pc1")
        provider.create_issue(
            title="Hello", body="body text", labels=["type:feature"], slug="hello-test"
        )
        # `kaji issue view local-pc1-1` が gh を呼ばずに local 経由で動く
        with patch("kaji_harness.cli_main.subprocess.run") as mock_run:
            rc = _handle_issue(["view", "local-pc1-1"])
        assert rc == 0
        mock_run.assert_not_called()  # gh は叩かれない
        captured = capsys.readouterr()
        assert "Hello" in captured.out
        assert "body text" in captured.out

    def test_local_provider_create_and_close(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
            ),
        )
        monkeypatch.chdir(repo)
        rc = _handle_issue(
            [
                "create",
                "--title",
                "Test",
                "--body",
                "body",
                "--slug",
                "test-issue",
                "--label",
                "type:bug",
            ]
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert "local-pc1-1" in captured.out

        rc = _handle_issue(["close", "local-pc1-1", "--reason", "completed"])
        assert rc == 0
        # 確認: state が closed に
        from kaji_harness.providers import LocalProvider as _LP

        provider = _LP(repo_root=repo, machine_id="pc1")
        issue = provider.view_issue("local-pc1-1")
        assert issue.state == "closed"


# ============================================================
# Review fixes: normalize_id 経由の id 解決 + write/read 分離
# ============================================================


@pytest.fixture()
def local_repo(tmp_path: Path) -> Path:
    """provider=local + machine_id=pc1 の最小 repo + Issue 1 件付き fixture。"""
    repo = _write_repo(
        tmp_path,
        provider_section=('\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'),
    )
    provider = LocalProvider(repo_root=repo, machine_id="pc1")
    provider.create_issue(
        title="Hello", body="body text", labels=["type:feature"], slug="hello-test"
    )
    return repo


@pytest.mark.medium
class TestLocalDispatcherIdNormalization:
    """``_handle_issue_local`` が ``normalize_id`` 経由で全 id 形式を扱う。"""

    def test_numeric_id_resolves_with_machine_id(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(local_repo)
        # `kaji issue view 1` → local-pc1-1 として解決される
        rc = _handle_issue(["view", "1"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Hello" in captured.out

    def test_short_form_id_resolves(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(local_repo)
        rc = _handle_issue(["view", "pc1-1"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Hello" in captured.out

    def test_full_local_id_resolves(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(local_repo)
        rc = _handle_issue(["view", "local-pc1-1"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Hello" in captured.out

    def test_gh_prefix_routes_to_remote_cache(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``gh:N`` は cache JSON 経路。cache 不在は明示エラーで exit 3。"""
        monkeypatch.chdir(local_repo)
        rc = _handle_issue(["view", "gh:153"])
        assert rc == 3  # IssueNotFoundError → EXIT_RUNTIME_ERROR
        captured = capsys.readouterr()
        assert "no cached issue" in captured.err.lower() or "cache" in captured.err.lower()

    def test_gh_prefix_with_cache_returns_issue(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """cache を投入してから ``gh:N`` が read-only に view される。"""
        cache_dir = local_repo / ".kaji" / "cache" / "issues"
        cache_dir.mkdir(parents=True)
        (cache_dir / "153.json").write_text(
            '{"number": 153, "title": "Cached", "body": "cached body", '
            '"state": "open", "labels": [], "comments": []}'
        )
        monkeypatch.chdir(local_repo)
        rc = _handle_issue(["view", "gh:153"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Cached" in captured.out
        assert "cached body" in captured.out

    def test_gh_prefix_write_rejected_as_readonly(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``gh:N`` への write 系（edit / comment / close）は exit 2。"""
        monkeypatch.chdir(local_repo)
        rc = _handle_issue(["edit", "gh:153", "--body", "x"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "read-only" in captured.err.lower() or "cannot modify" in captured.err.lower()

        rc = _handle_issue(["close", "gh:153"])
        assert rc == 2

        rc = _handle_issue(["comment", "gh:153", "--body", "x"])
        assert rc == 2

    def test_invalid_id_returns_exit_2(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(local_repo)
        rc = _handle_issue(["view", "Bogus-ID"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "invalid issue id" in captured.err.lower()


@pytest.mark.medium
class TestLocalDispatcherFlags:
    """Skill が依存する CLI フラグを LocalProvider 経由で受理する。"""

    def test_view_json_with_jq_emits_raw_string(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``-q '.body'`` は ``gh --jq`` 互換で **quote 無し** raw 出力する。

        Skill が ``CURRENT_BODY=$(kaji issue view N --json body -q '.body')``
        の形で shell 変数に代入するため、``"body text"`` のような quote
        付き出力では下流が壊れる。jq の ``-r`` モード採用を構造的に検証。
        """
        if not __import__("shutil").which("jq"):
            pytest.skip("jq not installed in CI environment")
        monkeypatch.chdir(local_repo)
        rc = _handle_issue(["view", "1", "--json", "body", "-q", ".body"])
        assert rc == 0
        captured = capsys.readouterr()
        # gh --jq 互換: raw string、末尾に改行 1 つのみ
        assert captured.out == "body text\n"
        # 構造的にも quote が混入していないことを assert（regression guard）
        assert '"' not in captured.out
        assert "'" not in captured.out

    def test_view_jq_array_result_keeps_json_format(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """array / object 結果は ``-r`` でも JSON のまま（gh と同じ挙動）。"""
        if not __import__("shutil").which("jq"):
            pytest.skip("jq not installed in CI environment")
        monkeypatch.chdir(local_repo)
        rc = _handle_issue(["view", "1", "--json", "labels", "--jq", "[.labels[].name]"])
        assert rc == 0
        captured = capsys.readouterr()
        # array は JSON 形式（quoted strings の配列）。型を厳密に確認する
        import json as _json

        parsed = _json.loads(captured.out)
        assert parsed == ["type:feature"]

    def test_view_jq_string_stream_emits_raw_lines(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """string ストリーム ``.labels[].name`` は raw 行で並ぶ（gh 互換）。"""
        if not __import__("shutil").which("jq"):
            pytest.skip("jq not installed in CI environment")
        monkeypatch.chdir(local_repo)
        # 追加 label を 1 つ足してストリームの確認を厚くする
        from kaji_harness.providers import LocalProvider as _LP

        provider = _LP(repo_root=local_repo, machine_id="pc1")
        provider.edit_issue("local-pc1-1", add_labels=["type:bug"])
        rc = _handle_issue(["view", "1", "--json", "labels", "--jq", ".labels[].name"])
        assert rc == 0
        captured = capsys.readouterr()
        # 各 label が独立行・quote 無し
        lines = captured.out.splitlines()
        assert lines == ["type:feature", "type:bug"]

    def test_view_jq_without_json_emits_raw_string(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``--jq`` 単独でも full Issue JSON を入力に raw 結果を返す。"""
        if not __import__("shutil").which("jq"):
            pytest.skip("jq not installed in CI environment")
        monkeypatch.chdir(local_repo)
        rc = _handle_issue(["view", "1", "--jq", ".title"])
        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out == "Hello\n"
        assert '"' not in captured.out

    def test_view_jq_shell_capture_round_trip(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """実際の subshell 経由（``$(...)``）の用途を端から端まで再現する。

        Skill ``issue-start`` / ``i-pr`` / ``_shared/worktree-resolve`` が
        ``CURRENT_BODY=$(kaji issue view ... --json body -q '.body')`` を
        使っている。capsys 経由のテストでは検出しきれない subshell 取り込み
        の挙動（末尾改行剥ぎ落とし含む）を、subprocess + ``$()`` 等価の
        ``str.rstrip("\\n")`` で再現する。
        """
        if not __import__("shutil").which("jq"):
            pytest.skip("jq not installed in CI environment")
        import subprocess
        import sys as _sys

        monkeypatch.chdir(local_repo)
        proc = subprocess.run(
            [
                _sys.executable,
                "-m",
                "kaji_harness.cli_main",
                "issue",
                "view",
                "1",
                "--json",
                "body",
                "-q",
                ".body",
            ],
            cwd=local_repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        # subshell 取り込み相当: 末尾改行を剥ぐ → 純 raw string
        captured = proc.stdout.rstrip("\n")
        assert captured == "body text"

    def test_view_jq_unavailable_emits_runtime_error(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``jq`` 不在環境では exit 3 + ガイダンス（pyproject 非依存契約）。"""
        monkeypatch.chdir(local_repo)
        with patch(
            "kaji_harness.cli_main.shutil.which",
            side_effect=lambda name: None if name == "jq" else "/usr/bin/" + name,
        ):
            rc = _handle_issue(["view", "1", "--json", "body", "-q", ".body"])
        assert rc == 3
        captured = capsys.readouterr()
        assert "jq" in captured.err.lower()

    def test_view_with_comments_flag(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # コメントを 1 件付ける
        provider = LocalProvider(repo_root=local_repo, machine_id="pc1")
        provider.comment_issue("local-pc1-1", "first comment body")

        monkeypatch.chdir(local_repo)
        rc = _handle_issue(["view", "1", "--comments"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "Hello" in captured.out
        assert "first comment body" in captured.out
        assert "pc1" in captured.out  # コメント author = machine_id

    def test_create_with_body_file(
        self,
        local_repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        body_file = tmp_path / "body.md"
        body_file.write_text("body from file\n## section")
        monkeypatch.chdir(local_repo)
        rc = _handle_issue(
            [
                "create",
                "--title",
                "T",
                "--body-file",
                str(body_file),
                "--slug",
                "from-file",
            ]
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert "local-pc1-2" in captured.out  # local-pc1-1 は fixture が消費済み
        # 内容検証
        provider = LocalProvider(repo_root=local_repo, machine_id="pc1")
        issue = provider.view_issue("local-pc1-2")
        assert "body from file" in issue.body
        assert "## section" in issue.body

    def test_comment_with_body_file_stdin(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``--body-file -`` で stdin からコメント本文を読む。"""
        import io

        monkeypatch.chdir(local_repo)
        monkeypatch.setattr("sys.stdin", io.StringIO("comment from stdin"))
        rc = _handle_issue(["comment", "1", "--body-file", "-"])
        assert rc == 0
        provider = LocalProvider(repo_root=local_repo, machine_id="pc1")
        issue = provider.view_issue("local-pc1-1")
        assert any("comment from stdin" in c.body for c in issue.comments)

    def test_body_and_body_file_are_mutually_exclusive(
        self,
        local_repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        body_file = tmp_path / "b.md"
        body_file.write_text("x")
        monkeypatch.chdir(local_repo)
        rc = _handle_issue(
            [
                "create",
                "--title",
                "T",
                "--body",
                "inline",
                "--body-file",
                str(body_file),
                "--slug",
                "x-y",
            ]
        )
        assert rc == 2
        captured = capsys.readouterr()
        assert "mutually exclusive" in captured.err.lower()

    def test_list_with_jq_filter(
        self,
        local_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        if not __import__("shutil").which("jq"):
            pytest.skip("jq not installed in CI environment")
        monkeypatch.chdir(local_repo)
        rc = _handle_issue(
            ["list", "--state", "open", "--json", "labels", "--jq", ".[0].labels[0].name"]
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert "type:feature" in captured.out


@pytest.mark.medium
class TestDispatcherFailFastOnConfig:
    """``ConfigLoadError`` / ``get_provider`` の ValueError は fail-fast。"""

    def test_invalid_provider_type_yields_exit_2_not_silent_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``type=gitlab`` のような明示設定ミスは gh fallback せず exit 2。"""
        repo = _write_repo(tmp_path, provider_section='\n[provider]\ntype = "gitlab"\n')
        monkeypatch.chdir(repo)
        with patch("kaji_harness.cli_main.subprocess.run") as mock_run:
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
        with patch("kaji_harness.cli_main.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            rc = _handle_issue(["view", "1"])
        assert rc == 2
        mock_run.assert_not_called()

    def test_local_missing_machine_id_yields_exit_2(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``type=local`` で machine_id 不在は traceback ではなく exit 2。"""
        repo = _write_repo(tmp_path, provider_section='\n[provider]\ntype = "local"\n')
        monkeypatch.chdir(repo)
        rc = _handle_issue(["view", "1"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "machine_id" in captured.err

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

    def test_overlay_can_switch_type_to_local(self, tmp_path: Path) -> None:
        """tracked が ``type=github`` でも overlay で ``type=local`` に切替できる。"""
        repo = _write_repo(
            tmp_path,
            provider_section=('\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "o/r"\n'),
        )
        # overlay で type と machine_id を導入
        (repo / ".kaji" / "config.local.toml").write_text(
            '[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
        )
        cfg = KajiConfig.discover(start_dir=repo)
        assert cfg.provider is not None
        assert cfg.provider.type == "local"
        assert cfg.provider.local.machine_id == "pc1"

    def test_overlay_only_introduces_provider_section(self, tmp_path: Path) -> None:
        """tracked に ``[provider]`` が無くても overlay だけで section を導入できる。"""
        repo = _write_repo(tmp_path)  # tracked は [provider] 無し
        (repo / ".kaji" / "config.local.toml").write_text(
            '[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
        )
        cfg = KajiConfig.discover(start_dir=repo)
        assert cfg.provider is not None
        assert cfg.provider.type == "local"


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
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
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
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _handle_issue(["view", "42", "--repo", "user/explicit"])
        cmd = mock_run.call_args[0][0]
        # config 由来の二重注入をしない
        assert cmd.count("--repo") == 1
        assert cmd[cmd.index("--repo") + 1] == "user/explicit"

    def test_no_provider_passthrough_does_not_inject_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``[provider]`` 未設定（legacy）では --repo を注入しない（既存挙動維持）。"""
        repo = _write_repo(tmp_path)  # provider なし
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _handle_issue(["view", "42"])
        cmd = mock_run.call_args[0][0]
        assert "--repo" not in cmd

    def test_pr_passthrough_injects_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from kaji_harness.cli_main import _handle_pr

        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "kamo/kaji"\n'
            ),
        )
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
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
        from kaji_harness.cli_main import _handle_pr

        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "kamo/kaji"\n'
            ),
        )
        monkeypatch.chdir(repo)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main.subprocess.run") as mock_run,
            # _detect_repo の auto-detect 経路（subprocess gh repo view）が
            # 呼ばれてはならない。override が機能していれば fallback しない
            patch(
                "kaji_harness.cli_main._detect_repo",
                wraps=__import__("kaji_harness.cli_main", fromlist=["_detect_repo"])._detect_repo,
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
