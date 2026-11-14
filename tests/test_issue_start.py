"""Small tests for the deterministic ``issue-start`` script (Issue #569).

- SKILL.md の複数 bash block 間でシェル変数を受け渡さない（#560 の回帰）
- worktree / symlink / NOTE / 報告コメントの判定関数
- PASS / ABORT verdict の契約
- 全 workflow の ``start`` step が exec_script 経路になり、遷移と recovery gate が不変
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from kaji_harness.issue_start import (
    BRANCH_LABEL,
    DIRECTORY_LABEL,
    VALID_STATUSES,
    ReportDecision,
    ReportFacts,
    ReportIdentity,
    ReportMatch,
    SymlinkState,
    WorktreeEntry,
    WorktreeState,
    build_abort_verdict,
    build_pass_verdict,
    classify_start_report,
    classify_start_reports,
    classify_symlink,
    classify_worktree,
    find_env_mismatches,
    has_worktree_note,
    parse_report_identity,
    parse_worktree_porcelain,
    render_report,
)
from kaji_harness.models import Verdict
from kaji_harness.providers.context import build_worktree_dir, build_worktree_note_body
from kaji_harness.providers.markers import build_kaji_verdict_marker, parse_kaji_verdict_marker
from kaji_harness.providers.models import Comment, IssueContext
from kaji_harness.recovery.gates import non_resumable_skill_hits
from kaji_harness.verdict import (
    dump_verdict_yaml,
    parse_verdict,
    validate_verdict_fields,
)
from kaji_harness.workflow import load_workflow

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ISSUE_START_SKILL = _REPO_ROOT / ".claude" / "skills" / "issue-start" / "SKILL.md"
_START_MARKER = "<!-- kaji-verdict: step=start status=PASS -->"

# #569 の実 start コメント（agent が現行テンプレートで投稿した旧形式）。
_LEGACY_REPORT = """<!-- kaji-verdict: step=start status=PASS -->
## Worktree セットアップ完了

| 項目 | 値 |
|------|-----|
| Issue | #569 |
| ブランチ | fix/569 |
| ディレクトリ | ../kaji-fix-569 |
| 基点 | origin/main（004114b） |
| venv | シンボリックリンク作成済み |
| config overlay | シンボリックリンク作成済み |
| メタ情報 | Issue本文に追記済み |

### 次のステップ

このタスクに関する今後のコマンドは、すべて以下のディレクトリ内で実行してください:

cd ../kaji-fix-569

### クリーンアップ（作業完了後）

作業が完了したら `/issue-close 569` を実行してください。

---VERDICT---
status: PASS
reason: |
  Worktree 構築成功
 evidence: |
  fetch 直後の origin/main（004114b）との FETCH_HEAD 一致を確認して fix/569 を作成しました。
suggestion: |
---END_VERDICT---"""

_IDENTITY = ReportIdentity(branch="fix/569", directory="../kaji-fix-569")

# workflow path -> start step の期待遷移（変更前と同じ）。
_START_WORKFLOWS: dict[str, dict[str, str]] = {
    ".kaji/wf/official/dev.yaml": {"PASS": "design", "ABORT": "end"},
    ".kaji/wf/official/docs.yaml": {"PASS": "doc-update", "ABORT": "end"},
    ".kaji/wf/custom/dev/dev-small.yaml": {"PASS": "baseline", "ABORT": "end"},
    ".kaji/wf/custom/dev/dev-thorough.yaml": {"PASS": "design", "ABORT": "end"},
    ".kaji/wf/custom/dev/dev-thorough-codex.yaml": {"PASS": "design", "ABORT": "end"},
    ".kaji/wf/custom/docs/docs-codex.yaml": {"PASS": "doc-update", "ABORT": "end"},
    ".kaji/wf/custom/docs/docs-thorough-codex.yaml": {"PASS": "doc-update", "ABORT": "end"},
}


def _comment(body: str, ref: str = "") -> Comment:
    return Comment(author="a", body=body, created_at="2026-11-13T00:00:00Z", ref=ref)


def _context(**overrides: str) -> IssueContext:
    values: dict[str, str] = {
        "issue_id": "569",
        "issue_ref": "#569",
        "issue_input": "569",
        "slug": "x",
        "branch_prefix": "fix",
        "branch_name": "fix/569",
        "worktree_dir": "/repos/kaji-fix-569",
        "design_path": "draft/design/issue-569-x.md",
        "provider_type": "github",
        "default_branch": "main",
        "git_remote": "origin",
    }
    values.update(overrides)
    return IssueContext(**values)


def _facts(identity: ReportIdentity = _IDENTITY, **overrides: object) -> ReportFacts:
    values: dict[str, object] = {
        "issue_id": "569",
        "issue_ref": "#569",
        "identity": identity,
        "remote": "origin",
        "base": "main",
        "base_sha": "abc1234",
        "reused": False,
        "venv_created": True,
        "overlay": "created",
        "note_added": True,
        "local_ahead": 0,
    }
    values.update(overrides)
    return ReportFacts(**values)  # type: ignore[arg-type]


# ============================================================
# 回帰（#560）: SKILL.md の bash block 間でシェル変数を受け渡さない
# ============================================================

_BASH_BLOCK_RE = re.compile(r"```bash\n(.*?)\n```", re.DOTALL)
_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)=", re.MULTILINE)


def _cross_block_references(text: str) -> list[tuple[str, int, int]]:
    """(変数名, 代入した block 番号, 参照した block 番号) を返す。"""
    blocks = _BASH_BLOCK_RE.findall(text)
    assigned = [set(_ASSIGN_RE.findall(block)) for block in blocks]
    found: list[tuple[str, int, int]] = []
    for user, block in enumerate(blocks):
        for owner, names in enumerate(assigned):
            if owner == user:
                continue
            for name in sorted(names - assigned[user]):
                if re.search(rf"\$\{{?{name}\b", block):
                    found.append((name, owner, user))
    return found


@pytest.mark.small
def test_cross_block_detection_flags_split_variable_handoff() -> None:
    text = '```bash\nREMOTE=$(x)\n```\n\n```bash\ngit fetch "$REMOTE"\n```\n'
    assert _cross_block_references(text) == [("REMOTE", 0, 1)]


@pytest.mark.small
def test_cross_block_detection_allows_same_block_usage() -> None:
    text = '```bash\nREMOTE=$(x)\ngit fetch "$REMOTE"\n```\n'
    assert _cross_block_references(text) == []


@pytest.mark.small
def test_skill_does_not_carry_shell_variables_across_bash_blocks() -> None:
    text = _ISSUE_START_SKILL.read_text(encoding="utf-8")
    assert _cross_block_references(text) == []


@pytest.mark.small
def test_skill_declares_exec_script_entry() -> None:
    text = _ISSUE_START_SKILL.read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter["name"] == "issue-start"
    assert frontmatter["exec_script"] == "kaji_harness.scripts.issue_start"


@pytest.mark.small
def test_skill_manual_route_calls_single_cli_command() -> None:
    text = _ISSUE_START_SKILL.read_text(encoding="utf-8")
    assert "kaji issue start [issue_id]" in text
    assert "kaji issue context" not in "\n".join(_BASH_BLOCK_RE.findall(text))


# ============================================================
# worktree 状態判定
# ============================================================

_PORCELAIN = """worktree /repos/kaji
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree /repos/kaji-fix-569
HEAD 2222222222222222222222222222222222222222
branch refs/heads/fix/569

worktree /repos/kaji-detached
HEAD 3333333333333333333333333333333333333333
detached

worktree /repos/kaji-gone
HEAD 4444444444444444444444444444444444444444
branch refs/heads/fix/9
prunable gitdir file points to non-existent location
"""


@pytest.mark.small
def test_parse_worktree_porcelain() -> None:
    assert parse_worktree_porcelain(_PORCELAIN) == [
        WorktreeEntry(path="/repos/kaji", branch="refs/heads/main", prunable=False),
        WorktreeEntry(path="/repos/kaji-fix-569", branch="refs/heads/fix/569", prunable=False),
        WorktreeEntry(path="/repos/kaji-detached", branch=None, prunable=False),
        WorktreeEntry(path="/repos/kaji-gone", branch="refs/heads/fix/9", prunable=True),
    ]


_ENTRIES = parse_worktree_porcelain(_PORCELAIN)


@pytest.mark.small
@pytest.mark.parametrize(
    ("worktree_dir", "branch", "branch_exists", "path_exists", "path_is_dir", "expected"),
    [
        ("/repos/kaji-fix-1", "fix/1", False, False, False, WorktreeState.NEW),
        ("/repos/kaji-fix-569", "fix/569", True, True, True, WorktreeState.REUSABLE),
        ("/repos/kaji-detached", "fix/2", False, True, True, WorktreeState.PATH_ON_OTHER_BRANCH),
        ("/repos/kaji-fix-569", "fix/3", False, True, True, WorktreeState.PATH_ON_OTHER_BRANCH),
        ("/repos/kaji-other", "fix/569", True, False, False, WorktreeState.BRANCH_IN_OTHER_PATH),
        ("/repos/kaji-fix-4", "fix/4", True, False, False, WorktreeState.BRANCH_WITHOUT_WORKTREE),
        ("/repos/kaji-fix-5", "fix/5", False, True, True, WorktreeState.PATH_EXISTS_UNREGISTERED),
        ("/repos/kaji-gone", "fix/9", True, False, False, WorktreeState.REGISTERED_PATH_MISSING),
    ],
)
def test_classify_worktree(
    worktree_dir: str,
    branch: str,
    branch_exists: bool,
    path_exists: bool,
    path_is_dir: bool,
    expected: WorktreeState,
) -> None:
    state = classify_worktree(
        _ENTRIES,
        worktree_dir=worktree_dir,
        branch=branch,
        branch_exists=branch_exists,
        path_exists=path_exists,
        path_is_dir=path_is_dir,
    )
    assert state is expected


@pytest.mark.small
def test_registered_path_that_is_not_a_directory_is_not_reusable() -> None:
    state = classify_worktree(
        _ENTRIES,
        worktree_dir="/repos/kaji-fix-569",
        branch="fix/569",
        branch_exists=True,
        path_exists=True,
        path_is_dir=False,
    )
    assert state is WorktreeState.REGISTERED_PATH_MISSING


# ============================================================
# symlink 判定
# ============================================================


@pytest.mark.small
@pytest.mark.parametrize(
    ("exists", "observed", "expected"),
    [
        (False, None, SymlinkState.MISSING),
        (True, "/repos/kaji/.venv", SymlinkState.MATCHES),
        (True, "/elsewhere/.venv", SymlinkState.CONFLICT),
        (True, None, SymlinkState.CONFLICT),
    ],
)
def test_classify_symlink(exists: bool, observed: str | None, expected: SymlinkState) -> None:
    assert classify_symlink(observed, exists=exists, expected="/repos/kaji/.venv") is expected


# ============================================================
# NOTE 判定
# ============================================================


@pytest.mark.small
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (build_worktree_note_body("## 概要\n", worktree="kaji-fix-569", branch="fix/569"), True),
        (build_worktree_note_body("", worktree="kaji-fix-569", branch="fix/569"), True),
        (
            "\n\n" + build_worktree_note_body("body", worktree="kaji-fix-569", branch="fix/569"),
            True,
        ),
        (
            build_worktree_note_body("body", worktree="kaji-fix-569", branch="fix/569").replace(
                "\n", "\r\n"
            ),
            True,
        ),
        ("## 概要\n", False),
        ("", False),
        (build_worktree_note_body("body", worktree="kaji-feat-569", branch="feat/569"), False),
        (
            build_worktree_note_body("body", worktree="kaji-fix-5690", branch="fix/5690"),
            False,
        ),
    ],
)
def test_has_worktree_note(body: str, expected: bool) -> None:
    assert has_worktree_note(body, worktree="kaji-fix-569", branch="fix/569") is expected


# ============================================================
# 報告の同一性判定（R1）
# ============================================================


@pytest.mark.small
def test_legacy_report_matches_same_identity() -> None:
    assert parse_report_identity(_LEGACY_REPORT) == _IDENTITY
    assert classify_start_report(_LEGACY_REPORT, _IDENTITY) is ReportMatch.SAME


@pytest.mark.small
@pytest.mark.parametrize(
    "identity",
    [
        ReportIdentity(branch="fix/570", directory="../kaji-fix-569"),
        ReportIdentity(branch="fix/569", directory="../kaji-feat-569"),
    ],
)
def test_legacy_report_is_other_for_different_identity(identity: ReportIdentity) -> None:
    assert classify_start_report(_LEGACY_REPORT, identity) is ReportMatch.OTHER


@pytest.mark.small
def test_rendered_report_round_trips_identity() -> None:
    verdict = build_pass_verdict(_facts(), comment_posted=True)
    body = render_report(_facts(), verdict)
    assert parse_report_identity(body) == _IDENTITY


@pytest.mark.small
def test_backtick_and_whitespace_wrapped_values_are_same() -> None:
    body = _LEGACY_REPORT.replace(
        "| ブランチ | fix/569 |", "|  ブランチ  |   `fix/569`  |"
    ).replace("| ディレクトリ | ../kaji-fix-569 |", "| ディレクトリ | ` ../kaji-fix-569 ` |")
    assert classify_start_report(body, _IDENTITY) is ReportMatch.SAME


@pytest.mark.small
@pytest.mark.parametrize(
    "body",
    [
        _LEGACY_REPORT.replace("| ブランチ | fix/569 |\n", ""),
        _LEGACY_REPORT.replace("| ディレクトリ | ../kaji-fix-569 |\n", ""),
        _LEGACY_REPORT.replace(
            "| ブランチ | fix/569 |", "| ブランチ | fix/569 |\n| ブランチ | x/1 |"
        ),
    ],
    ids=["branch-missing", "directory-missing", "conflicting-branch-rows"],
)
def test_unidentifiable_reports(body: str) -> None:
    assert classify_start_report(body, _IDENTITY) is ReportMatch.UNIDENTIFIABLE


@pytest.mark.small
def test_duplicate_rows_with_same_value_stay_identifiable() -> None:
    body = _LEGACY_REPORT.replace(
        "| ブランチ | fix/569 |", "| ブランチ | fix/569 |\n| ブランチ | fix/569 |"
    )
    assert classify_start_report(body, _IDENTITY) is ReportMatch.SAME


@pytest.mark.small
@pytest.mark.parametrize(
    "body",
    [
        _LEGACY_REPORT.split("\n", 1)[1],
        _LEGACY_REPORT.replace("status=PASS", "status=ABORT", 1),
        _LEGACY_REPORT.replace("step=start", "step=design", 1),
        "前置き\n" + _LEGACY_REPORT,
        "",
    ],
    ids=["no-marker", "start-abort", "other-step-pass", "marker-not-first-line", "empty"],
)
def test_non_candidates_are_ignored(body: str) -> None:
    assert classify_start_report(body, _IDENTITY) is None


@pytest.mark.small
def test_metadata_marker_is_still_a_candidate() -> None:
    body = _LEGACY_REPORT.replace(
        _START_MARKER, "<!-- kaji-verdict: step=start status=PASS run=1 -->", 1
    )
    assert classify_start_report(body, _IDENTITY) is ReportMatch.SAME


_UNIDENTIFIABLE = _LEGACY_REPORT.replace("| ブランチ | fix/569 |\n", "")
_OTHER = _LEGACY_REPORT.replace("fix/569", "fix/570")


@pytest.mark.small
@pytest.mark.parametrize(
    ("bodies", "expected"),
    [
        ([_LEGACY_REPORT, _UNIDENTIFIABLE], ReportDecision.SKIP),
        ([_UNIDENTIFIABLE, "unrelated"], ReportDecision.ABORT),
        ([_OTHER], ReportDecision.POST),
        (["unrelated", _LEGACY_REPORT.split("\n", 1)[1]], ReportDecision.POST),
        ([], ReportDecision.POST),
    ],
    ids=["same-and-unidentifiable", "unidentifiable-only", "other-only", "no-candidate", "empty"],
)
def test_issue_level_report_decision(bodies: list[str], expected: ReportDecision) -> None:
    existing = classify_start_reports([_comment(b) for b in bodies], _IDENTITY)
    assert existing.decision is expected


@pytest.mark.small
def test_unidentifiable_decision_lists_the_comments() -> None:
    existing = classify_start_reports([_comment(_UNIDENTIFIABLE, ref="https://x/1")], _IDENTITY)
    assert [c.ref for c in existing.unidentifiable] == ["https://x/1"]


# ============================================================
# 合法な worktree prefix（R2）
# ============================================================


@pytest.mark.small
@pytest.mark.parametrize("prefix", ["", "kaji", "_kaji", ".kaji", "-kaji"])
def test_legal_worktree_prefixes_round_trip(prefix: str) -> None:
    worktree_dir = build_worktree_dir("fix", "569", Path("/repos/kaji"), prefix)
    identity = ReportIdentity.from_context(_context(worktree_dir=worktree_dir))
    assert identity.directory == f"../{Path(worktree_dir).name}"
    marker = build_kaji_verdict_marker("start", "PASS")
    assert marker == _START_MARKER
    facts = _facts(identity)
    verdict = build_pass_verdict(facts, comment_posted=True)
    validate_verdict_fields(verdict, VALID_STATUSES)
    body = f"{marker}\n{render_report(facts, verdict)}"
    assert parse_report_identity(body) == identity
    assert classify_start_report(body, identity) is ReportMatch.SAME


# ============================================================
# 報告本文
# ============================================================


@pytest.mark.small
def test_report_contains_current_template_items() -> None:
    verdict = build_pass_verdict(_facts(), comment_posted=True)
    body = render_report(_facts(), verdict)
    assert body.startswith("## Worktree セットアップ完了\n")
    for row in (
        "| Issue | #569 |",
        f"| {BRANCH_LABEL} | fix/569 |",
        f"| {DIRECTORY_LABEL} | ../kaji-fix-569 |",
        "| 基点 | origin/main（abc1234） |",
        "| venv | シンボリックリンク作成済み |",
        "| config overlay | シンボリックリンク作成済み |",
        "| メタ情報 | Issue本文に追記済み |",
    ):
        assert row in body
    assert "注意" not in body
    assert "再利用" not in body
    assert "### 次のステップ" in body
    assert "cd ../kaji-fix-569" in body
    assert "### クリーンアップ（作業完了後）" in body
    assert "`/issue-close 569`" in body
    assert body.rstrip("\n").endswith("---END_VERDICT---")
    assert parse_verdict(body, set(VALID_STATUSES)) == verdict


@pytest.mark.small
def test_report_shows_local_ahead_overlay_absent_and_reuse() -> None:
    facts = _facts(
        reused=True,
        venv_created=False,
        overlay="absent",
        note_added=False,
        local_ahead=2,
    )
    body = render_report(facts, build_pass_verdict(facts, comment_posted=True))
    assert "| config overlay | 不要（main に未配置） |" in body
    assert "ローカル `main` に `origin/main` へ未 push の commit が 2 件" in body
    assert "| 再利用 |" in body
    assert "現在の共通祖先（abc1234）" in body
    assert "| 基点 | origin/main（abc1234） |" not in body


# ============================================================
# env と context の一致
# ============================================================


@pytest.mark.small
def test_env_mismatch_detection() -> None:
    ctx = _context()
    assert find_env_mismatches(ctx, {}) == []
    matching = {
        "KAJI_WORKTREE_DIR": "/repos/kaji-fix-569",
        "KAJI_BRANCH_NAME": "fix/569",
        "KAJI_GIT_REMOTE": "origin",
        "KAJI_DEFAULT_BRANCH": "main",
    }
    assert find_env_mismatches(ctx, matching) == []
    mismatched = {**matching, "KAJI_WORKTREE_DIR": "/repos/kaji-feat-569", "KAJI_BRANCH_NAME": ""}
    found = find_env_mismatches(ctx, mismatched)
    assert len(found) == 1
    assert "KAJI_WORKTREE_DIR" in found[0]


# ============================================================
# verdict
# ============================================================


@pytest.mark.small
def test_pass_verdict_validates() -> None:
    verdict = build_pass_verdict(_facts(), comment_posted=True)
    validate_verdict_fields(verdict, VALID_STATUSES)
    assert verdict.status == "PASS"
    assert "origin/main（abc1234）" in verdict.evidence
    block = f"---VERDICT---\n{dump_verdict_yaml(verdict)}---END_VERDICT---\n"
    assert parse_verdict(block, set(VALID_STATUSES)) == verdict


@pytest.mark.small
def test_abort_verdict_validates_and_suggests_rerun() -> None:
    verdict = build_abort_verdict(
        "569",
        reason="git fetch failed",
        evidence="git fetch origin main: exit 128",
        completed=("worktree 作成",),
    )
    validate_verdict_fields(verdict, VALID_STATUSES)
    assert verdict.status == "ABORT"
    assert "kaji issue start 569" in verdict.suggestion
    assert "worktree 作成" in verdict.evidence


@pytest.mark.small
def test_abort_verdict_without_side_effects_says_none() -> None:
    verdict = build_abort_verdict("569", reason="r", evidence="e", completed=())
    assert "完了済みの副作用: なし" in verdict.evidence
    assert isinstance(verdict, Verdict)


# ============================================================
# 全 workflow の start step（静的契約）
# ============================================================


@pytest.mark.small
@pytest.mark.parametrize(("rel", "expected_on"), sorted(_START_WORKFLOWS.items()))
def test_workflow_start_step_uses_exec_script_skill(rel: str, expected_on: dict[str, str]) -> None:
    data = yaml.safe_load((_REPO_ROOT / rel).read_text(encoding="utf-8"))
    start = next(step for step in data["steps"] if step["id"] == "start")
    assert start["skill"] == "issue-start"
    for key in ("agent", "model", "effort"):
        assert key not in start, f"{rel}: start step must not set {key}"
    workflow = load_workflow(_REPO_ROOT / rel)
    step = workflow.find_step("start")
    assert step is not None
    assert step.on == expected_on
    hits = non_resumable_skill_hits(workflow, "start", "start")
    assert [hit.name for hit in hits] == ["non_resumable_skill"]


@pytest.mark.small
def test_start_marker_is_parseable_without_metadata() -> None:
    parsed = parse_kaji_verdict_marker(_START_MARKER)
    assert parsed is not None
    assert (parsed.step, parsed.status, parsed.meta) == ("start", "PASS", {})
