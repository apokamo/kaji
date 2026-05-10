"""``kaji pr`` round-trip against a real GitLab project.

Covers Tier B subcommands ``create`` → ``view`` → ``list`` → ``comment``.
``review`` and ``merge`` get their own dedicated contract test
(``test_pr_review_contract.py``) because the assertions there are tight
enough to deserve isolation.

Each test creates a fresh source branch with one commit (via ``glab api``
to avoid setting up a local git remote) and an MR. ``created_resources``
teardown cleans up MR + branch best-effort.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_gitlab]


_IID_PATTERN = re.compile(r"!(\d+)\b")


def _extract_mr_iid(text: str) -> int:
    """Parse ``!<iid>`` from ``glab mr create`` stdout/stderr."""
    m = _IID_PATTERN.search(text)
    if not m:
        raise AssertionError(f"could not parse mr iid from glab output: {text!r}")
    return int(m.group(1))


def _create_test_branch_with_commit(
    repo: str,
    base: str,
    branch: str,
    file_path: str,
    file_content: str,
) -> None:
    """Create ``branch`` from ``base`` and commit one file via ``glab api``."""
    encoded = quote(repo, safe="")
    branch_create = subprocess.run(
        [
            "glab",
            "--hostname",
            "gitlab.com",
            "api",
            "-X",
            "POST",
            "-f",
            f"branch={branch}",
            "-f",
            f"ref={base}",
            f"projects/{encoded}/repository/branches",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if branch_create.returncode != 0:
        pytest.fail(
            f"failed to create branch {branch!r} from {base!r}: {branch_create.stderr.strip()}"
        )
    commit = subprocess.run(
        [
            "glab",
            "--hostname",
            "gitlab.com",
            "api",
            "-X",
            "POST",
            "-f",
            f"branch={branch}",
            "-f",
            f"commit_message=kaji-e2e: add {file_path}",
            "-f",
            "actions[][action]=create",
            "-f",
            f"actions[][file_path]={file_path}",
            "-f",
            f"actions[][content]={file_content}",
            f"projects/{encoded}/repository/commits",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if commit.returncode != 0:
        pytest.fail(f"failed to commit on branch {branch!r}: {commit.stderr.strip()}")


def test_pr_create_view_list_comment(
    kaji_workspace: Path,
    gitlab_repo: str,
    gitlab_default_branch: str,
    unique_suffix: str,
    created_resources,  # type: ignore[no-untyped-def]
    run_kaji,  # type: ignore[no-untyped-def]
) -> None:
    """create → view (--json) → list (--head) → comment."""
    branch = f"e2e/{unique_suffix}"
    created_resources.add_branch(branch)
    _create_test_branch_with_commit(
        gitlab_repo,
        gitlab_default_branch,
        branch,
        f"kaji-e2e/{unique_suffix}.txt",
        f"placeholder for {unique_suffix}\n",
    )

    title = f"pr-roundtrip {unique_suffix}"

    # ---- create ----
    create = run_kaji(
        kaji_workspace,
        "pr",
        "create",
        "--title",
        title,
        "--body",
        "round-trip body",
        "--base",
        gitlab_default_branch,
        "--head",
        branch,
    )
    assert create.returncode == 0, (
        f"pr create failed (rc={create.returncode}):\n"
        f"stdout: {create.stdout}\nstderr: {create.stderr}"
    )
    mr_iid = _extract_mr_iid(create.stdout + create.stderr)
    created_resources.add_mr(mr_iid)

    # ---- view --json title,state,headRefName,baseRefName ----
    view = run_kaji(
        kaji_workspace,
        "pr",
        "view",
        str(mr_iid),
        "--json",
        "title,state,headRefName,baseRefName",
    )
    assert view.returncode == 0, view.stderr
    payload = json.loads(view.stdout)
    assert payload["title"] == title
    assert payload["state"].upper() == "OPEN"
    assert payload["headRefName"] == branch
    assert payload["baseRefName"] == gitlab_default_branch

    # ---- list --head <branch> ----
    listing = run_kaji(
        kaji_workspace,
        "pr",
        "list",
        "--head",
        branch,
        "--state",
        "open",
        "--json",
        "number,title",
    )
    assert listing.returncode == 0, listing.stderr
    items = json.loads(listing.stdout)
    iids = {item["number"] for item in items}
    assert mr_iid in iids

    # ---- comment ----
    comment = run_kaji(
        kaji_workspace,
        "pr",
        "comment",
        str(mr_iid),
        "--body",
        f"comment from {unique_suffix}",
    )
    assert comment.returncode == 0, comment.stderr
