"""Phase 3-d preflight: 基盤補正に対する Small/Medium テスト。

対象:
1. canonical issue id (state / artifacts / run log / prompt 一貫適用)
2. Python ``jq`` package による ``--jq`` 出力
3. branch_prefix validation / slug 導出
4. ``atomic_write_new`` の short write 耐性

設計参照: ``draft/design/local-mode/phase3d-preflight-design.md``。
#448 で local provider を削除したため、local provider 固有のテスト
（frontmatter / comment retry / dirname identity 等）は削除済。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.commands.output import _format_jq_results
from kaji_harness.config import KajiConfig
from kaji_harness.providers.context import (
    derive_slug_from_title,
    validate_branch_prefix,
)

_GITHUB_PROVIDER_SECTION = (
    '\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "owner/name"\n'
)

# ============================================================
# Helpers
# ============================================================


def _write_repo(tmp_path: Path, *, provider_section: str = "") -> Path:
    """``.kaji/config.toml`` を持つ最小 repo を tmp_path 下に作る。

    ``resolve_issue_context`` の worktree path 解決が本物の git repo を前提とするため、
    ``git init`` 済の状態にしておく。
    """
    repo = tmp_path / "repo"
    (repo / ".kaji").mkdir(parents=True)
    (repo / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n" + provider_section
    )
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main", str(repo)],
        check=True,
    )
    return repo


def _make_runner(repo: Path, issue: str):
    from kaji_harness.runner import WorkflowRunner
    from kaji_harness.workflow import load_workflow

    wf_path = repo / "wf.yaml"
    wf_path.write_text(
        "name: t\ndescription: t\nexecution_policy: auto\n"
        "steps:\n  - id: s\n    skill: x\n    agent: claude\n"
        "    on:\n      PASS: end\n      ABORT: end\n"
    )
    cfg = KajiConfig.discover(start_dir=repo)
    return WorkflowRunner(
        workflow=load_workflow(wf_path),
        issue_number=issue,
        project_root=cfg.repo_root,
        artifacts_dir=cfg.artifacts_dir,
        config=cfg,
    )


# ============================================================
# 1. canonical issue id
# ============================================================


@pytest.mark.medium
class TestCanonicalIssueId:
    """``kaji run ... 1`` / ``... gh:1`` が同じ canonical id に解決される。"""

    @pytest.mark.parametrize("input_id", ["1", "gh:1"])
    @pytest.mark.usefixtures("stub_github_issue")
    def test_input_forms_resolve_to_same_canonical(self, tmp_path: Path, input_id: str) -> None:
        repo = _write_repo(tmp_path, provider_section=_GITHUB_PROVIDER_SECTION)
        runner = _make_runner(repo, input_id)
        run_ctx = runner._resolve_run_issue_context()
        assert run_ctx.canonical_id == "1"
        assert run_ctx.issue_ref == "#1"
        assert run_ctx.input_id == input_id
        assert run_ctx.issue_context is not None
        assert run_ctx.issue_context.issue_id == "1"

    def test_no_provider_section_raises_resolution_error(self, tmp_path: Path) -> None:
        """Phase 3-e: `[provider]` 未設定は fail-fast。canonical id は確定しない。"""
        from kaji_harness.errors import IssueContextResolutionError

        repo = _write_repo(tmp_path)
        runner = _make_runner(repo, "42")
        with pytest.raises(IssueContextResolutionError):
            runner._resolve_run_issue_context()

    @pytest.mark.usefixtures("stub_github_issue")
    def test_legacy_raw_artifacts_dir_emits_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """raw id 側に既存 artifacts directory がある場合、canonical を使いつつ WARN を出す。"""
        repo = _write_repo(tmp_path, provider_section=_GITHUB_PROVIDER_SECTION)
        # 旧 raw id ベース artifacts directory を再現
        artifacts = repo / ".kaji-artifacts"
        (artifacts / "gh:1").mkdir(parents=True)
        runner = _make_runner(repo, "gh:1")
        run_ctx = runner._resolve_run_issue_context()
        assert run_ctx.canonical_id == "1"
        captured = capsys.readouterr()
        assert "legacy artifact directory" in captured.err
        assert "'gh:1'" in captured.err and "'1'" in captured.err

    @pytest.mark.usefixtures("stub_github_issue")
    def test_run_persists_state_under_canonical_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``run()`` 完了後、state / progress.md は canonical id directory に書かれる。"""
        from kaji_harness.providers.github import GitHubProvider

        monkeypatch.setattr(GitHubProvider, "resolve_pr_context", lambda self, branch: None)
        repo = _write_repo(tmp_path, provider_section=_GITHUB_PROVIDER_SECTION)
        runner = _make_runner(repo, "gh:1")  # raw 入力
        # CLI 実行を mock：1 step 走らせて即 PASS で終了。Issue #220 以降 runner は
        # parse_verdict を直接呼ばず resolve_verdict 経由で artifact→comment→stdout を
        # 解決するため、stdout に valid な PASS block を含めて stdout 経路で解決させる。
        from kaji_harness.models import CLIResult

        with patch("kaji_harness.runner.execute_cli") as mock_exec:
            mock_exec.return_value = CLIResult(
                full_output="---VERDICT---\nstatus: PASS\nreason: ok\nevidence: ok\n"
                "suggestion: ok\n---END_VERDICT---",
                session_id=None,
                cost=None,
            )
            with patch("kaji_harness.runner.validate_skill_exists"):
                runner.run()
        assert runner.canonical_issue_id == "1"
        assert runner.canonical_issue_ref == "#1"
        # canonical dir に state が書かれる、raw dir には書かれない
        assert (repo / ".kaji-artifacts" / "1" / "session-state.json").exists()
        assert not (repo / ".kaji-artifacts" / "gh:1").exists()


# ============================================================
# 2. Python jq output formatting
# ============================================================


@pytest.mark.small
class TestJqOutputFormatting:
    """``_format_jq_results`` が ``gh --jq`` 互換 raw output を生成する。"""

    def test_string_value(self) -> None:
        assert _format_jq_results(["hello"]) == "hello\n"

    def test_string_with_newline(self) -> None:
        # 文字列内改行はそのまま、末尾に newline を 1 つ追加
        assert _format_jq_results(["line1\nline2"]) == "line1\nline2\n"

    def test_number_value(self) -> None:
        assert _format_jq_results([42]) == "42\n"
        assert _format_jq_results([3.5]) == "3.5\n"

    def test_boolean_value(self) -> None:
        assert _format_jq_results([True]) == "true\n"
        assert _format_jq_results([False]) == "false\n"

    def test_null_value_emits_blank_line(self) -> None:
        # null は空行（newline のみ）
        assert _format_jq_results([None]) == "\n"

    def test_object_emits_compact_json(self) -> None:
        assert _format_jq_results([{"a": 1, "b": "x"}]) == '{"a":1,"b":"x"}\n'

    def test_array_emits_compact_json(self) -> None:
        assert _format_jq_results([[1, 2, 3]]) == "[1,2,3]\n"

    def test_string_stream_newline_separated(self) -> None:
        assert _format_jq_results(["a", "b", "c"]) == "a\nb\nc\n"

    def test_stream_with_null_emits_blank_lines(self) -> None:
        # 例: 1, null, 2 → "1\n\n2\n"
        assert _format_jq_results([1, None, 2]) == "1\n\n2\n"

    def test_empty_stream_emits_no_output(self) -> None:
        assert _format_jq_results([]) == ""


@pytest.mark.small
class TestApplyJqExitCodes:
    """``_apply_jq`` が syntax / runtime error を ``EXIT_RUNTIME_ERROR`` にする。"""

    def test_syntax_error_returns_exit_3(self) -> None:
        from kaji_harness.commands.exit_codes import EXIT_RUNTIME_ERROR
        from kaji_harness.commands.output import _apply_jq

        out, rc = _apply_jq("{}", "..invalid..")
        assert rc == EXIT_RUNTIME_ERROR
        assert out == ""

    def test_runtime_error_returns_exit_3(self) -> None:
        from kaji_harness.commands.exit_codes import EXIT_RUNTIME_ERROR
        from kaji_harness.commands.output import _apply_jq

        # 数値に対して .foo は jq runtime error
        out, rc = _apply_jq("123", ".foo")
        assert rc == EXIT_RUNTIME_ERROR
        assert out == ""

    def test_invalid_input_json_returns_exit_3(self) -> None:
        from kaji_harness.commands.exit_codes import EXIT_RUNTIME_ERROR
        from kaji_harness.commands.output import _apply_jq

        out, rc = _apply_jq("not json", ".body")
        assert rc == EXIT_RUNTIME_ERROR
        assert out == ""


@pytest.mark.small
class TestBranchPrefixValidation:
    """``validate_branch_prefix`` の許容 / 拒否パターン。"""

    @pytest.mark.parametrize(
        "prefix", ["feat", "fix", "refactor", "docs", "test", "chore", "perf", "security"]
    )
    def test_known_prefixes_accepted(self, prefix: str) -> None:
        validate_branch_prefix(prefix)  # no raise

    @pytest.mark.parametrize("prefix", ["feature", "bugfix", "weird", "FEAT", "feat/foo", ""])
    def test_unknown_prefixes_rejected(self, prefix: str) -> None:
        with pytest.raises(ValueError, match="invalid branch_prefix"):
            validate_branch_prefix(prefix)


# ============================================================
# 4. slug derivation
# ============================================================


@pytest.mark.small
class TestSlugDerivation:
    """``derive_slug_from_title`` の fallback 動作。"""

    def test_derive_slug_from_title_basic(self) -> None:
        assert derive_slug_from_title("Hello World") == "hello-world"

    def test_derive_slug_from_punctuation_only_returns_untitled(self) -> None:
        assert derive_slug_from_title("!!!") == "untitled"


# ============================================================
# 5. atomic_write_new short write
# ============================================================


@pytest.mark.small
class TestAtomicWriteNewShortWrite:
    """phase3d-preflight review Finding 4: ``os.write`` short write 耐性。"""

    def test_loops_until_all_bytes_written(self, tmp_path: Path) -> None:
        """``os.write`` が要求 byte 数より少ない値を返す状況で全量書ききる。"""
        from kaji_harness import fsio

        target = tmp_path / "out.md"
        full_payload = b"hello world\n" * 100  # 1200 bytes
        real_write = fsio.os.write

        # 各呼び出しで先頭 7 bytes だけ書く偽 os.write
        def short_write(fd: int, data: bytes) -> int:
            chunk = data[:7]
            return real_write(fd, chunk)

        with patch.object(fsio.os, "write", side_effect=short_write):
            fsio.atomic_write_new(target, full_payload.decode("utf-8"))
        assert target.read_bytes() == full_payload

    def test_non_positive_return_raises(self, tmp_path: Path) -> None:
        """``os.write`` が 0 を返したら防御的に OSError を投げる。"""
        from kaji_harness import fsio

        target = tmp_path / "out.md"
        with patch.object(fsio.os, "write", return_value=0):
            with pytest.raises(OSError, match="non-positive"):
                fsio.atomic_write_new(target, "hello")
