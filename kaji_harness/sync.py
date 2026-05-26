"""GitHub → ローカル cache 同期 (`kaji sync from-github` / `kaji sync status`)。

Issue ``gl:34``。``provider.type='local'`` 配下から ``gh:N`` で GitHub
Issue を参照できるようにするため、本 module は GitHub repo の open Issue を
全件取得して ``.kaji/cache/gh-<number>.json`` に atomic write する。

Issue #191 で legacy forge cache migration detector
(``_detect_legacy_forge_cache``) のみが過去 forge リテラルを保持する（許容除外
規則 § 2、設計書 § Cache artifact 移行ポリシー）。
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

from .providers.local import _atomic_write

if TYPE_CHECKING:
    from .config import KajiConfig


_PER_PAGE = 100
_MAX_PAGES = 200
_SYNC_META_FILENAME = ".sync-meta.json"
_CACHE_SCHEMA_VERSION = 1


class SyncError(RuntimeError):
    """``kaji sync`` 固有のエラー（config 不在 / gh CLI 不在 / API 失敗等）。"""


@dataclass(frozen=True)
class SyncResult:
    """``sync_from_github()`` の結果サマリ。"""

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


def _list_existing_cached_numbers(cache_dir: Path, *, prefix: str) -> set[str]:
    """``cache_dir/<prefix><n>.json`` から番号文字列の集合を返す。"""
    if not cache_dir.is_dir():
        return set()
    numbers: set[str] = set()
    plen = len(prefix)
    for path in cache_dir.glob(f"{prefix}*.json"):
        stem = path.stem
        if stem.startswith(prefix) and stem[plen:].isdigit():
            numbers.add(stem[plen:])
    return numbers


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
    *,
    forge: str,
    repo: str,
    last_sync_at: str,
    issue_count: int,
    pages_fetched: int,
    path: Path,
) -> None:
    meta = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "forge": forge,
        "repo": repo,
        "last_sync_at": last_sync_at,
        "issue_count": issue_count,
        "pages_fetched": pages_fetched,
    }
    _atomic_write(path, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")


# ---------- legacy forge cache fail-fast (Issue #191 撤去) ----------

# 過去 forge のリテラル（Issue #191 で撤去された forge の名称）。
# 本 module 以外で参照しない（設計書 § ベースライン計測 § 許容除外規則 § 2）。
_LEGACY_FORGE_LITERAL = "gitlab"
_LEGACY_FORGE_DISPLAY = "GitLab"


def _detect_legacy_forge_cache(cache_dir: Path) -> None:
    """legacy forge cache を検出し ``SyncError`` で fail-fast (Issue #191)。

    検出条件は **OR 結合** (設計書 § Cache artifact 移行ポリシー):

    - (a) ``.sync-meta.json`` の ``forge`` が撤去済 forge
    - (b) ``cache_dir/gl-*.json`` の存在

    いずれか成立で recovery 手順を含む ``SyncError`` を raise する。
    両条件不成立（GitHub cache のみ / cache 空）は無音で通過する。
    ``sync_status`` / ``view_cached_*`` / ``list_issues`` の cache 統合経路の
    **すべての entry point** から先頭で呼び出す。
    """
    if not cache_dir.is_dir():
        return
    meta_path = cache_dir / _SYNC_META_FILENAME
    meta_legacy = False
    if meta_path.is_file():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("forge") == _LEGACY_FORGE_LITERAL:
            meta_legacy = True
    gl_files = sorted(cache_dir.glob("gl-*.json"))
    gl_count = len(gl_files)
    if not meta_legacy and gl_count == 0:
        return
    detail_lines: list[str] = []
    if meta_legacy:
        detail_lines.append(f"  - .sync-meta.json forge='{_LEGACY_FORGE_LITERAL}'")
    if gl_count:
        detail_lines.append(f"  - gl-*.json files: {gl_count} file(s)")
    recovery_lines = ["To recover:", "  1. Remove the legacy cache:"]
    if gl_count:
        recovery_lines.append(f"       rm -f {cache_dir}/gl-*.json")
    if meta_legacy:
        recovery_lines.append(f"       rm -f {cache_dir}/{_SYNC_META_FILENAME}")
    recovery_lines.append("  2. Re-sync from GitHub:")
    recovery_lines.append("       kaji sync from-github")
    message = (
        f"legacy {_LEGACY_FORGE_DISPLAY} cache detected at {cache_dir}/\n"
        + "\n".join(detail_lines)
        + "\n\n"
        + f"{_LEGACY_FORGE_DISPLAY} forge support has been removed in this version of kaji.\n"
        + "\n".join(recovery_lines)
    )
    raise SyncError(message)


# ---------- public API ----------


def _resolve_repo_github(config: KajiConfig, override: str | None) -> str:
    """``--repo`` override > ``[provider.github].repo`` の優先で repo を解決。

    どちらも空なら ``SyncError``。
    """
    if override:
        return override
    if (
        config.provider is not None
        and config.provider.github is not None
        and config.provider.github.repo
    ):
        return config.provider.github.repo
    raise SyncError(
        "'kaji sync from-github' requires a GitHub repo. Either:\n"
        '  - set [provider.github].repo = "owner/name" in .kaji/config.toml, or\n'
        "  - pass --repo owner/name on the command line."
    )


def _gh_api_get_issues(repo: str, *, state: str, per_page: int, page: int) -> object:
    """``gh api -X GET repos/<repo>/issues -F state=... -F per_page=... -F page=...`` を起動。

    失敗は ``SyncError``。
    """
    if shutil.which("gh") is None:
        raise SyncError("'gh' CLI not found in PATH. Install gh to use 'kaji sync from-github'.")
    endpoint = f"repos/{repo}/issues"
    cmd = [
        "gh",
        "api",
        "-X",
        "GET",
        endpoint,
        "-F",
        f"state={state}",
        "-F",
        f"per_page={per_page}",
        "-F",
        f"page={page}",
    ]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError as exc:
        raise SyncError(f"failed to invoke 'gh': {exc}") from exc
    if proc.returncode != 0:
        raise SyncError(
            f"gh api failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SyncError(f"gh returned invalid JSON: {exc}") from exc


def _fetch_open_issues_github_paginated(
    repo: str,
) -> tuple[list[dict[str, object]], list[int]]:
    """GitHub REST ``GET /repos/<repo>/issues?state=open`` を全 page 取得する。

    GitHub REST API は ``/issues`` endpoint から PR も返すため、``pull_request``
    キーを持つ entry は除外する (issue ``gl:34`` § 方針 § 2)。

    Returns:
        ``(全 issue list, 各 page の生件数 list)``。``page_sizes`` は PR 除外前の
        生件数（停止条件判定 / 進捗表示に使う）。
    """
    issues: list[dict[str, object]] = []
    page_sizes: list[int] = []
    page = 1
    while True:
        payload = _gh_api_get_issues(repo, state="open", per_page=_PER_PAGE, page=page)
        if not isinstance(payload, list):
            raise SyncError(f"gh api returned non-array JSON for issue list (page {page})")
        if not payload:
            break
        if page > _MAX_PAGES:
            raise SyncError(
                f"sync aborted after {_MAX_PAGES} pages (>{_MAX_PAGES * _PER_PAGE} issues). "
                f"Check repo or contact maintainer."
            )
        for entry in payload:
            if not isinstance(entry, dict):
                raise SyncError(f"gh api returned non-object element on page {page}")
            if "pull_request" in entry:
                continue
            issues.append(entry)
        page_sizes.append(len(payload))
        if len(payload) < _PER_PAGE:
            break
        page += 1
    return issues, page_sizes


def _github_cache_path(cache_dir: Path, number: str | int) -> Path:
    return cache_dir / f"gh-{number}.json"


def _read_existing_github_issue_payload(path: Path) -> dict[str, object] | None:
    """既存 GitHub wrapper から ``issue`` field を取り出す。形式異常は ``None``。"""
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


def _write_fresh_github_cache_file(entry: dict[str, object], cache_dir: Path, now_iso: str) -> None:
    """fresh GitHub entry の wrapper を atomic write する（既存 wrapper を完全置換）。"""
    number = entry.get("number")
    if number is None or not isinstance(number, (int, str)):
        raise SyncError(f"GitHub issue payload missing 'number' field: {entry!r}")
    path = _github_cache_path(cache_dir, number)
    wrapped = {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "forge": "github",
        "fetched_at": now_iso,
        "kaji_local": {
            "is_stale": False,
            "last_seen_at": now_iso,
            "staled_at": None,
        },
        "issue": entry,
    }
    _atomic_write(path, json.dumps(wrapped, ensure_ascii=False, indent=2) + "\n")


def sync_from_github(
    *,
    config: KajiConfig,
    repo_override: str | None,
    quiet: bool,
) -> SyncResult:
    """GitHub repo から open Issue を全件 fetch して cache を populate する。

    3 phase の all-or-nothing 契約 (issue ``gl:34`` § 方針 § 2)。GitHub REST
    ``/issues`` endpoint は PR も返すため、``pull_request`` キーを持つ entry を
    除外する。
    """
    repo = _resolve_repo_github(config, repo_override)
    cache_dir = _cache_dir_root(config.repo_root)
    _detect_legacy_forge_cache(cache_dir)

    import sys

    started = time.monotonic()
    if not quiet:
        sys.stdout.write(f"Fetching open issues from github.com:{repo} ...\n")
    issues, page_sizes = _fetch_open_issues_github_paginated(repo)
    pages_fetched = len(page_sizes)
    if not quiet:
        for idx, count in enumerate(page_sizes, start=1):
            sys.stdout.write(f"  page {idx}: {count} issues\n")

    fetched_numbers: set[str] = set()
    for entry in issues:
        number = entry.get("number")
        if number is None:
            raise SyncError(f"GitHub issue payload missing 'number' field: {entry!r}")
        fetched_numbers.add(str(number))
    existing_numbers = _list_existing_cached_numbers(cache_dir, prefix="gh-")
    stale_numbers = existing_numbers - fetched_numbers

    cache_dir.mkdir(parents=True, exist_ok=True)
    now_iso = _now_iso()
    newly_added = 0
    updated = 0
    unchanged_signature = 0
    for entry in issues:
        number = str(entry["number"])
        path = _github_cache_path(cache_dir, number)
        if number not in existing_numbers:
            newly_added += 1
        else:
            previous = _read_existing_github_issue_payload(path)
            if previous == entry:
                unchanged_signature += 1
            else:
                updated += 1
        _write_fresh_github_cache_file(entry, cache_dir, now_iso)
    for number in sorted(stale_numbers):
        _mark_cache_stale(_github_cache_path(cache_dir, number), now_iso)
    _write_sync_meta(
        forge="github",
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
    """cache 状態を ``.sync-meta.json`` + ``gh-*.json`` の数から組み立てる。

    ``.sync-meta.json`` 不在時は ``forge=None / issue_count=0`` を返す
    （error にしない。未 sync は正常状態の 1 種）。

    Issue #191 撤去後は ``_detect_legacy_forge_cache()`` を冒頭で呼び、
    legacy cache が残っていれば ``SyncError`` で fail-fast する。
    """
    cache_dir = _cache_dir_root(config.repo_root)
    _detect_legacy_forge_cache(cache_dir)
    meta_path = _sync_meta_path(config.repo_root)
    gh_count = len(_list_existing_cached_numbers(cache_dir, prefix="gh-"))
    if not meta_path.is_file():
        return SyncStatus(
            forge=None,
            repo=None,
            last_sync_at=None,
            elapsed_seconds=None,
            issue_count=gh_count,
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
        issue_count=gh_count,
    )


def format_elapsed_human(seconds: float) -> str:
    """``elapsed_seconds`` を ``1h 23m 12s`` のような人間可読文字列に整形する。

    負値は 0 として扱う（時計巻き戻り対応の防御的措置）。
    """
    total = max(int(seconds), 0)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"
