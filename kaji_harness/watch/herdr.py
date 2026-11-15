"""Herdr access for ``kaji watch`` (Issue #543 D4・D6).

watch は ``HerdrClient`` を通してだけ Herdr を呼ぶ。既定の実装は
``interactive_terminal_herdr`` の公開関数に委譲し、テストでは fake を注入する。
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .. import interactive_terminal_herdr as herdr_cli

TRIAGE_TAB_PREFIX = "watch-triage-"
WATCH_TAB_LABEL = "kaji-watch"
SERIES_TAB_PREFIX = "kaji-series-"
_TRIAGE_TAB_RE = re.compile(rf"^{re.escape(TRIAGE_TAB_PREFIX)}(\d+)$")


@dataclass(frozen=True)
class TabRef:
    """label で見つけたタブ。

    Attributes:
        tab_id: タブの ID。
        pane_id: root pane の ID（不明なら ``None``）。
        label: タブの label。
    """

    tab_id: str
    pane_id: str | None
    label: str


class HerdrClient(Protocol):
    """watch が使う Herdr の操作。"""

    def list_tabs(self) -> list[TabRef]:
        """label を持つタブの一覧。"""
        ...

    def create_tab(self, label: str, cwd: Path) -> tuple[str, str]:
        """タブを作り ``(tab_id, pane_id)`` を返す。"""
        ...

    def run_in_pane(self, pane_id: str, command: str) -> None:
        """pane で 1 行のコマンドを実行する。"""
        ...

    def close_tab(self, tab_id: str) -> None:
        """タブを閉じる。"""
        ...

    def pane_foreground(self, pane_id: str) -> tuple[str, list[str]]:
        """前景の状態（``active`` / ``shell_only`` / ``unknown``）と argv の列。"""
        ...

    def agent_status(self, pane_id: str, origin_pane: str | None, run_id: str | None) -> str:
        """所有を確認した step の pane の ``agent_status``（確認できなければ ``unknown``）。"""
        ...


def _str(value: object) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return value if isinstance(value, str) and value else None


def _first_pane_by_tab(panes: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """``herdr pane list`` の要素から、タブごとに最初に現れる pane ID を返す。"""
    found: dict[str, str] = {}
    for pane in panes:
        tab_id = _str(pane.get("tab_id"))
        pane_id = _str(pane.get("pane_id"))
        if tab_id is not None and pane_id is not None:
            found.setdefault(tab_id, pane_id)
    return found


def parse_tabs(
    raw: Iterable[Mapping[str, object]],
    panes: Iterable[Mapping[str, object]] = (),
) -> list[TabRef]:
    """``herdr tab list`` の要素から ``TabRef`` を作る（label の無いタブは除く）。

    Herdr 0.9.5 の ``TabInfo`` は pane ID を持たないため、``herdr pane list`` の
    ``PaneInfo.tab_id`` と結合して root pane を解決する（同じタブに pane が複数あれば、
    一覧で最初に現れる pane。watch が作るタブは pane が 1 個だけ）。tab 側に
    ``root_pane`` 等があればそれを優先する。
    """
    by_tab = _first_pane_by_tab(panes)
    tabs: list[TabRef] = []
    for tab in raw:
        tab_id = _str(tab.get("tab_id"))
        label = _str(tab.get("label"))
        if tab_id is None or label is None:
            continue
        root = tab.get("root_pane")
        pane_id = _str(root.get("pane_id")) if isinstance(root, dict) else None
        if pane_id is None:
            pane_id = _str(tab.get("root_pane_id")) or _str(tab.get("pane_id"))
        if pane_id is None:
            pane_id = by_tab.get(tab_id)
        tabs.append(TabRef(tab_id=tab_id, pane_id=pane_id, label=label))
    return tabs


class CliHerdrClient:
    """``herdr`` CLI に委譲する既定の実装。"""

    def __init__(self, herdr_bin: str) -> None:
        self.herdr_bin = herdr_bin

    def list_tabs(self) -> list[TabRef]:
        """label を持つタブの一覧（pane ID は ``herdr pane list`` と結合して解決する）。"""
        return parse_tabs(herdr_cli.list_tabs(self.herdr_bin), herdr_cli.list_panes(self.herdr_bin))

    def create_tab(self, label: str, cwd: Path) -> tuple[str, str]:
        """タブを作り ``(tab_id, pane_id)`` を返す。"""
        return herdr_cli.create_tab(self.herdr_bin, label=label, cwd=cwd)

    def run_in_pane(self, pane_id: str, command: str) -> None:
        """pane で 1 行のコマンドを実行する。"""
        herdr_cli.run_in_pane(self.herdr_bin, pane_id, command)

    def close_tab(self, tab_id: str) -> None:
        """タブを閉じる。"""
        herdr_cli.close_tab(self.herdr_bin, tab_id)

    def pane_foreground(self, pane_id: str) -> tuple[str, list[str]]:
        """前景の状態と argv の列。"""
        return herdr_cli.pane_foreground(self.herdr_bin, pane_id)

    def agent_status(self, pane_id: str, origin_pane: str | None, run_id: str | None) -> str:
        """所有を確認した step の pane の ``agent_status``。"""
        return herdr_cli.read_pane_agent_status(pane_id, origin_pane=origin_pane, run_id=run_id)


def triage_tab_label(issue: int) -> str:
    """triage タブの label。"""
    return f"{TRIAGE_TAB_PREFIX}{issue}"


def open_triage_tabs(tabs: Iterable[TabRef]) -> dict[int, TabRef]:
    """label ``watch-triage-<issue>`` のタブが開いている Issue。"""
    found: dict[int, TabRef] = {}
    for tab in tabs:
        match = _TRIAGE_TAB_RE.match(tab.label)
        if match:
            found[int(match.group(1))] = tab
    return found


def find_tab(tabs: Iterable[TabRef], label: str) -> TabRef | None:
    """label が一致する最初のタブ。"""
    return next((tab for tab in tabs if tab.label == label), None)


def triage_command(event_file: Path) -> str:
    """triage タブで実行する 1 行のコマンド（A7: agent は claude に固定）。"""
    return shlex.join(["claude", "--dangerously-skip-permissions", f"/watch-triage {event_file}"])


def foreground_has(commands: Iterable[str], needle: str) -> bool:
    """前景の argv のどれかに ``needle`` が含まれるか。"""
    return any(needle in command for command in commands)
