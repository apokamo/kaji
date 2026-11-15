"""Small tests: skill contracts for #543 (watch-triage forbidden list, issue-close labels)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.small

ROOT = Path(__file__).resolve().parent.parent
TRIAGE = ROOT / ".claude" / "skills" / "watch-triage" / "SKILL.md"
ISSUE_CLOSE = ROOT / ".claude" / "skills" / "issue-close" / "SKILL.md"
DESIGN = ROOT / "draft" / "design" / "issue-543-feat-series-issue-cli-skill.md"
_SECTION = re.compile(
    r"<!-- watch-triage-forbidden:start -->(.*?)<!-- watch-triage-forbidden:end -->", re.S
)


def _forbidden_ids(path: Path) -> set[str]:
    match = _SECTION.search(path.read_text(encoding="utf-8"))
    assert match is not None, f"{path}: forbidden section not found"
    ids: set[str] = set()
    for line in match.group(1).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] not in ("ID", "") and not cells[0].startswith("-"):
            ids.add(cells[0])
    return ids


def test_triage_forbidden_list_matches_design() -> None:
    expected = {
        "merge",
        "force-push",
        "rebase",
        "close",
        "labels",
        "issue-body",
        "kaji-state",
        "series-yaml",
        "code",
        "model-switch",
    }
    assert _forbidden_ids(TRIAGE) == expected
    if DESIGN.is_file():  # 設計書は merge 前の draft にだけある
        assert _forbidden_ids(DESIGN) == expected


def test_triage_answers_only_class_1_and_2_questions() -> None:
    """#545 A4: 絶対規則 1 は分類 1・2 だけ答え、分類 3 と迷ったものは保守者に回す。"""
    text = TRIAGE.read_text(encoding="utf-8")
    assert "agent の質問には答えない" not in text
    assert "分類 1・2 だけ答える" in text
    assert "分類 3 と、分類に迷ったものは答えずに報告して保守者に回す" in text
    assert "<!-- kaji-watch-triage: answer key=<event key> -->" in text
    assert "--retry-failed-step" in text
    assert "kaji watch record" in text
    assert "kaji watch retry" in text
    assert "retry-gate:" in text
    assert "kaji watch recoveries --issue <issue>" in text


AUTO_SECTION = re.compile(
    r"<!-- watch-triage-auto-recovery:start -->(.*?)<!-- watch-triage-auto-recovery:end -->", re.S
)
DESIGN_545 = ROOT / "draft" / "design" / "issue-545-feat-skills-skill.md"
_UNIT_NAMES = {"issue": "issue", "run": "run_id", "step": "step", "attempt": "attempt"}
_UNIT_NAMES["anchor"] = "anchor"


def _auto_table(path: Path) -> dict[str, tuple[tuple[str, ...], int]]:
    match = AUTO_SECTION.search(path.read_text(encoding="utf-8"))
    assert match is not None, f"{path}: auto-recovery section not found"
    table: dict[str, tuple[tuple[str, ...], int]] = {}
    for line in match.group(1).strip().splitlines()[2:]:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        unit = tuple(_UNIT_NAMES[part.strip()] for part in cells[3].split("・"))
        table[cells[0]] = (unit, int(cells[4]))
    return table


def test_triage_auto_recovery_table_matches_code_and_design() -> None:
    from kaji_harness.watch.constants import AUTO_RECOVERY_LIMITS

    assert _auto_table(TRIAGE) == AUTO_RECOVERY_LIMITS
    if DESIGN_545.is_file():  # 設計書は merge 前の draft にだけある
        assert _auto_table(DESIGN_545) == AUTO_RECOVERY_LIMITS
        design = AUTO_SECTION.search(DESIGN_545.read_text(encoding="utf-8"))
        skill = AUTO_SECTION.search(TRIAGE.read_text(encoding="utf-8"))
        assert design is not None and skill is not None
        assert design.group(1) == skill.group(1)


def _table_after(text: str, heading: str) -> list[str]:
    body = text.split(heading, 1)[1]
    rows: list[str] = []
    started = False
    for line in body.splitlines():
        if line.startswith("|"):
            started = True
            rows.append(line)
        elif started:
            break
    return rows[2:]


def test_triage_retry_child_table_matches_classifier() -> None:
    from kaji_harness.watch.recovery import RETRY_CHILD_STATES

    text = TRIAGE.read_text(encoding="utf-8")
    rows = _table_after(text, "#### やり直しと起動の確認")
    assert len(rows) == 5
    found = [re.search(r"（`([a-z_]+)`", row) for row in rows]
    assert tuple(m.group(1) for m in found if m is not None) == RETRY_CHILD_STATES
    assert "latest_run.recovery.parent_run_id" in text


def test_triage_usage_limit_table() -> None:
    text = TRIAGE.read_text(encoding="utf-8")
    rows = _table_after(text, "#### usage limit の予定時刻の前後")
    assert len(rows) == 9
    assert "**古い予定時刻を `wait` に渡さない**" in text
    assert "**`retry` より先に** `record --outcome wait --until <予定時刻>`" in text


def test_issue_close_calls_watch_labels() -> None:
    text = ISSUE_CLOSE.read_text(encoding="utf-8")
    assert "python -m kaji_harness.scripts.watch_labels mark-done" in text
    assert "python -m kaji_harness.scripts.watch_labels mark-follow-up" in text


RUN_WATCHED = ROOT / ".claude" / "skills" / "run-watched" / "SKILL.md"


def test_run_watched_forbids_hand_written_yaml_and_direct_launch() -> None:
    text = RUN_WATCHED.read_text(encoding="utf-8")
    assert "series YAML と draft を手で書かない" in text
    assert "`kaji run` / `kaji run-series` を直接起動しない" in text
    assert "python -m kaji_harness.scripts.run_watched plan" in text
    assert "python -m kaji_harness.scripts.run_watched execute --draft" in text
    link = ROOT / ".agents" / "skills" / "run-watched"
    assert link.is_symlink() and link.resolve() == RUN_WATCHED.parent.resolve()
