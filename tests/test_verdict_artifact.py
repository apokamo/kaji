"""Small tests for artifact verdict.yaml + comment fallback resolution.

Issue #220: verdict 解決を artifact `verdict.yaml`（primary）→ 作業報告 comment
末尾の `---VERDICT---` block（fallback）→ stdout parse（互換 fallback）の順に
する。本ファイルは純関数 / 単一ファイル I/O の Small 観点を検証する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kaji_harness.errors import InvalidVerdictValue, VerdictNotFound, VerdictParseError
from kaji_harness.models import Verdict
from kaji_harness.verdict import (
    ControlCharFinding,
    load_verdict_yaml,
    parse_verdict_block,
    resolve_verdict,
    write_verdict_yaml,
)

VALID = {"PASS", "RETRY", "BACK", "ABORT"}


@dataclass(frozen=True)
class _FakeComment:
    """`Comment` 互換の最小 stub（body / created_at のみ）。"""

    body: str
    created_at: str


def _block(
    status: str = "PASS", reason: str = "r", evidence: str = "e", suggestion: str = ""
) -> str:
    """末尾に付ける `---VERDICT---` block を組み立てる。"""
    lines = [
        "---VERDICT---",
        f"status: {status}",
        f'reason: "{reason}"',
        f'evidence: "{evidence}"',
    ]
    if suggestion:
        lines.append(f'suggestion: "{suggestion}"')
    lines.append("---END_VERDICT---")
    return "\n".join(lines)


# ============================================================
# load_verdict_yaml（pure YAML、delimiter 無し）
# ============================================================


@pytest.mark.small
class TestLoadVerdictYaml:
    def test_valid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        path.write_text(
            "status: PASS\nreason: ok\nevidence: tests pass\nsuggestion: ''\n",
            encoding="utf-8",
        )
        v = load_verdict_yaml(path, VALID)
        assert v.status == "PASS"
        assert v.reason == "ok"
        assert v.evidence == "tests pass"
        assert v.suggestion == ""

    def test_missing_reason_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        path.write_text("status: PASS\nevidence: e\n", encoding="utf-8")
        with pytest.raises(VerdictParseError):
            load_verdict_yaml(path, VALID)

    def test_invalid_status_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        path.write_text("status: BOGUS\nreason: r\nevidence: e\n", encoding="utf-8")
        with pytest.raises(InvalidVerdictValue):
            load_verdict_yaml(path, VALID)

    def test_abort_without_suggestion_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        path.write_text("status: ABORT\nreason: r\nevidence: e\nsuggestion: ''\n", encoding="utf-8")
        with pytest.raises(VerdictParseError):
            load_verdict_yaml(path, VALID)

    def test_back_without_suggestion_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        path.write_text("status: BACK\nreason: r\nevidence: e\n", encoding="utf-8")
        with pytest.raises(VerdictParseError):
            load_verdict_yaml(path, VALID)

    def test_broken_yaml_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        path.write_text("status: PASS\nreason: : : :\n  - broken\n", encoding="utf-8")
        with pytest.raises(VerdictParseError):
            load_verdict_yaml(path, VALID)


# ============================================================
# write_verdict_yaml → load_verdict_yaml round-trip
# ============================================================


@pytest.mark.small
class TestWriteVerdictYamlRoundTrip:
    def test_single_line_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        v = Verdict(status="RETRY", reason="needs fix", evidence="1 test failing", suggestion="")
        write_verdict_yaml(path, v)
        loaded = load_verdict_yaml(path, VALID)
        assert loaded == v

    def test_multiline_evidence_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        v = Verdict(
            status="ABORT",
            reason="多段の理由\n2行目",
            evidence="ruff: ok\nmypy: ok\npytest: 3 failed",
            suggestion="設計に戻る",
        )
        write_verdict_yaml(path, v)
        loaded = load_verdict_yaml(path, VALID)
        assert loaded == v

    def test_written_yaml_has_no_delimiter(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        v = Verdict(status="PASS", reason="r", evidence="e", suggestion="")
        write_verdict_yaml(path, v)
        text = path.read_text(encoding="utf-8")
        assert "---VERDICT---" not in text
        assert "---END_VERDICT---" not in text


# ============================================================
# parse_verdict_block（comment 末尾 block 抽出）
# ============================================================


@pytest.mark.small
class TestParseVerdictBlock:
    def test_block_present(self) -> None:
        text = "作業報告本文\n\n" + _block(status="PASS", reason="done", evidence="ok")
        v = parse_verdict_block(text, VALID)
        assert v is not None
        assert v.status == "PASS"

    def test_no_block_returns_none(self) -> None:
        assert parse_verdict_block("ただの作業報告。verdict なし。", VALID) is None

    def test_invalid_status_block_raises(self) -> None:
        text = _block(status="NOPE", reason="r", evidence="e")
        with pytest.raises(InvalidVerdictValue):
            parse_verdict_block(text, VALID)

    def test_multiple_blocks_adopts_last(self) -> None:
        # 過去ログ（先頭）に PASS、末尾の最新 block に RETRY。末尾を採用する。
        text = (
            "過去の引用:\n"
            + _block(status="PASS", reason="old", evidence="old")
            + "\n\n今回の作業報告\n\n"
            + _block(status="RETRY", reason="new", evidence="new")
        )
        v = parse_verdict_block(text, VALID)
        assert v is not None
        assert v.status == "RETRY"
        assert v.reason == "new"

    def test_relaxed_delimiter_block(self) -> None:
        text = "報告\n\n--- VERDICT ---\nstatus: PASS\nreason: r\nevidence: e\n--- END VERDICT ---"
        v = parse_verdict_block(text, VALID)
        assert v is not None
        assert v.status == "PASS"


# ============================================================
# resolve_verdict 優先順位
# ============================================================


@pytest.mark.small
class TestResolveVerdict:
    def _started(self) -> datetime:
        return datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)

    def _write_artifact(self, attempt_dir: Path, status: str = "PASS") -> None:
        write_verdict_yaml(
            attempt_dir / "verdict.yaml",
            Verdict(status=status, reason="r", evidence="e", suggestion=""),
        )

    def test_artifact_primary_and_loader_not_called(self, tmp_path: Path) -> None:
        self._write_artifact(tmp_path, status="PASS")
        called = False

        def loader() -> list[_FakeComment]:
            nonlocal called
            called = True
            return []

        verdict, source, findings = resolve_verdict(
            attempt_dir=tmp_path,
            full_output="(stdout に別の verdict があっても無視)"
            + _block(status="ABORT", suggestion="x"),
            valid_statuses=VALID,
            attempt_started_at=self._started(),
            comment_loader=loader,
        )
        assert source == "artifact"
        assert verdict.status == "PASS"
        assert called is False, "artifact 解決時は comment_loader を呼ばない"
        assert findings == []

    def test_current_comment_adopted(self, tmp_path: Path) -> None:
        started = self._started()
        newer = (started + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
        comment = _FakeComment(body="作業報告\n\n" + _block(status="PASS"), created_at=newer)
        verdict, source, findings = resolve_verdict(
            attempt_dir=tmp_path,
            full_output="",
            valid_statuses=VALID,
            attempt_started_at=started,
            comment_loader=lambda: [comment],
        )
        assert source == "comment"
        assert verdict.status == "PASS"
        assert findings == []

    def test_same_second_comment_adopted(self, tmp_path: Path) -> None:
        # dispatch はマイクロ秒精度（12:00:00.5）、comment は秒精度（12:00:00Z）。
        # 同一秒投稿の fresh comment がマイクロ秒差で取りこぼされないこと。
        started = self._started().replace(microsecond=500_000)
        same_second = self._started().strftime("%Y-%m-%dT%H:%M:%SZ")
        comment = _FakeComment(body="作業報告\n\n" + _block(status="PASS"), created_at=same_second)
        verdict, source, findings = resolve_verdict(
            attempt_dir=tmp_path,
            full_output="",
            valid_statuses=VALID,
            attempt_started_at=started,
            comment_loader=lambda: [comment],
        )
        assert source == "comment"
        assert verdict.status == "PASS"
        assert findings == []

    def test_old_comment_only_no_stdout_raises_not_found(self, tmp_path: Path) -> None:
        started = self._started()
        older = (started - timedelta(seconds=300)).strftime("%Y-%m-%dT%H:%M:%SZ")
        comment = _FakeComment(body=_block(status="PASS"), created_at=older)
        with pytest.raises(VerdictNotFound):
            resolve_verdict(
                attempt_dir=tmp_path,
                full_output="",
                valid_statuses=VALID,
                attempt_started_at=started,
                comment_loader=lambda: [comment],
            )

    def test_old_comment_only_with_stdout_uses_stdout(self, tmp_path: Path) -> None:
        started = self._started()
        older = (started - timedelta(seconds=300)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # 古い comment は RETRY だが、stdout は PASS。古い comment を採らず stdout 採用。
        comment = _FakeComment(body=_block(status="RETRY"), created_at=older)
        verdict, source, findings = resolve_verdict(
            attempt_dir=tmp_path,
            full_output="報告\n\n" + _block(status="PASS"),
            valid_statuses=VALID,
            attempt_started_at=started,
            comment_loader=lambda: [comment],
        )
        assert source == "stdout"
        assert verdict.status == "PASS"
        assert findings == []

    def test_no_comment_with_stdout_uses_stdout(self, tmp_path: Path) -> None:
        verdict, source, findings = resolve_verdict(
            attempt_dir=tmp_path,
            full_output="報告\n\n" + _block(status="RETRY"),
            valid_statuses=VALID,
            attempt_started_at=self._started(),
            comment_loader=lambda: [],
        )
        assert source == "stdout"
        assert verdict.status == "RETRY"
        assert findings == []

    def test_all_empty_raises_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(VerdictNotFound):
            resolve_verdict(
                attempt_dir=tmp_path,
                full_output="verdict 無し",
                valid_statuses=VALID,
                attempt_started_at=self._started(),
                comment_loader=lambda: [],
            )

    def test_broken_artifact_fails_loud(self, tmp_path: Path) -> None:
        (tmp_path / "verdict.yaml").write_text(
            "status: PASS\nreason: : :\n  bad\n", encoding="utf-8"
        )
        called = False

        def loader() -> list[_FakeComment]:
            nonlocal called
            called = True
            return []

        with pytest.raises(VerdictParseError):
            resolve_verdict(
                attempt_dir=tmp_path,
                full_output="報告\n\n" + _block(status="PASS"),
                valid_statuses=VALID,
                attempt_started_at=self._started(),
                comment_loader=loader,
            )
        assert called is False, "壊れた artifact は fail-loud。comment/stdout に落ちない"

    def test_comment_loader_failure_falls_through_to_stdout(self, tmp_path: Path) -> None:
        def loader() -> list[_FakeComment]:
            raise RuntimeError("provider down")

        verdict, source, findings = resolve_verdict(
            attempt_dir=tmp_path,
            full_output="報告\n\n" + _block(status="PASS"),
            valid_statuses=VALID,
            attempt_started_at=self._started(),
            comment_loader=loader,
        )
        assert source == "stdout"
        assert verdict.status == "PASS"
        assert findings == []

    def test_unparseable_created_at_excluded(self, tmp_path: Path) -> None:
        # created_at が parse 不能な comment は fail-safe で除外し、stdout へ。
        comment = _FakeComment(body=_block(status="PASS"), created_at="not-a-timestamp")
        with pytest.raises(VerdictNotFound):
            resolve_verdict(
                attempt_dir=tmp_path,
                full_output="",
                valid_statuses=VALID,
                attempt_started_at=self._started(),
                comment_loader=lambda: [comment],
            )


# ============================================================
# Issue #298: 禁止制御文字混入 verdict の findings 伝播（3 経路）
# ============================================================


@pytest.mark.small
class TestResolveVerdictControlCharFindings:
    def _started(self) -> datetime:
        return datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)

    def test_artifact_path_surfaces_findings_and_resolves_pass(self, tmp_path: Path) -> None:
        (tmp_path / "verdict.yaml").write_text(
            'status: PASS\nreason: "ok"\nevidence: "done\x1bhere"\nsuggestion: ""\n',
            encoding="utf-8",
        )
        verdict, source, findings = resolve_verdict(
            attempt_dir=tmp_path,
            full_output="",
            valid_statuses=VALID,
            attempt_started_at=self._started(),
            comment_loader=None,
        )
        assert source == "artifact"
        assert verdict.status == "PASS"
        assert findings == [ControlCharFinding(position=41, codepoint=0x1B)]

    def test_comment_path_surfaces_findings(self, tmp_path: Path) -> None:
        newer = (self._started() + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
        comment = _FakeComment(
            body="作業報告\n\n" + _block(status="PASS", evidence="done\x1bhere"),
            created_at=newer,
        )
        verdict, source, findings = resolve_verdict(
            attempt_dir=tmp_path,
            full_output="",
            valid_statuses=VALID,
            attempt_started_at=self._started(),
            comment_loader=lambda: [comment],
        )
        assert source == "comment"
        assert verdict.status == "PASS"
        assert len(findings) == 1
        assert findings[0].codepoint == 0x1B

    def test_stdout_path_surfaces_findings(self, tmp_path: Path) -> None:
        verdict, source, findings = resolve_verdict(
            attempt_dir=tmp_path,
            full_output="報告\n\n" + _block(status="PASS", evidence="done\x1bhere"),
            valid_statuses=VALID,
            attempt_started_at=self._started(),
            comment_loader=lambda: [],
        )
        assert source == "stdout"
        assert verdict.status == "PASS"
        assert len(findings) == 1
        assert findings[0].codepoint == 0x1B


@pytest.mark.small
class TestLoadVerdictYamlFindingsSink:
    def test_findings_sink_receives_control_char_findings(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        path.write_text(
            'status: PASS\nreason: "ok"\nevidence: "done\x1bhere"\nsuggestion: ""\n',
            encoding="utf-8",
        )
        sink: list[ControlCharFinding] = []
        verdict = load_verdict_yaml(path, VALID, findings_sink=sink)
        assert verdict.status == "PASS"
        assert len(sink) == 1
        assert sink[0].codepoint == 0x1B

    def test_no_findings_sink_behavior_unchanged(self, tmp_path: Path) -> None:
        path = tmp_path / "verdict.yaml"
        path.write_text(
            'status: PASS\nreason: "ok"\nevidence: "done\x1bhere"\nsuggestion: ""\n',
            encoding="utf-8",
        )
        verdict = load_verdict_yaml(path, VALID)
        assert verdict.status == "PASS"
