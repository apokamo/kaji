"""Medium tests: incident 記録の handler 統合（Issue #304 第1層）.

FakeProvider + 失敗 run artifact で新規起票 / 再発追記 / crash window / 再入ガード /
fail-open / backfill / transient 即クローズ / 非 GitHub provider を検証する。
"""

from __future__ import annotations

import io
import json
import subprocess as _sp
from pathlib import Path

import pytest

from kaji_harness.models import Step, Workflow
from kaji_harness.providers.models import Comment, Issue, Label
from kaji_harness.recovery.handler import RecoveryHandler
from kaji_harness.recovery.incident import (
    occurrences_path,
    parse_occurrence_markers,
    read_occurrences,
    render_identity_marker,
)
from kaji_harness.recovery.models import RECOVERY_FILE, RECOVERY_WAIT_SECONDS, read_recovery_json
from kaji_harness.recovery.signature import compute_signature
from kaji_harness.recovery.snapshot import collect_snapshot

pytestmark = pytest.mark.medium

_ISSUE = "304"


# 既存 test_recovery_handler.py と同型の run 構築（verdict_exception / VerdictNotFound = candidate）。
def _workflow() -> Workflow:
    return Workflow(
        name="dev",
        description="",
        execution_policy="auto",
        steps=[
            Step(
                id="implement", skill="issue-implement", agent="claude", on={"PASS": "review-code"}
            ),
            Step(id="review-code", skill="issue-review-code", agent="codex", on={"PASS": "end"}),
        ],
    )


def _git_repo(tmp_path: Path, branch: str = "feat/304") -> Path:
    wt = tmp_path / "worktree"
    wt.mkdir()
    _sp.run(["git", "init", "-q", f"--initial-branch={branch}", str(wt)], check=True)
    return wt


def _seed_state(tmp_path: Path, worktree: Path, branch: str = "feat/304") -> None:
    d = tmp_path / ".kaji-artifacts" / _ISSUE
    d.mkdir(parents=True, exist_ok=True)
    (d / "session-state.json").write_text(
        json.dumps(
            {
                "issue_number": _ISSUE,
                "sessions": {},
                "step_history": [],
                "cycle_counts": {},
                "last_completed_step": "implement",
                "last_transition_verdict": None,
                "worktree_dir": str(worktree),
                "branch_name": branch,
            }
        ),
        encoding="utf-8",
    )


_ERROR = "VerdictNotFound: No verdict delimiter found in output. Last 500 chars: unique-tail-260712010000"


def _build_run(tmp_path: Path, run_id: str = "260712010000", *, error: str = _ERROR) -> Path:
    run_dir = tmp_path / ".kaji-artifacts" / _ISSUE / "runs" / run_id
    run_dir.mkdir(parents=True)
    events = [
        {"event": "workflow_start", "issue": _ISSUE, "workflow": "dev", "schema_version": 1},
        {
            "event": "failure_event",
            "kind": "verdict_exception",
            "step_id": "review-code",
            "exception_type": "VerdictNotFound",
            "cycle_name": None,
            "synthetic": True,
        },
        {"event": "workflow_end", "status": "ERROR", "error": error},
    ]
    (run_dir / "run.log").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    attempt = run_dir / "steps" / "review-code" / "attempt-001"
    attempt.mkdir(parents=True)
    (attempt / "result.json").write_text(
        json.dumps(
            {
                "step_id": "review-code",
                "attempt": 1,
                "status": "ABORT",
                "error": error,
                "synthetic": True,
            }
        ),
        encoding="utf-8",
    )
    return run_dir


class _IncidentProvider:
    """search / comment / create / edit / close を記録する incident-capable FakeProvider。"""

    is_readonly = False

    def __init__(
        self, *, issues: list[Issue] | None = None, search_error: Exception | None = None
    ) -> None:
        self._issues = issues or []
        self._comments: dict[str, list[Comment]] = {}
        self.created: list[tuple[str, str, list[str]]] = []
        self.comments_posted: list[tuple[str, str]] = []
        self.edits: list[tuple[str, list[str], list[str]]] = []
        self.closed: list[tuple[str, str | None]] = []
        self._search_error = search_error
        self._next_id = 900

    def preload_comments(self, issue_id: str, comments: list[Comment]) -> None:
        self._comments[issue_id] = list(comments)

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        self.comments_posted.append((issue_id, body))
        c = Comment(
            author="", body=body, created_at="", ref=f"https://x/c/{len(self.comments_posted)}"
        )
        self._comments.setdefault(issue_id, []).append(c)
        return c

    def create_issue(
        self, *, title: str, body: str, labels: list[str] | None = None, slug: str | None = None
    ) -> Issue:
        self._next_id += 1
        iid = str(self._next_id)
        self.created.append((title, body, list(labels or [])))
        return Issue(
            id=iid,
            title=title,
            body=body,
            state="open",
            labels=[Label(name=x) for x in (labels or [])],
        )

    def search_issues_all(self, *, labels: list[str], state: str = "all") -> list[Issue]:
        if self._search_error is not None:
            raise self._search_error
        return list(self._issues)

    def list_issue_comments_all(self, issue_id: str) -> list[Comment]:
        return list(self._comments.get(issue_id, []))

    def edit_issue(
        self, issue_id: str, *, title=None, body=None, add_labels=None, remove_labels=None
    ) -> Issue:
        self.edits.append((issue_id, list(add_labels or []), list(remove_labels or [])))
        return Issue(id=issue_id, title="", body="", state="open")

    def close_issue(self, issue_id: str, reason: str | None = None) -> Issue:
        self.closed.append((issue_id, reason))
        return Issue(id=issue_id, title="", body="", state="closed")


def _handler(
    tmp_path: Path,
    run_dir: Path,
    *,
    provider: object,
    auto_recover: bool = False,
    child_launcher=None,
    stderr=None,
) -> RecoveryHandler:
    return RecoveryHandler(
        workflow=_workflow(),
        workflow_path=Path("dev.yaml"),
        issue_id=_ISSUE,
        issue_ref="#304",
        artifacts_dir=tmp_path / ".kaji-artifacts",
        run_dir=run_dir,
        workdir=tmp_path,
        provider=provider,  # type: ignore[arg-type]
        auto_recover=auto_recover,
        wait_seconds=0,
        sleep=lambda _s: None,
        child_launcher=child_launcher or (lambda _a, _c: 0),
        stderr=stderr or io.StringIO(),
    )


def _occurrence_comments(provider: _IncidentProvider) -> list[tuple[str, str]]:
    return [(i, b) for i, b in provider.comments_posted if "kaji-incident-occurrence" in b]


def _existing_incident_issue(
    tmp_path: Path,
    run_dir: Path,
    *,
    issue_id: str = "800",
    state: str = "open",
    labels: tuple[str, ...] = ("incident",),
) -> Issue:
    """当該 run の署名と同一の identity marker を持つ既存 incident issue を構築する。"""
    snap = collect_snapshot(
        run_dir=run_dir,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        issue_id=_ISSUE,
        provider_available=True,
    )
    from kaji_harness.recovery.classify import classify_failure

    sig = compute_signature(snap, classify_failure(snap))
    body = render_identity_marker(sig) + "\n\n```kaji-fingerprint\n" + sig.fingerprint + "\n```\n"
    return Issue(
        id=issue_id,
        title="incident",
        body=body,
        state=state,
        labels=[Label(name=x) for x in labels],
    )


# --- 新規起票経路 ---


def test_new_incident_is_created_with_labels_and_first_occurrence(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    provider = _IncidentProvider()

    result = _handler(tmp_path, run_dir, provider=provider).run()

    assert len(provider.created) == 1
    _title, _body, labels = provider.created[0]
    assert labels == ["incident", "incident:investigating"]
    occ = _occurrence_comments(provider)
    assert len(occ) == 1  # 起票直後の初回 occurrence コメント
    assert "`1`" in occ[0][1]  # N=1
    persisted = read_recovery_json(run_dir / RECOVERY_FILE)
    assert persisted.incident_action == "created"
    assert persisted.incident_ref == "901"
    # ローカル occurrence 記録が残る。
    assert occurrences_path(tmp_path / ".kaji-artifacts").is_file()
    events = [json.loads(x) for x in (run_dir / "run.log").read_text().splitlines()]
    assert any(e["event"] == "incident_recorded" for e in events)
    # triage decision / comment は不変。
    assert result.decision.decision == "comment_only"


# --- 再発経路 ---


def test_recurrence_appends_occurrence_without_reopen(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    existing = _existing_incident_issue(tmp_path, run_dir, issue_id="800")
    provider = _IncidentProvider(issues=[existing])
    # 既存 incident には過去 run の occurrence コメントが 1 件あるとする。
    from kaji_harness.recovery.classify import classify_failure

    snap = collect_snapshot(
        run_dir=run_dir,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        issue_id=_ISSUE,
        provider_available=True,
    )
    sig = compute_signature(snap, classify_failure(snap))
    from kaji_harness.recovery.incident import render_occurrence_marker

    provider.preload_comments(
        "800",
        [
            Comment(
                author="",
                body=render_occurrence_marker(sig, run_id="r_prev", source_issue="304"),
                created_at="",
            )
        ],
    )

    _handler(tmp_path, run_dir, provider=provider).run()

    assert provider.created == []  # 新規起票しない
    assert provider.closed == []  # reopen しない（そもそも open）
    occ = _occurrence_comments(provider)
    assert len(occ) == 1
    assert occ[0][0] == "800"
    assert "`2`" in occ[0][1]  # N=2（r_prev + 今回）
    persisted = read_recovery_json(run_dir / RECOVERY_FILE)
    assert persisted.incident_action == "recurred"


# --- crash window の再実行 ---


def test_crash_window_reexec_does_not_inflate_count(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    existing = _existing_incident_issue(tmp_path, run_dir, issue_id="800")
    provider = _IncidentProvider(issues=[existing])
    from kaji_harness.recovery.classify import classify_failure
    from kaji_harness.recovery.incident import render_occurrence_marker

    snap = collect_snapshot(
        run_dir=run_dir,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        issue_id=_ISSUE,
        provider_available=True,
    )
    sig = compute_signature(snap, classify_failure(snap))
    # remote には既に「今回 run」の marker が投稿済み（recovery.json 保存前に中断した想定）。
    provider.preload_comments(
        "800",
        [
            Comment(
                author="",
                body=render_occurrence_marker(sig, run_id=run_dir.name, source_issue="304"),
                created_at="",
            )
        ],
    )

    _handler(tmp_path, run_dir, provider=provider).run()

    occ = _occurrence_comments(provider)
    # 再投稿はされうるが、ユニーク run_id 件数（N）は 1 のまま。
    assert "`1`" in occ[0][1]


# --- 再入ガード ---


def test_reentry_guard_skips_remote_when_incident_ref_present(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    # 1 回目: 新規起票。
    provider1 = _IncidentProvider()
    _handler(tmp_path, run_dir, provider=provider1).run()
    assert len(provider1.created) == 1
    # 2 回目: recovery.json に incident_ref が残っている → remote スキップ。
    provider2 = _IncidentProvider()
    _handler(tmp_path, run_dir, provider=provider2).run()
    assert provider2.created == []
    assert _occurrence_comments(provider2) == []


# --- fail-open ---


def test_search_failure_is_fail_open(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    stderr = io.StringIO()
    provider = _IncidentProvider(search_error=RuntimeError("gh api down"))

    result = _handler(tmp_path, run_dir, provider=provider, stderr=stderr).run()

    # triage decision / comment は不変。
    assert result.decision.decision == "comment_only"
    assert provider.created == []
    # ローカル occurrence 記録は残る。
    assert occurrences_path(tmp_path / ".kaji-artifacts").is_file()
    assert "incident recording failed" in stderr.getvalue()
    events = [json.loads(x) for x in (run_dir / "run.log").read_text().splitlines()]
    assert any(e["event"] == "incident_recording_failed" for e in events)


# --- backfill ---


def test_backfill_includes_prior_run_id_after_create_failure(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    # 1 回目: create_issue が失敗 → ローカル記録のみ。
    run1 = _build_run(tmp_path, "260712010000")

    class _FailCreate(_IncidentProvider):
        def create_issue(self, **kw):  # type: ignore[override]
            raise RuntimeError("create failed")

    _handler(tmp_path, run1, provider=_FailCreate()).run()
    assert read_occurrences(tmp_path / ".kaji-artifacts")  # ローカル記録あり

    # 2 回目: 同一署名の別 run。今度は create 成功 → 初回 occurrence に過去 run_id を backfill。
    run2 = _build_run(tmp_path, "260712020000")
    provider = _IncidentProvider()
    _handler(tmp_path, run2, provider=provider).run()

    occ = _occurrence_comments(provider)
    markers = parse_occurrence_markers(occ[0][1])
    run_ids = {m.run_id for m in markers}
    assert "260712020000" in run_ids  # 今回
    assert "260712010000" in run_ids  # backfill された過去 run


# --- transient 即クローズ ---


def test_transient_close_on_created_and_child_complete(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    provider = _IncidentProvider()

    def _launch(_argv, _cwd) -> int:
        return 0  # COMPLETE

    handler = RecoveryHandler(
        workflow=_workflow(),
        workflow_path=Path("dev.yaml"),
        issue_id=_ISSUE,
        issue_ref="#304",
        artifacts_dir=tmp_path / ".kaji-artifacts",
        run_dir=run_dir,
        workdir=tmp_path,
        provider=provider,  # type: ignore[arg-type]
        auto_recover=True,
        wait_seconds=RECOVERY_WAIT_SECONDS,
        sleep=lambda _s: None,
        child_launcher=_launch,
        stderr=io.StringIO(),
    )
    result = handler.run()

    assert result.decision.decision == "resume"
    incident_id = provider.created and "901"
    # transient ラベル付与 + investigating 除去 + close。
    assert provider.edits, "transient label edit expected"
    eid, add, remove = provider.edits[0]
    assert eid == incident_id
    assert "incident:cause:transient" in add
    assert "incident:investigating" in remove
    assert provider.closed and provider.closed[0][0] == incident_id
    persisted = read_recovery_json(run_dir / RECOVERY_FILE)
    assert persisted.incident_transient_closed is True


def test_recurred_incident_is_not_transient_closed(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    existing = _existing_incident_issue(tmp_path, run_dir, issue_id="800")
    provider = _IncidentProvider(issues=[existing])

    handler = RecoveryHandler(
        workflow=_workflow(),
        workflow_path=Path("dev.yaml"),
        issue_id=_ISSUE,
        issue_ref="#304",
        artifacts_dir=tmp_path / ".kaji-artifacts",
        run_dir=run_dir,
        workdir=tmp_path,
        provider=provider,  # type: ignore[arg-type]
        auto_recover=True,
        wait_seconds=RECOVERY_WAIT_SECONDS,
        sleep=lambda _s: None,
        child_launcher=lambda _a, _c: 0,
        stderr=io.StringIO(),
    )
    result = handler.run()

    assert result.decision.incident_action == "recurred"
    # recurred は集約先の履歴が transient とは限らないため close しない。
    assert provider.closed == []
    assert provider.edits == []


# --- 非 GitHub provider ---


class _PlainProvider:
    """search 能力を持たない最小 provider（= 非 GitHub）。"""

    is_readonly = False

    def __init__(self) -> None:
        self.created: list = []
        self.comments: list = []

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        self.comments.append(body)
        return Comment(author="", body=body, created_at="", ref="")

    def create_issue(self, *, title, body, labels=None, slug=None) -> Issue:
        self.created.append(title)
        return Issue(id="1", title=title, body=body, state="open")


def test_non_github_provider_records_locally_only(tmp_path: Path) -> None:
    wt = _git_repo(tmp_path)
    _seed_state(tmp_path, wt)
    run_dir = _build_run(tmp_path)
    provider = _PlainProvider()

    _handler(tmp_path, run_dir, provider=provider).run()

    assert provider.created == []  # 起票 no-op
    assert occurrences_path(tmp_path / ".kaji-artifacts").is_file()  # ローカル記録のみ
