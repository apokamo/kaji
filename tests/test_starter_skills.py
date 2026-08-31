"""Static contracts for starter maintenance skills and runbooks."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.medium
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("name", "statuses"),
    [
        ("update-starter", ("PASS", "ABORT")),
        ("review-starter-update", ("PASS", "RETRY", "ABORT")),
        ("release-starter", ("PASS", "ABORT")),
    ],
)
def test_skill_frontmatter_and_verdict_vocabulary(name: str, statuses: tuple[str, ...]) -> None:
    skill_path = ROOT / ".claude" / "skills" / name / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    assert metadata["name"] == name
    assert metadata["description"]
    for status in statuses:
        assert status in body


def test_skill_guardrails_are_explicit() -> None:
    update = (ROOT / ".claude/skills/update-starter/SKILL.md").read_text(encoding="utf-8")
    review = (ROOT / ".claude/skills/review-starter-update/SKILL.md").read_text(encoding="utf-8")
    release = (ROOT / ".claude/skills/release-starter/SKILL.md").read_text(encoding="utf-8")

    assert all(
        term in update for term in ("3 区分", "lockfile", "review 前", "push", "コピーしない")
    )
    assert all(
        term in review for term in ("別 session", "修正しない", "target", "base", "candidate")
    )
    assert all(
        term in release
        for term in (
            "resolve-verdict",
            "独立 review PASS",
            "人間の明示承認",
            "git push --atomic",
            "force push",
            "kaji-vX.Y.Z-rN",
            "部分成功",
        )
    )
    assert "N/A を release-plan より先に分岐" in release
    assert "release-plan を呼ばない" in release


def test_release_notes_template_has_required_sections() -> None:
    text = (ROOT / ".claude/skills/release-starter/templates/release-notes.md").read_text(
        encoding="utf-8"
    )

    for heading in (
        "対応 kaji Release",
        "反映内容",
        "N/A とした変更と理由",
        "BREAKING 対応",
        "検証 evidence",
        "snapshot の利用方法",
    ):
        assert heading in text


def test_agent_skill_entries_resolve_to_canonical_skills() -> None:
    for name in ("update-starter", "review-starter-update", "release-starter"):
        entry = ROOT / ".agents" / "skills" / name
        assert entry.is_symlink()
        assert entry.resolve() == (ROOT / ".claude" / "skills" / name).resolve()


def test_starter_sync_runbook_contract_and_links() -> None:
    runbook = (ROOT / "docs/operations/release/starter-sync-runbook.md").read_text(encoding="utf-8")
    release_runbook = (ROOT / "docs/operations/release/runbook.md").read_text(encoding="utf-8")
    docs_index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    release_skill = (ROOT / ".claude/skills/release/SKILL.md").read_text(encoding="utf-8")

    assert "## Managed starters" in runbook
    assert "kaji-v0.12.1" in runbook
    assert "follow-up Issue" in runbook
    assert "starter-sync-runbook.md" in release_runbook
    assert "starter-sync-runbook.md" in docs_index
    assert "update-starter" in release_skill


def test_update_starter_references_task_plan_and_drops_old_pending_abort() -> None:
    """Issue #423: 古い `PENDING` で ABORT する記述は消え、task-plan 参照に置き換わる。"""
    update = (ROOT / ".claude/skills/update-starter/SKILL.md").read_text(encoding="utf-8")

    assert "古い" not in update
    assert "kaji starter task-plan" in update
    assert "active_target" in update
    assert "covered_targets" in update


def test_release_starter_references_completion_bookkeeping() -> None:
    """Issue #423: release-starter が task-plan completion / close_allowed を参照する。"""
    release = (ROOT / ".claude/skills/release-starter/SKILL.md").read_text(encoding="utf-8")
    preflight = (
        ROOT / ".claude/skills/release-starter/references/preflight-and-recovery.md"
    ).read_text(encoding="utf-8")

    for term in ("kaji starter task-plan", "completion", "close_allowed", "covered_targets"):
        assert term in release
        assert term in preflight


def test_release_starter_wires_pending_tasks_and_separates_planner_authority() -> None:
    """review-code Must Fix 4 回帰: release-plan の `tracking_issue_has_pending_tasks` の
    導出元と、`remaining_actions`(update_state_table/close_tracking_issue/promote_next_task)
    が task-plan に対して参考情報に留まる(実行の正本ではない)ことを skill / reference が
    明記する。
    """
    release = (ROOT / ".claude/skills/release-starter/SKILL.md").read_text(encoding="utf-8")
    preflight = (
        ROOT / ".claude/skills/release-starter/references/preflight-and-recovery.md"
    ).read_text(encoding="utf-8")

    assert "tracking_issue_has_pending_tasks" in release
    assert "tracking_issue_has_pending_tasks" in preflight
    assert "open" in preflight  # 導出規則: 現在の batch 以外に open 行が残っているか

    for term in ("直接実行しない", "正本"):
        assert term in preflight
    assert "参考情報" in release
    assert "参考情報" in preflight


def test_release_skill_references_tracking_plan_and_pending_on_abort() -> None:
    """Issue #423: /release Step 8 が tracking-plan と ABORT 時の PENDING 維持を明記する。"""
    release_skill = (ROOT / ".claude/skills/release/SKILL.md").read_text(encoding="utf-8")

    assert "kaji starter tracking-plan" in release_skill
    assert "CREATE" in release_skill
    assert "APPEND" in release_skill
    assert "PENDING" in release_skill


def test_starter_sync_runbook_documents_v1_schema_and_new_rules() -> None:
    """Issue #423: runbook が新 schema・複数候補 fail-closed・束ね規則・close 条件を記載する。"""
    runbook = (ROOT / "docs/operations/release/starter-sync-runbook.md").read_text(encoding="utf-8")

    assert "<!-- kaji-starter-sync: v1 -->" in runbook
    assert "starter-sync" in runbook  # label 名（発見キー）
    assert "未完了期間ごとに 1 件" in runbook
    assert "束ね" in runbook
    assert "close 条件" in runbook
    assert "fail-closed" in runbook
    assert "selected_issue_id" in runbook


def test_starter_sync_label_registered_and_counts_consistent() -> None:
    """Issue #423: `.github/labels.yml` に `starter-sync` があり、labels.md の件数と整合する。"""
    labels_yml = (ROOT / ".github/labels.yml").read_text(encoding="utf-8")
    labels_md = (ROOT / "docs/dev/labels.md").read_text(encoding="utf-8")

    assert 'name: "starter-sync"' in labels_yml
    assert "meta (10)" in labels_md
    assert "meta (10)" in labels_yml
    assert "= 29" in labels_md
    assert "合計 29" in labels_yml


def test_managed_starter_sets_match_release_notes_template() -> None:
    runbook = (ROOT / "docs/operations/release/starter-sync-runbook.md").read_text(encoding="utf-8")
    release_skill = (ROOT / ".claude/skills/release/SKILL.md").read_text(encoding="utf-8")
    expected_repositories = {
        "apokamo/kaji-starter-python",
        "apokamo/kaji-starter-typescript",
    }

    runbook_repositories = {
        row.split("`")[1]
        for row in runbook.splitlines()
        if row.startswith("| `apokamo/kaji-starter-")
    }
    release_repositories = {
        cells[1]
        for line in release_skill.splitlines()
        if line.startswith("| apokamo/kaji-starter-")
        and (cells := [cell.strip() for cell in line.split("|")])
    }

    assert runbook_repositories == expected_repositories
    assert release_repositories == expected_repositories
    assert runbook_repositories == release_repositories
