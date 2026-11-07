"""Tests for resume-session holder handling in the Herdr backend (Issue #532).

close-on-verdict=false kept the resume source pane alive, so ``codex resume`` met the
session lock screen and waited until the step timeout. These tests drive an in-process
fake Herdr patched at ``_run_herdr`` (the only Herdr subprocess boundary).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness.errors import CLIExecutionError, SessionResolution, StepTimeoutError
from kaji_harness.interactive_terminal_herdr import (
    HERDR_UNTRACKED_SESSIONS_FILENAME,
    UntrackedSessionEntry,
    UntrackedSessionRegistry,
    _build_herdr_marker_argv,
    _launch_herdr_pane,
    _load_untracked_sessions,
    _mark_herdr_pane,
    _record_untracked_session,
    _report_session_token_if_owned,
    execute_interactive_terminal_herdr,
)
from kaji_harness.models import Step

_HERDR = "kaji_harness.interactive_terminal_herdr"
_ORIGIN = "w1:p1"
_OTHER_ORIGIN = "w9:p1"
_CODEX_SESSION = "01a1b491-0000-4000-8000-000000000001"
_SESSION_S = "22222222-2222-4222-8222-222222222222"
_INPUT_SUBCOMMANDS = {"send-keys", "send-text"}


@dataclass
class _FakePane:
    pane_id: str
    workspace_id: str
    x: int
    y: int
    tokens: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeHerdr:
    """Stateful in-process Herdr: panes with tokens, layout rects and liveness."""

    panes: dict[str, _FakePane] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)
    on_run: Callable[[str], None] | None = None
    fail_report_metadata: set[str] = field(default_factory=set)
    refuse_close: set[str] = field(default_factory=set)
    _counter: int = 1

    def __post_init__(self) -> None:
        self.panes[_ORIGIN] = _FakePane(_ORIGIN, "w1", 0, 0)

    def add(
        self, pane_id: str, *, x: int = 100, y: int = 0, tokens: dict[str, str] | None = None
    ) -> None:
        workspace = pane_id.split(":", 1)[0]
        self.panes[pane_id] = _FakePane(pane_id, workspace, x, y, dict(tokens or {}))

    def __call__(
        self, herdr: str, arguments: list[str], *, workdir: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(arguments))
        return subprocess.CompletedProcess([herdr, *arguments], 0, self._dispatch(arguments), "")

    def _fail(self, message: str) -> CLIExecutionError:
        return CLIExecutionError("interactive_terminal", 1, message)

    def _info(self, pane: _FakePane) -> dict[str, object]:
        return {
            "pane_id": pane.pane_id,
            "workspace_id": pane.workspace_id,
            "tokens": dict(pane.tokens),
        }

    def _dispatch(self, arguments: list[str]) -> str:
        command = arguments[:2]
        if command == ["pane", "get"]:
            pane = self.panes.get(arguments[2])
            if pane is None:
                raise self._fail(f"pane not found: {arguments[2]}")
            return json.dumps({"result": {"type": "pane_info", "pane": self._info(pane)}})
        if command == ["pane", "list"]:
            workspace = arguments[3] if len(arguments) > 3 else None
            listed = [
                self._info(pane)
                for pane in self.panes.values()
                if workspace is None or pane.workspace_id == workspace
            ]
            return json.dumps({"result": {"type": "pane_list", "panes": listed}})
        if command == ["pane", "layout"]:
            layout = [
                {"pane_id": pane.pane_id, "rect": {"x": pane.x, "y": pane.y}}
                for pane in self.panes.values()
                if pane.workspace_id == "w1"
            ]
            return json.dumps({"result": {"type": "pane_layout", "layout": {"panes": layout}}})
        if command == ["pane", "split"]:
            target = self.panes[arguments[2]]
            direction = arguments[arguments.index("--direction") + 1]
            self._counter += 1
            pane_id = f"w1:pG{self._counter}"
            if direction == "right":
                self.add(pane_id, x=100, y=0)
            else:
                self.add(pane_id, x=target.x, y=target.y + 10)
            return json.dumps(
                {"result": {"type": "pane_info", "pane": self._info(self.panes[pane_id])}}
            )
        if command == ["pane", "report-metadata"]:
            pane_id = arguments[2]
            if pane_id in self.fail_report_metadata:
                raise self._fail("report-metadata rejected")
            pane = self.panes.get(pane_id)
            if pane is None:
                raise self._fail(f"pane not found: {pane_id}")
            values = [arguments[i + 1] for i, a in enumerate(arguments) if a == "--token"]
            for value in values:
                name, _, token = value.partition("=")
                pane.tokens[name] = token
            return ""
        if command == ["pane", "run"]:
            if self.on_run is not None:
                self.on_run(arguments[2])
            return ""
        if command == ["pane", "close"]:
            pane_id = arguments[2]
            if pane_id in self.refuse_close:
                raise self._fail("close rejected")
            self.panes.pop(pane_id, None)
            return ""
        if command == ["pane", "process-info"]:
            info = {"shell_pid": 1, "foreground_processes": [{"pid": 2}]}
            return json.dumps({"result": {"type": "pane_process_info", "process_info": info}})
        raise AssertionError(f"unexpected Herdr call: {arguments}")

    def calls_of(self, subcommand: str) -> list[list[str]]:
        return [call for call in self.calls if call[:2] == ["pane", subcommand]]

    def owned_alive(self) -> list[str]:
        return [
            pane.pane_id
            for pane in self.panes.values()
            if pane.tokens.get("kaji_origin") == _ORIGIN
        ]


@pytest.fixture
def fake() -> Iterator[FakeHerdr]:
    herdr = FakeHerdr()
    with (
        patch(f"{_HERDR}._run_herdr", side_effect=herdr),
        patch(f"{_HERDR}._preflight_herdr", return_value=("herdr", _ORIGIN, "herdr 0.9.5")),
        patch(f"{_HERDR}._wait_for_herdr_launcher_start"),
    ):
        yield herdr


class _Attempts:
    """Allocate attempt dirs and make ``pane run`` complete like an agent."""

    def __init__(self, root: Path, fake: FakeHerdr) -> None:
        self.root = root
        self.fake = fake
        self.count = 0
        self.registry = root / HERDR_UNTRACKED_SESSIONS_FILENAME
        self.before_verdict: Callable[[str], None] | None = None
        fake.on_run = self._on_run
        self._current: Path | None = None
        self._agent = ""

    def _on_run(self, pane_id: str) -> None:
        assert self._current is not None
        if self.before_verdict is not None:
            self.before_verdict(pane_id)
        log = self._current / "terminal.log"
        if self._agent == "codex":
            log.write_text(f"codex resume {_CODEX_SESSION}\n", encoding="utf-8")
        (self._current / "verdict.yaml").write_text(
            "status: PASS\nreason: ok\nevidence: ok\n", encoding="utf-8"
        )

    def run(
        self,
        step_id: str,
        agent: str,
        *,
        session_id: str | None = None,
        close_on_verdict: bool = False,
        timeout: int = 30,
    ) -> tuple[str | None, dict[str, object]]:
        self.count += 1
        self._current = self.root / f"attempt-{self.count}"
        self._current.mkdir()
        self._agent = agent
        prompt = self._current / "prompt.txt"
        prompt.write_text("do work", encoding="utf-8")
        result = execute_interactive_terminal_herdr(
            step=Step(id=step_id, skill=step_id, agent=agent),
            prompt_path=prompt,
            verdict_path=self._current / "verdict.yaml",
            workdir=self.root,
            timeout=timeout,
            session_id=session_id,
            close_on_verdict=close_on_verdict,
            untracked_sessions_path=self.registry,
        )
        return result.session_id, self.metadata()

    def metadata(self) -> dict[str, object]:
        assert self._current is not None
        return dict(json.loads((self._current / "pane-metadata.json").read_text(encoding="utf-8")))


@pytest.fixture
def attempts(tmp_path: Path, fake: FakeHerdr, monkeypatch: pytest.MonkeyPatch) -> _Attempts:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    return _Attempts(tmp_path, fake)


def _split_index(fake: FakeHerdr) -> int:
    splits = [i for i, call in enumerate(fake.calls) if call[:2] == ["pane", "split"]]
    return splits[-1]


def _close_index(fake: FakeHerdr, pane_id: str) -> int:
    return next(
        i
        for i, call in enumerate(fake.calls)
        if call[:2] == ["pane", "close"] and call[2] == pane_id
    )


# ============================================================
# Small: marker / helper / registry contracts
# ============================================================


@pytest.mark.small
class TestSessionMarkerContract:
    def test_marker_argv_adds_session_token_only_when_given(self) -> None:
        base = _build_herdr_marker_argv(
            "herdr", pane_id="w1:p2", origin_pane=_ORIGIN, run_id="run", step_id="s"
        )
        with_session = _build_herdr_marker_argv(
            "herdr",
            pane_id="w1:p2",
            origin_pane=_ORIGIN,
            run_id="run",
            step_id="s",
            session_id=_SESSION_S,
        )
        assert "kaji_session" not in " ".join(base)
        assert base[-2:] == ["--token", "kaji_step=s"]
        assert with_session == [*base, "--token", f"kaji_session={_SESSION_S}"]

    def test_mark_rejects_unconfirmed_session_token(self, fake: FakeHerdr) -> None:
        fake.add("w1:p2")
        original = fake._dispatch

        def drop_session(arguments: list[str]) -> str:
            output = original(arguments)
            if arguments[:2] == ["pane", "report-metadata"]:
                fake.panes["w1:p2"].tokens.pop("kaji_session", None)
            return output

        with (
            patch.object(fake, "_dispatch", side_effect=drop_session),
            pytest.raises(CLIExecutionError, match="ownership metadata was not confirmed"),
        ):
            _mark_herdr_pane(
                "herdr",
                "w1:p2",
                origin_pane=_ORIGIN,
                run_id="run",
                step_id="s",
                session_id=_SESSION_S,
            )

    @pytest.mark.parametrize(
        "tokens",
        [
            {"kaji_origin": _OTHER_ORIGIN, "kaji_run": "run"},
            {"kaji_origin": _ORIGIN, "kaji_run": "other-run"},
            {},
        ],
    )
    def test_report_refuses_changed_or_missing_ownership(
        self, fake: FakeHerdr, tokens: dict[str, str]
    ) -> None:
        fake.add("w1:p2", tokens=tokens)
        outcome = _report_session_token_if_owned(
            "herdr", "w1:p2", origin_pane=_ORIGIN, run_id="run", step_id="s", session_id="S"
        )
        assert outcome.status == "ownership_changed"
        assert fake.calls_of("report-metadata") == []
        assert fake.calls_of("close") == []
        assert fake.panes["w1:p2"].tokens == tokens

    def test_report_absent_pane_writes_nothing(self, fake: FakeHerdr) -> None:
        outcome = _report_session_token_if_owned(
            "herdr", "w1:p9", origin_pane=_ORIGIN, run_id="run", step_id="s", session_id="S"
        )
        assert outcome.status == "absent"
        assert fake.calls_of("report-metadata") == []

    def test_report_marks_all_four_tokens_on_owned_pane(self, fake: FakeHerdr) -> None:
        fake.add("w1:p2", tokens={"kaji_origin": _ORIGIN, "kaji_run": "run", "kaji_step": "s"})
        outcome = _report_session_token_if_owned(
            "herdr", "w1:p2", origin_pane=_ORIGIN, run_id="run", step_id="s", session_id="S"
        )
        assert outcome.status == "marked"
        sent = fake.calls_of("report-metadata")[0]
        assert [sent[i + 1] for i, a in enumerate(sent) if a == "--token"] == [
            f"kaji_origin={_ORIGIN}",
            "kaji_run=run",
            "kaji_step=s",
            "kaji_session=S",
        ]

    def test_report_failure_after_write_is_failed(self, fake: FakeHerdr) -> None:
        fake.add("w1:p2", tokens={"kaji_origin": _ORIGIN, "kaji_run": "run"})
        fake.fail_report_metadata.add("w1:p2")
        outcome = _report_session_token_if_owned(
            "herdr", "w1:p2", origin_pane=_ORIGIN, run_id="run", step_id="s", session_id="S"
        )
        assert outcome.status == "failed"
        assert outcome.error is not None

    @pytest.mark.parametrize("failing", [("report-metadata",), ("get", "list")])
    def test_report_os_error_is_failed(self, fake: FakeHerdr, failing: tuple[str, ...]) -> None:
        """Review C1: an OSError from any Herdr call becomes ``failed``, never propagates."""
        fake.add("w1:p2", tokens={"kaji_origin": _ORIGIN, "kaji_run": "run"})

        def run(
            herdr: str, arguments: list[str], *, workdir: Path | None = None
        ) -> subprocess.CompletedProcess[str]:
            if arguments[0] == "pane" and arguments[1] in failing:
                raise FileNotFoundError(2, "Herdr executable disappeared")
            return fake(herdr, arguments, workdir=workdir)

        with patch(f"{_HERDR}._run_herdr", side_effect=run):
            outcome = _report_session_token_if_owned(
                "herdr", "w1:p2", origin_pane=_ORIGIN, run_id="run", step_id="s", session_id="S"
            )
        assert outcome.status == "failed"
        assert outcome.error is not None
        assert "Herdr executable disappeared" in outcome.error


@pytest.mark.small
class TestResumeHolderSelection:
    def _owned(self, session: str, run: str = "run-a") -> dict[str, str]:
        return {"kaji_origin": _ORIGIN, "kaji_run": run, "kaji_step": "s", "kaji_session": session}

    def test_same_origin_holders_close_before_list_and_prune(
        self, fake: FakeHerdr, tmp_path: Path
    ) -> None:
        fake.add("w1:p2", y=0, tokens=self._owned(_SESSION_S))
        fake.add("w2:p3", tokens=self._owned(_SESSION_S, run="run-b"))
        fake.add("w1:p4", y=10, tokens=self._owned("other-session"))
        launch = _launch_herdr_pane(
            "herdr", _ORIGIN, workdir=tmp_path, resume_session_id=_SESSION_S
        )
        assert launch.session_panes_closed == ["w1:p2", "w2:p3"]
        # pane layout は既存の list / prune（_list_managed_herdr_panes）だけが呼ぶ。
        first_layout = next(
            i for i, call in enumerate(fake.calls) if call[:2] == ["pane", "layout"]
        )
        assert _close_index(fake, "w2:p3") < first_layout
        assert "w1:p4" in fake.panes
        assert launch.split_target_pane == "w1:p4"

    @pytest.mark.parametrize(
        ("pane_id", "tokens"),
        [
            ("w9:p4", {"kaji_origin": _OTHER_ORIGIN, "kaji_run": "r", "kaji_session": _SESSION_S}),
            ("w1:p4", {"kaji_origin": _ORIGIN, "kaji_session": _SESSION_S}),
            (_ORIGIN, {"kaji_origin": _ORIGIN, "kaji_run": "r", "kaji_session": _SESSION_S}),
        ],
    )
    def test_unowned_holder_fails_without_close_or_split(
        self, fake: FakeHerdr, tmp_path: Path, pane_id: str, tokens: dict[str, str]
    ) -> None:
        if pane_id == _ORIGIN:
            fake.panes[_ORIGIN].tokens = tokens
        else:
            fake.add(pane_id, tokens=tokens)
        with pytest.raises(CLIExecutionError) as error:
            _launch_herdr_pane("herdr", _ORIGIN, workdir=tmp_path, resume_session_id=_SESSION_S)
        assert pane_id in error.value.stderr
        assert _SESSION_S in error.value.stderr
        assert f"herdr pane close {pane_id}" in error.value.stderr
        assert fake.calls_of("close") == []
        assert fake.calls_of("split") == []

    def test_unconfirmed_holder_close_fails_without_split(
        self, fake: FakeHerdr, tmp_path: Path
    ) -> None:
        fake.add("w1:p2", tokens=self._owned(_SESSION_S))
        with (
            patch(f"{_HERDR}._close_owned_herdr_pane", return_value=False),
            pytest.raises(CLIExecutionError, match="w1:p2"),
        ):
            _launch_herdr_pane("herdr", _ORIGIN, workdir=tmp_path, resume_session_id=_SESSION_S)
        assert fake.calls_of("split") == []

    def test_fresh_launch_skips_session_handling(self, fake: FakeHerdr, tmp_path: Path) -> None:
        registry = tmp_path / HERDR_UNTRACKED_SESSIONS_FILENAME
        registry.write_text("not json", encoding="utf-8")
        fake.add("w1:p2", tokens=self._owned(_SESSION_S))
        launch = _launch_herdr_pane(
            "herdr", _ORIGIN, workdir=tmp_path, untracked_sessions_path=registry
        )
        assert launch.session_panes_closed == []
        assert [call for call in fake.calls if call == ["pane", "list"]] == []
        assert "w1:p2" in fake.panes


@pytest.mark.medium
class TestUntrackedRegistry:
    def _entry(self, **overrides: str) -> UntrackedSessionEntry:
        values = {
            "session_id": _SESSION_S,
            "pane_id": "w1:p2",
            "origin_pane": _ORIGIN,
            "run_id": "run-a",
            "step_id": "codex-fresh",
            "reason": "marker_failed_close_failed",
        }
        values.update(overrides)
        return UntrackedSessionEntry.model_validate(values)

    def test_record_appends_atomically(self, tmp_path: Path) -> None:
        path = tmp_path / "issue" / HERDR_UNTRACKED_SESSIONS_FILENAME
        assert _record_untracked_session(path, self._entry()).recorded is True
        assert _record_untracked_session(path, self._entry(pane_id="w1:p3")).recorded is True
        loaded = _load_untracked_sessions(path)
        assert [entry.pane_id for entry in loaded.entries] == ["w1:p2", "w1:p3"]
        assert sorted(p.name for p in path.parent.iterdir()) == [path.name]

    def test_record_without_path_does_nothing(self) -> None:
        outcome = _record_untracked_session(None, self._entry())
        assert outcome.recorded is False
        assert outcome.error is None

    @pytest.mark.parametrize(
        "content",
        ["{broken", '{"entries": [{"session_id": "S"}]}', '{"entries": [], "extra": 1}'],
    )
    def test_invalid_record_is_fail_loud(self, tmp_path: Path, content: str) -> None:
        path = tmp_path / HERDR_UNTRACKED_SESSIONS_FILENAME
        path.write_text(content, encoding="utf-8")
        with pytest.raises(CLIExecutionError, match="untracked session record is invalid"):
            _load_untracked_sessions(path)
        outcome = _record_untracked_session(path, self._entry())
        assert outcome.recorded is False
        assert outcome.error is not None

    def test_non_utf8_record_is_fail_loud(self, tmp_path: Path) -> None:
        """Review C2: undecodable bytes are normalized to CLIExecutionError with the path."""
        path = tmp_path / HERDR_UNTRACKED_SESSIONS_FILENAME
        path.write_bytes(b"\xff\xfe")
        with pytest.raises(CLIExecutionError) as error:
            _load_untracked_sessions(path)
        assert str(path) in error.value.stderr
        outcome = _record_untracked_session(path, self._entry())
        assert outcome.recorded is False
        assert outcome.error is not None
        assert path.read_bytes() == b"\xff\xfe"

    def test_resume_fails_loud_for_non_utf8_record(self, fake: FakeHerdr, tmp_path: Path) -> None:
        path = tmp_path / HERDR_UNTRACKED_SESSIONS_FILENAME
        path.write_bytes(b"\xff\xfe")
        with pytest.raises(CLIExecutionError) as error:
            _launch_herdr_pane(
                "herdr",
                _ORIGIN,
                workdir=tmp_path,
                resume_session_id=_SESSION_S,
                untracked_sessions_path=path,
            )
        assert str(path) in error.value.stderr
        assert fake.calls_of("split") == []

    def _write(self, path: Path, *entries: UntrackedSessionEntry) -> None:
        path.write_text(
            UntrackedSessionRegistry(entries=list(entries)).model_dump_json(), encoding="utf-8"
        )

    def test_resume_removes_record_for_gone_pane(self, fake: FakeHerdr, tmp_path: Path) -> None:
        path = tmp_path / HERDR_UNTRACKED_SESSIONS_FILENAME
        other = self._entry(session_id="other-session")
        self._write(path, self._entry(), other)
        launch = _launch_herdr_pane(
            "herdr",
            _ORIGIN,
            workdir=tmp_path,
            resume_session_id=_SESSION_S,
            untracked_sessions_path=path,
        )
        assert launch.session_panes_closed == []
        assert _load_untracked_sessions(path).entries == [other]

    def test_resume_closes_recorded_owned_pane(self, fake: FakeHerdr, tmp_path: Path) -> None:
        path = tmp_path / HERDR_UNTRACKED_SESSIONS_FILENAME
        fake.add("w1:p2", tokens={"kaji_origin": _ORIGIN, "kaji_run": "run-a"})
        self._write(path, self._entry())
        launch = _launch_herdr_pane(
            "herdr",
            _ORIGIN,
            workdir=tmp_path,
            resume_session_id=_SESSION_S,
            untracked_sessions_path=path,
        )
        assert launch.session_panes_closed == ["w1:p2"]
        assert "w1:p2" not in fake.panes
        assert _load_untracked_sessions(path).entries == []

    @pytest.mark.parametrize(
        ("tokens", "entry_origin", "refuse"),
        [
            ({"kaji_origin": _OTHER_ORIGIN, "kaji_run": "x"}, _ORIGIN, False),
            ({"kaji_origin": _ORIGIN, "kaji_run": "run-a"}, _OTHER_ORIGIN, False),
            ({}, _ORIGIN, False),
            ({"kaji_origin": _ORIGIN, "kaji_run": "run-a"}, _ORIGIN, True),
        ],
    )
    def test_resume_fails_for_unconfirmed_recorded_pane(
        self,
        fake: FakeHerdr,
        tmp_path: Path,
        tokens: dict[str, str],
        entry_origin: str,
        refuse: bool,
    ) -> None:
        path = tmp_path / HERDR_UNTRACKED_SESSIONS_FILENAME
        fake.add("w1:p2", tokens=tokens)
        if refuse:
            fake.refuse_close.add("w1:p2")
        self._write(path, self._entry(origin_pane=entry_origin))
        with pytest.raises(CLIExecutionError) as error:
            _launch_herdr_pane(
                "herdr",
                _ORIGIN,
                workdir=tmp_path,
                resume_session_id=_SESSION_S,
                untracked_sessions_path=path,
            )
        assert "w1:p2" in error.value.stderr
        assert _SESSION_S in error.value.stderr
        assert str(path) in error.value.stderr
        assert fake.calls_of("split") == []
        assert "w1:p2" in fake.panes
        assert len(_load_untracked_sessions(path).entries) == 1


# ============================================================
# Medium: OB reproduction and lifecycle through the fake Herdr
# ============================================================


@pytest.mark.medium
class TestResumeSessionLifecycle:
    def test_codex_resume_closes_source_pane_before_split(
        self, attempts: _Attempts, fake: FakeHerdr
    ) -> None:
        """OB → EB: the kept codex-fresh pane must not hold the session at resume."""
        fresh_session, fresh_meta = attempts.run("codex-fresh", "codex")
        fresh_pane = str(fresh_meta["pane_id"])
        assert fresh_session == _CODEX_SESSION
        assert fake.panes[fresh_pane].tokens["kaji_session"] == _CODEX_SESSION
        assert fresh_meta["session_marker"] == {"status": "marked", "error": None}

        resume_session, resume_meta = attempts.run(
            "codex-resume", "codex", session_id=fresh_session
        )

        assert resume_session == _CODEX_SESSION
        assert fresh_pane not in fake.panes
        assert _close_index(fake, fresh_pane) < _split_index(fake)
        assert resume_meta["kaji_session_panes_closed"] == [fresh_pane]
        assert fake.panes[str(resume_meta["pane_id"])].tokens["kaji_session"] == _CODEX_SESSION

    def test_claude_resume_follows_same_rule(self, attempts: _Attempts, fake: FakeHerdr) -> None:
        fresh_session, fresh_meta = attempts.run("claude-fresh", "claude")
        fresh_pane = str(fresh_meta["pane_id"])
        assert fresh_session is not None
        assert fake.panes[fresh_pane].tokens["kaji_session"] == fresh_session
        _, resume_meta = attempts.run("claude-resume", "claude", session_id=fresh_session)
        assert fresh_pane not in fake.panes
        assert resume_meta["kaji_session_panes_closed"] == [fresh_pane]

    def test_close_on_verdict_true_is_unchanged(self, attempts: _Attempts, fake: FakeHerdr) -> None:
        fresh_session, fresh_meta = attempts.run("codex-fresh", "codex", close_on_verdict=True)
        assert str(fresh_meta["pane_id"]) not in fake.panes
        assert "session_marker" not in fresh_meta
        assert fake.calls_of("report-metadata") and len(fake.calls_of("report-metadata")) == 1
        _, resume_meta = attempts.run(
            "codex-resume", "codex", session_id=fresh_session, close_on_verdict=True
        )
        assert resume_meta["kaji_session_panes_closed"] == []
        assert fake.owned_alive() == []

    def test_right_column_stays_at_two_with_intervening_step(
        self, attempts: _Attempts, fake: FakeHerdr
    ) -> None:
        fake.add("w1:user", x=100, y=50)  # 利用者の pane（kaji token なし）
        fresh_session, fresh_meta = attempts.run("codex-fresh", "codex")
        assert len(fake.owned_alive()) <= 2
        _, middle_meta = attempts.run("review", "claude")
        assert len(fake.owned_alive()) <= 2
        _, resume_meta = attempts.run("codex-resume", "codex", session_id=fresh_session)
        assert len(fake.owned_alive()) <= 2
        assert resume_meta["kaji_session_panes_closed"] == [fresh_meta["pane_id"]]
        assert resume_meta["kaji_agent_panes_pruned"] == []
        assert "w1:user" in fake.panes
        third_session, _ = attempts.run("extra", "claude")
        assert third_session is not None
        assert len(fake.owned_alive()) <= 2
        assert "w1:user" in fake.panes
        # 既存 prune（上から）が保たれる: middle の pane が最古として閉じられる。
        assert str(middle_meta["pane_id"]) not in fake.panes

    def test_never_sends_input_keys(self, attempts: _Attempts, fake: FakeHerdr) -> None:
        fresh_session, _ = attempts.run("codex-fresh", "codex")
        attempts.run("codex-resume", "codex", session_id=fresh_session)
        assert all(call[1] not in _INPUT_SUBCOMMANDS for call in fake.calls if len(call) > 1)
        runs = fake.calls_of("run")
        assert len(runs) == 2
        assert len({call[2] for call in runs}) == 2

    def test_marker_failure_closes_pane_and_keeps_session(
        self, attempts: _Attempts, fake: FakeHerdr
    ) -> None:
        def reject_second_marker(pane_id: str) -> None:
            fake.fail_report_metadata.add(pane_id)

        attempts.before_verdict = reject_second_marker
        session, meta = attempts.run("codex-fresh", "codex")
        assert session == _CODEX_SESSION
        assert str(meta["pane_id"]) not in fake.panes
        marker = meta["session_marker"]
        assert isinstance(marker, dict) and marker["status"] == "failed"
        assert meta["untracked_session_recorded"] is False

    @pytest.mark.parametrize("close_on_verdict", [False, True])
    def test_report_does_not_reclaim_changed_ownership(
        self, attempts: _Attempts, fake: FakeHerdr, close_on_verdict: bool
    ) -> None:
        """Design review R1: a pane taken over before the verdict stays with its owner."""
        foreign = {"kaji_origin": _OTHER_ORIGIN, "kaji_run": "foreign-run"}

        def take_over(pane_id: str) -> None:
            fake.panes[pane_id].tokens = dict(foreign)

        attempts.before_verdict = take_over
        session, meta = attempts.run("codex-fresh", "codex", close_on_verdict=close_on_verdict)
        pane_id = str(meta["pane_id"])
        assert session == _CODEX_SESSION
        assert len(fake.calls_of("report-metadata")) == 1  # 初回 marker のみ
        assert fake.panes[pane_id].tokens == foreign
        assert fake.calls_of("close") == []
        marker = meta["session_marker"]
        assert isinstance(marker, dict) and marker["status"] == "ownership_changed"
        assert meta["untracked_session_recorded"] is True
        entries = _load_untracked_sessions(attempts.registry).entries
        assert [(e.pane_id, e.reason) for e in entries] == [(pane_id, "ownership_changed")]

        attempts.before_verdict = None
        splits_before = len(fake.calls_of("split"))
        with pytest.raises(CLIExecutionError, match=pane_id):
            attempts.run("codex-resume", "codex", session_id=session)
        assert len(fake.calls_of("split")) == splits_before
        assert pane_id in fake.panes

    def test_double_failure_blocks_resume_until_pane_is_gone(
        self, attempts: _Attempts, fake: FakeHerdr, tmp_path: Path
    ) -> None:
        """Design review R2: token lost and close refused must not lead to a lock wait."""

        def break_pane(pane_id: str) -> None:
            fake.fail_report_metadata.add(pane_id)
            fake.refuse_close.add(pane_id)

        attempts.before_verdict = break_pane
        session, meta = attempts.run("codex-fresh", "codex")
        pane_id = str(meta["pane_id"])
        assert session == _CODEX_SESSION
        assert "kaji_session" not in fake.panes[pane_id].tokens
        assert meta["untracked_session_recorded"] is True
        entries = _load_untracked_sessions(attempts.registry).entries
        assert [(e.session_id, e.pane_id, e.reason) for e in entries] == [
            (_CODEX_SESSION, pane_id, "marker_failed_close_failed")
        ]

        attempts.before_verdict = None
        runs_before = len(fake.calls_of("run"))
        for _ in range(2):  # 同じ run の後続 step と、同じ記録を読む別 run 相当
            with pytest.raises(CLIExecutionError) as error:
                attempts.run("codex-resume", "codex", session_id=session)
            assert pane_id in error.value.stderr
            assert _CODEX_SESSION in error.value.stderr
        assert len(fake.calls_of("run")) == runs_before
        assert len(fake.calls_of("split")) == 1

        fake.panes.pop(pane_id)  # 利用者が手で閉じた
        resumed, _ = attempts.run("codex-resume", "codex", session_id=session)
        assert resumed == _CODEX_SESSION
        assert _load_untracked_sessions(attempts.registry).entries == []

    def test_true_close_failure_then_marker_failure_is_recorded(
        self, attempts: _Attempts, fake: FakeHerdr
    ) -> None:
        def break_pane(pane_id: str) -> None:
            fake.fail_report_metadata.add(pane_id)
            fake.refuse_close.add(pane_id)

        attempts.before_verdict = break_pane
        _, meta = attempts.run("codex-fresh", "codex", close_on_verdict=True)
        entries = _load_untracked_sessions(attempts.registry).entries
        assert [(e.pane_id, e.reason) for e in entries] == [
            (str(meta["pane_id"]), "close_failed_marker_failed")
        ]
        assert "close_error" in meta

    def test_timeout_close_failure_is_recorded(self, attempts: _Attempts, fake: FakeHerdr) -> None:
        fake.on_run = lambda pane_id: fake.refuse_close.add(pane_id)
        with (
            patch(
                f"{_HERDR}._resolve_abnormal_exit_session",
                return_value=SessionResolution(_CODEX_SESSION),
            ),
            pytest.raises(StepTimeoutError),
        ):
            attempts.run("codex-fresh", "codex", timeout=0)
        entries = _load_untracked_sessions(attempts.registry).entries
        assert [(e.session_id, e.reason) for e in entries] == [
            (_CODEX_SESSION, "abnormal_exit_close_failed")
        ]
        assert attempts.metadata()["untracked_session_recorded"] is True

    def test_foreign_origin_holder_fails_resume_before_launch(
        self, attempts: _Attempts, fake: FakeHerdr
    ) -> None:
        fake.add(
            "w9:p4",
            tokens={"kaji_origin": _OTHER_ORIGIN, "kaji_run": "r", "kaji_session": _SESSION_S},
        )
        with pytest.raises(CLIExecutionError, match="w9:p4"):
            attempts.run("codex-resume", "codex", session_id=_SESSION_S)
        assert "w9:p4" in fake.panes
        assert fake.calls_of("split") == []
        assert fake.calls_of("run") == []

    def test_report_os_error_keeps_verdict_and_session(
        self, attempts: _Attempts, fake: FakeHerdr
    ) -> None:
        """Review C1: an OSError on the post-verdict re-marker must not replace the result."""

        def run(
            herdr: str, arguments: list[str], *, workdir: Path | None = None
        ) -> subprocess.CompletedProcess[str]:
            if arguments[:2] == ["pane", "report-metadata"] and fake.calls_of("report-metadata"):
                raise FileNotFoundError(2, "Herdr executable disappeared")
            return fake(herdr, arguments, workdir=workdir)

        with patch(f"{_HERDR}._run_herdr", side_effect=run):
            session, meta = attempts.run("codex-fresh", "codex")
        assert session == _CODEX_SESSION
        marker = meta["session_marker"]
        assert isinstance(marker, dict) and marker["status"] == "failed"
        assert "Herdr executable disappeared" in str(marker["error"])
        # marker failed -> ownership-checked close still runs, so no holder is left.
        assert str(meta["pane_id"]) not in fake.panes
        assert meta["untracked_session_recorded"] is False

    def test_non_utf8_record_keeps_verdict_and_session(
        self, attempts: _Attempts, fake: FakeHerdr
    ) -> None:
        """Review C2: an undecodable registry is a diagnostic, not a step failure."""

        def break_pane(pane_id: str) -> None:
            attempts.registry.write_bytes(b"\xff\xfe")
            fake.fail_report_metadata.add(pane_id)
            fake.refuse_close.add(pane_id)

        attempts.before_verdict = break_pane
        session, meta = attempts.run("codex-fresh", "codex")
        assert session == _CODEX_SESSION
        assert meta["untracked_session_recorded"] is False
        assert str(attempts.registry) in str(meta["untracked_record_error"])
        assert attempts.registry.read_bytes() == b"\xff\xfe"

    def test_non_utf8_record_keeps_timeout_session(
        self, attempts: _Attempts, fake: FakeHerdr
    ) -> None:
        def break_pane(pane_id: str) -> None:
            attempts.registry.write_bytes(b"\xff\xfe")
            fake.refuse_close.add(pane_id)

        fake.on_run = break_pane
        with (
            patch(
                f"{_HERDR}._resolve_abnormal_exit_session",
                return_value=SessionResolution(_CODEX_SESSION),
            ),
            pytest.raises(StepTimeoutError) as error,
        ):
            attempts.run("codex-fresh", "codex", timeout=0)
        resolution = error.value.session_resolution
        assert resolution is not None and resolution.session_id == _CODEX_SESSION
        meta = attempts.metadata()
        assert meta["untracked_session_recorded"] is False
        assert str(attempts.registry) in str(meta["untracked_record_error"])
