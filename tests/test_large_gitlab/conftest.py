"""Shared fixtures and helpers for ``tests/test_large_gitlab/``.

These tests exercise ``provider.type='gitlab'`` end-to-end against a real
GitLab project via ``glab``. All fixtures gate on env / auth / project
prerequisites and ``pytest.skip`` cleanly when any are missing — running
``make test-large-gitlab`` without auth must produce ``exit 0`` with all
tests skipped (operator-friendly fail-soft).

Required prerequisites (any test that depends on the gated fixtures will
skip if these are missing):

- ``glab`` CLI on PATH
- ``GITLAB_TOKEN`` env OR ``glab auth status`` succeeds
- ``KAJI_TEST_GITLAB_REPO=<group>/<project>`` (a dedicated test fixture
  project — tests create / close real issues and merge requests against it)

Optional:

- ``KAJI_TEST_GITLAB_DEFAULT_BRANCH`` (default: ``main``)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import uuid
import warnings
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest

# Module-wide markers: every test under this package is large + large_gitlab.
# Individual test modules may add more (e.g. small/medium tags) but should
# not strip these.
pytestmark = [pytest.mark.large, pytest.mark.large_gitlab]


# ============================================================
# Helpers (re-exported as a small fixture for tests to call)
# ============================================================


_KAJI_CMD = [sys.executable, "-m", "kaji_harness.cli_main"]
_GLAB_HOSTNAME = "gitlab.com"

# Label applied to all test-created issues / MRs so the session-end fixture
# (and operators) can locate leftovers if cleanup fails.
_E2E_LABEL = "kaji-e2e"


def _run_kaji(
    cwd: Path,
    *args: str,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the kaji CLI as a subprocess.

    ``cwd`` is the repository / workspace; ``env`` overrides specific vars
    (PATH is preserved by default).
    """
    final_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [*_KAJI_CMD, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=final_env,
    )


def _run_glab_api(
    *path_and_args: str,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    """Invoke ``glab api`` for read-only verification.

    Always uses ``--hostname gitlab.com`` to mirror ``GitLabProvider``'s
    contract (confirmed item #3: ``gitlab.com``-only, no self-hosted).
    """
    return subprocess.run(
        ["glab", "--hostname", _GLAB_HOSTNAME, "api", *path_and_args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _glab_auth_ok() -> bool:
    """Return True if ``glab auth status`` exits 0 (auth path b)."""
    if shutil.which("glab") is None:
        return False
    try:
        rc = subprocess.run(
            ["glab", "auth", "status", "--hostname", _GLAB_HOSTNAME],
            capture_output=True,
            text=True,
            timeout=10,
        ).returncode
    except (subprocess.TimeoutExpired, OSError):
        return False
    return rc == 0


# ============================================================
# Skip gates (session-scoped where reasonable)
# ============================================================


@pytest.fixture(scope="session")
def gitlab_auth_or_skip() -> None:
    """Gate on auth: GITLAB_TOKEN env OR ``glab auth status`` success.

    Mirrors gitlab-mode.md § 1.2 (a)/(b).
    """
    if shutil.which("glab") is None:
        pytest.skip("glab CLI not on PATH (required for large_gitlab)")
    if os.environ.get("GITLAB_TOKEN"):
        return
    if _glab_auth_ok():
        return
    pytest.skip(
        "GITLAB_TOKEN unset and `glab auth status` failed; "
        "run `glab auth login --hostname gitlab.com` or set GITLAB_TOKEN"
    )


@pytest.fixture(scope="session")
def gitlab_repo(gitlab_auth_or_skip: None) -> str:
    """Return ``KAJI_TEST_GITLAB_REPO`` or skip.

    The value must be ``<group>/<project>`` of a dedicated test fixture
    project (production projects must NOT be used — tests create / close
    real issues and merge requests).
    """
    repo = os.environ.get("KAJI_TEST_GITLAB_REPO")
    if not repo:
        pytest.skip(
            "KAJI_TEST_GITLAB_REPO unset; "
            "set to <group>/<project> of a dedicated test fixture GitLab project"
        )
    if "/" not in repo:
        pytest.skip(f"KAJI_TEST_GITLAB_REPO={repo!r} is not <group>/<project>; refusing to proceed")
    return repo


@pytest.fixture(scope="session")
def gitlab_default_branch() -> str:
    """Return the test project's default branch (env or ``main``)."""
    return os.environ.get("KAJI_TEST_GITLAB_DEFAULT_BRANCH", "main")


@pytest.fixture(scope="session")
def gitlab_repo_encoded(gitlab_repo: str) -> str:
    """URL-encoded form of ``gitlab_repo`` for ``glab api projects/<encoded>/...``."""
    return quote(gitlab_repo, safe="")


@pytest.fixture(scope="session")
def gitlab_self_username(gitlab_auth_or_skip: None) -> str:
    """Return the authenticated user's username via ``glab api user``.

    Used by review contract tests to assert that the current user is in
    ``approvals.approved_by`` after ``kaji pr review --approve``.
    """
    proc = _run_glab_api("user")
    if proc.returncode != 0:
        pytest.skip(f"`glab api user` failed (rc={proc.returncode}): {proc.stderr.strip()}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.skip(f"`glab api user` returned non-JSON: {exc}")
    username = data.get("username")
    if not isinstance(username, str) or not username:
        pytest.skip("`glab api user` did not return a username field")
    return username


# ============================================================
# Per-test working state
# ============================================================


@pytest.fixture
def unique_suffix() -> str:
    """Per-test unique identifier (8 hex chars). Use as title / branch suffix
    so parallel ``-n auto`` runs do not collide on the test project."""
    return f"kaji-e2e-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def kaji_workspace(
    tmp_path: Path,
    gitlab_repo: str,
    gitlab_default_branch: str,
) -> Path:
    """tmp workspace with ``.kaji/config.toml`` configured for ``provider=gitlab``.

    The workspace is the cwd for ``kaji issue`` / ``kaji pr`` invocations
    in the test. ``provider.gitlab.repo`` is taken from ``KAJI_TEST_GITLAB_REPO``.
    """
    repo = tmp_path / "workspace"
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
            type = "gitlab"

            [provider.gitlab]
            repo = "{gitlab_repo}"
            default_branch = "{gitlab_default_branch}"
            """
        )
    )
    (repo / ".gitignore").write_text(".kaji-artifacts/\n")
    return repo


# ============================================================
# Cleanup tracking
# ============================================================


class _CreatedResources:
    """Per-test bookkeeping for issues / MRs / branches to best-effort clean up.

    Tests register IDs as they create things; teardown attempts close /
    delete and swallows errors (tests have already asserted by then).
    """

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.issue_iids: list[int] = []
        self.mr_iids: list[int] = []
        self.branches: list[str] = []

    def add_issue(self, iid: int) -> None:
        self.issue_iids.append(iid)

    def add_mr(self, iid: int) -> None:
        self.mr_iids.append(iid)

    def add_branch(self, name: str) -> None:
        self.branches.append(name)


@pytest.fixture
def created_resources(gitlab_repo: str) -> Iterator[_CreatedResources]:
    """Track resources created during a test and best-effort clean them up."""
    bag = _CreatedResources(gitlab_repo)
    yield bag
    # Teardown: best-effort. Errors are swallowed (warning) so they do not
    # mask test failures.
    for iid in bag.mr_iids:
        try:
            subprocess.run(
                [
                    "glab",
                    "--hostname",
                    _GLAB_HOSTNAME,
                    "mr",
                    "close",
                    str(iid),
                    "--repo",
                    bag.repo,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            warnings.warn(f"cleanup: glab mr close {iid} failed: {exc}", stacklevel=2)
    for iid in bag.issue_iids:
        try:
            subprocess.run(
                [
                    "glab",
                    "--hostname",
                    _GLAB_HOSTNAME,
                    "issue",
                    "close",
                    str(iid),
                    "--repo",
                    bag.repo,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            warnings.warn(f"cleanup: glab issue close {iid} failed: {exc}", stacklevel=2)
    for branch in bag.branches:
        try:
            subprocess.run(
                [
                    "glab",
                    "--hostname",
                    _GLAB_HOSTNAME,
                    "api",
                    "-X",
                    "DELETE",
                    f"projects/{quote(bag.repo, safe='')}/repository/branches/{quote(branch, safe='')}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            warnings.warn(f"cleanup: delete branch {branch} failed: {exc}", stacklevel=2)


# ============================================================
# Public helper exports (tests import these from conftest via fixtures)
# ============================================================


@pytest.fixture
def run_kaji():  # type: ignore[no-untyped-def]
    """Expose ``_run_kaji`` to test modules as a fixture."""
    return _run_kaji


@pytest.fixture
def run_glab_api():  # type: ignore[no-untyped-def]
    """Expose ``_run_glab_api`` to test modules as a fixture."""
    return _run_glab_api


@pytest.fixture
def e2e_label() -> str:
    """Common label applied to test-created issues / MRs."""
    return _E2E_LABEL
