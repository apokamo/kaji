"""Large-local E2E: ``kaji watch`` CLI wiring (Issue #543).

実 ``kaji`` CLI を subprocess で起動し、PATH 先頭の fake ``gh`` で外部依存なしに
parser → command → package の配線を通しで検証する。Herdr の外で動かす。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.runstate_helpers import write_config

pytestmark = [pytest.mark.large, pytest.mark.large_local]

_FAKE_GH = r"""
import json, sys
args = sys.argv[1:]
labels = ["state:planned", "state:running", "state:aborted", "state:done", "state:hold",
          "state:needs-maintainer"]
if args[:2] == ["label", "list"]:
    print(json.dumps([{"name": n, "description": "", "color": "ffffff"} for n in labels]))
    sys.exit(0)
if args[:1] == ["api"]:
    query = args[-1]
    issues = []
    if "state%3Arunning" in query:
        issues = [{"number": 101, "title": "t", "body": "", "state": "open",
                   "state_reason": None, "labels": [{"name": "state:running"}]}]
    print(json.dumps([issues]))
    sys.exit(0)
print("unexpected gh call: " + " ".join(args), file=sys.stderr)
sys.exit(1)
"""


@pytest.fixture
def repo(outside_project_tmp_path: Path) -> Path:
    write_config(outside_project_tmp_path)
    bin_dir = outside_project_tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n{_FAKE_GH}", encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return outside_project_tmp_path


def _kaji(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
    env["PATH"] = f"{repo / 'bin'}{os.pathsep}{env['PATH']}"
    root = Path(__file__).resolve().parents[1]
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root}{os.pathsep}{existing}" if existing else str(root)
    return subprocess.run(
        [sys.executable, "-m", "kaji_harness.cli_main", *args, "--workdir", str(repo)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _declare(repo: Path) -> None:
    path = repo / ".kaji" / "config.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n[watch]\n", encoding="utf-8")


def test_without_watch_exit_2(repo: Path) -> None:
    result = _kaji(repo, "watch", "once", "--dry-run")
    assert result.returncode == 2
    assert "[watch]" in result.stderr


def test_dry_run_and_status_json(repo: Path) -> None:
    _declare(repo)
    once = _kaji(repo, "watch", "once", "--dry-run")
    assert once.returncode == 0, once.stderr
    plan = json.loads(once.stdout)
    assert plan["schema_version"] == 1
    assert plan["herdr"] == "unavailable"
    assert [(i["issue"], i["target_label"]) for i in plan["issues"]] == [(101, "state:planned")]
    status = _kaji(repo, "watch", "status", "--json")
    assert status.returncode == 0, status.stderr
    document = json.loads(status.stdout)
    assert document["schema_version"] == 1
    assert document["issues"][0]["current_label"] == "state:running"
    assert not (repo / ".kaji-artifacts" / "_watch").exists()


def test_once_outside_herdr_exit_2(repo: Path) -> None:
    _declare(repo)
    result = _kaji(repo, "watch", "once")
    assert result.returncode == 2
    assert "Herdr" in result.stderr


def _pane_idle_event(repo: Path) -> Path:
    from kaji_harness.watch.models import WatchEventFile
    from kaji_harness.watch.store import WatchStore

    return WatchStore(repo / ".kaji-artifacts").write_event(
        WatchEventFile(
            key="101:pane_idle:261115000000:implement:1:0",
            kind="pane_idle",
            issue=101,
            detected_at="2026-11-15T03:00:00+00:00",
            run_id="261115000000",
            workflow="dev",
            step="implement",
            attempt=1,
            anchor="none",
        )
    )


def test_auto_recovery_limit_exit_5_and_recoveries(repo: Path) -> None:
    """Issue #545: 上限で exit 5、``recoveries --json`` / ``events --json`` の配線。"""
    _declare(repo)
    event = str(_pane_idle_event(repo))
    codes = []
    stderr = ""
    for _ in range(4):
        result = _kaji(
            repo,
            "watch",
            "record",
            "--event",
            event,
            "--outcome",
            "continue-sent",
            "--reason",
            "Selected model is at capacity",
        )
        codes.append(result.returncode)
        stderr = result.stderr
    assert codes == [0, 0, 0, 5]
    assert len([line for line in stderr.splitlines() if "limit" in line]) == 1
    dialog = _kaji(
        repo,
        "watch",
        "record",
        "--event",
        event,
        "--outcome",
        "dialog-answered",
        "--reason",
        "trust",
        "--path",
        "/etc",
    )
    assert dialog.returncode == 2
    listing = _kaji(repo, "watch", "recoveries", "--json")
    assert listing.returncode == 0, listing.stderr
    document = json.loads(listing.stdout)
    assert document["schema_version"] == 1
    assert [(r["count"], r["limit"]) for r in document["recoveries"]] == [(1, 3), (2, 3), (3, 3)]
    events = _kaji(repo, "watch", "events", "--json")
    assert events.returncode == 0, events.stderr
    assert json.loads(events.stdout)["schema_version"] == 2


def test_retry_outside_herdr_exit_2(repo: Path) -> None:
    _declare(repo)
    result = _kaji(repo, "watch", "retry", "--event", str(_pane_idle_event(repo)), "--reason", "x")
    assert result.returncode == 2
    assert "Herdr" in result.stderr
