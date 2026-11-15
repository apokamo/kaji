"""Resolve a workflow ``name:`` to its YAML file (Issue #545 MF-1).

``/run-watched`` の ``wf:<name>`` と ``kaji watch retry`` の event の ``workflow`` は、どちらも
workflow のトップレベル ``name:`` である。ファイル名（stem）では照合しない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml


class WorkflowResolutionError(ValueError):
    """workflow を一意に解決できない。

    Attributes:
        reason: ``unresolvable`` / ``ambiguous``。
    """

    def __init__(self, reason: Literal["unresolvable", "ambiguous"], message: str) -> None:
        self.reason = reason
        super().__init__(message)


def workflow_files(wf_root: Path) -> list[Path]:
    """``wf_root`` の下の ``*.yaml`` を再帰的に列挙する（ソート済み）。"""
    return sorted(path for path in wf_root.rglob("*.yaml") if path.is_file())


def resolve_workflow_name(name: str, repo_root: Path) -> Path:
    """``name:`` が完全一致する ``.kaji/wf/`` の下の workflow YAML（ちょうど 1 個）を返す。

    読めない・YAML として壊れたファイルは候補にしない。

    Raises:
        WorkflowResolutionError: 0 個（unresolvable）または 2 個以上（ambiguous）の場合。
    """
    matches: list[Path] = []
    for path in workflow_files(repo_root / ".kaji" / "wf"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if isinstance(data, dict) and data.get("name") == name:
            matches.append(path.resolve())
    if not matches:
        raise WorkflowResolutionError("unresolvable", f"workflow '{name}' matches no workflow")
    if len(matches) > 1:
        raise WorkflowResolutionError("ambiguous", f"workflow '{name}' matches several workflows")
    return matches[0]
