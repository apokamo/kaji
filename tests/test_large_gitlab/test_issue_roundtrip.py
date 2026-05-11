"""``kaji issue`` round-trip against a real GitLab project.

Covers ``create`` → ``view`` → ``edit`` → ``comment`` → ``list`` → ``close``,
all via subprocess on the kaji CLI configured with ``provider.type='gitlab'``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_gitlab]


_IID_PATTERN = re.compile(r"#(\d+)\b")


def _extract_iid(text: str) -> int:
    """Extract the first ``#<digits>`` token from glab output (issue create
    prints ``#<iid>`` on a line by itself)."""
    m = _IID_PATTERN.search(text)
    if not m:
        raise AssertionError(f"could not parse iid from glab output: {text!r}")
    return int(m.group(1))


def test_issue_full_roundtrip(
    kaji_workspace: Path,
    unique_suffix: str,
    created_resources,  # type: ignore[no-untyped-def]
    run_kaji,  # type: ignore[no-untyped-def]
) -> None:
    """create → view (--json) → edit → comment → list (--label) → close."""
    title = f"issue-roundtrip {unique_suffix}"

    # ---- create ----
    create = run_kaji(
        kaji_workspace,
        "issue",
        "create",
        "--title",
        title,
        "--body",
        "round-trip body",
        "--label",
        "kaji-e2e",
    )
    assert create.returncode == 0, (
        f"issue create failed (rc={create.returncode}):\n"
        f"stdout: {create.stdout}\nstderr: {create.stderr}"
    )
    iid = _extract_iid(create.stdout + create.stderr)
    created_resources.add_issue(iid)

    # ---- context (gl:7 regression: GitLab provider must accept this sub) ----
    # type:* label が付与されていない (kaji-e2e のみ) ため、context は ``chore`` に
    # fallback する。assert は label に依存しない shape のみを検証する。
    context = run_kaji(
        kaji_workspace,
        "issue",
        "context",
        str(iid),
        "--json",
        "branch_prefix,branch_name,provider_type,issue_ref,branch_prefix_fallback",
    )
    assert context.returncode == 0, (
        f"issue context failed (rc={context.returncode}):\nstderr: {context.stderr}"
    )
    ctx_payload = json.loads(context.stdout)
    assert ctx_payload["provider_type"] == "gitlab"
    assert ctx_payload["issue_ref"] == f"gl:{iid}"
    # type:* label 不在 → chore fallback
    assert ctx_payload["branch_prefix"] == "chore"
    assert ctx_payload["branch_prefix_fallback"] is True
    assert ctx_payload["branch_name"] == f"chore/{iid}"

    # Also exercise the gl:N input form with -q raw output
    context_q = run_kaji(
        kaji_workspace,
        "issue",
        "context",
        f"gl:{iid}",
        "-q",
        ".branch_prefix",
    )
    assert context_q.returncode == 0, context_q.stderr
    assert context_q.stdout.strip() == "chore"

    # ---- view (--json title,state,labels) ----
    view = run_kaji(
        kaji_workspace,
        "issue",
        "view",
        str(iid),
        "--json",
        "title,state,labels",
    )
    assert view.returncode == 0, view.stderr
    payload = json.loads(view.stdout)
    assert payload["title"] == title
    assert payload["state"].lower() == "open"
    label_names = {lbl["name"] for lbl in payload["labels"] if isinstance(lbl, dict)}
    assert "kaji-e2e" in label_names

    # ---- edit (--body) ----
    edit = run_kaji(
        kaji_workspace,
        "issue",
        "edit",
        str(iid),
        "--body",
        "edited body",
    )
    assert edit.returncode == 0, edit.stderr

    view_after_edit = run_kaji(
        kaji_workspace,
        "issue",
        "view",
        str(iid),
        "--json",
        "body",
    )
    assert view_after_edit.returncode == 0
    body_payload = json.loads(view_after_edit.stdout)
    assert "edited body" in body_payload["body"]

    # ---- comment ----
    comment = run_kaji(
        kaji_workspace,
        "issue",
        "comment",
        str(iid),
        "--body",
        f"comment from {unique_suffix}",
    )
    assert comment.returncode == 0, comment.stderr

    # ---- list --label kaji-e2e --state open ----
    listing = run_kaji(
        kaji_workspace,
        "issue",
        "list",
        "--label",
        "kaji-e2e",
        "--state",
        "open",
        "--json",
        "number,title",
    )
    assert listing.returncode == 0, listing.stderr
    items = json.loads(listing.stdout)
    iids = {item["number"] for item in items}
    assert iid in iids, f"created iid {iid} not found in `kaji issue list` output"

    # ---- close ----
    close = run_kaji(
        kaji_workspace,
        "issue",
        "close",
        str(iid),
    )
    assert close.returncode == 0, close.stderr

    view_after_close = run_kaji(
        kaji_workspace,
        "issue",
        "view",
        str(iid),
        "--json",
        "state",
    )
    assert view_after_close.returncode == 0
    closed_payload = json.loads(view_after_close.stdout)
    assert closed_payload["state"].lower() == "closed"
