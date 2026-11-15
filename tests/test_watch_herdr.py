"""Small tests: ``kaji_harness.watch.herdr`` helpers (Issue #543 D6・A7)."""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from kaji_harness.watch.herdr import (
    TabRef,
    find_tab,
    foreground_has,
    open_triage_tabs,
    parse_tabs,
    triage_command,
    triage_tab_label,
)

pytestmark = pytest.mark.small


def test_parse_tabs_reads_ids_and_skips_unlabeled() -> None:
    tabs = parse_tabs(
        [
            {"tab_id": "t1", "label": "watch-triage-101", "root_pane": {"pane_id": "p_1"}},
            {"tab_id": "t2", "label": None},
            {"tab_id": 3, "label": "kaji-watch", "root_pane_id": "p_3"},
        ]
    )
    assert tabs == [
        TabRef(tab_id="t1", pane_id="p_1", label="watch-triage-101"),
        TabRef(tab_id="3", pane_id="p_3", label="kaji-watch"),
    ]


def test_open_triage_tabs_and_find() -> None:
    tabs = [
        TabRef("t1", "p_1", triage_tab_label(101)),
        TabRef("t2", "p_2", "watch-triage-x"),
        TabRef("t3", "p_3", "kaji-watch"),
    ]
    assert set(open_triage_tabs(tabs)) == {101}
    assert find_tab(tabs, "kaji-watch") == tabs[2]
    assert find_tab(tabs, "nope") is None


def test_triage_command_is_quoted_claude() -> None:
    command = triage_command(Path("/a b/.kaji-artifacts/_watch/events/101-aborted-1.json"))
    argv = shlex.split(command)
    assert argv[:2] == ["claude", "--dangerously-skip-permissions"]
    assert argv[2] == "/watch-triage /a b/.kaji-artifacts/_watch/events/101-aborted-1.json"


def test_foreground_has() -> None:
    assert foreground_has(["bash", "kaji run-series x.yaml"], "run-series")
    assert not foreground_has(["bash"], "claude")


# Herdr 0.9.5 の実応答の形（TabInfo は pane ID を持たず、PaneInfo が tab_id を持つ）
_REAL_TABS = [
    {
        "agent_status": "unknown",
        "focused": False,
        "label": "watch-triage-101",
        "number": 4,
        "pane_count": 1,
        "tab_id": "w36:t9",
        "workspace_id": "w36",
    },
    {
        "agent_status": "idle",
        "focused": True,
        "label": "kaji",
        "number": 2,
        "pane_count": 2,
        "tab_id": "w36:t2",
        "workspace_id": "w36",
    },
]
_REAL_PANES = [
    {"pane_id": "w36:p27", "tab_id": "w36:t2", "workspace_id": "w36", "agent_status": "idle"},
    {"pane_id": "w36:p4N", "tab_id": "w36:t2", "workspace_id": "w36", "agent_status": "unknown"},
    {"pane_id": "w36:p9A", "tab_id": "w36:t9", "workspace_id": "w36", "agent_status": "unknown"},
]


def test_parse_tabs_resolves_pane_from_pane_list_for_real_herdr_shape() -> None:
    tabs = parse_tabs(_REAL_TABS, _REAL_PANES)
    assert tabs == [
        TabRef(tab_id="w36:t9", pane_id="w36:p9A", label="watch-triage-101"),
        TabRef(tab_id="w36:t2", pane_id="w36:p27", label="kaji"),
    ]


def test_parse_tabs_without_pane_list_leaves_pane_unknown() -> None:
    assert parse_tabs(_REAL_TABS)[0].pane_id is None


def test_cli_client_list_tabs_joins_pane_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from kaji_harness.watch import herdr as herdr_mod

    monkeypatch.setattr(herdr_mod.herdr_cli, "list_tabs", lambda _bin: _REAL_TABS)
    monkeypatch.setattr(herdr_mod.herdr_cli, "list_panes", lambda _bin: _REAL_PANES)
    tabs = herdr_mod.CliHerdrClient("herdr").list_tabs()
    assert find_tab(tabs, "watch-triage-101") == TabRef("w36:t9", "w36:p9A", "watch-triage-101")
