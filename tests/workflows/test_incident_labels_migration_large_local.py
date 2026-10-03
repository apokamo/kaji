"""incident ラベル移行手順の ``labeled()`` を実 bash で実行して検証する（Issue #457）。

抽出仕様: ``docs/dev/incident-labels.md`` の ``labeled() {`` を含む最初の ```bash ブロックを
取り出し、PATH 上の偽 ``gh`` と組み合わせて実行する。ブロックが見つからなければ fail する。
偽 ``gh`` は ``issue list`` / ``pr list`` ごとに、環境変数の指定で成功（番号を出力）または
失敗（stderr にメッセージ・終了コード 1）を返す。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_local]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOC_PATH = REPO_ROOT / "docs" / "dev" / "incident-labels.md"

FAKE_GH = """#!/usr/bin/env bash
case "$1" in
  issue) fail="$FAKE_ISSUE_FAIL"; numbers="$FAKE_ISSUE_NUMBERS" ;;
  pr) fail="$FAKE_PR_FAIL"; numbers="$FAKE_PR_NUMBERS" ;;
  *) exit 2 ;;
esac
if [ "$fail" = "1" ]; then
  echo "simulated API failure ($1)" >&2
  exit 1
fi
printf '%s' "$numbers"
"""


def _extract_labeled_function() -> str:
    text = DOC_PATH.read_text(encoding="utf-8")
    for block in re.finditer(r"```bash\n(.*?)\n```", text, re.DOTALL):
        if "labeled() {" in block.group(1):
            return block.group(1)
    raise AssertionError(f"no bash block defining labeled() in {DOC_PATH}")


def _run_labeled(
    tmp_path: Path,
    *,
    issue_fail: bool = False,
    pr_fail: bool = False,
    issue_numbers: str = "",
    pr_numbers: str = "",
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "FAKE_ISSUE_FAIL": "1" if issue_fail else "0",
        "FAKE_PR_FAIL": "1" if pr_fail else "0",
        "FAKE_ISSUE_NUMBERS": issue_numbers,
        "FAKE_PR_NUMBERS": pr_numbers,
    }
    script = _extract_labeled_function() + '\nlabeled "kaji:incident"\n'
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


class TestLabeledSuccess:
    def test_merges_issue_and_pr_numbers_sorted(self, tmp_path: Path) -> None:
        result = _run_labeled(tmp_path, issue_numbers="12\n3\n", pr_numbers="7\n")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "3\n7\n12\n"

    def test_empty_when_no_label_assigned(self, tmp_path: Path) -> None:
        result = _run_labeled(tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""


class TestLabeledFailure:
    @pytest.mark.parametrize(
        ("issue_fail", "pr_fail"),
        [(True, True), (True, False), (False, True)],
        ids=["both-fail", "issue-only-fails", "pr-only-fails"],
    )
    def test_any_listing_failure_is_nonzero_with_no_output(
        self, tmp_path: Path, issue_fail: bool, pr_fail: bool
    ) -> None:
        result = _run_labeled(
            tmp_path,
            issue_fail=issue_fail,
            pr_fail=pr_fail,
            issue_numbers="1\n",
            pr_numbers="2\n",
        )
        assert result.returncode != 0
        assert result.stdout == ""
        assert "simulated API failure" in result.stderr
