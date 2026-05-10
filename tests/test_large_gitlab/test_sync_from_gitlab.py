"""``kaji sync from-gitlab`` against a real GitLab project.

Top-level confirmation that the sync round-trip from issue #4 works end
to end against a live ``gitlab.com`` project: real ``glab api`` paginated
fetch → ``.kaji-artifacts/`` cache write → ``kaji issue view gl:<iid>``
read-only resolution against the cache.

This test runs under ``provider.type='local'`` (not ``gitlab``) because
``kaji sync from-gitlab`` is the cache-populate path used by local-mode
operators who collaborate with a GitLab project from a host that does
not own the project.
"""

from __future__ import annotations

import json
import re
import subprocess
import textwrap
from pathlib import Path
from urllib.parse import quote

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_gitlab]


_IID_PATTERN = re.compile(r"#(\d+)\b")


@pytest.fixture
def local_workspace_with_gitlab_ref(
    tmp_path: Path,
    gitlab_repo: str,
    gitlab_default_branch: str,
) -> Path:
    """Workspace configured with ``provider.type='local'`` plus a populated
    ``[provider.gitlab]`` block so ``kaji sync from-gitlab`` resolves the
    repo from config.
    """
    repo = tmp_path / "workspace_local"
    repo.mkdir()
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.toml").write_text(
        textwrap.dedent(
            f"""\
            [paths]
            artifacts_dir = ".kaji-artifacts"
            skill_dir = ".claude/skills"

            [execution]
            default_timeout = 1800

            [provider]
            type = "local"

            [provider.local]
            machine_id = "pc1"
            default_branch = "main"

            [provider.gitlab]
            repo = "{gitlab_repo}"
            default_branch = "{gitlab_default_branch}"
            """
        )
    )
    (repo / ".gitignore").write_text(".kaji-artifacts/\n")
    return repo


def test_sync_from_gitlab_populates_cache_and_view_can_read(
    local_workspace_with_gitlab_ref: Path,
    gitlab_repo: str,
    unique_suffix: str,
    created_resources,  # type: ignore[no-untyped-def]
    run_kaji,  # type: ignore[no-untyped-def]
) -> None:
    workspace = local_workspace_with_gitlab_ref

    # 1. Create a known seed issue on the GitLab project so the sync has at
    #    least one item to write to cache.
    title = f"sync-fixture {unique_suffix}"
    create = subprocess.run(
        [
            "glab",
            "--hostname",
            "gitlab.com",
            "api",
            "-X",
            "POST",
            "-f",
            f"title={title}",
            "-f",
            "description=created by test_sync_from_gitlab.py",
            "-f",
            "labels=kaji-e2e",
            f"projects/{quote(gitlab_repo, safe='')}/issues",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert create.returncode == 0, create.stderr
    iid = json.loads(create.stdout)["iid"]
    created_resources.add_issue(iid)

    # 2. Run the sync.
    sync = run_kaji(
        workspace,
        "sync",
        "from-gitlab",
        timeout=180,
    )
    assert sync.returncode == 0, (
        f"kaji sync from-gitlab failed (rc={sync.returncode}):\n"
        f"stdout: {sync.stdout}\nstderr: {sync.stderr}"
    )

    # 3. Cache directory exists and contains at least our seed issue.
    cache_dir = workspace / ".kaji-artifacts"
    assert cache_dir.is_dir(), f"sync did not create {cache_dir}"
    cached_iids: set[int] = set()
    for entry in cache_dir.rglob("*.json"):
        try:
            data = json.loads(entry.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        # Either single object or array; just look for any iid we recognize.
        candidates = data if isinstance(data, list) else [data]
        for c in candidates:
            if isinstance(c, dict):
                value = c.get("iid") or c.get("number")
                if isinstance(value, int):
                    cached_iids.add(value)
    assert iid in cached_iids, (
        f"sync cache did not include seed iid {iid}; cached: {sorted(cached_iids)!r}"
    )

    # 4. ``kaji issue view gl:<iid>`` reads from the cache (read-only path).
    view = run_kaji(
        workspace,
        "issue",
        "view",
        f"gl:{iid}",
        "--json",
        "title,number",
    )
    assert view.returncode == 0, (
        f"kaji issue view gl:{iid} failed:\nstdout: {view.stdout}\nstderr: {view.stderr}"
    )
    payload = json.loads(view.stdout)
    assert payload["title"] == title
    assert payload["number"] == iid
