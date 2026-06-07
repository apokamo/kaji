"""起動コンソール progress logging のテスト（Issue #235）。

console_log.configure_console_logging() の stdout/stderr routing・formatter・
冪等性、および ``kaji run --log-level`` の argparse 契約を固定する。
"""

from __future__ import annotations

import logging
import re

import pytest

from kaji_harness.cli_main import create_parser
from kaji_harness.console_log import ROOT_LOGGER_NAME, configure_console_logging


@pytest.fixture
def _clean_root() -> None:
    """各テストの前後で kaji ルート logger のハンドラ/状態をリセットする。"""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    saved = list(root.handlers)
    saved_level = root.level
    saved_propagate = root.propagate
    for h in list(root.handlers):
        root.removeHandler(h)
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved:
        root.addHandler(h)
    root.setLevel(saved_level)
    root.propagate = saved_propagate


@pytest.mark.small
class TestConsoleRouting:
    def test_info_and_debug_go_to_stdout_only(
        self, _clean_root: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_console_logging(logging.DEBUG)
        log = logging.getLogger("kaji.runner")
        log.debug("debug line")
        log.info("info line")
        captured = capsys.readouterr()
        assert "debug line" in captured.out
        assert "info line" in captured.out
        assert "debug line" not in captured.err
        assert "info line" not in captured.err

    def test_warning_and_error_go_to_stderr_only(
        self, _clean_root: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_console_logging(logging.DEBUG)
        log = logging.getLogger("kaji.runner")
        log.warning("warn line")
        log.error("err line")
        captured = capsys.readouterr()
        assert "warn line" in captured.err
        assert "err line" in captured.err
        assert "warn line" not in captured.out
        assert "err line" not in captured.out


@pytest.mark.small
class TestFormatter:
    def test_info_format_has_iso_timestamp_and_kaji_prefix(
        self, _clean_root: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_console_logging(logging.INFO)
        logging.getLogger("kaji.runner").info("workflow start: demo issue #1")
        line = capsys.readouterr().out.strip()
        assert re.match(
            r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\] \[kaji\] workflow start: demo issue #1$",
            line,
        ), f"unexpected info format: {line!r}"

    def test_warning_format_includes_levelname(
        self, _clean_root: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_console_logging(logging.INFO)
        logging.getLogger("kaji.runner").warning("barrier missed: review")
        line = capsys.readouterr().err.strip()
        assert re.match(
            r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\] \[kaji\] WARNING: barrier missed: review$",
            line,
        ), f"unexpected warning format: {line!r}"

    def test_log_level_warning_suppresses_info(
        self, _clean_root: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_console_logging(logging.WARNING)
        log = logging.getLogger("kaji.runner")
        log.info("should not appear")
        log.warning("should appear")
        captured = capsys.readouterr()
        assert "should not appear" not in captured.out
        assert "should appear" in captured.err


@pytest.mark.small
class TestIdempotency:
    def test_double_configure_does_not_duplicate_handlers(
        self, _clean_root: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_console_logging(logging.INFO)
        configure_console_logging(logging.INFO)
        root = logging.getLogger(ROOT_LOGGER_NAME)
        kaji_handlers = [h for h in root.handlers if getattr(h, "_kaji", False)]
        # stdout + stderr ちょうど 2 個のみ（重複登録されない）
        assert len(kaji_handlers) == 2
        logging.getLogger("kaji.runner").info("once")
        out_lines = [ln for ln in capsys.readouterr().out.splitlines() if "once" in ln]
        assert len(out_lines) == 1, f"line emitted {len(out_lines)} times: {out_lines}"


@pytest.mark.small
class TestLogLevelArg:
    def test_default_is_info(self) -> None:
        parser = create_parser()
        ns = parser.parse_args(["run", "wf.yaml", "1"])
        assert ns.log_level == "INFO"

    def test_explicit_choice_parsed(self) -> None:
        parser = create_parser()
        ns = parser.parse_args(["run", "wf.yaml", "1", "--log-level", "WARNING"])
        assert ns.log_level == "WARNING"

    def test_invalid_choice_exits_2(self) -> None:
        parser = create_parser()
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(["run", "wf.yaml", "1", "--log-level", "TRACE"])
        assert excinfo.value.code == 2
