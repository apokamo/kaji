"""follow-up Issue の「確認できる時期」書式の契約テスト（Issue #538）。

書式の正本は ``docs/dev/workflow_completion_criteria.md`` § 確認できる時期の書き方 の表 1 か所。
本テストは正本表の「書き方」列からパターンを導出し、テンプレート・docs の例が正本に合うことを
確かめる。テスト側に書式を二重定義しない。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_DIR = _REPO_ROOT / ".claude/skills"
_DOCS_DIR = _REPO_ROOT / "docs"
_CRITERIA = _DOCS_DIR / "dev/workflow_completion_criteria.md"
_FOLLOW_UP_TEMPLATE = _SKILLS_DIR / "issue-close/templates/follow-up-issue.md"
_ISSUE_CLOSE_SKILL = _SKILLS_DIR / "issue-close/SKILL.md"
_ISSUE_CREATE_SKILL = _SKILLS_DIR / "issue-create/SKILL.md"
_ISSUE_CREATE_TEMPLATES = tuple(
    _SKILLS_DIR / f"issue-create/templates/issue-{kind}.md"
    for kind in ("feat", "bug", "refactor", "docs")
)

_CANONICAL_HEADING = "## 確認できる時期の書き方"
_CANONICAL_NAME = "確認できる時期の書き方"
_TABLE_HEADER = "| 書き方 | 時期が来た条件 |"
_TIMING_HEADING_RE = re.compile(r"^#{2,4} 確認できる時期$")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")

# 正本表の「書き方」セルに現れるパラメータ部分と、その値に合う正規表現。
_PARAMETER_PATTERNS = {
    "YYYY-MM-DD": r"\d{4}-\d{2}-\d{2}",
    "<n>": r"\d+",
    "vX.Y.Z": r"v\d+\.\d+\.\d+",
    "<自由記述>": r".+",
}
_PARAMETER_SPLIT_RE = re.compile("(" + "|".join(map(re.escape, _PARAMETER_PATTERNS)) + ")")
# 行末の全角括弧は理由の注記で、判定には使わない（任意）。
_ANNOTATION_PATTERN = r"(?:（[^（）]*）)?"

_EXPECTED_FORMS = (
    "- 即時",
    "- 日付: YYYY-MM-DD 以降",
    "- close: #<n> の close の後",
    "- release: vX.Y.Z の後",
    "- その他: <自由記述>",
)


def format_to_pattern(form: str) -> re.Pattern[str]:
    """正本表の「書き方」1 セルを、その書き方に合う行の正規表現へ変換する。

    Args:
        form: 正本表の「書き方」列の値（バッククォートを除いたもの）。

    Returns:
        行全体に一致させる正規表現。行末の全角括弧注記を任意で許容する。
    """
    parts = _PARAMETER_SPLIT_RE.split(form)
    body = "".join(
        _PARAMETER_PATTERNS[part] if part in _PARAMETER_PATTERNS else re.escape(part)
        for part in parts
    )
    return re.compile(f"^{body}{_ANNOTATION_PATTERN}$")


def matches_any(line: str, patterns: list[re.Pattern[str]]) -> bool:
    """行がいずれかのパターンに合うかを返す。"""
    return any(pattern.fullmatch(line) for pattern in patterns)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """見出し ``heading`` から、同じか上位の次の見出しまでを返す（コードフェンス内は見出しとみなさない）。"""
    lines = text.splitlines()
    begin = lines.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    in_fence = False
    for index in range(begin + 1, len(lines)):
        line = lines[index]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line.startswith("#"):
            continue
        line_level = len(line) - len(line.lstrip("#"))
        if line_level <= level and line[line_level : line_level + 1] == " ":
            return "\n".join(lines[begin:index])
    return "\n".join(lines[begin:])


def _canonical_section() -> str:
    return _section(_read(_CRITERIA), _CANONICAL_HEADING)


def _canonical_forms() -> list[str]:
    lines = _canonical_section().splitlines()
    header_index = lines.index(_TABLE_HEADER)
    forms: list[str] = []
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        first_cell = line.split("|")[1].strip()
        match = _INLINE_CODE_RE.fullmatch(first_cell)
        assert match is not None, f"書き方セルがインラインコードではない: {line}"
        forms.append(match.group(1))
    return forms


def _canonical_patterns() -> list[re.Pattern[str]]:
    return [format_to_pattern(form) for form in _canonical_forms()]


def _timing_block_lines(text: str) -> list[list[str]]:
    """``確認できる時期`` 見出し直下の箇条書きを、見出しごとに返す。"""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if _TIMING_HEADING_RE.match(line):
            current = []
            blocks.append(current)
            continue
        if current is None:
            continue
        if line.startswith("#") or line.lstrip().startswith("```"):
            current = None
            continue
        if line.startswith("- "):
            current.append(line)
    return blocks


def _template_timing_examples(path: Path) -> list[str]:
    """issue-create テンプレートの時期ヒント行に載るインラインコードの例を返す。"""
    examples: list[str] = []
    for line in _read(path).splitlines():
        if "#### 確認できる時期" not in line:
            continue
        examples.extend(code for code in _INLINE_CODE_RE.findall(line) if code.startswith("- "))
    return examples


# --- 観点 6: パターン変換の自己検査（Small） ---


@pytest.mark.small
@pytest.mark.parametrize(
    "line",
    [
        "- 即時",
        "- 日付: 2026-12-01 以降",
        "- 日付: 2026-12-01 以降（月次集計の後）",
        "- close: #546 の close の後",
        "- release: v0.25.0 の後",
        "- release: v0.25.0 の後（PyPI 配布物で確かめる）",
        "- その他: 運用者が手動で確かめられるようになった後",
    ],
)
def test_pattern_accepts_standard_lines(line: str) -> None:
    patterns = [format_to_pattern(form) for form in _EXPECTED_FORMS]
    assert matches_any(line, patterns)


@pytest.mark.small
@pytest.mark.parametrize(
    "line",
    [
        "- 営業日: 2026-11-04 から 3 営業日の後",
        "- deploy: staging の後",
        "- release: 0.25.0 の後",
        "- release: v0.25 の後",
        "- 日付: 2026/12/01 以降",
        "- close: 546 の close の後",
        "- 即時（理由）余分",
        "- その他: ",
        "即時",
    ],
)
def test_pattern_rejects_nonstandard_lines(line: str) -> None:
    patterns = [format_to_pattern(form) for form in _EXPECTED_FORMS]
    assert not matches_any(line, patterns)


# --- 観点 1: follow-up テンプレートの節（Medium） ---


@pytest.mark.medium
def test_follow_up_template_has_single_timing_section() -> None:
    lines = _read(_FOLLOW_UP_TEMPLATE).splitlines()
    assert lines.count("## 確認できる時期") == 1
    index = lines.index("## 確認できる時期")
    assert lines[index + 1 : index + 3] == ["", "[check_timing]"]


@pytest.mark.medium
def test_follow_up_template_places_timing_after_unchecked_items() -> None:
    lines = _read(_FOLLOW_UP_TEMPLATE).splitlines()
    headings = [line for line in lines if line.startswith("## ")]
    unchecked = headings.index("## 未完了の確認項目")
    assert headings[unchecked + 1] == "## 確認できる時期"


# --- 観点 2: 正本の書式表が人間決定どおり（Medium） ---


@pytest.mark.medium
def test_canonical_table_lists_exactly_the_decided_forms() -> None:
    forms = _canonical_forms()
    assert forms == list(_EXPECTED_FORMS)
    assert not any(form.startswith(("- 営業日:", "- deploy:")) for form in forms)


@pytest.mark.medium
def test_canonical_release_condition_requires_published_release() -> None:
    section = _canonical_section()
    release_row = next(line for line in section.splitlines() if line.startswith("| `- release:"))
    assert "draft ではない" in release_row
    assert "tag があるだけでは来ていない" in release_row


# --- 観点 3: 例が正本と一致（Medium。文書間照合） ---


@pytest.mark.medium
def test_canonical_section_examples_match_canonical_forms() -> None:
    patterns = _canonical_patterns()
    blocks = _timing_block_lines(_canonical_section())
    examples = [line for block in blocks for line in block]
    assert examples, "正本節に例がない"
    assert [line for line in examples if not matches_any(line, patterns)] == []


@pytest.mark.medium
def test_issue_body_format_example_matches_canonical_forms() -> None:
    section = _section(_read(_CRITERIA), "### Issue 本文の形式")
    patterns = _canonical_patterns()
    blocks = _timing_block_lines(section)
    assert len(blocks) == 1, "Issue 本文の形式の例に #### 確認できる時期 が 1 つない"
    assert blocks[0], "Issue 本文の形式の例に時期の行がない"
    assert [line for line in blocks[0] if not matches_any(line, patterns)] == []


@pytest.mark.medium
@pytest.mark.parametrize("template", _ISSUE_CREATE_TEMPLATES, ids=lambda p: p.name)
def test_issue_create_template_examples_match_canonical_forms(template: Path) -> None:
    patterns = _canonical_patterns()
    examples = _template_timing_examples(template)
    assert examples, f"{template.name} に確認できる時期の例がない"
    assert [line for line in examples if not matches_any(line, patterns)] == []


# --- 観点 4: 正本は 1 か所（Medium。走査） ---


@pytest.mark.medium
def test_format_table_exists_only_in_canonical_file() -> None:
    holders = sorted(
        str(path.relative_to(_REPO_ROOT))
        for root in (_SKILLS_DIR, _DOCS_DIR)
        for path in root.rglob("*.md")
        if _TABLE_HEADER in _read(path)
    )
    assert holders == [str(_CRITERIA.relative_to(_REPO_ROOT))]


@pytest.mark.medium
@pytest.mark.parametrize(
    "path",
    (_ISSUE_CLOSE_SKILL, _ISSUE_CREATE_SKILL, *_ISSUE_CREATE_TEMPLATES),
    ids=lambda p: str(p.relative_to(_SKILLS_DIR)),
)
def test_referrers_link_to_canonical_section(path: Path) -> None:
    text = _read(path)
    assert "workflow_completion_criteria.md" in text
    assert _CANONICAL_NAME in text


# --- 観点 5: issue-close 手順（Medium） ---


@pytest.mark.medium
def test_issue_close_transfer_defines_check_timing() -> None:
    section = _section(_read(_ISSUE_CLOSE_SKILL), "## 共通: ワークフロー完了後の確認項目の移管")
    assert "[check_timing]" in section
    assert "#### 確認できる時期" in section
    assert "- その他: 親 Issue [issue_ref] に確認できる時期の記載がない" in section
    assert "`- 即時` を既定値にしない" in section
