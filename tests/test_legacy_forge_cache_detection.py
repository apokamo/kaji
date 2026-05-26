"""Bridging tests: legacy forge cache detection (Issue #191 撤去後の fail-fast)。

設計書 § テスト戦略 § Medium § 新規 bridging test (MF-4 / MF-2 round 3) を実装する。
``_detect_legacy_forge_cache()`` が以下 3 ケースで ``SyncError`` を raise する
ことを確認する。

case (a) AND (b): meta + gl-*.json 両方残存
case (a) のみ: meta 単独残存
case (b) のみ: gl-*.json 単独残存（meta 不在）

加えて (b) ケースの ``view_cached_*`` / ``list_issues`` 経由でも同じ
``SyncError`` が raise されることを確認し、silent regression（base の
``providers/local.py:694-698,818-881`` で list 表示されていた entry が無言で
消える）を防いでいることを保証する。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from kaji_harness.config import (
    ExecutionConfig,
    GitHubProviderConfig,
    KajiConfig,
    LocalProviderConfig,
    PathsConfig,
    ProviderConfig,
)
from kaji_harness.providers.local import LocalProvider
from kaji_harness.sync import (
    SyncError,
    _detect_legacy_forge_cache,
    read_sync_status,
)


def _seed_legacy_meta(cache_dir: Path) -> None:
    """``.sync-meta.json`` に legacy ``forge='gitlab'`` を仕込む。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / ".sync-meta.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "forge": "gitlab",
                "repo": "group/project",
                "last_sync_at": "2026-05-01T12:00:00Z",
                "issue_count": 1,
                "pages_fetched": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _seed_legacy_gl_file(cache_dir: Path, iid: int = 42) -> None:
    """``gl-<iid>.json`` を仕込む（本体内容は最低限）。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"gl-{iid}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "forge": "gitlab",
                "fetched_at": "2026-05-01T12:00:00Z",
                "kaji_local": {
                    "is_stale": False,
                    "last_seen_at": "2026-05-01T12:00:00Z",
                    "staled_at": None,
                },
                "issue": {"iid": iid, "title": "old", "description": "", "state": "opened"},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _make_config(tmp_path: Path, *, machine_id: str = "pc1") -> KajiConfig:
    """``provider.type='local'`` config を構築する。"""
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig(
        repo_root=tmp_path,
        paths=PathsConfig(artifacts_dir=".kaji-artifacts", skill_dir=".claude/skills"),
        execution=ExecutionConfig(default_timeout=1800),
        provider=ProviderConfig(
            type="local",
            local=LocalProviderConfig(machine_id=machine_id),
            github=GitHubProviderConfig(repo="owner/name"),
        ),
    )


# ---------------------------------------------------------------------------
# Medium: case (a) AND (b)
# ---------------------------------------------------------------------------


@pytest.mark.medium
def test_meta_and_gl_files_both_present_raises(tmp_path: Path) -> None:
    """meta + gl-*.json 両方残存で recovery 文に両 rm 行が出る。"""
    config = _make_config(tmp_path)
    cache_dir = tmp_path / ".kaji" / "cache"
    _seed_legacy_meta(cache_dir)
    _seed_legacy_gl_file(cache_dir, iid=42)
    with pytest.raises(SyncError) as exc_info:
        read_sync_status(config=config)
    message = str(exc_info.value)
    assert "legacy GitLab cache" in message
    assert "forge='gitlab'" in message
    assert "gl-*.json files: 1" in message
    # recovery 文に両 rm 行
    assert "rm -f " in message
    assert "gl-*.json" in message
    assert ".sync-meta.json" in message
    assert "kaji sync from-github" in message


# ---------------------------------------------------------------------------
# Medium: case (a) only
# ---------------------------------------------------------------------------


@pytest.mark.medium
def test_meta_only_raises(tmp_path: Path) -> None:
    """meta のみ残存（gl-*.json 無し）で recovery 文に meta 削除行が含まれる。"""
    config = _make_config(tmp_path)
    cache_dir = tmp_path / ".kaji" / "cache"
    _seed_legacy_meta(cache_dir)
    with pytest.raises(SyncError) as exc_info:
        read_sync_status(config=config)
    message = str(exc_info.value)
    assert "legacy GitLab cache" in message
    assert ".sync-meta.json" in message


# ---------------------------------------------------------------------------
# Medium: case (b) only — silent regression 防止
# ---------------------------------------------------------------------------


@pytest.mark.medium
def test_gl_files_only_raises_from_sync_status(tmp_path: Path) -> None:
    """gl-*.json のみ残存（meta 不在）で recovery 文に gl-*.json 削除行が含まれる。"""
    config = _make_config(tmp_path)
    cache_dir = tmp_path / ".kaji" / "cache"
    _seed_legacy_gl_file(cache_dir, iid=42)
    with pytest.raises(SyncError) as exc_info:
        read_sync_status(config=config)
    message = str(exc_info.value)
    assert "legacy GitLab cache" in message
    assert "gl-*.json" in message


@pytest.mark.medium
def test_gl_files_only_raises_from_view_cached_issue(tmp_path: Path) -> None:
    """gl-*.json 単独残存時、``view_cached_issue`` 経由でも SyncError raise。

    base の ``providers/local.py:694-698,818-881`` で list 表示されていた
    entry が無言で消える silent regression を防ぐ。
    """
    _ = _make_config(tmp_path)
    cache_dir = tmp_path / ".kaji" / "cache"
    _seed_legacy_gl_file(cache_dir, iid=42)
    provider = LocalProvider(repo_root=tmp_path, machine_id="pc1")
    with pytest.raises(SyncError) as exc_info:
        provider.view_cached_issue("99")
    assert "legacy GitLab cache" in str(exc_info.value)


@pytest.mark.medium
def test_gl_files_only_raises_from_list_issues(tmp_path: Path) -> None:
    """gl-*.json 単独残存時、``list_issues`` 経由でも SyncError raise。"""
    _ = _make_config(tmp_path)
    cache_dir = tmp_path / ".kaji" / "cache"
    _seed_legacy_gl_file(cache_dir, iid=42)
    provider = LocalProvider(repo_root=tmp_path, machine_id="pc1")
    with pytest.raises(SyncError) as exc_info:
        provider.list_issues(state="open")
    assert "legacy GitLab cache" in str(exc_info.value)


@pytest.mark.medium
def test_gl_files_only_raises_from_list_issues_with_limit(tmp_path: Path) -> None:
    """``list_issues(limit=...)`` 早期 return 経路でも guard を bypass しない。

    local issue が ``limit`` 件以上存在する場合、guard を local 列挙の後ろに
    置くと early return で legacy cache 検出が skip される。entry 列挙より
    前に guard を置く契約の regression test。
    """
    config = _make_config(tmp_path)
    # local issue を 1 件作成
    provider_pre = LocalProvider(repo_root=tmp_path, machine_id=config.provider.local.machine_id)
    provider_pre.create_issue(title="seed local issue", body="x", labels=[])
    # legacy gl-*.json を残置
    cache_dir = tmp_path / ".kaji" / "cache"
    _seed_legacy_gl_file(cache_dir, iid=42)
    provider = LocalProvider(repo_root=tmp_path, machine_id=config.provider.local.machine_id)
    with pytest.raises(SyncError) as exc_info:
        provider.list_issues(state="open", limit=1)
    assert "legacy GitLab cache" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Medium: legitimate cache passes through
# ---------------------------------------------------------------------------


@pytest.mark.medium
def test_github_only_cache_passes(tmp_path: Path) -> None:
    """legacy 検出条件が成立しなければ通過する（GitHub cache のみ）。"""
    cache_dir = tmp_path / ".kaji" / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / ".sync-meta.json").write_text(
        json.dumps({"schema_version": 1, "forge": "github", "repo": "owner/name"}) + "\n",
        encoding="utf-8",
    )
    (cache_dir / "gh-1.json").write_text(
        json.dumps({"schema_version": 1, "forge": "github", "issue": {"number": 1}}) + "\n",
        encoding="utf-8",
    )
    # raise しない
    _detect_legacy_forge_cache(cache_dir)


@pytest.mark.medium
def test_empty_cache_dir_passes(tmp_path: Path) -> None:
    """cache_dir 不在では通過する（OR 結合不成立）。"""
    _detect_legacy_forge_cache(tmp_path / "nonexistent")


@pytest.mark.medium
def test_empty_existing_cache_dir_passes(tmp_path: Path) -> None:
    """cache_dir 存在するが空なら通過する。"""
    cache_dir = tmp_path / ".kaji" / "cache"
    cache_dir.mkdir(parents=True)
    _detect_legacy_forge_cache(cache_dir)


# ---------------------------------------------------------------------------
# Medium: CLI dispatcher が SyncError を fail-fast exit に翻訳する
# ---------------------------------------------------------------------------


@pytest.mark.medium
def test_handle_issue_local_list_translates_sync_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``kaji issue list`` 経路の SyncError が EXIT_INVALID_INPUT に翻訳される。

    `_handle_issue_local` が `SyncError` を catch しない場合、CLI は uncaught
    traceback で落ちる（codex PR #194 review P1 指摘）。回帰防止のため、
    legacy gl-*.json 残置 → `_handle_issue(["list"])` が rc=2 + stderr に
    'legacy GitLab cache' を出すことを確認する。
    """
    from kaji_harness.cli_main import _handle_issue

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    (repo / ".kaji").mkdir()
    (repo / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
    )
    _seed_legacy_gl_file(repo / ".kaji" / "cache", iid=42)
    monkeypatch.chdir(repo)

    rc = _handle_issue(["list"])
    captured = capsys.readouterr()
    # EXIT_INVALID_INPUT (=2): sync コマンド系と同じ contract に揃える
    assert rc == 2
    assert "legacy GitLab cache" in captured.err
    # traceback ではなく "Error: " prefix の user-facing message
    assert "Traceback" not in captured.err


@pytest.mark.medium
def test_handle_issue_local_view_cached_translates_sync_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``kaji issue view gh:N`` 経路の SyncError も EXIT_INVALID_INPUT に翻訳。"""
    from kaji_harness.cli_main import _handle_issue

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    (repo / ".kaji").mkdir()
    (repo / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
    )
    _seed_legacy_gl_file(repo / ".kaji" / "cache", iid=42)
    monkeypatch.chdir(repo)

    rc = _handle_issue(["view", "gh:99"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "legacy GitLab cache" in captured.err
    assert "Traceback" not in captured.err
