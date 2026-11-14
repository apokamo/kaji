"""Issue #568: agent step の verdict を検証・直列化・原子的に公開する共通処理のテスト。

agent が ``verdict.yaml`` を手書きすると、字下げ 1 文字の誤りで副作用完了後に判定だけが
失われる（#551 run ``261113020532`` の ``start``、incident #565）。本ファイルは次を検証する。

- Small: 手書き経路の失敗の固定、``dump_verdict_yaml`` の round-trip、入力検証、契約 model
- Medium: ``atomic_publish_new`` の原子性・上書き禁止、``publish_agent_verdict`` の状況検証、
  ``kaji verdict publish`` CLI の exit code、runner の契約書き出しと e2e（モック agent）
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kaji_harness.commands.main import main
from kaji_harness.config import KajiConfig
from kaji_harness.errors import (
    InvalidVerdictValue,
    VerdictParseError,
    VerdictPublishContextError,
    VerdictPublishInputError,
    VerdictSerializationError,
)
from kaji_harness.fsio import atomic_publish_new
from kaji_harness.models import CLIResult, Step, Verdict, Workflow
from kaji_harness.providers import Comment
from kaji_harness.providers.github import GitHubProvider
from kaji_harness.runner import WorkflowRunner
from kaji_harness.skill import SkillMetadata
from kaji_harness.verdict import (
    _parse_yaml_fields,
    dump_verdict_yaml,
    load_verdict_yaml,
    load_verdict_yaml_for_marker_vocabulary,
    normalize_verdict_fields,
    validate_verdict_fields,
    write_verdict_yaml,
)
from kaji_harness.verdict_publish import (
    VERDICT_CONTRACT_ENV,
    VERDICT_CONTRACT_FILENAME,
    VerdictContract,
    publish_agent_verdict,
    write_verdict_contract,
)

# Issue #551 run 261113020532 の start で agent が保存した verdict.yaml（evidence に余分な空白）
_ISSUE_551_HANDWRITTEN = (
    "status: PASS\n"
    "reason: |\n"
    "  Issue #551 の worktree セットアップが完了\n"
    " evidence: |\n"
    "  fetch した origin/main と FETCH_HEAD は一致した\n"
)
_ISSUE_551_REASON = "Issue #551 の worktree セットアップが完了"
_ISSUE_551_EVIDENCE = "fetch した origin/main と FETCH_HEAD は一致した"

_VALID = ["PASS", "RETRY", "BACK", "BACK_DESIGN", "ABORT"]


def _round_trip(verdict: Verdict) -> Verdict:
    return _parse_yaml_fields(dump_verdict_yaml(verdict))


# ============================================================
# Small: #551 の回帰
# ============================================================


@pytest.mark.small
class TestIssue551Regression:
    def test_handwritten_extra_indent_fails_to_parse(self, tmp_path: Path) -> None:
        """OB: 手書き経路では 1 文字の字下げ誤りで VerdictParseError になる。"""
        path = tmp_path / "verdict.yaml"
        path.write_text(_ISSUE_551_HANDWRITTEN, encoding="utf-8")

        with pytest.raises(VerdictParseError, match="expected <block end>"):
            load_verdict_yaml(path, {"PASS", "ABORT"})

    def test_common_path_round_trips_same_fields(self) -> None:
        """EB: 同じ判定フィールドを共通処理に通すと、既存 parser で同じ値に戻る。"""
        verdict = normalize_verdict_fields(
            status="PASS", reason=_ISSUE_551_REASON, evidence=_ISSUE_551_EVIDENCE, suggestion=""
        )
        validate_verdict_fields(verdict, ["PASS", "ABORT"])

        assert _round_trip(verdict) == Verdict(
            status="PASS", reason=_ISSUE_551_REASON, evidence=_ISSUE_551_EVIDENCE, suggestion=""
        )


# ============================================================
# Small: dump_verdict_yaml の round-trip
# ============================================================

_ROUND_TRIP_VALUES = [
    pytest.param("line1\nline2\nline3", id="multiline"),
    pytest.param("key: value", id="colon-space"),
    pytest.param("text # not a comment", id="hash"),
    pytest.param("it's \"quoted\" 'both'", id="quotes"),
    pytest.param("C:\\path\\to\\file \\n literal", id="backslash"),
    pytest.param("日本語の根拠と絵文字 🎉", id="unicode"),
    pytest.param("- item\n- item2", id="leading-dash"),
    pytest.param("| pipe start", id="leading-pipe"),
    pytest.param("> folded start", id="leading-gt"),
    pytest.param("before\n---\nafter", id="document-marker-line"),
    pytest.param("before\n---END_VERDICT---\nafter", id="end-verdict-line"),
    pytest.param("long " * 40, id="long-line"),
    pytest.param("first\n\n\nafter blank lines", id="blank-lines"),
    pytest.param("  indented\n    deeper\nback", id="inner-indent"),
    pytest.param("tab\there\r\ncrlf", id="tab-cr"),
    pytest.param("yes", id="yaml-bool-like"),
    pytest.param("123", id="yaml-int-like"),
    pytest.param("null", id="yaml-null-like"),
]


@pytest.mark.small
class TestDumpVerdictYamlRoundTrip:
    @pytest.mark.parametrize("value", _ROUND_TRIP_VALUES)
    def test_value_round_trips_through_existing_parser(self, value: str) -> None:
        verdict = normalize_verdict_fields(
            status="ABORT", reason=value, evidence=value, suggestion=value
        )

        assert _round_trip(verdict) == verdict

    def test_surrounding_whitespace_is_stripped_before_dump(self) -> None:
        verdict = normalize_verdict_fields(
            status=" PASS ", reason="  r  \n", evidence="\n e \n", suggestion="   "
        )

        assert verdict == Verdict(status="PASS", reason="r", evidence="e", suggestion="")
        assert _round_trip(verdict) == verdict

    def test_control_chars_are_replaced_like_the_loader(self) -> None:
        verdict = normalize_verdict_fields(
            status="PASS", reason="esc\x1b[31m", evidence="del\x7f", suggestion=""
        )

        assert verdict.reason == "esc\ufffd[31m"
        assert verdict.evidence == "del\ufffd"
        assert _round_trip(verdict) == verdict

    def test_output_is_readable_by_existing_loaders(self, tmp_path: Path) -> None:
        verdict = Verdict(status="BACK", reason="r: x", evidence="e # y", suggestion="設計へ")
        path = tmp_path / "verdict.yaml"
        path.write_text(dump_verdict_yaml(verdict), encoding="utf-8")

        assert load_verdict_yaml(path, {"PASS", "BACK"}) == verdict
        assert load_verdict_yaml_for_marker_vocabulary(path) == verdict

    def test_keys_are_in_fixed_order_without_delimiters(self) -> None:
        text = dump_verdict_yaml(Verdict(status="PASS", reason="r", evidence="e", suggestion=""))

        keys = [line.split(":", 1)[0] for line in text.splitlines() if not line.startswith(" ")]
        assert keys == ["status", "reason", "evidence", "suggestion"]
        assert "---VERDICT---" not in text


@pytest.mark.small
class TestDumpVerdictYamlNonLfLineBreaks:
    """review-design MF1: U+0085 / U+2028 / U+2029 を含む値も内容を保持する。"""

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("line\x85next", id="nel"),
            pytest.param("line\u2028next", id="ls"),
            pytest.param("line\u2029next", id="ps"),
            pytest.param("first\nmid\x85dle\nlast", id="nel-in-multiline"),
            pytest.param("日本語\x85日本語", id="nel-between-japanese"),
        ],
    )
    def test_value_is_preserved(self, value: str) -> None:
        verdict = Verdict(status="PASS", reason=value, evidence="e", suggestion="")

        assert _round_trip(verdict) == verdict

    def test_japanese_only_value_is_not_escaped(self) -> None:
        text = dump_verdict_yaml(
            Verdict(status="PASS", reason="日本語の理由", evidence="根拠", suggestion="")
        )

        assert "日本語の理由" in text
        assert "\\u" not in text


@pytest.mark.small
class TestDumpVerdictYamlFallback:
    def test_falls_back_to_ascii_escape_when_readable_form_breaks(self) -> None:
        verdict = Verdict(status="PASS", reason="日本語", evidence="e", suggestion="")
        real_render: Callable[..., str] = __import__(
            "kaji_harness.verdict", fromlist=["_render_verdict_yaml"]
        )._render_verdict_yaml

        def broken_readable(v: Verdict, *, allow_unicode: bool) -> str:
            text = real_render(v, allow_unicode=allow_unicode)
            return text.replace("日本語", "壊れた") if allow_unicode else text

        with patch("kaji_harness.verdict._render_verdict_yaml", side_effect=broken_readable):
            text = dump_verdict_yaml(verdict)

        assert "\\u" in text
        assert _parse_yaml_fields(text) == verdict

    def test_raises_when_no_rendering_round_trips(self) -> None:
        verdict = Verdict(status="PASS", reason="r", evidence="e", suggestion="")

        with (
            patch(
                "kaji_harness.verdict._render_verdict_yaml",
                return_value="status: PASS\nreason: other\nevidence: e\n",
            ),
            pytest.raises(VerdictSerializationError),
        ):
            dump_verdict_yaml(verdict)


# ============================================================
# Small: 入力検証
# ============================================================


@pytest.mark.small
class TestValidateVerdictFields:
    def test_status_outside_on_keys_is_rejected(self) -> None:
        """marker 文法上は正しい RETRY でも on: {PASS, ABORT} にはない。"""
        verdict = Verdict(status="RETRY", reason="r", evidence="e", suggestion="")

        with pytest.raises(InvalidVerdictValue):
            validate_verdict_fields(verdict, ["PASS", "ABORT"])

    def test_status_is_case_sensitive(self) -> None:
        verdict = Verdict(status="pass", reason="r", evidence="e", suggestion="")

        with pytest.raises(InvalidVerdictValue):
            validate_verdict_fields(verdict, ["PASS"])

    @pytest.mark.parametrize("field", ["reason", "evidence"])
    def test_blank_required_field_is_rejected(self, field: str) -> None:
        fields = {"status": "PASS", "reason": "r", "evidence": "e", "suggestion": ""}
        fields[field] = "   \n "
        verdict = normalize_verdict_fields(**fields)

        with pytest.raises(VerdictParseError, match=field):
            validate_verdict_fields(verdict, _VALID)

    @pytest.mark.parametrize("status", ["ABORT", "BACK", "BACK_DESIGN"])
    def test_abort_and_back_require_suggestion(self, status: str) -> None:
        verdict = Verdict(status=status, reason="r", evidence="e", suggestion="")

        with pytest.raises(VerdictParseError, match="suggestion"):
            validate_verdict_fields(verdict, _VALID)

    @pytest.mark.parametrize("status", ["PASS", "RETRY"])
    def test_pass_and_retry_accept_empty_suggestion(self, status: str) -> None:
        validate_verdict_fields(
            Verdict(status=status, reason="r", evidence="e", suggestion=""), _VALID
        )


# ============================================================
# Small: 契約 model
# ============================================================


def _contract_dict(attempt_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "issue_id": "568",
        "run_id": "261114152607",
        "step_id": "design",
        "attempt": 1,
        "verdict_path": str(attempt_dir / "verdict.yaml"),
        "valid_statuses": ["PASS", "ABORT"],
    }


@pytest.mark.small
class TestVerdictContractModel:
    def test_valid_contract(self, tmp_path: Path) -> None:
        contract = VerdictContract.model_validate_json(json.dumps(_contract_dict(tmp_path)))

        assert contract.valid_statuses == ["PASS", "ABORT"]

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            pytest.param("schema_version", 2, id="schema-version"),
            pytest.param("valid_statuses", [], id="empty-statuses"),
            pytest.param("verdict_path", "relative/verdict.yaml", id="relative-path"),
            pytest.param("verdict_path", "/abs/other.yaml", id="wrong-name"),
            pytest.param("attempt", 0, id="attempt-zero"),
            pytest.param("attempt", "1", id="attempt-string"),
        ],
    )
    def test_invalid_contract_is_rejected(self, tmp_path: Path, key: str, value: object) -> None:
        data = _contract_dict(tmp_path)
        data[key] = value

        with pytest.raises(ValueError):
            VerdictContract.model_validate_json(json.dumps(data))

    def test_extra_key_is_rejected(self, tmp_path: Path) -> None:
        data = _contract_dict(tmp_path)
        data["extra"] = "x"

        with pytest.raises(ValueError):
            VerdictContract.model_validate_json(json.dumps(data))


# ============================================================
# Medium: atomic_publish_new
# ============================================================


def _tmp_leftovers(directory: Path) -> list[Path]:
    return [p for p in directory.iterdir() if p.name.endswith(".tmp")]


@pytest.mark.medium
class TestAtomicPublishNew:
    def test_link_sees_complete_temp_file_and_no_destination(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        content = dump_verdict_yaml(Verdict(status="PASS", reason="r", evidence="e", suggestion=""))
        observed: dict[str, Any] = {}
        real_link = os.link

        def spy_link(src: Any, dst: Any) -> None:
            observed["destination_existed"] = Path(dst).exists()
            observed["temp_name"] = Path(src).name
            observed["temp_parsed"] = _parse_yaml_fields(Path(src).read_text(encoding="utf-8"))
            real_link(src, dst)

        with patch("kaji_harness.fsio.os.link", side_effect=spy_link):
            outcome = atomic_publish_new(path, content)

        assert observed["destination_existed"] is False
        assert observed["temp_name"] != "verdict.yaml"
        assert observed["temp_parsed"].status == "PASS"
        assert path.read_text(encoding="utf-8") == content
        assert outcome.cleanup_error is None
        assert _tmp_leftovers(tmp_path) == []

    def test_existing_destination_is_not_overwritten(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        path.write_text("original\n", encoding="utf-8")

        with pytest.raises(FileExistsError):
            atomic_publish_new(path, "new\n")

        assert path.read_text(encoding="utf-8") == "original\n"
        assert _tmp_leftovers(tmp_path) == []

    def test_link_failure_leaves_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"

        with (
            patch("kaji_harness.fsio.os.link", side_effect=OSError(errno.EPERM, "no hard link")),
            pytest.raises(OSError, match="no hard link"),
        ):
            atomic_publish_new(path, "content\n")

        assert not path.exists()
        assert _tmp_leftovers(tmp_path) == []

    def test_write_failure_leaves_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"

        with (
            patch("kaji_harness.fsio.os.fsync", side_effect=OSError(errno.EIO, "disk error")),
            pytest.raises(OSError, match="disk error"),
        ):
            atomic_publish_new(path, "content\n")

        assert not path.exists()
        assert _tmp_leftovers(tmp_path) == []

    def test_cleanup_failure_after_link_keeps_published_file(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        real_unlink = os.unlink
        calls: list[str] = []

        def failing_unlink(target: Any, *args: Any, **kwargs: Any) -> None:
            calls.append(str(target))
            raise OSError(errno.EACCES, "cannot unlink")

        with patch("kaji_harness.fsio.os.unlink", side_effect=failing_unlink):
            outcome = atomic_publish_new(path, "content\n")

        assert path.read_text(encoding="utf-8") == "content\n"
        assert isinstance(outcome.cleanup_error, OSError)
        assert len(calls) == 1
        for leftover in _tmp_leftovers(tmp_path):
            real_unlink(leftover)


# ============================================================
# Medium: publish_agent_verdict
# ============================================================


def _attempt(
    tmp_path: Path, valid_statuses: list[str] | None = None
) -> tuple[Path, dict[str, str]]:
    attempt_dir = tmp_path / "runs" / "261114152607" / "steps" / "design" / "attempt-001"
    attempt_dir.mkdir(parents=True)
    contract_path = write_verdict_contract(
        attempt_dir,
        issue_id="568",
        run_id="261114152607",
        step_id="design",
        attempt=1,
        valid_statuses=valid_statuses or ["PASS", "RETRY", "ABORT"],
    )
    return attempt_dir, {VERDICT_CONTRACT_ENV: str(contract_path)}


def _publish(environ: dict[str, str], **overrides: str) -> Any:
    fields = {"status": "PASS", "reason": "r", "evidence": "e", "suggestion": ""}
    fields.update(overrides)
    return publish_agent_verdict(environ, **fields)


@pytest.mark.medium
class TestPublishAgentVerdict:
    def test_publishes_contract_verdict_path(self, tmp_path: Path) -> None:
        attempt_dir, environ = _attempt(tmp_path)

        published = _publish(
            environ, reason=_ISSUE_551_REASON, evidence=f"{_ISSUE_551_EVIDENCE}\n- 2行目: # x"
        )

        assert published.path == attempt_dir / "verdict.yaml"
        assert published.step_id == "design"
        assert published.attempt == 1
        assert published.cleanup_warning is None
        loaded = load_verdict_yaml(published.path, {"PASS", "RETRY", "ABORT"})
        assert loaded.evidence == f"{_ISSUE_551_EVIDENCE}\n- 2行目: # x"
        assert _tmp_leftovers(attempt_dir) == []

    def test_contract_file_lists_on_keys_in_order(self, tmp_path: Path) -> None:
        attempt_dir, _ = _attempt(tmp_path, ["RETRY", "PASS"])

        data = json.loads((attempt_dir / VERDICT_CONTRACT_FILENAME).read_text(encoding="utf-8"))
        assert data["valid_statuses"] == ["RETRY", "PASS"]
        assert data["verdict_path"] == str(attempt_dir.absolute() / "verdict.yaml")

    def test_invalid_input_creates_nothing_and_can_be_corrected(self, tmp_path: Path) -> None:
        attempt_dir, environ = _attempt(tmp_path)

        with pytest.raises(VerdictPublishInputError, match="PASS, RETRY, ABORT"):
            _publish(environ, status="BACK", suggestion="x")
        assert not (attempt_dir / "verdict.yaml").exists()

        published = _publish(environ, status="RETRY")
        assert published.status == "RETRY"

    @pytest.mark.parametrize(
        "overrides",
        [
            pytest.param({"reason": " "}, id="blank-reason"),
            pytest.param({"evidence": ""}, id="blank-evidence"),
            pytest.param({"status": "ABORT", "suggestion": "  "}, id="abort-no-suggestion"),
        ],
    )
    def test_required_fields_are_enforced(self, tmp_path: Path, overrides: dict[str, str]) -> None:
        attempt_dir, environ = _attempt(tmp_path)

        with pytest.raises(VerdictPublishInputError):
            _publish(environ, **overrides)
        assert not (attempt_dir / "verdict.yaml").exists()

    def test_handwritten_verdict_is_not_overwritten(self, tmp_path: Path) -> None:
        attempt_dir, environ = _attempt(tmp_path)
        (attempt_dir / "verdict.yaml").write_text(_ISSUE_551_HANDWRITTEN, encoding="utf-8")

        with pytest.raises(VerdictPublishContextError, match="already published"):
            _publish(environ)
        assert (attempt_dir / "verdict.yaml").read_text(encoding="utf-8") == _ISSUE_551_HANDWRITTEN

    def test_second_publish_is_rejected(self, tmp_path: Path) -> None:
        attempt_dir, environ = _attempt(tmp_path)
        _publish(environ, reason="first")

        with pytest.raises(VerdictPublishContextError, match="already published"):
            _publish(environ, reason="second")
        assert "first" in (attempt_dir / "verdict.yaml").read_text(encoding="utf-8")

    def test_race_at_link_is_reported_as_already_published(self, tmp_path: Path) -> None:
        attempt_dir, environ = _attempt(tmp_path)

        def racing_link(src: Any, dst: Any) -> None:
            Path(dst).write_text("other\n", encoding="utf-8")
            raise FileExistsError(errno.EEXIST, "exists", str(dst))

        with (
            patch("kaji_harness.fsio.os.link", side_effect=racing_link),
            pytest.raises(VerdictPublishContextError, match="already published"),
        ):
            _publish(environ)
        assert (attempt_dir / "verdict.yaml").read_text(encoding="utf-8") == "other\n"

    def test_finished_attempt_is_rejected(self, tmp_path: Path) -> None:
        attempt_dir, environ = _attempt(tmp_path)
        (attempt_dir / "result.json").write_text("{}", encoding="utf-8")

        with pytest.raises(VerdictPublishContextError, match="result.json"):
            _publish(environ)
        assert not (attempt_dir / "verdict.yaml").exists()

    def test_contract_pointing_to_another_attempt_is_rejected(self, tmp_path: Path) -> None:
        attempt_dir, environ = _attempt(tmp_path)
        other = tmp_path / "other-attempt"
        other.mkdir()
        contract_path = attempt_dir / VERDICT_CONTRACT_FILENAME
        data = json.loads(contract_path.read_text(encoding="utf-8"))
        data["verdict_path"] = str(other / "verdict.yaml")
        contract_path.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(VerdictPublishContextError, match="same directory"):
            _publish(environ)
        assert not (other / "verdict.yaml").exists()

    def test_missing_env_is_rejected(self) -> None:
        with pytest.raises(VerdictPublishContextError, match=VERDICT_CONTRACT_ENV):
            _publish({})

    def test_relative_env_is_rejected(self) -> None:
        with pytest.raises(VerdictPublishContextError, match="absolute"):
            _publish({VERDICT_CONTRACT_ENV: "relative/verdict-contract.json"})

    def test_missing_contract_file_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(VerdictPublishContextError, match="not found"):
            _publish({VERDICT_CONTRACT_ENV: str(tmp_path / VERDICT_CONTRACT_FILENAME)})

    def test_corrupt_contract_is_rejected(self, tmp_path: Path) -> None:
        attempt_dir, environ = _attempt(tmp_path)
        (attempt_dir / VERDICT_CONTRACT_FILENAME).write_text("{not json", encoding="utf-8")

        with pytest.raises(VerdictPublishContextError, match="invalid verdict contract"):
            _publish(environ)
        assert not (attempt_dir / "verdict.yaml").exists()

    def test_cleanup_failure_after_link_is_a_warning(self, tmp_path: Path) -> None:
        attempt_dir, environ = _attempt(tmp_path)

        with patch("kaji_harness.fsio.os.unlink", side_effect=OSError(errno.EACCES, "denied")):
            published = _publish(environ)

        assert published.cleanup_warning is not None
        assert "denied" in published.cleanup_warning
        assert load_verdict_yaml(published.path, {"PASS"}).status == "PASS"


# ============================================================
# Medium: kaji verdict publish CLI
# ============================================================


def _cli(monkeypatch: pytest.MonkeyPatch, environ: dict[str, str], *args: str) -> int:
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    return main(["verdict", "publish", *args])


@pytest.mark.medium
class TestVerdictPublishCli:
    def test_success_prints_one_line(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        attempt_dir, environ = _attempt(tmp_path)

        rc = _cli(
            monkeypatch,
            environ,
            "--status=PASS",
            "--reason=- 先頭がハイフンの理由",
            "--evidence=e: 1\n# 2",
            "--suggestion=",
        )

        out = capsys.readouterr()
        assert rc == 0
        assert out.out == (
            f"verdict published: step=design attempt=001 status=PASS "
            f"path={attempt_dir / 'verdict.yaml'}\n"
        )
        loaded = load_verdict_yaml(attempt_dir / "verdict.yaml", {"PASS"})
        assert loaded.reason == "- 先頭がハイフンの理由"
        assert loaded.evidence == "e: 1\n# 2"

    def test_invalid_status_exits_1_with_allowed_statuses(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        attempt_dir, environ = _attempt(tmp_path)

        rc = _cli(monkeypatch, environ, "--status=BACK", "--reason=r", "--evidence=e")

        assert rc == 1
        assert "PASS, RETRY, ABORT" in capsys.readouterr().err
        assert not (attempt_dir / "verdict.yaml").exists()

    def test_missing_evidence_exits_1(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        attempt_dir, environ = _attempt(tmp_path)

        rc = _cli(monkeypatch, environ, "--status=PASS", "--reason=r")

        assert rc == 1
        assert "evidence" in capsys.readouterr().err
        assert not (attempt_dir / "verdict.yaml").exists()

    def test_context_error_exits_2(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = _cli(monkeypatch, {}, "--status=PASS", "--reason=r", "--evidence=e")

        assert rc == 2
        assert VERDICT_CONTRACT_ENV in capsys.readouterr().err

    def test_republish_exits_2_and_says_already_published(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _, environ = _attempt(tmp_path)
        assert _cli(monkeypatch, environ, "--status=PASS", "--reason=r", "--evidence=e") == 0
        capsys.readouterr()

        rc = _cli(monkeypatch, environ, "--status=PASS", "--reason=r", "--evidence=e")

        assert rc == 2
        assert "already published" in capsys.readouterr().err

    def test_io_error_exits_3_without_artifact(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        attempt_dir, environ = _attempt(tmp_path)

        with patch("kaji_harness.fsio.os.link", side_effect=OSError(errno.EPERM, "no link")):
            rc = _cli(monkeypatch, environ, "--status=PASS", "--reason=r", "--evidence=e")

        assert rc == 3
        assert "not published" in capsys.readouterr().err
        assert not (attempt_dir / "verdict.yaml").exists()
        assert _tmp_leftovers(attempt_dir) == []

    def test_serialization_invariant_violation_exits_3(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        attempt_dir, environ = _attempt(tmp_path)

        with patch(
            "kaji_harness.verdict._render_verdict_yaml",
            return_value="status: PASS\nreason: other\nevidence: e\n",
        ):
            rc = _cli(monkeypatch, environ, "--status=PASS", "--reason=r", "--evidence=e")

        assert rc == 3
        assert not (attempt_dir / "verdict.yaml").exists()

    def test_cleanup_failure_exits_0_with_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        attempt_dir, environ = _attempt(tmp_path)

        with patch("kaji_harness.fsio.os.unlink", side_effect=OSError(errno.EACCES, "denied")):
            rc = _cli(monkeypatch, environ, "--status=PASS", "--reason=r", "--evidence=e")

        out = capsys.readouterr()
        assert rc == 0
        assert out.out.startswith("verdict published:")
        assert "Warning" in out.err and "denied" in out.err
        assert (attempt_dir / "verdict.yaml").is_file()
        for leftover in _tmp_leftovers(attempt_dir):
            leftover.unlink()

        rc = _cli(monkeypatch, environ, "--status=PASS", "--reason=r", "--evidence=e")
        assert rc == 2
        assert "already published" in capsys.readouterr().err


# ============================================================
# Medium: runner の契約書き出しと e2e（モック agent）
# ============================================================


def _make_config(tmp_path: Path, *, execution_extra: str = "") -> KajiConfig:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(exist_ok=True)
    cfg = kaji_dir / "config.toml"
    cfg.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        f"[execution]\ndefault_timeout = 60\n{execution_extra}\n\n"
        '[provider]\ntype = "github"\n\n'
        '[provider.github]\nrepo = "owner/name"\n'
    )
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig._load(cfg)


def _make_runner(config: KajiConfig, tmp_path: Path, step: Step) -> WorkflowRunner:
    workflow = Workflow(name="t", description="", execution_policy="auto", steps=[step])
    return WorkflowRunner(
        workflow=workflow,
        issue_number=99,
        project_root=tmp_path,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        config=config,
    )


_INTERACTIVE = 'agent_runner = "interactive_terminal"\ninteractive_terminal_backend = "herdr"'


@pytest.fixture
def _offline_github(monkeypatch: pytest.MonkeyPatch, stub_github_issue: None) -> None:
    """runner が触る GitHubProvider の gh 呼び出しを決定的 stub にする。"""
    monkeypatch.setattr(GitHubProvider, "resolve_pr_context", lambda self, branch: None)
    monkeypatch.setattr(
        GitHubProvider,
        "comment_issue",
        lambda self, issue_id, body: Comment(author="", body=body, created_at="", ref="r"),
    )
    monkeypatch.setattr(GitHubProvider, "list_issue_comments_all", lambda self, issue_id: [])
    monkeypatch.setattr(GitHubProvider, "search_issues_all", lambda self, **kwargs: [])


@pytest.mark.medium
@pytest.mark.usefixtures("_offline_github")
class TestRunnerVerdictContract:
    @pytest.mark.parametrize(
        ("execution_extra", "target"),
        [
            pytest.param("", "kaji_harness.runner.execute_cli", id="headless"),
            pytest.param(
                _INTERACTIVE, "kaji_harness.runner.execute_interactive_terminal", id="interactive"
            ),
        ],
    )
    def test_agent_publishes_through_contract_and_artifact_is_adopted(
        self, tmp_path: Path, execution_extra: str, target: str
    ) -> None:
        step = Step(
            id="design",
            skill="plain",
            agent="claude",
            on={"PASS": "end", "RETRY": "design", "ABORT": "end"},
        )
        runner = _make_runner(
            _make_config(tmp_path, execution_extra=execution_extra), tmp_path, step
        )
        captured: dict[str, Any] = {}

        def fake_agent(**kwargs: Any) -> CLIResult:
            env = kwargs["env"]
            contract_path = Path(env[VERDICT_CONTRACT_ENV])
            captured["contract"] = json.loads(contract_path.read_text(encoding="utf-8"))
            captured["contract_dir"] = contract_path.parent
            published = publish_agent_verdict(
                env,
                status="PASS",
                reason=_ISSUE_551_REASON,
                evidence=_ISSUE_551_EVIDENCE,
                suggestion="",
            )
            captured["published_text"] = published.path.read_text(encoding="utf-8")
            return CLIResult(full_output="")

        meta = SkillMetadata(name="plain", description="", exec_script=None)
        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=meta),
            patch(target, side_effect=fake_agent),
            patch("kaji_harness.runner.write_verdict_yaml") as normalize,
        ):
            runner.run()

        contract = captured["contract"]
        attempt_dir = captured["contract_dir"]
        assert contract["step_id"] == "design"
        assert contract["attempt"] == 1
        assert contract["issue_id"] == "99"
        assert contract["valid_statuses"] == ["PASS", "RETRY", "ABORT"]
        assert contract["verdict_path"] == str(attempt_dir / "verdict.yaml")
        assert attempt_dir.name == "attempt-001"
        # artifact source で採用され、正規化による再保存は起きない
        normalize.assert_not_called()
        assert (attempt_dir / "verdict.yaml").read_text(encoding="utf-8") == captured[
            "published_text"
        ]
        sources = [
            json.loads(line)
            for line in (attempt_dir.parents[2] / "run.log").read_text().splitlines()
            if json.loads(line).get("event") == "verdict_source"
        ]
        assert sources[-1]["source"] == "artifact"

    def test_exec_step_does_not_receive_contract(self, tmp_path: Path) -> None:
        step = Step(id="collect", exec=["true"], on={"PASS": "end"})
        runner = _make_runner(_make_config(tmp_path), tmp_path, step)
        captured: dict[str, Any] = {}

        def fake_exec(**kwargs: Any) -> CLIResult:
            captured.update(kwargs)
            Path(kwargs["env"]["KAJI_VERDICT_PATH"]).write_text(
                "status: PASS\nreason: r\nevidence: e\n", encoding="utf-8"
            )
            return CLIResult(full_output="")

        with patch("kaji_harness.runner.execute_exec", side_effect=fake_exec):
            runner.run()

        assert VERDICT_CONTRACT_ENV not in captured["env"]
        attempt_dir = Path(captured["env"]["KAJI_VERDICT_PATH"]).parent
        assert not (attempt_dir / VERDICT_CONTRACT_FILENAME).exists()


@pytest.mark.medium
class TestWriteVerdictYamlAtomic:
    """runner の正規化保存も同じ serializer を使い、原子的に置き換える。"""

    def test_overwrite_round_trips_and_leaves_no_temp_file(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        path.write_text(_ISSUE_551_HANDWRITTEN, encoding="utf-8")
        verdict = Verdict(status="PASS", reason="line\x85next", evidence="日本語", suggestion="")
        replaced: list[tuple[str, str]] = []
        real_replace = os.replace

        def spy_replace(src: Any, dst: Any) -> None:
            replaced.append((Path(src).name, Path(dst).name))
            real_replace(src, dst)

        with patch("kaji_harness.fsio.os.replace", side_effect=spy_replace):
            write_verdict_yaml(path, verdict)

        assert replaced == [("verdict.yaml.tmp", "verdict.yaml")]
        assert load_verdict_yaml(path, {"PASS"}) == verdict
        assert _tmp_leftovers(tmp_path) == []


@pytest.mark.medium
def test_autouse_fixture_unsets_contract_env() -> None:
    assert VERDICT_CONTRACT_ENV not in os.environ
