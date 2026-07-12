"""cli_main の characterization test（Issue #282 / R0）。

後続リファクタ（#283 = R1、``kaji_harness/commands/`` への分割）の safety net。
着手時 main の ``kaji_harness/cli_main.py`` の主要正常系・主要エラー分岐について、
現挙動を写し取って固定する。既存 test が未カバーだった関数・分岐を優先対象とする。

分割耐性（R1-robust）方針:
    subprocess 分岐を検証する unit では ``kaji_harness.cli_main.subprocess`` の名前空間
    patch を **新規に導入しない**。cli_main が常に stdlib ``subprocess`` module を import
    する事実に依存し、stdlib 側（``subprocess.run`` / ``shutil.which``）を patch する。
    これは object identity 経由で解決されるため、対象関数が別 module へ移っても届く。
    その結果、本 file は ``scripts/inventory_cli_main_patch_targets.sh`` が数える
    ``kaji_harness.cli_main.<symbol>`` patch target を 1 件も増やさない（棚卸し baseline
    を汚さない）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kaji_harness.cli_main import (
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    _compose_json_and_jq,
    _detect_repo,
    _forward_to_gh,
    _gh_capture_value,
    _has_approve_flag,
    _has_request_changes_flag,
    _has_verdict_flags,
    _is_ascii_decimal,
    _resolve_project_root_for_validate,
    _resolve_recover_issue_context,
    _resolve_target_run_dir,
    _resolve_verdict_marker,
)
from kaji_harness.providers.markers import build_kaji_verdict_marker


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    """subprocess.run の戻り値スタブ（returncode / stdout / stderr のみ参照される）。"""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# _is_ascii_decimal — 純粋分岐（既存直接 test 無し。unicode digit 拒否契約を固定）
# ---------------------------------------------------------------------------
class TestIsAsciiDecimal:
    @pytest.mark.small
    def test_ascii_decimal_true(self) -> None:
        assert _is_ascii_decimal("123") is True
        assert _is_ascii_decimal("0") is True

    @pytest.mark.small
    def test_empty_is_false(self) -> None:
        assert _is_ascii_decimal("") is False

    @pytest.mark.small
    def test_unicode_digits_rejected(self) -> None:
        # str.isdigit() は全角数字を True にするが、本関数は ASCII のみ許可する。
        assert _is_ascii_decimal("１２３") is False

    @pytest.mark.small
    def test_non_digit_false(self) -> None:
        assert _is_ascii_decimal("12a") is False
        assert _is_ascii_decimal("-1") is False
        assert _is_ascii_decimal(" 1") is False


# ---------------------------------------------------------------------------
# _resolve_verdict_marker — verdict フラグ解決（両必須 / 片方は ValueError）
# ---------------------------------------------------------------------------
class TestResolveVerdictMarker:
    @pytest.mark.small
    def test_both_none_returns_none(self) -> None:
        assert _resolve_verdict_marker(None, None) is None

    @pytest.mark.small
    def test_both_given_returns_marker(self) -> None:
        # 現挙動: build_kaji_verdict_marker の戻り値をそのまま返す。
        assert _resolve_verdict_marker("implement", "PASS") == build_kaji_verdict_marker(
            "implement", "PASS"
        )

    @pytest.mark.small
    def test_step_only_raises(self) -> None:
        with pytest.raises(ValueError, match="must be specified together"):
            _resolve_verdict_marker("implement", None)

    @pytest.mark.small
    def test_status_only_raises(self) -> None:
        with pytest.raises(ValueError, match="must be specified together"):
            _resolve_verdict_marker(None, "PASS")


# ---------------------------------------------------------------------------
# _detect_repo — override 優先 / gh repo view 経路（stdlib subprocess patch）
# ---------------------------------------------------------------------------
class TestDetectRepo:
    @pytest.mark.small
    def test_override_short_circuits(self) -> None:
        # override があれば subprocess を一切呼ばない。
        with patch("subprocess.run") as mock_run:
            assert _detect_repo(override="owner/name") == "owner/name"
        mock_run.assert_not_called()

    @pytest.mark.small
    def test_gh_success_returns_stripped_repo(self) -> None:
        with patch("subprocess.run", return_value=_completed(0, stdout="owner/name\n")):
            assert _detect_repo() == "owner/name"

    @pytest.mark.small
    def test_gh_nonzero_returns_none(self) -> None:
        with patch("subprocess.run", return_value=_completed(1, stdout="", stderr="boom")):
            assert _detect_repo() is None

    @pytest.mark.small
    def test_gh_empty_stdout_returns_none(self) -> None:
        with patch("subprocess.run", return_value=_completed(0, stdout="  \n")):
            assert _detect_repo() is None

    @pytest.mark.small
    def test_oserror_returns_none(self) -> None:
        with patch("subprocess.run", side_effect=OSError("gh not runnable")):
            assert _detect_repo() is None


# ---------------------------------------------------------------------------
# _forward_to_gh — gh 転送 wrapper（merge flag 強制 / --repo 注入 / エラー分岐）
# ---------------------------------------------------------------------------
class TestForwardToGh:
    @pytest.mark.small
    def test_gh_missing_returns_runtime_error(self) -> None:
        with patch("shutil.which", return_value=None):
            assert _forward_to_gh("issue", ["list"]) == EXIT_RUNTIME_ERROR

    @pytest.mark.small
    def test_plain_forward_passes_cmd_and_returns_code(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run", return_value=_completed(0)) as mock_run,
        ):
            rc = _forward_to_gh("issue", ["list", "--state", "open"])
        assert rc == 0
        assert mock_run.call_args[0][0] == ["gh", "issue", "list", "--state", "open"]

    @pytest.mark.small
    def test_leading_double_dash_stripped(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run", return_value=_completed(0)) as mock_run,
        ):
            _forward_to_gh("issue", ["--", "view", "1"])
        assert mock_run.call_args[0][0] == ["gh", "issue", "view", "1"]

    @pytest.mark.small
    def test_pr_merge_strips_method_flags_and_forces_merge(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run", return_value=_completed(0)) as mock_run,
        ):
            _forward_to_gh("pr", ["merge", "123", "--squash", "--rebase"])
        # method flag（--squash/--rebase/--merge）は除去され、常に末尾 --merge を強制。
        assert mock_run.call_args[0][0] == ["gh", "pr", "merge", "123", "--merge"]

    @pytest.mark.small
    def test_repo_injected_when_absent(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run", return_value=_completed(0)) as mock_run,
        ):
            _forward_to_gh("pr", ["view", "1"], repo="owner/name")
        assert mock_run.call_args[0][0] == ["gh", "pr", "view", "1", "--repo", "owner/name"]

    @pytest.mark.small
    def test_repo_not_injected_when_user_specified(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run", return_value=_completed(0)) as mock_run,
        ):
            _forward_to_gh("pr", ["view", "1", "--repo", "user/own"], repo="owner/name")
        assert mock_run.call_args[0][0] == ["gh", "pr", "view", "1", "--repo", "user/own"]

    @pytest.mark.small
    def test_returncode_passthrough(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run", return_value=_completed(5)),
        ):
            assert _forward_to_gh("issue", ["list"]) == 5

    @pytest.mark.small
    def test_oserror_returns_runtime_error(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/bin/gh"),
            patch("subprocess.run", side_effect=OSError("cannot exec")),
        ):
            assert _forward_to_gh("issue", ["list"]) == EXIT_RUNTIME_ERROR


# ---------------------------------------------------------------------------
# _gh_capture_value — gh capture wrapper（rc0→stdout / rc≠0→None / OSError→None）
# ---------------------------------------------------------------------------
class TestGhCaptureValue:
    @pytest.mark.small
    def test_success_returns_stripped_stdout(self) -> None:
        with patch("subprocess.run", return_value=_completed(0, stdout="apokamo\n")):
            assert _gh_capture_value(["api", "user"]) == "apokamo"

    @pytest.mark.small
    def test_nonzero_returns_none_and_relays_stderr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("subprocess.run", return_value=_completed(1, stderr="gh: not found\n")):
            assert _gh_capture_value(["api", "x"]) is None
        assert "gh: not found" in capsys.readouterr().err

    @pytest.mark.small
    def test_oserror_returns_none(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("subprocess.run", side_effect=OSError("boom")):
            assert _gh_capture_value(["api", "x"]) is None
        assert "failed to invoke 'gh'" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _resolve_recover_issue_context — normalize_id → provider.resolve_issue_context
# ---------------------------------------------------------------------------
class TestResolveRecoverIssueContext:
    @pytest.mark.small
    def test_github_numeric_resolves_via_provider(self) -> None:
        config = SimpleNamespace(provider=SimpleNamespace(type="github"))
        sentinel = object()
        provider = MagicMock()
        provider.resolve_issue_context.return_value = sentinel

        result = _resolve_recover_issue_context(config, provider, "282")

        assert result is sentinel
        # github numeric は normalize_id で value="282" に正規化されて provider へ渡る。
        provider.resolve_issue_context.assert_called_once_with("282")

    @pytest.mark.small
    def test_github_rejects_local_form_id(self) -> None:
        config = SimpleNamespace(provider=SimpleNamespace(type="github"))
        provider = MagicMock()
        # provider.type='github' で local-form id は normalize_id が ValueError を送出する。
        with pytest.raises(ValueError):
            _resolve_recover_issue_context(config, provider, "local-pc1-3")
        provider.resolve_issue_context.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_project_root_for_validate — 明示 root / pyproject 探索 / fallback
# ---------------------------------------------------------------------------
class TestResolveProjectRootForValidate:
    @pytest.mark.small
    def test_explicit_root_wins(self, tmp_path: Path) -> None:
        explicit = tmp_path / "root"
        explicit.mkdir()
        yaml_path = tmp_path / "wf.yaml"
        assert _resolve_project_root_for_validate(explicit, yaml_path) == explicit.resolve()

    @pytest.mark.medium
    def test_walks_up_to_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        yaml_path = nested / "wf.yaml"
        yaml_path.write_text("name: x\n", encoding="utf-8")
        assert _resolve_project_root_for_validate(None, yaml_path) == tmp_path.resolve()

    @pytest.mark.medium
    def test_falls_back_to_yaml_parent(self, tmp_path: Path) -> None:
        # .kaji/config.toml も pyproject.toml も無い場合、YAML の親 dir を返す。
        yaml_dir = tmp_path / "only"
        yaml_dir.mkdir()
        yaml_path = yaml_dir / "wf.yaml"
        yaml_path.write_text("name: x\n", encoding="utf-8")
        assert _resolve_project_root_for_validate(None, yaml_path) == yaml_dir.resolve()


# ---------------------------------------------------------------------------
# _resolve_target_run_dir — recover 対象 run の解決（fs I/O。既存直接 test 無し）
# ---------------------------------------------------------------------------
def _write_run(run_dir: Path, events: list[dict[str, object]] | None) -> None:
    """run_dir を作り、events を run.log(JSONL) に書く。events=None なら run.log 無し。"""
    run_dir.mkdir(parents=True, exist_ok=True)
    if events is not None:
        lines = "\n".join(json.dumps(e) for e in events)
        (run_dir / "run.log").write_text(lines + "\n", encoding="utf-8")


class TestResolveTargetRunDir:
    @pytest.mark.medium
    def test_runs_dir_missing_returns_none(self, tmp_path: Path) -> None:
        assert _resolve_target_run_dir(tmp_path / "nope", None) is None

    @pytest.mark.medium
    def test_explicit_run_id_missing_returns_none(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        runs.mkdir()
        assert _resolve_target_run_dir(runs, "missing") is None

    @pytest.mark.medium
    def test_no_candidates_returns_none(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        runs.mkdir()
        assert _resolve_target_run_dir(runs, None) is None

    @pytest.mark.medium
    def test_run_log_missing_returns_none(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        _write_run(runs / "r1", events=None)  # run dir だが run.log 無し
        assert _resolve_target_run_dir(runs, "r1") is None

    @pytest.mark.medium
    def test_in_progress_run_refused(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        # workflow_end event が無い = 進行中とみなし拒否。
        _write_run(runs / "r1", events=[{"event": "workflow_start"}])
        assert _resolve_target_run_dir(runs, "r1") is None

    @pytest.mark.medium
    def test_non_error_status_refused(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        _write_run(runs / "r1", events=[{"event": "workflow_end", "status": "SUCCESS"}])
        assert _resolve_target_run_dir(runs, "r1") is None

    @pytest.mark.medium
    def test_error_run_resolved(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        target = runs / "r1"
        _write_run(target, events=[{"event": "workflow_end", "status": "ERROR"}])
        assert _resolve_target_run_dir(runs, "r1") == target

    @pytest.mark.medium
    def test_abort_run_resolved(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        target = runs / "r1"
        _write_run(target, events=[{"event": "workflow_end", "status": "ABORT"}])
        assert _resolve_target_run_dir(runs, "r1") == target

    @pytest.mark.medium
    def test_latest_candidate_picked_when_run_id_none(self, tmp_path: Path) -> None:
        runs = tmp_path / "runs"
        _write_run(runs / "20260101", events=[{"event": "workflow_end", "status": "ERROR"}])
        latest = runs / "20260202"
        _write_run(latest, events=[{"event": "workflow_end", "status": "ERROR"}])
        # sorted の末尾（辞書順最大）が選ばれる。
        assert _resolve_target_run_dir(runs, None) == latest

    @pytest.mark.medium
    def test_returns_ok_exit_constant_symbol_sanity(self) -> None:
        # EXIT_OK が 0 である現挙動を固定（recover 経路の成功コード契約）。
        assert EXIT_OK == 0


# ---------------------------------------------------------------------------
# _has_approve_flag / _has_request_changes_flag — gh pr review flag pre-scan
# （設計 §テスト戦略 Small が明記する純粋分岐。`--` 以降の positional 無視契約を固定）
# ---------------------------------------------------------------------------
class TestHasApproveFlag:
    @pytest.mark.small
    def test_long_short_and_inline_forms_true(self) -> None:
        assert _has_approve_flag(["--approve"]) is True
        assert _has_approve_flag(["-a"]) is True
        assert _has_approve_flag(["--approve=maybe"]) is True

    @pytest.mark.small
    def test_absent_is_false(self) -> None:
        assert _has_approve_flag([]) is False
        assert _has_approve_flag(["--body", "x"]) is False

    @pytest.mark.small
    def test_double_dash_stops_scan(self) -> None:
        # ``--`` 以降は positional 扱いで無視する。
        assert _has_approve_flag(["--", "--approve"]) is False


class TestHasRequestChangesFlag:
    @pytest.mark.small
    def test_long_short_and_inline_forms_true(self) -> None:
        assert _has_request_changes_flag(["--request-changes"]) is True
        assert _has_request_changes_flag(["-r"]) is True
        assert _has_request_changes_flag(["--request-changes=x"]) is True

    @pytest.mark.small
    def test_absent_is_false(self) -> None:
        assert _has_request_changes_flag(["--approve"]) is False

    @pytest.mark.small
    def test_double_dash_stops_scan(self) -> None:
        assert _has_request_changes_flag(["--", "-r"]) is False


# ---------------------------------------------------------------------------
# _has_verdict_flags — verdict marker フラグ検出（--flag=value 形式も検出）
# ---------------------------------------------------------------------------
class TestHasVerdictFlags:
    @pytest.mark.small
    def test_bare_flags_true(self) -> None:
        assert _has_verdict_flags(["--verdict-step", "implement"]) is True
        assert _has_verdict_flags(["--verdict-status", "PASS"]) is True

    @pytest.mark.small
    def test_inline_equals_form_true(self) -> None:
        assert _has_verdict_flags(["--verdict-step=implement"]) is True
        assert _has_verdict_flags(["--verdict-status=PASS"]) is True

    @pytest.mark.small
    def test_absent_is_false(self) -> None:
        assert _has_verdict_flags(["--body", "x", "--commit"]) is False


# ---------------------------------------------------------------------------
# _compose_json_and_jq — --json FIELDS / --jq EXPR の合成（4 分岐）
# ---------------------------------------------------------------------------
class TestComposeJsonAndJq:
    @pytest.mark.small
    def test_neither_returns_none(self) -> None:
        assert _compose_json_and_jq(None, None) is None

    @pytest.mark.small
    def test_fields_only_projects(self) -> None:
        assert _compose_json_and_jq(["a", "b"], None) == "[.[] | {a: .a, b: .b}]"

    @pytest.mark.small
    def test_jq_only_passthrough(self) -> None:
        assert _compose_json_and_jq(None, ".foo") == ".foo"

    @pytest.mark.small
    def test_both_chained(self) -> None:
        assert _compose_json_and_jq(["a"], "length") == "[.[] | {a: .a}] | length"
