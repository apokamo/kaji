"""Internal pytest plugin for lossless baseline failure collection."""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

from pluggy import HookimplMarker

from kaji_harness.fsio import atomic_write

hookimpl = HookimplMarker("pytest")


class _ExceptionInfo(Protocol):
    """Typed subset of pytest ExceptionInfo used by the plugin."""

    value: BaseException
    typename: str


class _CallInfo(Protocol):
    """Typed subset of pytest CallInfo used by the plugin."""

    excinfo: _ExceptionInfo | None


class _Report(Protocol):
    """Typed subset shared by pytest runtest and collection reports."""

    nodeid: str
    failed: bool
    passed: bool
    skipped: bool
    when: str
    longreprtext: str
    longrepr: object
    __dict__: dict[str, object]


class _Session(Protocol):
    """Typed subset of pytest Session used by the plugin."""

    testscollected: int


@dataclass(frozen=True)
class _Failure:
    """One collected failure before JSON serialization."""

    nodeid: str
    kind: str
    error_type: str
    message_head: str


@dataclass
class _PluginState:
    """Mutable state accumulated during one pytest process."""

    failures: list[_Failure] = field(default_factory=list)
    passed: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    collection_error_types: dict[str, str] = field(default_factory=dict)
    collected: int = 0


_STATE = _PluginState()


def _message_head(report: _Report) -> str:
    """Return the first non-empty line of pytest's long representation."""
    text = getattr(report, "longreprtext", "") or str(getattr(report, "longrepr", ""))
    return next((line.strip() for line in text.splitlines() if line.strip()), "")[:300]


def _deepest_exception_type(excinfo: object) -> str:
    """Return the deepest chained type for wrapped collection errors."""
    value = getattr(excinfo, "value", None)
    if not isinstance(value, BaseException):
        return str(getattr(excinfo, "typename", "UnknownError"))
    seen: set[int] = set()
    while id(value) not in seen:
        seen.add(id(value))
        nested = value.__cause__ or value.__context__
        if nested is None:
            break
        value = nested
    return type(value).__name__


@hookimpl(tryfirst=True)
def pytest_configure(config: object) -> None:
    """Reset plugin state before collection."""
    del config
    global _STATE
    _STATE = _PluginState()


@hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: object, call: _CallInfo) -> Generator[None, object, object]:
    """Preserve runtest ``ExceptionInfo.typename`` on the resulting report."""
    del item
    report = cast(_Report, (yield))
    if call.excinfo is not None:
        report.__dict__["_kaji_error_type"] = call.excinfo.typename
    return report


@hookimpl(wrapper=True, tryfirst=True)
def pytest_make_collect_report(collector: object) -> Generator[None, object, object]:
    """Preserve collection exception type before pytest discards ``rep.call``."""
    del collector
    report = cast(_Report, (yield))
    report_call = getattr(report, "call", None)
    excinfo = getattr(report_call, "excinfo", None)
    if excinfo is not None:
        error_type = _deepest_exception_type(excinfo)
        report.__dict__["_kaji_error_type"] = error_type
        _STATE.collection_error_types[report.nodeid] = error_type
    return report


def pytest_runtest_logreport(report: _Report) -> None:
    """Collect runtest outcomes and lossless failure identities."""
    if report.passed and report.when == "call":
        _STATE.passed.add(report.nodeid)
    if report.skipped:
        _STATE.skipped.add(report.nodeid)
    if not report.failed:
        return
    kind = "FAILED" if report.when == "call" else "ERROR"
    _STATE.failures.append(
        _Failure(
            nodeid=report.nodeid,
            kind=kind,
            error_type=str(getattr(report, "_kaji_error_type", "UnknownError")),
            message_head=_message_head(report),
        )
    )


def pytest_collectreport(report: _Report) -> None:
    """Collect collection errors using the typename preserved by the wrapper."""
    if not report.failed:
        return
    _STATE.failures.append(
        _Failure(
            nodeid=report.nodeid,
            kind="ERROR",
            error_type=_STATE.collection_error_types.get(
                report.nodeid,
                str(getattr(report, "_kaji_error_type", "CollectError")),
            ),
            message_head=_message_head(report),
        )
    )


def pytest_sessionfinish(session: _Session, exitstatus: int) -> None:
    """Atomically write the report after pytest has finalized all outcomes."""
    del exitstatus
    report_path_raw = os.environ.get("KAJI_BASELINE_REPORT_PATH", "")
    if not report_path_raw:
        return
    _STATE.collected = session.testscollected
    failed = sum(failure.kind == "FAILED" for failure in _STATE.failures)
    errors = sum(failure.kind == "ERROR" for failure in _STATE.failures)
    payload = {
        "summary": {
            "collected": _STATE.collected,
            "passed": len(_STATE.passed),
            "failed": failed,
            "errors": errors,
            "skipped": len(_STATE.skipped),
        },
        "failures": [
            {
                "nodeid": failure.nodeid,
                "kind": failure.kind,
                "error_type": failure.error_type,
                "message_head": failure.message_head,
            }
            for failure in _STATE.failures
        ],
    }
    atomic_write(
        Path(report_path_raw),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
