"""issue-close SKILL.md が承認 SHA を Bash 呼び出しをまたいで失わないことを検証する（Issue #537）。

Bash tool は呼び出しのたびに新しいシェルを起動するため、Step 2.5.4 で代入したシェル変数
``$REVIEWED_SHA`` は Step 3 のブロックに届かない。次を固定する。

- 照合した SHA を Step をまたいでシェル変数で受け渡さない（文脈変数 ``[reviewed_sha]`` に記録する）
- Step 3 の経路選択は文脈変数 ``[review_poll_mode]`` で決まり、未記録・未知の値は ABORT
- 照合付き merge のブロックは代入・検証・merge を自己完結し、空・不正な SHA では merge しない
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

_SKILL = Path(__file__).resolve().parent.parent / ".claude/skills/issue-close/SKILL.md"
_SHELL_BLOCK_RE = re.compile(
    r"^(?P<indent>[ \t]*)```(?:bash|sh|shell|console)?[ \t]*\n(?P<body>.*?)\n(?P=indent)```",
    re.DOTALL | re.MULTILINE,
)
_VALID_SHA = "0123456789abcdef0123456789abcdef01234567"


def _skill_text() -> str:
    return _SKILL.read_text(encoding="utf-8")


def _between(text: str, start: str, end: str) -> str:
    """``start`` 見出しから ``end`` 見出しの直前までを返す。"""
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def extract_shell_blocks(markdown: str) -> list[str]:
    """markdown から shell 系フェンス（言語指定なしを含む）の本文を出現順に返す。

    Args:
        markdown: 検査対象の markdown 文字列。字下げされたフェンスも対象にする。

    Returns:
        各コードブロックの本文（フェンス行を除く）。
    """
    return [match.group("body") for match in _SHELL_BLOCK_RE.finditer(markdown)]


def find_cross_block_references(blocks: list[str], variable: str) -> list[int]:
    """``variable`` を参照するのに同じブロック内で代入していないブロックの添字を返す。

    Args:
        blocks: ``extract_shell_blocks`` の戻り値。各要素は 1 回の Bash 呼び出しに相当する。
        variable: 検査するシェル変数名（``$`` なし）。

    Returns:
        別のブロック（= 別のシェル）で代入された値に依存しているブロックの添字。
    """
    reference = re.compile(rf"\$(?:\{{)?{re.escape(variable)}\b")
    assignment = re.compile(
        rf"^\s*(?:(?:export|local|readonly)\s+)?{re.escape(variable)}=", re.MULTILINE
    )
    return [
        index
        for index, block in enumerate(blocks)
        if reference.search(block) and not assignment.search(block)
    ]


def _merge_with_head_block() -> str:
    step_3 = _between(_skill_text(), "### Step 3: PRのマージ", "### Step 4:")
    blocks = [block for block in extract_shell_blocks(step_3) if "--match-head-commit" in block]
    assert len(blocks) == 1, blocks
    return blocks[0]


_BEFORE_FIX = """\
#### 2.5.4

```bash
REVIEWED_SHA=$(jq -r '.reviewed_head.sha' "$EVIDENCE")
```

### Step 3

```bash
kaji pr merge [branch_name] --match-head-commit "$REVIEWED_SHA"
```
"""

_AFTER_FIX = """\
#### 2.5.4

```bash
REVIEWED_SHA=$(jq -r '.reviewed_head.sha' "$EVIDENCE")
echo "REVIEWED_SHA=$REVIEWED_SHA"
```

### Step 3

1. item

   ```bash
   REVIEWED_SHA='[reviewed_sha]'
   kaji pr merge [branch_name] --match-head-commit "${REVIEWED_SHA}"
   ```
"""


@pytest.mark.small
def test_extract_shell_blocks_includes_indented_fences() -> None:
    blocks = extract_shell_blocks(_AFTER_FIX)
    assert len(blocks) == 2
    assert "--match-head-commit" in blocks[1]


@pytest.mark.small
def test_cross_block_reference_detected_in_before_fix_form() -> None:
    blocks = extract_shell_blocks(_BEFORE_FIX)
    assert find_cross_block_references(blocks, "REVIEWED_SHA") == [1]


@pytest.mark.small
def test_cross_block_reference_not_detected_in_after_fix_form() -> None:
    blocks = extract_shell_blocks(_AFTER_FIX)
    assert find_cross_block_references(blocks, "REVIEWED_SHA") == []


@pytest.mark.medium
def test_reviewed_sha_is_not_passed_across_bash_blocks() -> None:
    section = _between(_skill_text(), "#### 2.5.4", "### Step 4:")
    blocks = extract_shell_blocks(section)
    assert any("REVIEWED_SHA" in block for block in blocks)
    assert find_cross_block_references(blocks, "REVIEWED_SHA") == []


@pytest.mark.medium
def test_merge_with_head_block_uses_context_variable_and_validates() -> None:
    block = _merge_with_head_block()
    assert "REVIEWED_SHA='[reviewed_sha]'" in block
    assert "^[0-9a-f]{40}$" in block
    assert "exit 1" in block
    assert 'kaji pr merge [branch_name] --match-head-commit "$REVIEWED_SHA"' in block


@pytest.mark.medium
def test_step_3_routes_by_review_poll_mode() -> None:
    step_3 = _between(_skill_text(), "### Step 3: PRのマージ", "### Step 4:")
    assert "[review_poll_mode]" in step_3
    for mode in ("`verify`", "`skip`", "`manual`"):
        assert mode in step_3, mode
    assert "未記録" in step_3
    assert "ABORT" in step_3
    assert "`$REVIEWED_SHA` が設定されている場合" not in step_3


@pytest.mark.medium
def test_step_2_5_records_context_variables() -> None:
    text = _skill_text()
    step_2_5_1 = _between(text, "#### 2.5.1 適用判定", "#### 2.5.2")
    assert "[review_poll_mode]" in step_2_5_1
    assert 'echo "MODE=$MODE"' in step_2_5_1
    assert "`manual`" in step_2_5_1

    step_2_5_4 = _between(text, "#### 2.5.4", "#### 2.5.5")
    assert "[reviewed_sha]" in step_2_5_4
    assert 'echo "REVIEWED_SHA=$REVIEWED_SHA"' in step_2_5_4
    assert "^[0-9a-f]{40}$" in step_2_5_4

    step_2_5_5 = _between(text, "#### 2.5.5", "### Step 4:")
    assert "`$REVIEWED_SHA` が設定された場合だけ" not in step_2_5_5


@pytest.mark.medium
def test_missing_or_invalid_reviewed_sha_aborts_instead_of_unverified_merge() -> None:
    text = _skill_text()
    step_2_5_5 = _between(text, "#### 2.5.5", "### Step 4:")
    assert "40 桁の 16 進数でない" in step_2_5_5
    assert "照合なしの merge に進まず" in step_2_5_5
    assert "**ABORT**" in step_2_5_5

    abort_rows = [line for line in text.splitlines() if line.startswith("| ABORT |")]
    assert len(abort_rows) == 1, abort_rows
    assert "照合した SHA の欠落・不正" in abort_rows[0]


def _write_kaji_stub(bin_dir: Path, argv_file: Path) -> None:
    stub = bin_dir / "kaji"
    stub.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{argv_file}"\n', encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_merge_block(tmp_path: Path, reviewed_sha: str | None) -> tuple[int, str, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_file = tmp_path / "argv.txt"
    _write_kaji_stub(bin_dir, argv_file)

    script = _merge_with_head_block().replace("[branch_name]", "fix/999")
    if reviewed_sha is not None:
        script = script.replace("[reviewed_sha]", reviewed_sha)

    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    env.pop("REVIEWED_SHA", None)
    completed = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    return completed.returncode, completed.stdout, argv_file


@pytest.mark.large
@pytest.mark.large_local
def test_merge_block_passes_valid_sha_to_match_head_commit(tmp_path: Path) -> None:
    returncode, _stdout, argv_file = _run_merge_block(tmp_path, _VALID_SHA)
    assert returncode == 0
    assert argv_file.read_text(encoding="utf-8").splitlines() == [
        "pr",
        "merge",
        "fix/999",
        "--match-head-commit",
        _VALID_SHA,
    ]


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.parametrize(
    "reviewed_sha",
    [
        pytest.param("", id="empty"),
        pytest.param(_VALID_SHA[:-1], id="39-chars"),
        pytest.param(_VALID_SHA + "0", id="41-chars"),
        pytest.param("ABCDEF" + _VALID_SHA[6:], id="uppercase"),
        pytest.param("g" + _VALID_SHA[1:], id="non-hex"),
        pytest.param(None, id="placeholder-not-replaced"),
    ],
)
def test_merge_block_aborts_without_merge_on_invalid_sha(
    tmp_path: Path, reviewed_sha: str | None
) -> None:
    returncode, stdout, argv_file = _run_merge_block(tmp_path, reviewed_sha)
    assert returncode != 0
    assert not argv_file.exists()
    assert "ABORT" in stdout
