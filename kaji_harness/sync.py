"""GitLab → ローカル cache 同期 (`kaji sync from-gitlab` / `kaji sync status`)。

Issue ``local-p1-8``。``provider.type='local'`` 配下から ``gl:N`` で GitLab
Issue を参照できるようにするため、本 module は GitLab project の open Issue を
全件取得して ``.kaji/cache/gl-<iid>.json`` に atomic write する。

設計書: ``draft/design/issue-local-pc5090-8-kaji-sync-from-gitlab.md``。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from .providers.local import _atomic_write

if TYPE_CHECKING:
    from .config import KajiConfig


_GITLAB_HOSTNAME = "gitlab.com"
_PER_PAGE = 100
_MAX_PAGES = 200
_SYNC_META_FILENAME = ".sync-meta.json"
_CACHE_SCHEMA_VERSION = 1


class SyncError(RuntimeError):
    """``kaji sync`` 固有のエラー（config 不在 / glab CLI 不在 / API 失敗等）。"""


@dataclass(frozen=True)
class SyncResult:
    """``sync_from_gitlab()`` の結果サマリ。"""

    issue_count: int
    pages_fetched: int
    elapsed_seconds: float
    last_sync_at: str  # UTC ISO-8601


@dataclass(frozen=True)
class SyncStatus:
    """``read_sync_status()`` の結果サマリ。

    未 sync 状態（``.sync-meta.json`` 不在）は forge=None / repo=None /
    last_sync_at=None / elapsed_seconds=None / issue_count=0 で表現する。
    """

    forge: str | None
    repo: str | None
    last_sync_at: str | None
    elapsed_seconds: float | None
    issue_count: int


# ---------- internal helpers ----------


def _now_iso() -> str:
    """現在時刻を UTC ISO-8601（秒精度・``Z`` suffix）で返す。"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cache_dir_root(repo_root: Path) -> Path:
    return repo_root / ".kaji" / "cache"


def _sync_meta_path(repo_root: Path) -> Path:
    return _cache_dir_root(repo_root) / _SYNC_META_FILENAME


def _gitlab_cache_path(repo_root: Path, iid: str | int) -> Path:
    return _cache_dir_root(repo_root) / f"gl-{iid}.json"


def _resolve_repo(config: KajiConfig, override: str | None) -> str:
    """``--repo`` override > ``[provider.gitlab].repo`` の優先で repo を解決。

    どちらも空なら ``SyncError`` を投げる（CLI 層で exit 2 にマップ）。
    """
    if override:
        return override
    if (
        config.provider is not None
        and config.provider.gitlab is not None
        and config.provider.gitlab.repo
    ):
        return config.provider.gitlab.repo
    raise SyncError(
        "'kaji sync from-gitlab' requires a GitLab repo. Either:\n"
        '  - set [provider.gitlab].repo = "group/project" in .kaji/config.toml, or\n'
        "  - pass --repo group/project on the command line."
    )


def _glab_api_get(endpoint: str) -> object:
    """``glab api --hostname gitlab.com <endpoint>`` を起動し JSON を parse して返す。

    GitLabProvider._glab_api_get と機能等価だが、provider instance 非依存
    （``provider.type='local'`` 配下でも使える）。失敗は ``SyncError``。
    """
    if shutil.which("glab") is None:
        raise SyncError(
            "'glab' CLI not found in PATH. Install glab to use 'kaji sync from-gitlab'."
        )
    cmd = ["glab", "--hostname", _GITLAB_HOSTNAME, "api", endpoint]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise SyncError(f"failed to invoke 'glab': {exc}") from exc
    if proc.returncode != 0:
        raise SyncError(
            f"glab api failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SyncError(f"glab returned invalid JSON: {exc}") from exc


def _fetch_open_issues_paginated(
    repo: str,
) -> tuple[list[dict[str, object]], list[int]]:
    """GitLab REST ``GET /projects/:id/issues?state=opened`` を全 page 取得する。

    ``?per_page=100`` + ``?page=N`` (1-indexed) のオフセット pagination。
    空配列 / ``< per_page`` 件で終了。``MAX_PAGES`` を「accept する page 上限」として
    扱い、ちょうど ``MAX_PAGES`` page × ``per_page`` 件は成功扱い。``MAX_PAGES + 1``
    page目のデータを観測した場合のみ、暴走防止のため ``SyncError`` を投げる。

    全 page 完了するまで in-memory に貯める（**all-or-nothing 契約**: 任意 page の
    失敗は呼出側に伝搬し、cache file は一切触らない）。

    Returns:
        ``(全 issue list, 各 page の件数 list)``。後者は進捗表示に使う。
    """
    encoded = quote(repo, safe="")
    issues: list[dict[str, object]] = []
    page_sizes: list[int] = []
    page = 1
    while True:
        endpoint = f"projects/{encoded}/issues?state=opened&per_page={_PER_PAGE}&page={page}"
        payload = _glab_api_get(endpoint)
        if not isinstance(payload, list):
            raise SyncError(f"glab api returned non-array JSON for issue list (page {page})")
        if not payload:
            break
        if page > _MAX_PAGES:
            # MAX_PAGES + 1 page目に到達してもデータが残っている = 真の上限超過
            raise SyncError(
                f"sync aborted after {_MAX_PAGES} pages (>{_MAX_PAGES * _PER_PAGE} issues). "
                f"Check repo or contact maintainer."
            )
        for entry in payload:
            if not isinstance(entry, dict):
                raise SyncError(f"glab api returned non-object element on page {page}")
            issues.append(entry)
        page_sizes.append(len(payload))
        if len(payload) < _PER_PAGE:
            break
        page += 1
    return issues, page_sizes


def _list_existing_cached_iids(cache_dir: Path) -> set[str]:
    """``cache_dir/gl-*.json`` から iid 文字列の集合を返す。"""
    if not cache_dir.is_dir():
        return set()
    iids: set[str] = set()
    for path in cache_dir.glob("gl-*.json"):
        stem = path.stem  # "gl-42"
        if stem.startswith("gl-") and stem[3:].isdigit():
            iids.add(stem[3:])
    return iids


def _read_existing_issue_payload(path: Path) -> dict[str, object] | None:
    """既存 wrapper から ``issue`` field を取り出す。読めない / 形式異常は ``None``。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    issue_obj = payload.get("issue")
    return issue_obj if isinstance(issue_obj, dict) else None


def _write_fresh_cache_file(entry: dict[str, object], cache_dir: Path, now_iso: str) -> None:
    """fresh entry の wrapper を atomic write する（既存 wrapper を完全置換）。"""
    iid = entry.get("iid")
    if iid is None:
        raise SyncError(f"GitLab issue payload missing 'iid' field: {entry!r}")
    path = cache_dir / f"gl-{iid}.json"
    wrapped = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "forge": "gitlab",
        "fetched_at": now_iso,
        "kaji_local": {
            "is_stale": False,
            "last_seen_at": now_iso,
            "staled_at": None,
        },
        "issue": entry,
    }
    _atomic_write(path, json.dumps(wrapped, ensure_ascii=False, indent=2) + "\n")


def _mark_cache_stale(path: Path, now_iso: str) -> None:
    """既存 cache entry を stale 化する。issue 本体は触らない。

    既に ``is_stale=true`` の entry は再 write しない（``staled_at`` /
    ``last_seen_at`` を上書きしない invariant）。壊れた cache file は silently skip。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    kl_obj = payload.get("kaji_local")
    kl: dict[str, object] = dict(kl_obj) if isinstance(kl_obj, dict) else {}
    if kl.get("is_stale"):
        return
    kl["is_stale"] = True
    kl["staled_at"] = now_iso
    if "last_seen_at" not in kl:
        kl["last_seen_at"] = None
    payload["kaji_local"] = kl
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_sync_meta(
    *, repo: str, last_sync_at: str, issue_count: int, pages_fetched: int, path: Path
) -> None:
    meta = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "forge": "gitlab",
        "repo": repo,
        "last_sync_at": last_sync_at,
        "issue_count": issue_count,
        "pages_fetched": pages_fetched,
    }
    _atomic_write(path, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")


# ---------- public API ----------


def sync_from_gitlab(
    *,
    config: KajiConfig,
    repo_override: str | None,
    quiet: bool,
) -> SyncResult:
    """GitLab project から open Issue を全件 fetch して cache を populate する。

    3 phase の all-or-nothing 契約:

    - phase 1 (fetch): 全 page 完了まで in-memory。任意 page の失敗 → cache を
      一切触らずに ``SyncError``。
    - phase 2 (stale 判定): fetched_iids vs existing_iids の集合差分（IO なし）。
    - phase 3 (write): fresh entry を overwrite → stale entry を ``_mark_cache_stale``
      → 最後に ``.sync-meta.json`` を書く。

    Args:
        config: 解決済 ``KajiConfig``。
        repo_override: ``--repo`` flag。``[provider.gitlab].repo`` より優先。
        quiet: 進捗ログ抑制（最終サマリの 1 行は出る）。

    Raises:
        SyncError: glab CLI 不在 / GitLab API 失敗 / repo 未設定 / pagination
            上限到達 / payload 形式異常等。
        OSError: cache 書き込み失敗（atomic write の失敗パス）。
    """
    repo = _resolve_repo(config, repo_override)
    cache_dir = _cache_dir_root(config.repo_root)

    # phase 1: fetch
    import sys

    started = time.monotonic()
    if not quiet:
        sys.stdout.write(f"Fetching open issues from gitlab.com:{repo} ...\n")
    issues, page_sizes = _fetch_open_issues_paginated(repo)
    pages_fetched = len(page_sizes)
    if not quiet:
        for idx, count in enumerate(page_sizes, start=1):
            sys.stdout.write(f"  page {idx}: {count} issues\n")

    # phase 2: stale 判定
    fetched_iids: set[str] = set()
    for entry in issues:
        iid = entry.get("iid")
        if iid is None:
            raise SyncError(f"GitLab issue payload missing 'iid' field: {entry!r}")
        fetched_iids.add(str(iid))
    existing_iids = _list_existing_cached_iids(cache_dir)
    stale_iids = existing_iids - fetched_iids

    # phase 3: write
    cache_dir.mkdir(parents=True, exist_ok=True)
    now_iso = _now_iso()
    newly_added = 0
    updated = 0
    unchanged_signature = 0
    for entry in issues:
        iid = str(entry["iid"])
        path = cache_dir / f"gl-{iid}.json"
        if iid not in existing_iids:
            newly_added += 1
        else:
            previous = _read_existing_issue_payload(path)
            if previous == entry:
                unchanged_signature += 1
            else:
                updated += 1
        _write_fresh_cache_file(entry, cache_dir, now_iso)
    for iid in sorted(stale_iids):
        _mark_cache_stale(cache_dir / f"gl-{iid}.json", now_iso)
    _write_sync_meta(
        repo=repo,
        last_sync_at=now_iso,
        issue_count=len(issues),
        pages_fetched=pages_fetched,
        path=_sync_meta_path(config.repo_root),
    )
    elapsed = time.monotonic() - started

    if not quiet:
        sys.stdout.write(
            f"Wrote {len(issues)} issues to .kaji/cache/ "
            f"({newly_added} newly added, {updated} updated, "
            f"{unchanged_signature} unchanged signature).\n"
        )

    return SyncResult(
        issue_count=len(issues),
        pages_fetched=pages_fetched,
        elapsed_seconds=elapsed,
        last_sync_at=now_iso,
    )


def read_sync_status(*, config: KajiConfig) -> SyncStatus:
    """cache 状態を ``.sync-meta.json`` + ``gl-*.json`` の数から組み立てる。

    ``.sync-meta.json`` 不在時は ``forge=None / issue_count=0`` を返す
    （error にしない。未 sync は正常状態の 1 種）。
    """
    cache_dir = _cache_dir_root(config.repo_root)
    meta_path = _sync_meta_path(config.repo_root)
    issue_count = len(_list_existing_cached_iids(cache_dir))
    if not meta_path.is_file():
        return SyncStatus(
            forge=None,
            repo=None,
            last_sync_at=None,
            elapsed_seconds=None,
            issue_count=issue_count,
        )
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SyncError(f".sync-meta.json malformed: {exc}") from exc
    if not isinstance(payload, dict):
        raise SyncError(".sync-meta.json must be a JSON object")
    last_sync_at = payload.get("last_sync_at")
    elapsed_seconds: float | None = None
    if isinstance(last_sync_at, str) and last_sync_at:
        try:
            parsed = datetime.strptime(last_sync_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            elapsed_seconds = (datetime.now(UTC) - parsed).total_seconds()
        except ValueError:
            elapsed_seconds = None
    forge = payload.get("forge")
    repo = payload.get("repo")
    return SyncStatus(
        forge=str(forge) if isinstance(forge, str) else None,
        repo=str(repo) if isinstance(repo, str) else None,
        last_sync_at=last_sync_at if isinstance(last_sync_at, str) else None,
        elapsed_seconds=elapsed_seconds,
        issue_count=issue_count,
    )


def format_elapsed_human(seconds: float) -> str:
    """``elapsed_seconds`` を ``1h 23m 12s`` のような人間可読文字列に整形する。

    負値は 0 として扱う（時計巻き戻り対応の防御的措置）。
    """
    total = max(int(seconds), 0)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"
