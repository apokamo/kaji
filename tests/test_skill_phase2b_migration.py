"""Medium tests: Phase 2-B Skill migration static checks.

Verifies that Skill markdown files are fully migrated off `gh` direct calls and
legacy `[issue-number]` placeholders, and that `kaji pr review-comments` is
reachable through the CLI dispatcher.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILL_GLOB = list((PROJECT_ROOT / ".claude" / "skills").glob("*/SKILL.md"))
SHARED_GLOB = list((PROJECT_ROOT / ".claude" / "skills" / "_shared").glob("*.md"))
ALL_SKILL_DOCS = SKILL_GLOB + SHARED_GLOB


def _scan(pattern: str) -> list[str]:
    """Return matching `path:line` entries across Skill markdown."""
    rx = re.compile(pattern)
    hits: list[str] = []
    for f in ALL_SKILL_DOCS:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if rx.search(line):
                hits.append(f"{f.relative_to(PROJECT_ROOT)}:{i}: {line}")
    return hits


@pytest.mark.medium
class TestSkillNoGhMentions:
    """Skill markdown must not mention `gh issue`/`gh pr`/`gh api` anywhere.

    検査範囲はコマンド呼び出しだけでなく prose（説明文・失敗条件・description
    frontmatter 等）も含む。Phase 2-B の本旨は「Skill が `gh` を意識しない」
    状態にすることなので、command-context だけ見ていると `gh issue edit` で
    失敗した場合 のような prose 残存を見逃す（PR review 指摘）。
    """

    def test_no_gh_mentions_anywhere(self) -> None:
        pattern = r"\bgh (issue|pr|api)\b"
        hits = _scan(pattern)
        assert hits == [], "gh mentions remain (command or prose):\n" + "\n".join(hits)


@pytest.mark.medium
class TestSkillNoLegacyPlaceholders:
    """Skill markdown must not contain legacy `[issue-number]` placeholders."""

    def test_no_legacy_placeholders(self) -> None:
        patterns = [
            r"\[issue-number\]",
            r"#\[issue-number\]",
            r"issue-\[number\]",
            r"\[prefix\]/\[number\]",
            r"kaji-\[prefix\]-\[number\]",
            r"\[number\]",
            r"^\s*issue_number:",
            # `<issue-number>` メタ変数（$ARGUMENTS = <issue-number> 等）も
            # 旧表記。`<issue_id>` に統一する。
            r"<issue-number>",
            # backtick `issue-number` や bare prose `issue-number` も検出。
            # 角括弧形式 `[issue-number]` だけ見ていると `- \`issue-number\` (必須)`
            # のような説明文を見逃す（PR review 指摘）。`pr-number` は
            # provider-neutral PR 識別子の議論が Phase 3 で別途行われるため
            # 本 Phase の検査対象外（語境界に `pr-` を含めない正規表現）。
            r"(?<![\w-])issue-number(?![\w])",
        ]
        hits: list[str] = []
        for pat in patterns:
            hits.extend(_scan(pat))
        assert hits == [], "legacy placeholders remain:\n" + "\n".join(hits)


@pytest.mark.medium
class TestSkillNoHashIssueIdHardcode:
    """`#[issue_id]` は禁止（local mode で `#local-pc1-1` を生成し ref 契約を壊す）。

    `#` の hard-code は `[issue_ref]` に集約する。`prompt.py` 側で github なら
    `#153`、local なら bare ID にフォーマットされるため、Skill 側は `[issue_ref]`
    を使うのが正解。`promote-design.md` の `git commit` 例で `#[issue_id]` が
    残っていた事案（PR review 指摘）の回帰防止。
    """

    def test_no_hash_issue_id_hardcode(self) -> None:
        hits = _scan(r"#\[issue_id\]")
        assert hits == [], "#[issue_id] hard-codes remain (use [issue_ref]):\n" + "\n".join(hits)


@pytest.mark.medium
class TestSkillNoIssueHashHardcode:
    """`Issue #[issue` / `Closes #[issue` hard-codes must be removed."""

    def test_no_issue_hash_hardcode(self) -> None:
        hits = _scan(r"Issue #\[issue|Closes #\[issue")
        assert hits == [], "`Issue #[issue` / `Closes #[issue` hard-codes remain:\n" + "\n".join(
            hits
        )


@pytest.mark.medium
class TestSkillNoMergeFlag:
    """`kaji pr merge X --merge` must not appear (wrapper supplies --merge)."""

    def test_no_merge_flag(self) -> None:
        hits = _scan(r"kaji pr merge .* --merge\b")
        assert hits == [], "`--merge` flag still present:\n" + "\n".join(hits)


@pytest.mark.medium
class TestPrReviewCommentsCliRunnerIntegration:
    """Verify the `kaji pr review-comments` builtin composes argv via CLI dispatch."""

    def test_review_comments_invokes_gh_with_composed_jq(self) -> None:
        from kaji_harness.cli_main import main

        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with (
            patch("kaji_harness.cli_main.shutil.which", return_value="/usr/bin/gh"),
            patch("kaji_harness.cli_main._detect_repo", return_value="acme/widgets"),
            patch("kaji_harness.cli_main.subprocess.run", return_value=completed) as m,
        ):
            rc = main(["pr", "review-comments", "153", "--json", "id,body", "--jq", ".[]"])

        assert rc == 0
        m.assert_called_once()
        argv = m.call_args.args[0]
        assert argv[:3] == ["gh", "api", "repos/acme/widgets/pulls/153/comments"]
        assert "--jq" in argv
        composed = argv[argv.index("--jq") + 1]
        assert composed == "[.[] | {id: .id, body: .body}] | .[]"
