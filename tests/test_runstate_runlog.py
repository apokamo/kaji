"""Small tests: run.log interpretation helpers (Issue #540)."""

from __future__ import annotations

import pytest

from kaji_harness.runstate.runlog import find_unterminated_step
from tests.runstate_helpers import start, step_end, step_start

pytestmark = pytest.mark.small


def test_step_start_only_attempt_is_unterminated() -> None:
    events = [start(), step_start("design"), step_end("design"), step_start("implement")]
    assert find_unterminated_step(events) == ("implement", 1)


def test_second_attempt_of_same_step_is_unterminated() -> None:
    events = [
        start(),
        step_start("implement", 1),
        step_end("implement", 1, "RETRY"),
        step_start("implement", 2),
    ]
    assert find_unterminated_step(events) == ("implement", 2)


def test_all_steps_ended_returns_none() -> None:
    events = [start(), step_start("implement"), step_end("implement")]
    assert find_unterminated_step(events) is None
