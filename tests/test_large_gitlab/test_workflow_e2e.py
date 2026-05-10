"""``provider.type='gitlab'`` × workflow runner round-trip E2E.

Verifies the runner ↔ GitLabProvider boundary by running the fixture
workflow (``fixtures/feature-development-gitlab.yaml``,
``requires_provider: gitlab``) end-to-end against a real GitLab project.

Real LLM agents are NOT invoked: a fake ``claude`` script is placed on
PATH that emits Claude-compatible JSONL with a PASS verdict (same pattern
as ``tests/test_verdict_e2e.py``). The contract under test is "the runner
constructs a GitLab-typed IssueContext, passes the provider-match check,
and the step subprocess sees a well-formed prompt" — not "the design
skill produces a correct design".
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path
from urllib.parse import quote

import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_gitlab]


_FIXTURE_WORKFLOW = Path(__file__).parent / "fixtures" / "feature-development-gitlab.yaml"


def _setup_fake_claude_and_skill(workspace: Path) -> Path:
    """Provision a fake ``claude`` CLI in ``<workspace>/bin`` and a stub
    skill at ``.claude/skills/test-stub/SKILL.md``. Returns ``bin`` dir
    so the caller can prepend it to PATH.
    """
    init_event = json.dumps(
        {"type": "system", "subtype": "init", "session_id": "fake-large-gitlab-001"}
    )
    verdict_text = (
        "---VERDICT---\n"
        "status: PASS\n"
        "reason: |\n"
        "  Fake claude completed successfully.\n"
        "evidence: |\n"
        "  Stub run for test_large_gitlab E2E.\n"
        "suggestion: |\n"
        "---END_VERDICT---\n"
    )
    text_event = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": verdict_text}]},
        }
    )
    result_event = json.dumps({"type": "result", "result": "done", "total_cost_usd": 0.0})

    bin_dir = workspace / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import sys
            print({init_event!r})
            print({text_event!r})
            print({result_event!r})
            sys.exit(0)
            """
        )
    )
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)

    skill_dir = workspace / ".claude" / "skills" / "test-stub"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "# test-stub\n\nMinimal stub skill for test_large_gitlab workflow E2E.\n"
    )
    return bin_dir


def _create_test_issue(
    repo: str,
    title: str,
    body: str,
) -> int:
    """Create an issue via ``glab api`` and return its iid."""
    proc = subprocess.run(
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
            f"description={body}",
            "-f",
            "labels=kaji-e2e",
            f"projects/{quote(repo, safe='')}/issues",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"failed to create test issue (rc={proc.returncode}): stderr={proc.stderr.strip()}"
        )
    payload = json.loads(proc.stdout)
    iid = payload.get("iid")
    if not isinstance(iid, int):
        pytest.fail(f"glab api issue create returned non-int iid: {payload!r}")
    return iid


def test_workflow_runs_under_requires_provider_gitlab(
    kaji_workspace: Path,
    gitlab_repo: str,
    gitlab_repo_encoded: str,
    unique_suffix: str,
    created_resources,  # type: ignore[no-untyped-def]
    run_kaji,  # type: ignore[no-untyped-def]
) -> None:
    """``kaji run <fixture-workflow> <iid>`` returns 0 when provider=gitlab.

    Asserts:

    - The workflow's ``requires_provider: gitlab`` matches ``provider.type=
      'gitlab'`` (no exit-2 fail-fast from cli_main.py).
    - The runner constructs an IssueContext via ``GitLabProvider`` (so the
      preceding ``glab api`` round-trip on the real test issue must succeed
      end-to-end).
    - The fake claude completes the step (PASS verdict) and the runner
      terminates cleanly.
    - The original GitLab issue is unchanged in title / state by the
      stub run (sanity: no provider write happened).
    """
    title = f"workflow-e2e {unique_suffix}"
    body = "test_workflow_e2e fixture issue (auto-created)."
    iid = _create_test_issue(gitlab_repo, title, body)
    created_resources.add_issue(iid)

    bin_dir = _setup_fake_claude_and_skill(kaji_workspace)

    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        # Ensure the fake claude is what the adapter sees regardless of
        # whichever real claude may be installed on the developer machine.
    }
    result = run_kaji(
        kaji_workspace,
        "run",
        str(_FIXTURE_WORKFLOW),
        str(iid),
        "--workdir",
        str(kaji_workspace),
        timeout=180,
        env=env,
    )

    assert result.returncode == 0, (
        f"kaji run exit={result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # Sanity: the original issue still exists and has not been written to
    # by the stub step (no `kaji issue comment` was made).
    view_proc = subprocess.run(
        [
            "glab",
            "--hostname",
            "gitlab.com",
            "api",
            f"projects/{gitlab_repo_encoded}/issues/{iid}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert view_proc.returncode == 0, view_proc.stderr
    payload = json.loads(view_proc.stdout)
    assert payload["title"] == title
    assert payload["state"] == "opened"
