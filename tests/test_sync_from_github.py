"""Tests for ``kaji sync from-github`` (issue ``gl:34``).

``gh`` CLI を実呼びせず ``subprocess.run`` を mock してロジック検証のみ行う。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kaji_harness import sync as sync_mod
from kaji_harness.config import (
    ExecutionConfig,
    GitHubProviderConfig,
    KajiConfig,
    LocalProviderConfig,
    PathsConfig,
    ProviderConfig,
)
from kaji_harness.sync import (
    SyncError,
    SyncResult,
    _fetch_open_issues_github_paginated,
    _list_existing_cached_numbers,
    _resolve_repo_github,
    _write_fresh_github_cache_file,
    sync_from_github,
)


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _fail(stdout: str = "", stderr: str = "boom") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout=stdout, stderr=stderr)


def _make_config(
    tmp_path: Path, *, repo: str = "owner/name", machine_id: str = "pc1"
) -> KajiConfig:
    """``provider.type='local'`` 配下で ``[provider.github].repo`` を持つ config 雛形。"""
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig(
        repo_root=tmp_path,
        paths=PathsConfig(artifacts_dir=".kaji-artifacts", skill_dir=".claude/skills"),
        execution=ExecutionConfig(default_timeout=1800),
        provider=ProviderConfig(
            type="local",
            local=LocalProviderConfig(machine_id=machine_id),
            github=GitHubProviderConfig(repo=repo),
        ),
    )


def _gh_present() -> object:
    return patch("kaji_harness.sync.shutil.which", return_value="/usr/bin/gh")


def _patch_gh_pages(pages: list[object]) -> object:
    outputs = iter(pages)

    def fake_run(*_a: object, **_kw: object) -> subprocess.CompletedProcess[str]:
        try:
            payload = next(outputs)
        except StopIteration:
            return _ok(stdout="[]")
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, subprocess.CompletedProcess):
            return payload
        return _ok(stdout=json.dumps(payload))

    return patch("kaji_harness.sync.subprocess.run", side_effect=fake_run)


# ---------------------------------------------------------------------------
# Small
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestResolveRepoGitHub:
    def test_override_takes_precedence(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, repo="from/config")
        assert _resolve_repo_github(cfg, "from/cli") == "from/cli"

    def test_falls_back_to_config(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, repo="from/config")
        assert _resolve_repo_github(cfg, None) == "from/config"

    def test_raises_when_both_missing(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, repo="")
        with pytest.raises(SyncError, match="requires a GitHub repo"):
            _resolve_repo_github(cfg, None)


@pytest.mark.small
class TestPaginationGitHub:
    def test_single_page_short(self) -> None:
        page = [{"number": i} for i in range(1, 51)]
        with _gh_present(), _patch_gh_pages([page]):
            issues, sizes = _fetch_open_issues_github_paginated("o/n")
        assert len(issues) == 50
        assert sizes == [50]

    def test_two_pages_then_empty(self) -> None:
        page1 = [{"number": i} for i in range(1, 101)]
        with _gh_present(), _patch_gh_pages([page1, []]):
            issues, sizes = _fetch_open_issues_github_paginated("o/n")
        assert len(issues) == 100
        assert sizes == [100]

    def test_excludes_pull_requests(self) -> None:
        """GitHub REST `/issues` endpoint は PR も返す → `pull_request` キー除外。"""
        page = [
            {"number": 1, "title": "issue-a"},
            {"number": 2, "title": "pr-b", "pull_request": {"url": "..."}},
            {"number": 3, "title": "issue-c"},
            {"number": 4, "title": "pr-d", "pull_request": {"url": "..."}},
            {"number": 5, "title": "issue-e"},
        ]
        with _gh_present(), _patch_gh_pages([page]):
            issues, sizes = _fetch_open_issues_github_paginated("o/n")
        numbers = [e["number"] for e in issues]
        assert numbers == [1, 3, 5]
        # page_sizes は除外前の生件数
        assert sizes == [5]

    def test_max_pages_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sync_mod, "_MAX_PAGES", 2)
        full = [{"number": i} for i in range(1, 101)]
        with _gh_present(), _patch_gh_pages([full, full, full]):
            with pytest.raises(SyncError, match="aborted after"):
                _fetch_open_issues_github_paginated("o/n")

    def test_gh_failure_propagates(self) -> None:
        with (
            _gh_present(),
            patch("kaji_harness.sync.subprocess.run", return_value=_fail(stderr="api boom")),
        ):
            with pytest.raises(SyncError, match="gh api failed"):
                _fetch_open_issues_github_paginated("o/n")

    def test_missing_gh_raises(self) -> None:
        with patch("kaji_harness.sync.shutil.which", return_value=None):
            with pytest.raises(SyncError, match="'gh' CLI not found"):
                _fetch_open_issues_github_paginated("o/n")

    def test_endpoint_uses_repo_and_state(self) -> None:
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            return _ok(stdout="[]")

        with _gh_present(), patch("kaji_harness.sync.subprocess.run", side_effect=fake_run):
            _fetch_open_issues_github_paginated("owner/repo")
        cmd = captured[0]
        assert cmd[:4] == ["gh", "api", "-X", "GET"]
        assert "repos/owner/repo/issues" in cmd
        assert "state=open" in cmd
        assert any(x.startswith("per_page=") for x in cmd)


@pytest.mark.small
class TestWriteFreshGithubCacheFile:
    def test_writes_wrapper_with_kaji_local(self, tmp_path: Path) -> None:
        _write_fresh_github_cache_file(
            {"number": 42, "title": "T", "state": "open"},
            tmp_path,
            "2026-05-21T08:42:11Z",
        )
        path = tmp_path / "gh-42.json"
        assert path.is_file()
        payload = json.loads(path.read_text())
        assert payload["schema_version"] == 1
        assert payload["forge"] == "github"
        assert payload["fetched_at"] == "2026-05-21T08:42:11Z"
        assert payload["kaji_local"] == {
            "is_stale": False,
            "last_seen_at": "2026-05-21T08:42:11Z",
            "staled_at": None,
        }
        assert payload["issue"]["number"] == 42

    def test_missing_number_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SyncError, match="missing 'number'"):
            _write_fresh_github_cache_file({"title": "x"}, tmp_path, "2026-05-21T00:00:00Z")


@pytest.mark.small
class TestListExistingCachedNumbersPrefix:
    def test_separates_gl_and_gh(self, tmp_path: Path) -> None:
        (tmp_path / "gl-1.json").write_text("{}")
        (tmp_path / "gl-2.json").write_text("{}")
        (tmp_path / "gh-3.json").write_text("{}")
        (tmp_path / "gh-4.json").write_text("{}")
        (tmp_path / ".sync-meta.json").write_text("{}")
        assert _list_existing_cached_numbers(tmp_path, prefix="gl-") == {"1", "2"}
        assert _list_existing_cached_numbers(tmp_path, prefix="gh-") == {"3", "4"}


# ---------------------------------------------------------------------------
# Medium
# ---------------------------------------------------------------------------


@pytest.mark.medium
class TestSyncFromGitHubRoundTrip:
    def test_writes_cache_and_meta(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        page = [
            {
                "number": i,
                "title": f"t{i}",
                "body": f"b{i}",
                "state": "open",
                "labels": [],
            }
            for i in range(1, 4)
        ]
        with _gh_present(), _patch_gh_pages([page, []]):
            result = sync_from_github(config=cfg, repo_override=None, quiet=True)
        assert isinstance(result, SyncResult)
        assert result.issue_count == 3
        assert result.pages_fetched == 1
        cache = tmp_path / ".kaji" / "cache"
        assert (cache / "gh-1.json").is_file()
        assert (cache / "gh-3.json").is_file()
        meta = json.loads((cache / ".sync-meta.json").read_text())
        assert meta["forge"] == "github"
        assert meta["repo"] == "owner/name"
        assert meta["issue_count"] == 3

    def test_excludes_prs_from_cache(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        page = [
            {"number": 1, "title": "i1", "state": "open"},
            {"number": 2, "title": "pr2", "state": "open", "pull_request": {"url": "..."}},
            {"number": 3, "title": "i3", "state": "open"},
        ]
        with _gh_present(), _patch_gh_pages([page, []]):
            result = sync_from_github(config=cfg, repo_override=None, quiet=True)
        assert result.issue_count == 2
        cache = tmp_path / ".kaji" / "cache"
        assert (cache / "gh-1.json").is_file()
        assert not (cache / "gh-2.json").exists()
        assert (cache / "gh-3.json").is_file()


@pytest.mark.medium
class TestStaleTransitionsGitHub:
    def _seed_fresh(self, tmp_path: Path, number: int) -> Path:
        cache = tmp_path / ".kaji" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"gh-{number}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "forge": "github",
                    "fetched_at": "2026-04-01T00:00:00Z",
                    "kaji_local": {
                        "is_stale": False,
                        "last_seen_at": "2026-04-01T00:00:00Z",
                        "staled_at": None,
                    },
                    "issue": {"number": number, "state": "open", "title": "old"},
                }
            )
        )
        return path

    def test_disappearing_entry_becomes_stale(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        seeded = self._seed_fresh(tmp_path, 99)
        page = [{"number": 100, "state": "open"}]
        with _gh_present(), _patch_gh_pages([page, []]):
            sync_from_github(config=cfg, repo_override=None, quiet=True)
        payload = json.loads(seeded.read_text())
        assert payload["kaji_local"]["is_stale"] is True
        assert payload["kaji_local"]["staled_at"] is not None


@pytest.mark.medium
class TestAllOrNothingGitHub:
    def test_first_page_failure_does_not_touch_cache(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        cache = tmp_path / ".kaji" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        existing = cache / "gh-1.json"
        existing.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "forge": "github",
                    "fetched_at": "2026-04-01T00:00:00Z",
                    "kaji_local": {
                        "is_stale": False,
                        "last_seen_at": "2026-04-01T00:00:00Z",
                        "staled_at": None,
                    },
                    "issue": {"number": 1, "state": "open"},
                }
            )
        )
        before_text = existing.read_text()
        with (
            _gh_present(),
            patch("kaji_harness.sync.subprocess.run", return_value=_fail(stderr="boom")),
        ):
            with pytest.raises(SyncError):
                sync_from_github(config=cfg, repo_override=None, quiet=True)
        assert existing.read_text() == before_text
        assert not (cache / ".sync-meta.json").exists()


# ---------------------------------------------------------------------------
# Medium: CLI integration
# ---------------------------------------------------------------------------


def _bootstrap_local_repo(tmp_path: Path, *, repo: str = "owner/name") -> Path:
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.toml").write_text(
        "[paths]\n"
        'artifacts_dir = ".kaji-artifacts"\n'
        'skill_dir = ".claude/skills"\n\n'
        "[execution]\n"
        "default_timeout = 1800\n\n"
        "[provider]\n"
        'type = "local"\n\n'
        "[provider.local]\n"
        'machine_id = "pc1"\n\n'
        "[provider.github]\n"
        f'repo = "{repo}"\n'
    )
    (kaji_dir / "issues").mkdir()
    return tmp_path


@pytest.mark.medium
class TestCliSyncFromGitHub:
    def test_fail_fast_include_closed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        from kaji_harness.cli_main import main

        rc = main(["sync", "from-github", "--include-closed"])
        assert rc == 2
        assert "--include-closed" in capsys.readouterr().err

    def test_fail_fast_state(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        from kaji_harness.cli_main import main

        rc = main(["sync", "from-github", "--state", "all"])
        assert rc == 2
        assert "--state" in capsys.readouterr().err

    def test_fail_fast_since(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        from kaji_harness.cli_main import main

        rc = main(["sync", "from-github", "--since", "2026-01-01"])
        assert rc == 2
        assert "--since" in capsys.readouterr().err

    def test_summary_line_emitted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        from kaji_harness.cli_main import main

        page = [{"number": 1, "state": "open", "title": "t"}]
        with _gh_present(), _patch_gh_pages([page, []]):
            rc = main(["sync", "from-github", "--quiet"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Sync completed at" in out
        assert "1 issues" in out

    def test_repo_override(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path, repo="from/config")
        monkeypatch.chdir(repo_root)
        captured: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
            captured.append(cmd)
            return _ok(stdout="[]")

        from kaji_harness.cli_main import main

        with _gh_present(), patch("kaji_harness.sync.subprocess.run", side_effect=fake_run):
            rc = main(["sync", "from-github", "--quiet", "--repo", "from/cli"])
        assert rc == 0
        # 起動された gh api endpoint に "from/cli" が含まれる
        assert any("repos/from/cli/issues" in c for cmd in captured for c in cmd)


@pytest.mark.medium
class TestSyncStatusGitHubRoundTrip:
    def test_after_github_sync(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        page = [{"number": 1, "state": "open", "title": "t"}]
        from kaji_harness.cli_main import main

        with _gh_present(), _patch_gh_pages([page, []]):
            assert main(["sync", "from-github", "--quiet"]) == 0
        capsys.readouterr()  # clear
        rc = main(["sync", "status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "forge        github" in out
        assert "repo         owner/name" in out
        assert "gh-*.json" in out


@pytest.mark.medium
class TestRunnerResolvePrContextGitHubError:
    """runner._resolve_pr_context_safe catches GitHubProviderError."""

    def test_warn_and_return_none(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from kaji_harness.providers.github import GitHubProviderError
        from kaji_harness.runner import WorkflowRunner

        class FakeProvider:
            def resolve_pr_context(self, branch: str) -> object:
                raise GitHubProviderError("multiple open pull requests found")

        # We don't need a full WorkflowRunner — just call the bound method on instance.
        runner = WorkflowRunner.__new__(WorkflowRunner)
        result = runner._resolve_pr_context_safe(FakeProvider(), "feat/153")  # type: ignore[arg-type]
        assert result is None
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "multiple open pull requests" in err
