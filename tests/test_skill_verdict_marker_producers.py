"""判定コメントを投稿する skill が verdict marker を常に付けることを確認する（#547）。

marker の無い作業報告は runner の comment fallback で全 step の候補に残るため、
review-ready / fix / verify / i-doc 系の判定コメントにも ``--verdict-step`` /
``--verdict-status`` を付ける。issue-fix-ready は見出し検索ではなく
``step=review-ready`` marker で review-ready の判定を取得し、最新候補が不正なら
古い RETRY に遡らず停止する（``resolve_latest_verdict`` と同じ停止契約）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from kaji_harness.providers.markers import parse_kaji_verdict_marker

_REPO = Path(__file__).resolve().parent.parent
_SKILLS = _REPO / ".claude/skills"
_WORKFLOWS = _REPO / ".kaji/wf"

# skill -> workflow YAML 上の step id（設計書「skill と step id の対応」）。
_PRODUCER_STEP_IDS: dict[str, str] = {
    "issue-review-ready": "review-ready",
    "issue-fix-ready": "fix-ready",
    "issue-fix-design": "fix-design",
    "issue-verify-design": "verify-design",
    "issue-fix-code": "fix-code",
    "issue-verify-code": "verify-code",
    "i-doc-update": "doc-update",
    "i-doc-review": "doc-review",
    "i-doc-fix": "doc-fix",
    "i-doc-verify": "doc-verify",
}

_COMMENT_START_RE = re.compile(r"^[ \t]*kaji issue comment\b", re.MULTILINE)
_BODY_ARG_RE = re.compile(r"--body(?:-file)?\b")
_STEP_FLAG_RE = re.compile(r"--verdict-step (?P<step>\S+) --verdict-status (?P<status>\S+)")
_STATUS_TOKEN_RE = re.compile(r"^(<STATUS>|PASS|RETRY|ABORT|BACK|BACK_[A-Z0-9_]+)$")

_FIX_READY_JQ_RE = re.compile(
    r"REVIEW_READY_JSON=\$\(kaji issue view \[issue_id\] --json comments \| jq -c '\n"
    r"(?P<program>.*?)\n[ \t]*'\)",
    re.DOTALL,
)

_VALID_RETRY = "<!-- kaji-verdict: step=review-ready status=RETRY -->"

# 最新候補の 1 行目。parser（markers.py）と jq の判定を差分照合する。
_FIRST_LINE_CASES: list[tuple[str, str]] = [
    ("retry", _VALID_RETRY),
    ("pass", "<!-- kaji-verdict: step=review-ready status=PASS -->"),
    ("abort", "<!-- kaji-verdict: step=review-ready status=ABORT -->"),
    ("meta", "<!-- kaji-verdict: step=review-ready status=RETRY key=v -->"),
    ("two_meta_keys", "<!-- kaji-verdict: step=review-ready status=RETRY a=1 b=x.y/z -->"),
    ("unclosed", "<!-- kaji-verdict: step=review-ready status=RETRY"),
    ("missing_status", "<!-- kaji-verdict: step=review-ready -->"),
    ("uppercase_meta_key", "<!-- kaji-verdict: step=review-ready status=RETRY Key=v -->"),
    ("duplicate_meta_key", "<!-- kaji-verdict: step=review-ready status=RETRY key=v key=w -->"),
    ("duplicate_meta_same", "<!-- kaji-verdict: step=review-ready status=RETRY key=v key=v -->"),
    ("lowercase_status", "<!-- kaji-verdict: step=review-ready status=retry -->"),
    ("value_with_equals", "<!-- kaji-verdict: step=review-ready status=RETRY key=a=b -->"),
    ("trailing_text", "<!-- kaji-verdict: step=review-ready status=RETRY --> extra"),
]


def _read_skill(skill: str) -> str:
    return (_SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")


def _comment_commands(text: str) -> list[str]:
    """SKILL.md 内の ``kaji issue comment`` 呼び出しを、行継続を含む 1 コマンド単位で返す。"""
    commands: list[str] = []
    for match in _COMMENT_START_RE.finditer(text):
        lines: list[str] = []
        for line in text[match.start() :].splitlines():
            lines.append(line)
            if not line.rstrip().endswith("\\"):
                break
        commands.append("\n".join(lines))
    return commands


def _verdict_flags(command: str) -> tuple[str, str] | None:
    """body 引数より前にある ``--verdict-step`` / ``--verdict-status`` の値を返す。"""
    body = _BODY_ARG_RE.search(command)
    head = command[: body.start()] if body else command
    flags = _STEP_FLAG_RE.search(head)
    if flags is None:
        return None
    return flags.group("step"), flags.group("status")


def _workflow_step_ids() -> dict[str, set[str]]:
    step_ids: dict[str, set[str]] = {}
    for path in sorted(_WORKFLOWS.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for step in data.get("steps", []) or []:
            if "skill" in step:
                step_ids.setdefault(step["skill"], set()).add(step["id"])
    return step_ids


@pytest.mark.small
class TestProducerSkillsAttachVerdictMarker:
    """10 skill の判定コメントが常に marker を付ける（OB の回帰）。"""

    @pytest.mark.parametrize(("skill", "step_id"), sorted(_PRODUCER_STEP_IDS.items()))
    def test_every_comment_command_has_verdict_flags(self, skill: str, step_id: str) -> None:
        commands = _comment_commands(_read_skill(skill))
        assert commands, f"{skill}: kaji issue comment の呼び出しが無い"
        for command in commands:
            flags = _verdict_flags(command)
            assert flags is not None, f"{skill}: body 引数より前に verdict フラグが無い\n{command}"
            step, status = flags
            assert step == step_id, f"{skill}: --verdict-step {step} (期待: {step_id})"
            assert _STATUS_TOKEN_RE.match(status), f"{skill}: --verdict-status {status}"

    def test_review_ready_templates_fix_each_status(self) -> None:
        commands = _comment_commands(_read_skill("issue-review-ready"))
        statuses = sorted(flags[1] for c in commands if (flags := _verdict_flags(c)))
        assert statuses == ["ABORT", "PASS", "RETRY"]

    def test_fix_ready_uses_marker_instead_of_heading_search(self) -> None:
        text = _read_skill("issue-fix-ready")
        assert "最新の「レディネスレビュー」コメントから" not in text
        assert "step=review-ready" in text


@pytest.mark.medium
class TestProducerStepIdsMatchWorkflows:
    """SKILL.md の ``--verdict-step`` が全 workflow の step id と一致する。"""

    @pytest.mark.parametrize(("skill", "step_id"), sorted(_PRODUCER_STEP_IDS.items()))
    def test_skill_maps_to_single_step_id(self, skill: str, step_id: str) -> None:
        step_ids = _workflow_step_ids().get(skill)
        assert step_ids == {step_id}, f"{skill}: workflow 上の step id = {step_ids}"
        flagged = {
            flags[0] for c in _comment_commands(_read_skill(skill)) if (flags := _verdict_flags(c))
        }
        assert flagged == {step_id}


def _fix_ready_program() -> str:
    match = _FIX_READY_JQ_RE.search(_read_skill("issue-fix-ready"))
    assert match, "issue-fix-ready SKILL.md の jq プログラムを抽出できない"
    return match.group("program")


def _comment(body: str) -> dict[str, str]:
    return {"author": "apokamo", "body": body}


def _run_fix_ready_jq(bodies: list[str]) -> dict[str, Any]:
    payload = {"comments": [_comment(body) for body in bodies]}
    result = subprocess.run(
        ["jq", "-c", _fix_ready_program()],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


@pytest.mark.medium
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
class TestFixReadyReviewReadySelection:
    """issue-fix-ready の jq が最新の review-ready marker を fail-closed で選ぶ。"""

    def test_selects_latest_retry(self) -> None:
        result = _run_fix_ready_jq(
            [
                f"{_VALID_RETRY}\n## レディネスレビュー\nold",
                "<!-- kaji-verdict: step=review-ready status=PASS -->\n## レディネスレビュー",
                f"{_VALID_RETRY}\n## レディネスレビュー\nnew",
            ]
        )
        assert result["state"] == "ok"
        assert result["status"] == "RETRY"
        assert result["body"].endswith("new")

    def test_later_pass_supersedes_retry(self) -> None:
        result = _run_fix_ready_jq(
            [
                f"{_VALID_RETRY}\n## レディネスレビュー",
                "<!-- kaji-verdict: step=review-ready status=PASS -->\n## レディネスレビュー",
            ]
        )
        assert result == {
            "state": "ok",
            "status": "PASS",
            "body": "<!-- kaji-verdict: step=review-ready status=PASS -->\n## レディネスレビュー",
        }

    def test_ignores_marker_quoted_after_first_line(self) -> None:
        result = _run_fix_ready_jq(
            [
                f"{_VALID_RETRY}\n## レディネスレビュー\nretry",
                f"## 作業報告\n{_VALID_RETRY.replace('RETRY', 'PASS')}",
            ]
        )
        assert result["status"] == "RETRY"
        assert result["body"].endswith("retry")

    @pytest.mark.parametrize(
        "other",
        [
            "<!-- kaji-verdict: step=fix-ready status=PASS -->",
            "<!-- kaji-verdict: step=review-code status=PASS -->",
            "<!-- kaji-verdict: step=review-readyx status=PASS -->",
        ],
    )
    def test_ignores_other_step_markers(self, other: str) -> None:
        result = _run_fix_ready_jq([f"{_VALID_RETRY}\nretry", f"{other}\nother"])
        assert result["state"] == "ok"
        assert result["body"].endswith("retry")

    def test_unmarked_heading_comment_is_not_found(self) -> None:
        result = _run_fix_ready_jq(["## レディネスレビュー\n\n### 指摘事項\n- x"])
        assert result == {"state": "not_found"}

    def test_no_comments_is_not_found(self) -> None:
        assert _run_fix_ready_jq([]) == {"state": "not_found"}

    def test_meta_marker_extracts_status(self) -> None:
        result = _run_fix_ready_jq(
            ["<!-- kaji-verdict: step=review-ready status=RETRY key=v -->\nbody"]
        )
        assert result["state"] == "ok"
        assert result["status"] == "RETRY"

    @pytest.mark.parametrize(
        "latest",
        [
            "<!-- kaji-verdict: step=review-ready status=RETRY",
            "<!-- kaji-verdict: step=review-ready -->",
            "<!-- kaji-verdict: step=review-ready status=RETRY Key=v -->",
            "<!-- kaji-verdict: step=review-ready status=RETRY key=v key=w -->",
            "<!-- kaji-verdict: step=review-ready status=retry -->",
        ],
    )
    def test_malformed_latest_does_not_fall_back_to_older_retry(self, latest: str) -> None:
        result = _run_fix_ready_jq([f"{_VALID_RETRY}\nold retry", f"{latest}\nbroken"])
        assert result == {"state": "malformed", "first_line": latest}

    @pytest.mark.parametrize(("name", "first_line"), _FIRST_LINE_CASES)
    def test_agrees_with_marker_parser(self, name: str, first_line: str) -> None:
        result = _run_fix_ready_jq([f"{_VALID_RETRY}\nold retry", f"{first_line}\nlatest"])
        parsed = parse_kaji_verdict_marker(first_line)
        if parsed is None:
            assert result["state"] == "malformed", name
        else:
            assert result["state"] == "ok", name
            assert result["status"] == parsed.status, name
            assert result["body"].endswith("latest"), name
