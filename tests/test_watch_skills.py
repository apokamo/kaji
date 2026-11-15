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


def test_triage_does_not_answer_agent_questions() -> None:
    text = TRIAGE.read_text(encoding="utf-8")
    assert "agent の質問には答えない" in text
    assert "--retry-failed-step" in text
    assert "kaji watch record" in text


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
