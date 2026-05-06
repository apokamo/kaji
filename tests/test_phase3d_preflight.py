"""Phase 3-d preflight: 5 つの基盤補正に対する Small/Medium テスト。

対象:
1. canonical issue id (state / artifacts / run log / prompt 一貫適用)
2. Python ``jq`` package による ``--jq`` 出力
3. PyYAML frontmatter (inline list / null / quote の round-trip)
4. ``--slug`` optional 化
5. comment 書き込みの ``O_CREAT|O_EXCL`` retry

設計参照: ``draft/design/local-mode/phase3d-preflight-design.md``。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.cli_main import _format_jq_results, _handle_issue
from kaji_harness.config import KajiConfig
from kaji_harness.providers.context import (
    derive_slug_from_title,
    validate_branch_prefix,
)
from kaji_harness.providers.local import (
    MAX_COMMENT_WRITE_RETRIES,
    LocalProvider,
    LocalProviderError,
    _parse_frontmatter,
    _serialize_frontmatter,
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
    """``kaji run ... 1`` / ``... pc1-1`` / ``... local-pc1-1`` が同じ canonical id。"""

    @pytest.mark.parametrize("input_id", ["1", "pc1-1", "local-pc1-1"])
    def test_local_input_forms_resolve_to_same_canonical(
        self, tmp_path: Path, input_id: str
    ) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
            ),
        )
        provider = LocalProvider(repo_root=repo, machine_id="pc1")
        provider.create_issue(title="Hello", body="b", labels=["type:feature"], slug="hello")
        runner = _make_runner(repo, input_id)
        run_ctx = runner._resolve_run_issue_context()
        assert run_ctx.canonical_id == "local-pc1-1"
        assert run_ctx.issue_ref == "local-pc1-1"
        assert run_ctx.input_id == input_id
        assert run_ctx.issue_context is not None
        assert run_ctx.issue_context.issue_id == "local-pc1-1"

    def test_no_provider_fallback_uses_raw_input_as_canonical(self, tmp_path: Path) -> None:
        """``[provider]`` 未設定の Phase 2-B 互換経路では raw 入力をそのまま canonical 扱い。"""
        import kaji_harness.providers as providers_pkg

        providers_pkg._PROVIDER_FALLBACK_WARNED = False
        repo = _write_repo(tmp_path)
        runner = _make_runner(repo, "42")
        run_ctx = runner._resolve_run_issue_context()
        assert run_ctx.canonical_id == "42"
        assert run_ctx.issue_ref == "#42"
        assert run_ctx.issue_context is None

    def test_legacy_raw_artifacts_dir_emits_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """raw id 側に既存 artifacts directory がある場合、canonical を使いつつ WARN を出す。"""
        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
            ),
        )
        provider = LocalProvider(repo_root=repo, machine_id="pc1")
        provider.create_issue(title="x", body="b", slug="x")
        # 旧 raw id ベース artifacts directory を再現
        artifacts = repo / ".kaji-artifacts"
        (artifacts / "1").mkdir(parents=True)
        runner = _make_runner(repo, "1")
        run_ctx = runner._resolve_run_issue_context()
        assert run_ctx.canonical_id == "local-pc1-1"
        captured = capsys.readouterr()
        assert "legacy artifact directory" in captured.err
        assert "1" in captured.err and "local-pc1-1" in captured.err

    def test_run_persists_state_under_canonical_dir(self, tmp_path: Path) -> None:
        """``run()`` 完了後、state / progress.md は canonical id directory に書かれる。"""
        from kaji_harness.models import Verdict

        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
            ),
        )
        provider = LocalProvider(repo_root=repo, machine_id="pc1")
        provider.create_issue(title="x", body="b", slug="x", labels=["type:feature"])
        runner = _make_runner(repo, "1")  # raw 入力
        # CLI 実行を mock：1 step 走らせて即 PASS で終了
        from kaji_harness.models import CLIResult

        with patch("kaji_harness.runner.execute_cli") as mock_exec:
            mock_exec.return_value = CLIResult(
                full_output="---VERDICT---\nstatus: PASS\nreason: ok\nevidence: ok\n"
                "suggestion: ok\n---END_VERDICT---",
                session_id=None,
                cost=None,
            )
            with patch("kaji_harness.runner.parse_verdict") as mock_v:
                mock_v.return_value = Verdict("PASS", "ok", "ok", "ok")
                with patch("kaji_harness.runner.validate_skill_exists"):
                    runner.run()
        assert runner.canonical_issue_id == "local-pc1-1"
        assert runner.canonical_issue_ref == "local-pc1-1"
        # canonical dir に state が書かれる、raw dir には書かれない
        assert (repo / ".kaji-artifacts" / "local-pc1-1" / "session-state.json").exists()
        assert not (repo / ".kaji-artifacts" / "1").exists()


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
        from kaji_harness.cli_main import EXIT_RUNTIME_ERROR, _apply_jq

        out, rc = _apply_jq("{}", "..invalid..")
        assert rc == EXIT_RUNTIME_ERROR
        assert out == ""

    def test_runtime_error_returns_exit_3(self) -> None:
        from kaji_harness.cli_main import EXIT_RUNTIME_ERROR, _apply_jq

        # 数値に対して .foo は jq runtime error
        out, rc = _apply_jq("123", ".foo")
        assert rc == EXIT_RUNTIME_ERROR
        assert out == ""

    def test_invalid_input_json_returns_exit_3(self) -> None:
        from kaji_harness.cli_main import EXIT_RUNTIME_ERROR, _apply_jq

        out, rc = _apply_jq("not json", ".body")
        assert rc == EXIT_RUNTIME_ERROR
        assert out == ""


# ============================================================
# 3. PyYAML frontmatter round-trip
# ============================================================


@pytest.mark.small
class TestPyYamlFrontmatter:
    """PyYAML 採用後の semantic round-trip / inline list / null / quote。"""

    def test_inline_list_labels_round_trip(self) -> None:
        """``labels: [type:feature, area:harness]`` の inline list が読める。"""
        text = (
            "---\nid: local-pc1-1\nstate: open\nlabels: [type:feature, area:harness]\n"
            "slug: foo\n---\nbody\n"
        )
        meta, body = _parse_frontmatter(text)
        assert meta["labels"] == ["type:feature", "area:harness"]
        assert body == "body\n"

    def test_null_value_round_trip(self) -> None:
        """``closed_at: null`` が None として読める。"""
        text = "---\nid: local-pc1-1\nstate: open\nslug: x\nclosed_at: null\n---\nbody\n"
        meta, _ = _parse_frontmatter(text)
        assert meta["closed_at"] is None

    def test_title_with_quote_round_trip(self) -> None:
        meta = {"id": "local-pc1-1", "state": "open", "slug": "x", "title": 'Add "foo" support'}
        text = f"---\n{_serialize_frontmatter(meta)}---\nbody\n"
        parsed, _ = _parse_frontmatter(text)
        assert parsed["title"] == 'Add "foo" support'

    def test_title_with_colon_round_trip(self) -> None:
        meta = {"id": "local-pc1-1", "state": "open", "slug": "x", "title": "A: tricky value"}
        text = f"---\n{_serialize_frontmatter(meta)}---\nbody\n"
        parsed, _ = _parse_frontmatter(text)
        assert parsed["title"] == "A: tricky value"

    def test_invalid_yaml_raises(self) -> None:
        with pytest.raises(LocalProviderError, match="invalid YAML frontmatter"):
            _parse_frontmatter("---\n: : : :\n---\nbody\n")

    def test_non_mapping_yaml_raises(self) -> None:
        with pytest.raises(LocalProviderError, match="must be a YAML mapping"):
            _parse_frontmatter("---\n- a\n- b\n---\nbody\n")


@pytest.mark.medium
class TestResolveContextValidation:
    """``resolve_issue_context`` が invalid frontmatter を fail-fast する。"""

    def _make_provider(self, tmp_path: Path) -> LocalProvider:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".kaji").mkdir()
        return LocalProvider(repo_root=repo, machine_id="pc1")

    def test_invalid_slug_in_frontmatter_fails(self, tmp_path: Path) -> None:
        provider = self._make_provider(tmp_path)
        d = provider.repo_root / ".kaji" / "issues" / "local-pc1-9"
        d.mkdir(parents=True)
        (d / "issue.md").write_text(
            "---\nid: local-pc1-9\nstate: open\nslug: 'Bad Slug'\n---\nbody\n"
        )
        with pytest.raises(LocalProviderError, match="slug"):
            provider.resolve_issue_context("local-pc1-9")

    def test_invalid_branch_prefix_in_frontmatter_fails(self, tmp_path: Path) -> None:
        provider = self._make_provider(tmp_path)
        d = provider.repo_root / ".kaji" / "issues" / "local-pc1-9-x"
        d.mkdir(parents=True)
        (d / "issue.md").write_text(
            "---\nid: local-pc1-9\nstate: open\nslug: x\nbranch_prefix: weirdo\n---\nbody\n"
        )
        with pytest.raises(LocalProviderError, match="branch_prefix"):
            provider.resolve_issue_context("local-pc1-9")


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
# 4. slug optional
# ============================================================


@pytest.mark.medium
class TestSlugOptional:
    """``--slug`` 省略時の derive_slug_from_title fallback と CLI 経由動作。"""

    def test_derive_slug_from_title_basic(self) -> None:
        assert derive_slug_from_title("Hello World") == "hello-world"

    def test_derive_slug_from_punctuation_only_returns_untitled(self) -> None:
        assert derive_slug_from_title("!!!") == "untitled"

    def test_cli_create_without_slug_derives_from_title(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
            ),
        )
        monkeypatch.chdir(repo)
        rc = _handle_issue(["create", "--title", "Hello World", "--body", "b"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert out == "local-pc1-1"
        # directory が <id>-<derived-slug> 形式
        issue_dir = repo / ".kaji" / "issues" / "local-pc1-1-hello-world"
        assert issue_dir.is_dir()

    def test_cli_create_with_explicit_slug_overrides_derived(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _write_repo(
            tmp_path,
            provider_section=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
            ),
        )
        monkeypatch.chdir(repo)
        rc = _handle_issue(["create", "--title", "Hello", "--slug", "custom-name", "--body", "b"])
        assert rc == 0
        capsys.readouterr()
        issue_dir = repo / ".kaji" / "issues" / "local-pc1-1-custom-name"
        assert issue_dir.is_dir()


# ============================================================
# 5. comment write retry
# ============================================================


@pytest.mark.medium
class TestCommentWriteRetry:
    """``comment_issue`` が ``O_CREAT|O_EXCL`` 失敗時に seq を再採番して retry。"""

    @pytest.fixture
    def provider(self, tmp_path: Path) -> LocalProvider:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".kaji").mkdir()
        prov = LocalProvider(repo_root=repo, machine_id="pc1")
        prov.create_issue(title="t", body="b", slug="t")
        return prov

    def test_retries_on_existing_filename(self, provider: LocalProvider) -> None:
        """``0001-pc1.md`` が既に存在する場合、``0002-pc1.md`` に retry する。"""
        issue_dir = provider._resolve_issue_dir("local-pc1-1")
        cdir = issue_dir / "comments"
        cdir.mkdir()
        # 競合を装う: 別 process が seq=1 を先に書いたとして手動配置
        (cdir / "0001-pc1.md").write_text("---\nauthor: pc1\n---\npre-existing\n")
        c = provider.comment_issue("local-pc1-1", "second")
        assert c.seq == "0002"
        assert (cdir / "0002-pc1.md").is_file()
        # 既存ファイルは上書きされていない
        assert "pre-existing" in (cdir / "0001-pc1.md").read_text()

    def test_fails_fast_after_retry_limit(self, provider: LocalProvider) -> None:
        """retry 上限を超えると ``LocalProviderError`` で停止する。"""
        # 永続的に FileExistsError を返す mock で耐久試験
        from kaji_harness.providers import local as _local_mod

        with patch.object(_local_mod, "_atomic_write_new", side_effect=FileExistsError):
            with pytest.raises(LocalProviderError, match="failed to allocate"):
                provider.comment_issue("local-pc1-1", "x")

    def test_max_retries_constant_is_eight(self) -> None:
        """設計書 § 5: ``MAX_COMMENT_WRITE_RETRIES = 8``。"""
        assert MAX_COMMENT_WRITE_RETRIES == 8
