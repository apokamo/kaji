"""Phase 4 commit 4: ``cmd_run`` の workflow ↔ provider 整合検証。

Medium テスト:

- ``requires_provider != config.provider.type`` で exit 2 + stderr に切替手順
- ``requires_provider == "any"`` ではどの provider でも通る
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from kaji_harness.cli_main import main as cli_main

_BASE_CONFIG = """
[paths]
artifacts_dir = ".kaji/artifacts"
skill_dir = ".claude/skills"

[execution]
default_timeout = 600
""".lstrip()

_PROVIDER_GH = '[provider]\ntype = "github"\n[provider.github]\nrepo = "owner/name"\n'
_PROVIDER_LOCAL = '[provider]\ntype = "local"\n[provider.local]\nmachine_id = "pc1"\n'

# Minimal valid workflow content (skill `noop` doesn't need to exist for cmd_run
# entry-time provider match check; we exit before runner/skill validation).
_WORKFLOW_MIN = """
name: {name}
description: ""
execution_policy: auto
requires_provider: {provider}
steps:
  - id: only
    skill: noop
    agent: echo
    on:
      PASS: end
"""


def _setup(tmp_path: Path, *, provider: str, requires: str) -> Path:
    """Set up tmp repo with config + a single workflow YAML; returns YAML path."""
    (tmp_path / ".kaji").mkdir()
    body = _PROVIDER_GH if provider == "github" else _PROVIDER_LOCAL
    (tmp_path / ".kaji" / "config.toml").write_text(_BASE_CONFIG + body)
    wf_path = tmp_path / "wf.yaml"
    wf_path.write_text(_WORKFLOW_MIN.format(name="test-wf", provider=requires))
    return wf_path


def _run(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli_main(argv)
        except SystemExit as e:
            rc = int(e.code) if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


@pytest.mark.medium
def test_cmd_run_rejects_github_workflow_under_local_provider(tmp_path: Path) -> None:
    wf = _setup(tmp_path, provider="local", requires="github")
    rc, _, stderr = _run(["run", str(wf), "1", "--workdir", str(tmp_path)])
    assert rc == 2
    assert "requires provider.type='github'" in stderr
    assert "provider.type='local'" in stderr


@pytest.mark.medium
def test_cmd_run_rejects_local_workflow_under_github_provider(tmp_path: Path) -> None:
    wf = _setup(tmp_path, provider="github", requires="local")
    rc, _, stderr = _run(["run", str(wf), "1", "--workdir", str(tmp_path)])
    assert rc == 2
    assert "requires provider.type='local'" in stderr
    assert "provider.type='github'" in stderr


@pytest.mark.medium
def test_cmd_run_passes_match_github(tmp_path: Path) -> None:
    """provider == requires は突合 OK（その先の skill 検証で別エラーになるが、
    本 test は provider 整合段階の直後を見たいので runner エラーは無視する）。"""
    wf = _setup(tmp_path, provider="github", requires="github")
    rc, _, stderr = _run(["run", str(wf), "1", "--workdir", str(tmp_path)])
    # Provider match passes; runner will then fail because skill doesn't exist.
    # Either way, we should NOT see the provider mismatch message.
    assert "requires provider.type" not in stderr
    # Different non-zero exit codes are acceptable here (skill-not-found etc.)
    assert rc != 0  # later validation/runtime error, not provider mismatch
    # Provider mismatch returns 2; here we may also see 2 from skill validation.


@pytest.mark.medium
def test_cmd_run_any_passes_under_local(tmp_path: Path) -> None:
    wf = _setup(tmp_path, provider="local", requires="any")
    rc, _, stderr = _run(["run", str(wf), "1", "--workdir", str(tmp_path)])
    assert "requires provider.type" not in stderr
    # exits later in runner / skill validation
    assert rc != 0


@pytest.mark.medium
def test_cmd_run_any_passes_under_github(tmp_path: Path) -> None:
    wf = _setup(tmp_path, provider="github", requires="any")
    rc, _, stderr = _run(["run", str(wf), "1", "--workdir", str(tmp_path)])
    assert "requires provider.type" not in stderr
    assert rc != 0
