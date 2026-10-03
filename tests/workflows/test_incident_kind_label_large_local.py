"""incident-* skill の ``kind_label`` 解決手順を実 bash / python3 で実行して検証する（Issue #457）。

抽出仕様: ``incident-investigate/SKILL.md`` の見出し「`kind_label` の解決」の直後にある最初の
```bash ブロックを取り出し、末尾に ``printf '%s' "$KIND_LABEL"`` を付けて実行する。
見出しまたはブロックが見つからなければ fail する。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from kaji_harness.config import IncidentConfig

pytestmark = [pytest.mark.large, pytest.mark.large_local]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "incident-investigate" / "SKILL.md"
HEADING = "`kind_label` の解決"


def _extract_resolve_script() -> str:
    text = SKILL_PATH.read_text(encoding="utf-8")
    heading = re.search(rf"^#+ {re.escape(HEADING)}\s*$", text, re.MULTILINE)
    assert heading is not None, f"heading {HEADING!r} not found in {SKILL_PATH}"
    block = re.search(r"```bash\n(.*?)\n```", text[heading.end() :], re.DOTALL)
    assert block is not None, f"no bash block after heading {HEADING!r}"
    return block.group(1) + "\nprintf '%s' \"$KIND_LABEL\"\n"


def _run(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", _extract_resolve_script()],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _write_config(root: Path, body: str) -> None:
    config = root / ".kaji" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(body, encoding="utf-8")


class TestResolveKindLabelSuccess:
    def test_default_when_incident_section_absent(self, tmp_path: Path) -> None:
        _write_config(tmp_path, '[provider]\ntype = "github"\n')
        result = _run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == IncidentConfig().kind_label

    def test_configured_value(self, tmp_path: Path) -> None:
        _write_config(tmp_path, '[incident]\nkind_label = "custom:incident"\n')
        result = _run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "custom:incident"

    def test_searches_parent_directories(self, tmp_path: Path) -> None:
        _write_config(tmp_path, '[incident]\nkind_label = "custom:incident"\n')
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        result = _run(nested)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "custom:incident"


class TestResolveKindLabelFailure:
    def _assert_fails(self, result: subprocess.CompletedProcess[str]) -> None:
        assert result.returncode != 0
        assert result.stdout == ""
        assert result.stderr.strip() != ""

    def test_config_not_found(self, outside_project_tmp_path: Path) -> None:
        self._assert_fails(_run(outside_project_tmp_path))

    def test_invalid_toml(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "[incident\nkind_label = ")
        self._assert_fails(_run(tmp_path))

    def test_incident_not_a_table(self, tmp_path: Path) -> None:
        _write_config(tmp_path, 'incident = "x"\n')
        self._assert_fails(_run(tmp_path))

    @pytest.mark.parametrize("value", ["1", '""', '"  "'])
    def test_kind_label_not_non_empty_string(self, tmp_path: Path, value: str) -> None:
        _write_config(tmp_path, f"[incident]\nkind_label = {value}\n")
        self._assert_fails(_run(tmp_path))
