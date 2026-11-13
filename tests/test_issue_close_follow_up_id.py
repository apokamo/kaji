"""issue-close の follow-up ID が Bash 呼び出しをまたいでも失われないことを確認する（Issue #551）。

agent の Bash tool は呼び出しごとに別の shell プロセスで実行するため、§ 2 / § 3 で設定した
シェル変数 ``FOLLOW_UP_ID`` は § 4 のブロックへ引き継がれない。SKILL.md の各ブロックは
文脈変数 ``[follow_up_issue_id]`` の実値をブロック冒頭で受け取り、空・非数値・placeholder
未置換なら親本文の更新前に停止しなければならない。

各ブロックを SKILL.md から抜き出し、``FOLLOW_UP_ID`` を持たない新しい bash プロセスで、
``kaji`` を stub に差し替えて実行する（実 Issue の作成・編集・close はしない）。
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_SKILL = Path(__file__).resolve().parent.parent / ".claude/skills/issue-close/SKILL.md"
_SECTION_2 = "### 2. 親マーカーと既存 Issue の確認"
_SECTION_3 = "### 3. follow-up Issue の作成"
_SECTION_4 = "### 4. 親本文へのマーカー追記"
_SECTION_END = "> **結果を記録**: `follow_up_result`"
_PLACEHOLDER = "[follow_up_issue_id]"
_PARENT_ID = "551"
_MARKER_PREFIX = "<!-- kaji-follow-up-issue:"
_BASH_BLOCK_RE = re.compile(r"```bash\n(?P<body>.*?)\n```", re.DOTALL)

_STUB_KAJI = r"""#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$STUB_DIR/calls.log"
case "$1 $2" in
  "issue view")
    case " $* " in
      *" body "*) cat "$STUB_DIR/body" ;;
      *" state "*) cat "$STUB_DIR/state" ;;
      *" title "*) cat "$STUB_DIR/title" ;;
      *) exit 2 ;;
    esac
    ;;
  "issue edit")
    id=$3
    shift 3
    body_file=
    while [ $# -gt 0 ]; do
      case $1 in
        --body-file) body_file=$2; shift 2 ;;
        *) shift ;;
      esac
    done
    printf '%s\n' "$id" >> "$STUB_DIR/edited_ids"
    if [ "$id" = "$STUB_PARENT_ID" ] && [ ! -e "$STUB_DIR/no_persist" ]; then
      cp "$body_file" "$STUB_DIR/body"
    fi
    ;;
  "issue create")
    cat "$STUB_DIR/create_output"
    ;;
  *)
    exit 2
    ;;
esac
"""

_PARENT_BODY = "## 概要\n\n親 Issue の本文\n\n### ワークフロー完了後の確認項目\n\n- [ ] 事後確認\n"


@dataclass
class _Result:
    returncode: int
    stdout: str
    edited_ids: list[str]
    parent_body: str


def _skill_text() -> str:
    return _SKILL.read_text(encoding="utf-8")


def _section(start: str, end: str) -> str:
    text = _skill_text()
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def _bash_block(start: str, end: str, needle: str) -> str:
    blocks = [m["body"] for m in _BASH_BLOCK_RE.finditer(_section(start, end))]
    matched = [block for block in blocks if needle in block]
    assert len(matched) == 1, f"{start} に {needle!r} を含む bash ブロックが 1 件ではない"
    return matched[0]


def _run_block(
    block: str,
    tmp_path: Path,
    *,
    follow_up_id: str | None,
    state: str = "OPEN",
    create_output: str = "",
    persist: bool = True,
) -> _Result:
    stub_dir = tmp_path / "stub"
    bin_dir = tmp_path / "bin"
    skill_tmp = tmp_path / "skill-tmp"
    for d in (stub_dir, bin_dir, skill_tmp):
        d.mkdir(exist_ok=True)
    kaji = bin_dir / "kaji"
    kaji.write_text(_STUB_KAJI, encoding="utf-8")
    kaji.chmod(kaji.stat().st_mode | stat.S_IXUSR)
    (stub_dir / "body").write_text(_PARENT_BODY, encoding="utf-8")
    (stub_dir / "state").write_text(f"{state}\n", encoding="utf-8")
    (stub_dir / "title").write_text("親 Issue のタイトル\n", encoding="utf-8")
    (stub_dir / "create_output").write_text(create_output, encoding="utf-8")
    if not persist:
        (stub_dir / "no_persist").touch()
    (skill_tmp / "kaji-follow-up-body.md").write_text("follow-up body\n", encoding="utf-8")

    script = (
        block.replace("[skill_tmp_dir]", str(skill_tmp))
        .replace("[issue_id]", _PARENT_ID)
        .replace("[issue_ref]", f"#{_PARENT_ID}")
    )
    if follow_up_id is not None:
        script = script.replace(_PLACEHOLDER, follow_up_id)

    # 前の Bash 呼び出しのシェル変数は残らない: FOLLOW_UP_ID を持たない新しいプロセスで実行する
    env = {k: v for k, v in os.environ.items() if not k.startswith("FOLLOW_UP_")}
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        STUB_DIR=str(stub_dir),
        STUB_PARENT_ID=_PARENT_ID,
    )
    proc = subprocess.run(
        ["bash", "-c", script],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    edited = stub_dir / "edited_ids"
    return _Result(
        returncode=proc.returncode,
        stdout=proc.stdout,
        edited_ids=edited.read_text(encoding="utf-8").split() if edited.exists() else [],
        parent_body=(stub_dir / "body").read_text(encoding="utf-8"),
    )


def _marker_lines(body: str) -> list[str]:
    return [line for line in body.splitlines() if line.startswith(_MARKER_PREFIX)]


def _parent_marker_block() -> str:
    return _bash_block(_SECTION_4, _SECTION_END, "kaji-parent-body-with-follow-up.md")


def _state_check_block() -> str:
    return _bash_block(_SECTION_2, _SECTION_3, "FOLLOW_UP_STATE=")


def _create_block() -> str:
    return _bash_block(_SECTION_3, _SECTION_4, "kaji issue create")


_INVALID_IDS = [
    pytest.param("", id="empty"),
    pytest.param("abc", id="non_numeric"),
    pytest.param("12a", id="mixed"),
    pytest.param(" 12", id="leading_space"),
    pytest.param(None, id="placeholder_unsubstituted"),
]


@pytest.mark.medium
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
class TestParentMarkerBlock:
    @pytest.mark.parametrize("follow_up_id", _INVALID_IDS)
    def test_invalid_id_aborts_before_parent_update(
        self, follow_up_id: str | None, tmp_path: Path
    ) -> None:
        result = _run_block(_parent_marker_block(), tmp_path, follow_up_id=follow_up_id)

        assert result.returncode != 0
        assert "ABORT" in result.stdout
        assert result.edited_ids == []
        assert _marker_lines(result.parent_body) == []

    def test_valid_id_is_used_without_shell_variable(self, tmp_path: Path) -> None:
        result = _run_block(_parent_marker_block(), tmp_path, follow_up_id="999")

        assert result.returncode == 0, result.stdout
        assert result.edited_ids == [_PARENT_ID]
        assert _marker_lines(result.parent_body) == ["<!-- kaji-follow-up-issue: 999 -->"]
        assert result.parent_body.startswith(_PARENT_BODY.rstrip("\n"))

    def test_marker_missing_after_update_aborts(self, tmp_path: Path) -> None:
        result = _run_block(_parent_marker_block(), tmp_path, follow_up_id="999", persist=False)

        assert result.returncode != 0
        assert "ABORT" in result.stdout


@pytest.mark.medium
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
class TestExistingMarkerStateBlock:
    def test_open_follow_up_is_reused(self, tmp_path: Path) -> None:
        result = _run_block(_state_check_block(), tmp_path, follow_up_id="999", state="OPEN")

        assert result.returncode == 0, result.stdout
        assert result.edited_ids == []

    def test_closed_follow_up_aborts(self, tmp_path: Path) -> None:
        result = _run_block(_state_check_block(), tmp_path, follow_up_id="999", state="CLOSED")

        assert result.returncode != 0
        assert "ABORT" in result.stdout

    @pytest.mark.parametrize("follow_up_id", _INVALID_IDS)
    def test_invalid_id_aborts(self, follow_up_id: str | None, tmp_path: Path) -> None:
        result = _run_block(_state_check_block(), tmp_path, follow_up_id=follow_up_id)

        assert result.returncode != 0
        assert "ABORT" in result.stdout


@pytest.mark.medium
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
class TestCreateBlock:
    def test_created_id_is_reported_for_next_call(self, tmp_path: Path) -> None:
        result = _run_block(
            _create_block(),
            tmp_path,
            follow_up_id=None,
            create_output="https://github.com/owner/repo/issues/1234\n",
        )

        assert result.returncode == 0, result.stdout
        assert result.edited_ids == ["1234"]
        assert "follow_up_issue_id=1234" in result.stdout.splitlines()

    def test_unparsable_create_output_aborts_before_persist(self, tmp_path: Path) -> None:
        result = _run_block(
            _create_block(),
            tmp_path,
            follow_up_id=None,
            create_output="https://github.com/owner/repo/issues/\n",
        )

        assert result.returncode != 0
        assert "ABORT" in result.stdout
        assert result.edited_ids == []


@pytest.mark.small
@pytest.mark.parametrize(
    ("start", "end"),
    [(_SECTION_2, _SECTION_3), (_SECTION_3, _SECTION_4)],
    ids=["section_2", "section_3"],
)
def test_sections_record_follow_up_issue_id(start: str, end: str) -> None:
    assert "文脈変数 `follow_up_issue_id`" in _section(start, end)


@pytest.mark.small
def test_section_4_keeps_skip_for_existing_valid_marker() -> None:
    assert "既存の 1 件を維持する" in _section(_SECTION_4, _SECTION_END)
