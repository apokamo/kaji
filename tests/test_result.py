"""Small tests for kaji_harness.result (Issue #222).

``derive_signal`` の境界、``AttemptResult`` の JSON round-trip、``write_result_json``
の出力、および ``CLIResult`` への ``exit_code`` / ``signal`` 追加が後方互換である
ことを検証する。いずれも外部依存なしの純ロジック / tmp ファイル I/O のため Small。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kaji_harness.models import CLIResult
from kaji_harness.result import AttemptResult, derive_signal, write_result_json


@pytest.mark.small
class TestDeriveSignal:
    """``derive_signal`` の number → signal 名 導出。"""

    def test_143_is_sigterm(self) -> None:
        """shell 慣例 128+15 = SIGTERM（Claude Code CLI の trap exit 経路）。"""
        assert derive_signal(143) == "SIGTERM"

    def test_137_is_sigkill(self) -> None:
        """128+9 = SIGKILL。"""
        assert derive_signal(137) == "SIGKILL"

    def test_zero_is_none(self) -> None:
        """clean exit は signal 由来でない。"""
        assert derive_signal(0) is None

    def test_negative_15_is_sigterm(self) -> None:
        """POSIX: signal 終了は負の returncode（-15 = SIGTERM）。"""
        assert derive_signal(-15) == "SIGTERM"

    def test_negative_9_is_sigkill(self) -> None:
        assert derive_signal(-9) == "SIGKILL"

    def test_none_is_none(self) -> None:
        """取得不能な exit_code は None。"""
        assert derive_signal(None) is None

    def test_plain_nonzero_exit_is_none(self) -> None:
        """signal 由来でない通常の失敗コード（1）は None。"""
        assert derive_signal(1) is None

    def test_unknown_high_code_is_none(self) -> None:
        """>128 だが既知 signal 番号に対応しない値は None。"""
        # 200 - 128 = 72 は有効な signal 番号でない
        assert derive_signal(200) is None

    def test_128_is_none(self) -> None:
        """ちょうど 128（signal 0 相当）は signal 名を持たない。"""
        assert derive_signal(128) is None


@pytest.mark.small
class TestAttemptResultRoundTrip:
    """``AttemptResult`` + ``write_result_json`` の JSON round-trip。"""

    def test_full_fields_round_trip(self, tmp_path: Path) -> None:
        result = AttemptResult(
            step_id="implement",
            attempt=1,
            status="RETRY",
            exit_code=143,
            signal="SIGTERM",
            started_at="2026-06-04T22:00:00.000000+00:00",
            ended_at="2026-06-04T22:01:47.000000+00:00",
            duration_ms=107335,
            session_id="abc123",
            dispatch="agent",
            error=None,
        )
        path = tmp_path / "attempt-001" / "result.json"
        write_result_json(path, result)

        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == {
            "step_id": "implement",
            "attempt": 1,
            "status": "RETRY",
            "exit_code": 143,
            "signal": "SIGTERM",
            "started_at": "2026-06-04T22:00:00.000000+00:00",
            "ended_at": "2026-06-04T22:01:47.000000+00:00",
            "duration_ms": 107335,
            "session_id": "abc123",
            "dispatch": "agent",
            "error": None,
        }

    def test_null_fields_serialized_as_json_null(self, tmp_path: Path) -> None:
        """exit_code / signal / session_id / error の None が JSON null になる。"""
        result = AttemptResult(
            step_id="design",
            attempt=2,
            status="PASS",
            exit_code=None,
            signal=None,
            started_at="2026-06-04T22:00:00+00:00",
            ended_at="2026-06-04T22:00:05+00:00",
            duration_ms=5000,
            session_id=None,
            dispatch="exec_script",
            error=None,
        )
        path = tmp_path / "result.json"
        write_result_json(path, result)

        raw = path.read_text(encoding="utf-8")
        loaded = json.loads(raw)
        assert loaded["exit_code"] is None
        assert loaded["signal"] is None
        assert loaded["session_id"] is None
        assert loaded["error"] is None
        # pure JSON（trailing newline 付き）
        assert raw.endswith("\n")

    def test_parent_dir_created(self, tmp_path: Path) -> None:
        """親ディレクトリが無くても write_result_json が作成する。"""
        result = AttemptResult(
            step_id="s",
            attempt=1,
            status="PASS",
            exit_code=0,
            signal=None,
            started_at="t",
            ended_at="t",
            duration_ms=0,
            session_id=None,
            dispatch="agent",
            error=None,
        )
        path = tmp_path / "a" / "b" / "c" / "result.json"
        write_result_json(path, result)
        assert path.exists()


@pytest.mark.small
class TestCLIResultBackwardCompat:
    """``CLIResult`` への ``exit_code`` / ``signal`` 追加が後方互換であること。"""

    def test_existing_construction_still_works(self) -> None:
        """exit_code / signal を渡さない既存呼び出しが default で構築できる。"""
        result = CLIResult(full_output="output")
        assert result.exit_code is None
        assert result.signal is None

    def test_new_fields_assignable(self) -> None:
        result = CLIResult(full_output="o", exit_code=143, signal="SIGTERM")
        assert result.exit_code == 143
        assert result.signal == "SIGTERM"
