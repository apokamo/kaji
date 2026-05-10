"""``kaji pr <unsupported>`` rejects with ``EXIT_INVALID_INPUT`` (no silent
passthrough).

The kaji-pr-mr-bridge.md decision is that subcommands outside Tier A/B
must be explicitly rejected — silent passthrough to ``glab`` would expose
GitLab-specific UX inconsistent with GitHub mode.

This test sets up a real GitLab provider configuration (which is why it
sits under ``large_gitlab`` rather than ``large_local``) but does NOT
require write access for any of the rejected subcommands — kaji rejects
them before any subprocess is launched. The shared skip gates still
apply so the test file is consistent with the rest of the suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_gitlab]


# Subcommands that must be rejected. Drawn from gh's pr command set; if
# kaji adds Tier A/B support for any of these in the future, move them
# out of this list.
_UNSUPPORTED_SUBS = [
    "approvers",
    "checkout",
    "checks",
    "diff",
    "edit",
    "lock",
    "ready",
    "reopen",
    "status",
    "subscribe",
    "todo",
    "unlock",
    "unsubscribe",
    "update-branch",
]


@pytest.mark.parametrize("sub", _UNSUPPORTED_SUBS)
def test_unsupported_pr_sub_is_rejected(
    kaji_workspace: Path,
    run_kaji,  # type: ignore[no-untyped-def]
    sub: str,
) -> None:
    """Each unsupported sub exits 2 with a message naming the supported
    list. We invoke without an iid argument to keep the surface narrow —
    the rejection happens before argument parsing of the sub anyway."""
    result = run_kaji(kaji_workspace, "pr", sub)
    assert result.returncode == 2, (
        f"`kaji pr {sub}` should exit 2 (invalid input), got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert sub in result.stderr, (
        f"stderr should name the rejected sub {sub!r}; got: {result.stderr!r}"
    )
    # Should NOT be a runtime / glab passthrough error
    assert "glab" not in result.stderr.lower() or "supported" in result.stderr.lower(), (
        f"stderr should clearly indicate the sub is not supported, not a glab error: "
        f"{result.stderr!r}"
    )
