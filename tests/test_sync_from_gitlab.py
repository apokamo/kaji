"""Tests for ``kaji sync from-gitlab`` / ``kaji sync status`` (issue ``local-pc5090-8``).

``glab`` CLI を実呼びせず ``subprocess.run`` を mock してロジック検証のみ行う。
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
    GitLabProviderConfig,
    KajiConfig,
    LocalProviderConfig,
    PathsConfig,
    ProviderConfig,
)
from kaji_harness.sync import (
    SyncError,
    SyncResult,
    _fetch_open_issues_paginated,
    _list_existing_cached_iids,
    _mark_cache_stale,
    _resolve_repo,
    _write_fresh_cache_file,
    format_elapsed_human,
    read_sync_status,
    sync_from_gitlab,
)


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _fail(stdout: str = "", stderr: str = "boom") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout=stdout, stderr=stderr)


def _make_config(
    tmp_path: Path, *, repo: str = "group/project", machine_id: str = "pc1"
) -> KajiConfig:
    """``provider.type='local'`` 配下で ``[provider.gitlab].repo`` を持つ config 雛形。"""
    return KajiConfig(
        repo_root=tmp_path,
        paths=PathsConfig(artifacts_dir=".kaji-artifacts", skill_dir=".claude/skills"),
        execution=ExecutionConfig(default_timeout=1800),
        provider=ProviderConfig(
            type="local",
            local=LocalProviderConfig(machine_id=machine_id),
            github=GitHubProviderConfig(),
            gitlab=GitLabProviderConfig(repo=repo),
        ),
    )


def _glab_present() -> object:
    return patch("kaji_harness.sync.shutil.which", return_value="/usr/bin/glab")


# ---------------------------------------------------------------------------
# Small: pure logic
# ---------------------------------------------------------------------------


@pytest.mark.small
class TestResolveRepo:
    def test_override_takes_precedence(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, repo="from/config")
        assert _resolve_repo(cfg, "from/cli") == "from/cli"

    def test_falls_back_to_config(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, repo="from/config")
        assert _resolve_repo(cfg, None) == "from/config"

    def test_raises_when_both_missing(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, repo="")
        with pytest.raises(SyncError, match="requires a GitLab repo"):
            _resolve_repo(cfg, None)


@pytest.mark.small
class TestHostnameEnvPinning:
    """Issue ``local-p1-23`` 回帰防止: ``sync._glab_api_get`` も env 経由で hostname pin する。

    ``--hostname`` を引数注入していた旧実装は ``glab api`` 経路に限り偶発的に動作していたが、
    一貫性のため env 経路に揃える。``--hostname`` が cmd に **含まれない**、``GITLAB_HOST``
    が ``env`` kwarg に **含まれる**、の 2 点を assert する。
    """

    def test_glab_api_get_uses_gitlab_host_env(self) -> None:
        captured: list[tuple[list[str], dict[str, str] | None]] = []

        def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
            env_kw = kw.get("env")
            captured.append((cmd, env_kw if isinstance(env_kw, dict) else None))
            return _ok(stdout="[]")

        with _glab_present(), patch("kaji_harness.sync.subprocess.run", side_effect=fake_run):
            sync_mod._glab_api_get("projects/g%2Fp/issues")
        cmd, env = captured[0]
        assert "--hostname" not in cmd
        assert cmd[:2] == ["glab", "api"]
        assert env is not None
        assert env.get("GITLAB_HOST") == "gitlab.com"


@pytest.mark.small
class TestPagination:
    def _patch_payloads(self, payloads: list[object]) -> object:
        outputs = iter(payloads)

        def fake_run(*_a: object, **_kw: object) -> subprocess.CompletedProcess[str]:
            return _ok(stdout=json.dumps(next(outputs)))

        return patch("kaji_harness.sync.subprocess.run", side_effect=fake_run)

    def test_single_page_short(self) -> None:
        payload = [{"iid": i} for i in range(1, 51)]
        with _glab_present(), self._patch_payloads([payload]):
            issues, sizes = _fetch_open_issues_paginated("g/p")
        assert len(issues) == 50
        assert sizes == [50]

    def test_two_pages_then_empty(self) -> None:
        page1 = [{"iid": i} for i in range(1, 101)]
        page2: list[dict[str, object]] = []
        with _glab_present(), self._patch_payloads([page1, page2]):
            issues, sizes = _fetch_open_issues_paginated("g/p")
        assert len(issues) == 100
        assert sizes == [100]

    def test_two_pages_short_second(self) -> None:
        page1 = [{"iid": i} for i in range(1, 101)]
        page2 = [{"iid": i} for i in range(101, 131)]
        with _glab_present(), self._patch_payloads([page1, page2]):
            issues, sizes = _fetch_open_issues_paginated("g/p")
        assert len(issues) == 130
        assert sizes == [100, 30]

    def test_max_pages_aborts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # MAX_PAGES を小さくして検証 (3 page目もフルなので abort)
        monkeypatch.setattr(sync_mod, "_MAX_PAGES", 2)
        full_page = [{"iid": i} for i in range(1, 101)]
        outputs = iter([full_page, full_page, full_page])

        def fake_run(*_a: object, **_kw: object) -> subprocess.CompletedProcess[str]:
            return _ok(stdout=json.dumps(next(outputs)))

        with _glab_present(), patch("kaji_harness.sync.subprocess.run", side_effect=fake_run):
            with pytest.raises(SyncError, match="aborted after"):
                _fetch_open_issues_paginated("g/p")

    def test_exactly_max_pages_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """境界条件: ちょうど MAX_PAGES × per_page 件は成功する (regression)。

        以前は ``if page > _MAX_PAGES`` で false positive abort していた。
        """
        monkeypatch.setattr(sync_mod, "_MAX_PAGES", 3)
        full_page = [{"iid": i} for i in range(1, 101)]
        # 3 page 連続フル → 4 page目空配列 (= 終端確認)
        outputs = iter([full_page, full_page, full_page, []])

        def fake_run(*_a: object, **_kw: object) -> subprocess.CompletedProcess[str]:
            return _ok(stdout=json.dumps(next(outputs)))

        with _glab_present(), patch("kaji_harness.sync.subprocess.run", side_effect=fake_run):
            issues, sizes = _fetch_open_issues_paginated("g/p")
        assert len(issues) == 300
        assert sizes == [100, 100, 100]

    def test_glab_failure_propagates(self) -> None:
        with (
            _glab_present(),
            patch("kaji_harness.sync.subprocess.run", return_value=_fail(stderr="api error")),
        ):
            with pytest.raises(SyncError, match="glab api failed"):
                _fetch_open_issues_paginated("g/p")

    def test_missing_glab_raises(self) -> None:
        with patch("kaji_harness.sync.shutil.which", return_value=None):
            with pytest.raises(SyncError, match="not found in PATH"):
                _fetch_open_issues_paginated("g/p")


@pytest.mark.small
class TestWriteFreshCacheFile:
    def test_writes_wrapper_with_kaji_local(self, tmp_path: Path) -> None:
        _write_fresh_cache_file(
            {"iid": 42, "title": "T", "state": "opened"},
            tmp_path,
            "2026-05-10T08:42:11Z",
        )
        path = tmp_path / "gl-42.json"
        assert path.is_file()
        payload = json.loads(path.read_text())
        assert payload["schema_version"] == 1
        assert payload["forge"] == "gitlab"
        assert payload["fetched_at"] == "2026-05-10T08:42:11Z"
        assert payload["kaji_local"] == {
            "is_stale": False,
            "last_seen_at": "2026-05-10T08:42:11Z",
            "staled_at": None,
        }
        assert payload["issue"]["iid"] == 42

    def test_overwrites_existing_wrapper(self, tmp_path: Path) -> None:
        # 古い stale wrapper を置いてから fresh write すると完全に置換される
        path = tmp_path / "gl-7.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "forge": "gitlab",
                    "fetched_at": "2026-05-01T00:00:00Z",
                    "kaji_local": {
                        "is_stale": True,
                        "last_seen_at": "2026-05-01T00:00:00Z",
                        "staled_at": "2026-05-05T00:00:00Z",
                    },
                    "issue": {"iid": 7, "state": "opened"},
                }
            )
        )
        _write_fresh_cache_file(
            {"iid": 7, "title": "back", "state": "opened"},
            tmp_path,
            "2026-05-10T08:42:11Z",
        )
        payload = json.loads(path.read_text())
        assert payload["kaji_local"]["is_stale"] is False
        assert payload["kaji_local"]["staled_at"] is None
        assert payload["kaji_local"]["last_seen_at"] == "2026-05-10T08:42:11Z"
        assert payload["issue"]["title"] == "back"

    def test_missing_iid_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SyncError, match="missing 'iid'"):
            _write_fresh_cache_file({"title": "x"}, tmp_path, "2026-05-10T08:42:11Z")


@pytest.mark.small
class TestMarkCacheStale:
    def _make_fresh(self, tmp_path: Path, iid: int) -> Path:
        path = tmp_path / f"gl-{iid}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "forge": "gitlab",
                    "fetched_at": "2026-05-01T00:00:00Z",
                    "kaji_local": {
                        "is_stale": False,
                        "last_seen_at": "2026-05-01T00:00:00Z",
                        "staled_at": None,
                    },
                    "issue": {
                        "iid": iid,
                        "state": "opened",
                        "title": "still open",
                    },
                }
            )
        )
        return path

    def test_marks_fresh_to_stale(self, tmp_path: Path) -> None:
        path = self._make_fresh(tmp_path, 9)
        _mark_cache_stale(path, "2026-05-10T08:42:11Z")
        payload = json.loads(path.read_text())
        assert payload["kaji_local"]["is_stale"] is True
        assert payload["kaji_local"]["staled_at"] == "2026-05-10T08:42:11Z"
        # last_seen_at は前回 fresh 時刻のまま保持
        assert payload["kaji_local"]["last_seen_at"] == "2026-05-01T00:00:00Z"
        # issue 本体は不変
        assert payload["issue"]["state"] == "opened"
        assert payload["issue"]["title"] == "still open"

    def test_does_not_rewrite_if_already_stale(self, tmp_path: Path) -> None:
        path = tmp_path / "gl-9.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "forge": "gitlab",
                    "fetched_at": "2026-04-01T00:00:00Z",
                    "kaji_local": {
                        "is_stale": True,
                        "last_seen_at": "2026-04-01T00:00:00Z",
                        "staled_at": "2026-04-15T00:00:00Z",
                    },
                    "issue": {"iid": 9, "state": "opened"},
                }
            )
        )
        before = path.read_text()
        _mark_cache_stale(path, "2026-05-10T08:42:11Z")
        after = path.read_text()
        assert before == after  # 1 byte も変わらない

    def test_silently_skips_malformed(self, tmp_path: Path) -> None:
        path = tmp_path / "gl-11.json"
        path.write_text("not json")
        before = path.read_text()
        _mark_cache_stale(path, "2026-05-10T08:42:11Z")
        assert path.read_text() == before


@pytest.mark.small
class TestListExistingCachedIids:
    def test_empty_dir(self, tmp_path: Path) -> None:
        assert _list_existing_cached_iids(tmp_path) == set()

    def test_collects_gl_files(self, tmp_path: Path) -> None:
        (tmp_path / "gl-1.json").write_text("{}")
        (tmp_path / "gl-42.json").write_text("{}")
        (tmp_path / ".sync-meta.json").write_text("{}")
        (tmp_path / "issues").mkdir()
        assert _list_existing_cached_iids(tmp_path) == {"1", "42"}


@pytest.mark.small
class TestFormatElapsedHuman:
    def test_zero(self) -> None:
        assert format_elapsed_human(0) == "0h 0m 0s"

    def test_basic(self) -> None:
        assert format_elapsed_human(4992) == "1h 23m 12s"

    def test_negative_clamped(self) -> None:
        assert format_elapsed_human(-5) == "0h 0m 0s"


@pytest.mark.small
class TestReadSyncStatusUnSynced:
    def test_returns_none_when_meta_absent(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        status = read_sync_status(config=cfg)
        assert status.forge is None
        assert status.repo is None
        assert status.last_sync_at is None
        assert status.elapsed_seconds is None
        assert status.issue_count == 0

    def test_counts_existing_cache(self, tmp_path: Path) -> None:
        cache = tmp_path / ".kaji" / "cache"
        cache.mkdir(parents=True)
        (cache / "gl-1.json").write_text("{}")
        (cache / "gl-2.json").write_text("{}")
        cfg = _make_config(tmp_path)
        status = read_sync_status(config=cfg)
        assert status.issue_count == 2


# ---------------------------------------------------------------------------
# Medium: subprocess + file I/O end-to-end
# ---------------------------------------------------------------------------


def _patch_glab_pages(pages: list[object]) -> object:
    """各 ``glab api`` 呼び出しに対し ``pages`` を順に消費する mock。"""
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


@pytest.mark.medium
class TestSyncFromGitLabRoundTrip:
    def test_writes_cache_and_meta(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        page = [
            {"iid": i, "title": f"t{i}", "description": f"b{i}", "state": "opened", "labels": []}
            for i in range(1, 51)
        ]
        with _glab_present(), _patch_glab_pages([page, []]):
            result = sync_from_gitlab(config=cfg, repo_override=None, quiet=True)
        assert isinstance(result, SyncResult)
        assert result.issue_count == 50
        assert result.pages_fetched == 1  # 50 < per_page で 1 page で終了
        cache = tmp_path / ".kaji" / "cache"
        assert (cache / "gl-1.json").is_file()
        assert (cache / "gl-50.json").is_file()
        meta = json.loads((cache / ".sync-meta.json").read_text())
        assert meta["forge"] == "gitlab"
        assert meta["repo"] == "group/project"
        assert meta["issue_count"] == 50
        assert meta["last_sync_at"] == result.last_sync_at
        # tmp ファイルが残っていない
        leftover = list(cache.glob("*.tmp"))
        assert leftover == []

    def test_three_pages(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        page1 = [{"iid": i, "state": "opened"} for i in range(1, 101)]
        page2 = [{"iid": i, "state": "opened"} for i in range(101, 201)]
        page3 = [{"iid": i, "state": "opened"} for i in range(201, 251)]
        with _glab_present(), _patch_glab_pages([page1, page2, page3]):
            result = sync_from_gitlab(config=cfg, repo_override=None, quiet=True)
        assert result.issue_count == 250
        assert result.pages_fetched == 3
        assert (tmp_path / ".kaji" / "cache" / "gl-250.json").is_file()


@pytest.mark.medium
class TestStaleTransitions:
    def _seed_fresh(self, tmp_path: Path, iid: int, *, state: str = "opened") -> Path:
        cache = tmp_path / ".kaji" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"gl-{iid}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "forge": "gitlab",
                    "fetched_at": "2026-04-01T00:00:00Z",
                    "kaji_local": {
                        "is_stale": False,
                        "last_seen_at": "2026-04-01T00:00:00Z",
                        "staled_at": None,
                    },
                    "issue": {
                        "iid": iid,
                        "state": state,
                        "title": "old title",
                        "description": "old body",
                        "labels": [],
                    },
                }
            )
        )
        return path

    def test_disappearing_entry_becomes_stale(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        seeded = self._seed_fresh(tmp_path, 99)
        # 99 が含まれない fetch 結果
        page = [{"iid": 100, "state": "opened"}]
        with _glab_present(), _patch_glab_pages([page, []]):
            sync_from_gitlab(config=cfg, repo_override=None, quiet=True)
        # 99 は delete されない
        assert seeded.is_file()
        payload = json.loads(seeded.read_text())
        assert payload["kaji_local"]["is_stale"] is True
        assert payload["kaji_local"]["staled_at"] is not None
        # issue 本体は不変
        assert payload["issue"]["state"] == "opened"
        assert payload["issue"]["title"] == "old title"

    def test_already_stale_entry_not_rewritten(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        cache = tmp_path / ".kaji" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / "gl-50.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "forge": "gitlab",
                    "fetched_at": "2026-03-01T00:00:00Z",
                    "kaji_local": {
                        "is_stale": True,
                        "last_seen_at": "2026-03-01T00:00:00Z",
                        "staled_at": "2026-03-15T00:00:00Z",
                    },
                    "issue": {"iid": 50, "state": "opened"},
                }
            )
        )
        before_text = path.read_text()
        before_mtime = path.stat().st_mtime
        with _glab_present(), _patch_glab_pages([[], []]):
            sync_from_gitlab(config=cfg, repo_override=None, quiet=True)
        # 既に stale なので rewrite されない
        assert path.read_text() == before_text
        assert path.stat().st_mtime == before_mtime

    def test_revived_entry_returns_to_fresh(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        cache = tmp_path / ".kaji" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / "gl-7.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "forge": "gitlab",
                    "fetched_at": "2026-04-01T00:00:00Z",
                    "kaji_local": {
                        "is_stale": True,
                        "last_seen_at": "2026-04-01T00:00:00Z",
                        "staled_at": "2026-04-15T00:00:00Z",
                    },
                    "issue": {"iid": 7, "state": "opened", "title": "old"},
                }
            )
        )
        page = [{"iid": 7, "state": "opened", "title": "back"}]
        with _glab_present(), _patch_glab_pages([page, []]):
            sync_from_gitlab(config=cfg, repo_override=None, quiet=True)
        payload = json.loads(path.read_text())
        assert payload["kaji_local"]["is_stale"] is False
        assert payload["kaji_local"]["staled_at"] is None
        assert payload["issue"]["title"] == "back"


@pytest.mark.medium
class TestAllOrNothing:
    """phase 1 (fetch) 失敗時に cache が一切触られないことを検証 (MF-1)。"""

    def test_first_page_failure_does_not_touch_cache(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        cache = tmp_path / ".kaji" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        existing = cache / "gl-1.json"
        existing.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "forge": "gitlab",
                    "fetched_at": "2026-04-01T00:00:00Z",
                    "kaji_local": {
                        "is_stale": False,
                        "last_seen_at": "2026-04-01T00:00:00Z",
                        "staled_at": None,
                    },
                    "issue": {"iid": 1, "state": "opened"},
                }
            )
        )
        before_text = existing.read_text()
        before_mtime = existing.stat().st_mtime
        with (
            _glab_present(),
            patch("kaji_harness.sync.subprocess.run", return_value=_fail(stderr="boom")),
        ):
            with pytest.raises(SyncError):
                sync_from_gitlab(config=cfg, repo_override=None, quiet=True)
        # 既存 cache は 1 byte も変わらず、meta も書かれない
        assert existing.read_text() == before_text
        assert existing.stat().st_mtime == before_mtime
        assert not (cache / ".sync-meta.json").exists()

    def test_second_page_failure_does_not_write_first_page(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        page1 = [{"iid": i, "state": "opened"} for i in range(1, 101)]
        outputs: list[object] = [_ok(stdout=json.dumps(page1)), _fail(stderr="page2 down")]
        idx = iter(outputs)

        def fake_run(*_a: object, **_kw: object) -> subprocess.CompletedProcess[str]:
            return next(idx)  # type: ignore[return-value]

        with _glab_present(), patch("kaji_harness.sync.subprocess.run", side_effect=fake_run):
            with pytest.raises(SyncError):
                sync_from_gitlab(config=cfg, repo_override=None, quiet=True)
        cache = tmp_path / ".kaji" / "cache"
        # 1 ページ目分も書かれない (all-or-nothing)
        assert not (cache / "gl-1.json").exists()
        assert not (cache / ".sync-meta.json").exists()


@pytest.mark.medium
class TestReadSyncStatusAfterSync:
    def test_meta_round_trip(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path)
        page = [{"iid": i, "state": "opened"} for i in range(1, 4)]
        with _glab_present(), _patch_glab_pages([page, []]):
            sync_from_gitlab(config=cfg, repo_override=None, quiet=True)
        status = read_sync_status(config=cfg)
        assert status.forge == "gitlab"
        assert status.repo == "group/project"
        assert status.last_sync_at is not None
        assert status.issue_count == 3
        assert status.elapsed_seconds is not None
        assert status.elapsed_seconds >= 0


# ---------------------------------------------------------------------------
# Medium: CLI integration via main()
# ---------------------------------------------------------------------------


def _bootstrap_local_repo(tmp_path: Path, *, repo: str = "group/project") -> Path:
    """``provider.type='local'`` の最小 config を持つ repo_root を作る。"""
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
        "[provider.gitlab]\n"
        f'repo = "{repo}"\n'
    )
    (kaji_dir / "issues").mkdir()
    return tmp_path


@pytest.mark.medium
class TestCliSyncFromGitLab:
    def test_fail_fast_include_closed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        from kaji_harness.cli_main import main

        rc = main(["sync", "from-gitlab", "--include-closed"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "--include-closed" in err

    def test_fail_fast_state(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        from kaji_harness.cli_main import main

        rc = main(["sync", "from-gitlab", "--state", "all"])
        assert rc == 2
        assert "--state" in capsys.readouterr().err

    def test_fail_fast_since(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        from kaji_harness.cli_main import main

        rc = main(["sync", "from-gitlab", "--since", "2026-01-01"])
        assert rc == 2
        assert "--since" in capsys.readouterr().err

    def test_summary_line_emitted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        page = [{"iid": 1, "state": "opened", "title": "t"}]
        from kaji_harness.cli_main import main

        with _glab_present(), _patch_glab_pages([page, []]):
            rc = main(["sync", "from-gitlab", "--quiet"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Sync completed at" in out
        assert "1 issues" in out

    def test_wrote_breakdown_matches_design(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """設計書 § インターフェース 1 で定義された出力契約を検証する。

        ``Wrote N issues to .kaji/cache/ (X newly added, Y updated, Z unchanged signature).``
        の breakdown を出す。ここでは初回 sync (= 全件 newly added) と再 sync
        (= 全件 unchanged signature + 1 件 updated) で内訳を確認する。
        """
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        from kaji_harness.cli_main import main

        page1 = [
            {"iid": 1, "state": "opened", "title": "a", "description": "x"},
            {"iid": 2, "state": "opened", "title": "b", "description": "y"},
            {"iid": 3, "state": "opened", "title": "c", "description": "z"},
        ]
        with _glab_present(), _patch_glab_pages([page1, []]):
            assert main(["sync", "from-gitlab"]) == 0
        out1 = capsys.readouterr().out
        assert "Wrote 3 issues to .kaji/cache/" in out1
        assert "(3 newly added, 0 updated, 0 unchanged signature)" in out1

        # 再 sync: iid=1 のみ title 変更 / 残り unchanged
        page2 = [
            {"iid": 1, "state": "opened", "title": "a-renamed", "description": "x"},
            {"iid": 2, "state": "opened", "title": "b", "description": "y"},
            {"iid": 3, "state": "opened", "title": "c", "description": "z"},
        ]
        with _glab_present(), _patch_glab_pages([page2, []]):
            assert main(["sync", "from-gitlab"]) == 0
        out2 = capsys.readouterr().out
        assert "Wrote 3 issues to .kaji/cache/" in out2
        assert "(0 newly added, 1 updated, 2 unchanged signature)" in out2


@pytest.mark.medium
class TestCliSyncStatus:
    def test_unsynced_table(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        from kaji_harness.cli_main import main

        rc = main(["sync", "status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "(none)" in out
        assert "(never)" in out
        assert "n/a" in out
        assert "cached       0" in out

    def test_unsynced_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        from kaji_harness.cli_main import main

        rc = main(["sync", "status", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["forge"] is None
        assert payload["repo"] is None
        assert payload["last_sync_at"] is None
        assert payload["elapsed_seconds"] is None
        assert payload["issue_count"] == 0

    def test_after_sync_table(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        page = [{"iid": 1, "state": "opened", "title": "t"}]
        from kaji_harness.cli_main import main

        with _glab_present(), _patch_glab_pages([page, []]):
            assert main(["sync", "from-gitlab", "--quiet"]) == 0
        capsys.readouterr()  # clear

        rc = main(["sync", "status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "forge        gitlab" in out
        assert "repo         group/project" in out
        assert "cached       1" in out


# ---------------------------------------------------------------------------
# Medium: kaji issue list integration
# ---------------------------------------------------------------------------


def _seed_cache_entry(
    repo_root: Path,
    iid: int,
    *,
    is_stale: bool,
    title: str = "t",
    issue_state: str = "opened",
    labels: list[str] | None = None,
) -> None:
    cache = repo_root / ".kaji" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"gl-{iid}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "forge": "gitlab",
                "fetched_at": "2026-05-10T00:00:00Z",
                "kaji_local": {
                    "is_stale": is_stale,
                    "last_seen_at": "2026-05-10T00:00:00Z",
                    "staled_at": "2026-05-10T00:00:00Z" if is_stale else None,
                },
                "issue": {
                    "iid": iid,
                    "state": issue_state,
                    "title": title,
                    "description": "body",
                    "labels": labels or [],
                },
            }
        )
    )


@pytest.mark.medium
class TestIssueListIntegration:
    def test_empty_cache_unchanged_behavior(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        from kaji_harness.cli_main import main

        rc = main(["issue", "list"])
        assert rc == 0
        # cache 不在 → 出力なし（既存挙動と同じ）
        assert capsys.readouterr().out == ""

    def test_default_open_lists_fresh_only(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        for iid in (1, 2, 3):
            _seed_cache_entry(repo_root, iid, is_stale=False)
        for iid in (10, 11):
            _seed_cache_entry(repo_root, iid, is_stale=True)
        from kaji_harness.cli_main import main

        rc = main(["issue", "list"])
        assert rc == 0
        out = capsys.readouterr().out
        ids = {line.split("\t")[0] for line in out.strip().splitlines()}
        assert ids == {"gl:1", "gl:2", "gl:3"}
        for line in out.strip().splitlines():
            parts = line.split("\t")
            assert parts[1] == "open"

    def test_state_closed_lists_stale(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        for iid in (1, 2, 3):
            _seed_cache_entry(repo_root, iid, is_stale=False)
        for iid in (10, 11):
            _seed_cache_entry(repo_root, iid, is_stale=True)
        from kaji_harness.cli_main import main

        rc = main(["issue", "list", "--state", "closed"])
        assert rc == 0
        out = capsys.readouterr().out
        ids = {line.split("\t")[0] for line in out.strip().splitlines()}
        assert ids == {"gl:10", "gl:11"}

    def test_state_all_lists_everything(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        for iid in (1, 2, 3):
            _seed_cache_entry(repo_root, iid, is_stale=False)
        for iid in (10, 11):
            _seed_cache_entry(repo_root, iid, is_stale=True)
        from kaji_harness.cli_main import main

        rc = main(["issue", "list", "--state", "all"])
        assert rc == 0
        out = capsys.readouterr().out
        ids = {line.split("\t")[0] for line in out.strip().splitlines()}
        assert ids == {"gl:1", "gl:2", "gl:3", "gl:10", "gl:11"}

    def test_label_filter_applies_to_cache(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        _seed_cache_entry(repo_root, 1, is_stale=False, labels=["type:feature"])
        _seed_cache_entry(repo_root, 2, is_stale=False, labels=["type:bug"])
        from kaji_harness.cli_main import main

        rc = main(["issue", "list", "--label", "type:feature"])
        assert rc == 0
        out = capsys.readouterr().out
        ids = [line.split("\t")[0] for line in out.strip().splitlines()]
        assert ids == ["gl:1"]


@pytest.mark.medium
class TestIssueViewIntegration:
    def test_view_gl_reads_cache(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        _seed_cache_entry(repo_root, 42, is_stale=False, title="Add foo bar")
        from kaji_harness.cli_main import main

        rc = main(["issue", "view", "gl:42"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Add foo bar" in out

    def test_view_gh_still_reads_legacy_cache(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # gh: 経路に regression がないか
        repo_root = _bootstrap_local_repo(tmp_path)
        monkeypatch.chdir(repo_root)
        gh_cache = repo_root / ".kaji" / "cache" / "issues"
        gh_cache.mkdir(parents=True, exist_ok=True)
        (gh_cache / "5.json").write_text(
            json.dumps({"number": 5, "title": "github cached", "body": "x", "state": "open"})
        )
        from kaji_harness.cli_main import main

        rc = main(["issue", "view", "gh:5"])
        assert rc == 0
        assert "github cached" in capsys.readouterr().out
