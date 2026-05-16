"""LocalProvider: ``.kaji/issues/`` ベースの Issue CRUD + cache reader。

design.md § file layout / § ID 採番 / § BCP に詳述された仕様の実装。
phase3-design.md § 詳細設計 で確定した追加事項（atomic write / コメント
seq / Windows 暫定 / IssueContext 解決）を本 module で扱う。
"""

from __future__ import annotations

import errno
import json
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

import yaml

from ._mappings import DEFAULT_BRANCH_PREFIX
from .context import (
    build_branch_name,
    build_design_path,
    build_worktree_dir,
    derive_slug_from_title,
    format_issue_ref,
    validate_branch_prefix,
    validate_slug,
)
from .models import Comment, Issue, IssueContext, Label, PRContext

_MACHINE_ID_RE = re.compile(r"^[a-z0-9]{1,16}$")
_LOCAL_ID_RE = re.compile(r"^local-([a-z0-9]{1,16})-([1-9]\d*)$")
_POS_INT_RE = re.compile(r"^[1-9]\d*$")
# comment filename: <YYYYMMDDTHHMMSSZ>-<machine>.md
# - timestamp 部 16 文字固定（8 digit date + "T" + 6 digit time + "Z"）
# - machine 部は validate_machine_id と同じ正規表現
_COMMENT_FILENAME_RE = re.compile(r"^(?P<ts>\d{8}T\d{6}Z)-(?P<machine>[a-z0-9]{1,16})$")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_SUPPRESS_WIN_WARNING_ENV = "KAJI_SUPPRESS_WIN_WARNING"
_WIN_WARNING_EMITTED = False
_VALID_ISSUE_STATES: frozenset[str] = frozenset({"open", "closed"})
# phase3d-preflight § 5: comment filename 競合 retry 上限
MAX_COMMENT_WRITE_RETRIES: int = 8


class LocalProviderError(RuntimeError):
    """LocalProvider 特有のエラー。"""


class IssueNotFoundError(LocalProviderError):
    """Issue ディレクトリが存在しない。"""


class IssueReadOnlyError(LocalProviderError):
    """``provider=local`` 配下で remote_cache 由来の Issue を変更しようとした。"""


def validate_machine_id(machine_id: str) -> None:
    """machine_id 文法を検証する。違反は ``ValueError``。"""
    if not isinstance(machine_id, str) or not _MACHINE_ID_RE.match(machine_id):
        raise ValueError(
            f"invalid machine_id {machine_id!r}: must match [a-z0-9]{{1,16}} "
            f"(lowercase alphanumeric, hyphen disallowed, max 16 chars)"
        )


# -------- atomic write & flock --------


def _atomic_write(path: Path, content: str) -> None:
    """``*.tmp`` → ``os.replace`` による atomic な text 書き込み。

    部分書き込みが残らないため、git の add/commit 段で中間状態を取り込まない
    （phase3-design.md § Issue ファイル / コメントファイルの atomic 書き込み）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_new(path: Path, content: str) -> None:
    """``O_CREAT | O_EXCL`` で新規ファイルとして atomic に書き込む。

    既存ファイルがある場合は ``FileExistsError`` を投げる。``path.open("x")`` は
    buffering / kill 時の 0 byte file 懸念があるため、``os.open`` で fd を作って
    bytes を loop で書ききる（phase3d-preflight-design § 5）。

    POSIX ``write(2)`` は short write を許す契約なので、返り値が要求 byte 数より
    少ない場合に備えて残バイトを再 write する。``n <= 0`` は通常起きないが、
    EINTR を裸で晒さないため最低限の defensive な扱いとする。

    既存 ``_atomic_write()`` は edit / close 等の上書き用として残す。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(path, flags, 0o644)
    try:
        data = content.encode("utf-8")
        written = 0
        while written < len(data):
            n = os.write(fd, data[written:])
            if n <= 0:
                raise OSError(f"os.write returned non-positive count {n}")
            written += n
    finally:
        os.close(fd)


def _emit_windows_warning() -> None:
    """Windows 上で flock が無いことを 1 度のみ警告する。"""
    global _WIN_WARNING_EMITTED
    if _WIN_WARNING_EMITTED:
        return
    if os.environ.get(_SUPPRESS_WIN_WARNING_ENV) == "1":
        _WIN_WARNING_EMITTED = True
        return
    _WIN_WARNING_EMITTED = True
    print(
        "WARNING: kaji local mode is running on Windows without process-level "
        "locking. Windows native is not a supported local-mode environment. "
        "Use WSL for supported local-mode operation.",
        file=sys.stderr,
    )


@contextmanager
def _counter_lock(counter_path: Path) -> Iterator[IO[str]]:
    """counter file に対する advisory lock を取る context manager。

    POSIX では ``fcntl.flock`` で blocking lock。Windows では skip して
    no-op、警告を 1 度出す（phase3-design.md § Windows 暫定挙動）。
    file descriptor の close で自動解除されるため stale lock は構造的に
    発生しない（phase3-design.md L185）。
    """
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    counter_path.touch(exist_ok=True)
    fh = counter_path.open("r+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            _emit_windows_warning()
        else:
            try:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                fh.close()
                raise LocalProviderError(
                    "flock unavailable on this filesystem (NFS / FUSE?). "
                    "Set provider.local.machine_id to a unique value per "
                    "process and retry."
                ) from exc
        yield fh
    finally:
        try:
            fh.close()
        except OSError:
            pass


# -------- frontmatter parse / serialize --------
#
# Phase 3-d preflight: 自前 parser を撤去し PyYAML に委譲する。
# round-trip は byte-for-byte ではなく semantic 等価のみ保証する
# （phase3d-preflight-design § 3）。


def _serialize_frontmatter(meta: dict[str, object]) -> str:
    """frontmatter dict を YAML 文字列に整形する（PyYAML safe_dump 委譲）。

    `sort_keys=False` で挿入順を維持。Unicode は escape せず素通し。
    """
    return yaml.safe_dump(
        meta,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def _parse_frontmatter(raw: str) -> tuple[dict[str, object], str]:
    """frontmatter + body へ分割し、frontmatter は PyYAML で読む。

    frontmatter が無い場合は ``({}, raw)``。``safe_load`` の戻り値が ``None``
    の場合は空 dict として扱う。mapping 以外で返ってきた場合（先頭が list 等の
    変則 YAML）は ``LocalProviderError``。
    """
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw
    fm_text = m.group(1)
    body = m.group(2)
    try:
        loaded = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise LocalProviderError(f"invalid YAML frontmatter: {exc}") from exc
    if loaded is None:
        return {}, body
    if not isinstance(loaded, dict):
        raise LocalProviderError(f"frontmatter must be a YAML mapping, got {type(loaded).__name__}")
    return loaded, body


def _expected_id_from_dirname(dirname: str) -> str | None:
    """``local-<machine>-<n>[-<slug>]`` の prefix 部分を抽出する。

    ``local-pc1-9`` → ``local-pc1-9``、``local-pc1-9-foo-bar`` → ``local-pc1-9``。
    parse できない場合は ``None``。`_resolve_issue_dir` 経由で得た directory
    name に対する identity チェックに使う（phase3d-preflight review Finding 1）。
    """
    m = re.match(r"^(local-[a-z0-9]{1,16}-[1-9]\d*)(?:-|$)", dirname)
    return m.group(1) if m else None


def _validate_issue_meta(
    meta: dict[str, object],
    *,
    strict_slug: bool,
    expected_id: str | None = None,
) -> None:
    """frontmatter dict の最小限の構造を検証する。

    Phase 3-d preflight § 3 の表に基づき、view / write 共通で id / state /
    labels を fail-fast 検証する。slug は ``view_issue()`` では不在許容、
    ``resolve_issue_context()`` / write 系では必須。

    Phase 3-d preflight review:

    - Finding 1: ``expected_id`` が渡された場合は frontmatter ``id`` と一致する
      ことを fail-fast 検証する。dirname から導出した値を渡すことで、人間編集
      ミス / merge ミスで dirname と frontmatter id が乖離した Issue を canonical
      id として採用する事故を防ぐ
    - Finding 3: ``labels`` の各要素も ``str`` / ``dict`` 限定で fail-fast 検証
      する（silent drop を許さない）

    Args:
        meta: parsed frontmatter dict。
        strict_slug: True なら slug 必須。
        expected_id: 期待される ``id``。渡されたら一致確認。
    """
    issue_id = meta.get("id")
    if not isinstance(issue_id, str) or not _LOCAL_ID_RE.match(issue_id):
        raise LocalProviderError(
            f"frontmatter 'id' must match local-<machine>-<n>, got {issue_id!r}"
        )
    if expected_id is not None and issue_id != expected_id:
        raise LocalProviderError(
            f"frontmatter 'id' {issue_id!r} does not match expected id "
            f"{expected_id!r} derived from issue directory; the directory may "
            f"have been renamed or the frontmatter edited in isolation"
        )
    state = meta.get("state", "open")
    if not isinstance(state, str) or state not in _VALID_ISSUE_STATES:
        raise LocalProviderError(f"frontmatter 'state' must be 'open' or 'closed', got {state!r}")
    labels = meta.get("labels")
    if labels is not None:
        if not isinstance(labels, list):
            raise LocalProviderError(
                f"frontmatter 'labels' must be a list, got {type(labels).__name__}"
            )
        for index, entry in enumerate(labels):
            if not isinstance(entry, (str, dict)):
                raise LocalProviderError(
                    f"frontmatter 'labels[{index}]' must be str or dict, got {type(entry).__name__}"
                )
    slug_value = meta.get("slug")
    if slug_value not in (None, ""):
        if not isinstance(slug_value, str):
            raise LocalProviderError(
                f"frontmatter 'slug' must be a string, got {type(slug_value).__name__}"
            )
        try:
            validate_slug(slug_value)
        except ValueError as exc:
            raise LocalProviderError(f"frontmatter 'slug' invalid: {exc}") from exc
    elif strict_slug:
        raise LocalProviderError(
            f"issue {issue_id!r} has no 'slug' in frontmatter; required for "
            f"context resolution and write operations"
        )
    prefix_value = meta.get("branch_prefix")
    if prefix_value not in (None, ""):
        if not isinstance(prefix_value, str):
            raise LocalProviderError(
                f"frontmatter 'branch_prefix' must be a string, got {type(prefix_value).__name__}"
            )
        try:
            validate_branch_prefix(prefix_value)
        except ValueError as exc:
            raise LocalProviderError(f"frontmatter 'branch_prefix' invalid: {exc}") from exc


# -------- LocalProvider --------


@dataclass
class LocalProvider:
    """``.kaji/issues/`` 配下を正本とする provider。

    Attributes:
        repo_root: repo のルート（``.kaji/`` を含む親）。
        machine_id: 採番に用いる本 PC の machine_id。
        default_branch: ``provider.local.default_branch``。``main`` 等。
        git_remote: ``provider.local.git_remote``。default ``"origin"``。
            ``IssueContext.git_remote`` の source。skill 内 ``git push`` / ``git fetch``
            等の対象 remote 名（gl:6 で導入）。
    """

    repo_root: Path
    machine_id: str
    default_branch: str = "main"
    git_remote: str = "origin"

    def __post_init__(self) -> None:
        validate_machine_id(self.machine_id)
        if sys.platform == "win32":
            _emit_windows_warning()

    @property
    def is_readonly(self) -> bool:
        return False

    # -------- 内部 path helpers --------

    @property
    def _issues_dir(self) -> Path:
        return self.repo_root / ".kaji" / "issues"

    @property
    def _counter_path(self) -> Path:
        # machine_id ごとに分離する。共有 counter にすると、pc1 commit を
        # pc2 が pull した直後に pc2 の最初の Issue 番号が pc1 の max+1 へ
        # 引きずられ、machine_id 番号空間の独立性が壊れる。
        return self.repo_root / ".kaji" / "counters" / f"{self.machine_id}.txt"

    @property
    def _cache_dir(self) -> Path:
        return self.repo_root / ".kaji" / "cache" / "issues"

    @property
    def _cache_dir_root(self) -> Path:
        """``.kaji/cache/`` 直下。``gl-*.json`` / ``.sync-meta.json`` を置く。

        既存 ``_cache_dir`` (``= .kaji/cache/issues``) は ``gh:`` 専用 layout として
        据え置く（issue ``local-p1-8`` 設計 § 互換性保持）。
        """
        return self.repo_root / ".kaji" / "cache"

    def _gitlab_cache_path(self, iid: str) -> Path:
        """``.kaji/cache/gl-<iid>.json`` の絶対 path。"""
        return self._cache_dir_root / f"gl-{iid}.json"

    def _resolve_issue_dir(self, issue_id: str) -> Path:
        """``local-<machine>-<n>`` から Issue ディレクトリを解決する。

        glob ``local-<machine>-<n>-*`` で検索し、複数 hit は重複エラー、
        0 hit は ``IssueNotFoundError``（phase3-design.md § resolve_issue_dir）。
        """
        if not _LOCAL_ID_RE.match(issue_id):
            raise ValueError(f"not a local issue id: {issue_id!r}")
        if not self._issues_dir.exists():
            raise IssueNotFoundError(f"no .kaji/issues directory under {self.repo_root}")
        candidates = sorted(self._issues_dir.glob(f"{issue_id}-*"))
        if not candidates:
            # slug 無し（migration 用に許容）
            bare = self._issues_dir / issue_id
            if bare.is_dir():
                return bare
            raise IssueNotFoundError(
                f"no issue directory for {issue_id!r} under {self._issues_dir}"
            )
        if len(candidates) > 1:
            names = ", ".join(c.name for c in candidates)
            raise LocalProviderError(
                f"multiple issue directories matched {issue_id!r}: {names}. "
                f"Resolve the duplicate before continuing."
            )
        return candidates[0]

    # -------- ID 採番 --------

    def _existing_local_max(self) -> int:
        """同一 machine_id の既存 Issue ディレクトリから max(n) を返す。"""
        if not self._issues_dir.exists():
            return 0
        prefix = f"local-{self.machine_id}-"
        max_n = 0
        for entry in self._issues_dir.iterdir():
            if not entry.is_dir():
                continue
            if not entry.name.startswith(prefix):
                continue
            tail = entry.name[len(prefix) :]
            num_part = tail.split("-", 1)[0]
            if num_part.isdigit():
                max_n = max(max_n, int(num_part))
        return max_n

    def _next_local_id(self) -> int:
        """flock 配下で次の n を採番する。

        counter file の値と既存ディレクトリ max(n) の大きい方 +1 を返す。
        counter は採番後に書き戻す。phase3-design.md § next_local_id 参照。
        """
        with _counter_lock(self._counter_path) as fh:
            fh.seek(0)
            raw = fh.read().strip()
            counter_n = int(raw) if raw.isdigit() else 0
            n = max(counter_n, self._existing_local_max()) + 1
            fh.seek(0)
            fh.truncate()
            fh.write(str(n))
            fh.flush()
        return n

    # -------- frontmatter helpers --------

    @staticmethod
    def _build_issue_md(meta: dict[str, object], body: str) -> str:
        return f"---\n{_serialize_frontmatter(meta)}---\n{body}"

    def _read_issue(self, issue_dir: Path) -> Issue:
        issue_path = issue_dir / "issue.md"
        if not issue_path.is_file():
            raise IssueNotFoundError(f"missing issue.md in {issue_dir}")
        meta, body = _parse_frontmatter(issue_path.read_text(encoding="utf-8"))
        # view 経路では slug 不在を許容（migration 用）。dirname から導出した
        # expected_id を渡すことで、frontmatter id と directory の乖離を fail-fast。
        expected_id = _expected_id_from_dirname(issue_dir.name)
        _validate_issue_meta(meta, strict_slug=False, expected_id=expected_id)
        issue_id = str(meta["id"])
        labels = self._labels_from_meta(meta.get("labels"))
        comments = self._read_comments(issue_dir)
        slug_value = meta.get("slug", "")
        return Issue(
            id=issue_id,
            title=str(meta.get("title", "") or ""),
            body=body,
            state=str(meta.get("state", "open")),
            labels=labels,
            comments=comments,
            slug=str(slug_value or ""),
        )

    @staticmethod
    def _labels_from_meta(value: object) -> list[Label]:
        if not isinstance(value, list):
            return []
        out: list[Label] = []
        for entry in value:
            if isinstance(entry, str):
                out.append(Label(name=entry))
            elif isinstance(entry, dict):
                out.append(
                    Label(
                        name=str(entry.get("name", "") or ""),
                        description=str(entry.get("description", "") or ""),
                        color=str(entry.get("color", "") or ""),
                    )
                )
        return out

    def _read_comments(self, issue_dir: Path) -> list[Comment]:
        """``comments/<ts>-<machine>.md`` を読み frontmatter ``created_at`` 順で返す。

        comment ordering の正本は frontmatter ``created_at``（Issue local-pc5090-21
        設計 § 制約）。filename の timestamp は uniqueness 用であり、同秒衝突
        retry で +1s ずれた値が入りうるため ordering の正本にはしない。

        既知形式（``<YYYYMMDDTHHMMSSZ>-<machine>``）にマッチしない filename、
        または frontmatter ``created_at`` 不在は ``LocalProviderError`` で
        fail-fast する（旧 ``<NNNN>-<machine>`` 形式 fallback は本 issue で削除）。
        """
        cdir = issue_dir / "comments"
        if not cdir.is_dir():
            return []
        result: list[Comment] = []
        for path in cdir.iterdir():
            if path.suffix != ".md":
                continue
            m = _COMMENT_FILENAME_RE.match(path.stem)
            if m is None:
                raise LocalProviderError(
                    f"unrecognized comment filename: {path}. "
                    f"Expected '<YYYYMMDDTHHMMSSZ>-<machine>.md'."
                )
            ts, machine = m["ts"], m["machine"]
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            created_at = str(meta.get("created_at", "") or "")
            if not created_at:
                raise LocalProviderError(
                    f"missing 'created_at' in {path}; ordering source must exist."
                )
            result.append(
                Comment(
                    author=str(meta.get("author", "") or ""),
                    body=body,
                    created_at=created_at,
                    seq=ts,
                    machine_id=machine,
                )
            )
        # ordering 正本は frontmatter created_at。同 created_at は filename
        # （= seq フィールド）でタイブレーク。Python sort は stable なので
        # (created_at, seq) lexicographic で決定的順序になる。
        result.sort(key=lambda c: (c.created_at, c.seq))
        return result

    # -------- CRUD --------

    def create_issue(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
        slug: str | None = None,
    ) -> Issue:
        # Phase 3-d preflight § 4: slug を optional 化。未指定なら title から
        # 導出する（GitHubProvider と同じ fallback）。明示 slug は従来どおり
        # validate_slug で検証する。
        if slug is None:
            slug = derive_slug_from_title(title)
        validate_slug(slug)
        n = self._next_local_id()
        issue_id = f"local-{self.machine_id}-{n}"
        issue_dir = self._issues_dir / f"{issue_id}-{slug}"
        if issue_dir.exists():
            raise LocalProviderError(
                f"issue directory already exists: {issue_dir}. "
                f"This indicates a counter / glob inconsistency."
            )
        issue_dir.mkdir(parents=True)
        meta: dict[str, object] = {
            "id": issue_id,
            "title": title,
            "state": "open",
            "slug": slug,
            "labels": list(labels or []),
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        _atomic_write(issue_dir / "issue.md", self._build_issue_md(meta, body))
        return self._read_issue(issue_dir)

    def view_issue(self, issue_id: str) -> Issue:
        return self._read_issue(self._resolve_issue_dir(issue_id))

    def edit_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> Issue:
        issue_dir = self._resolve_issue_dir(issue_id)
        issue_path = issue_dir / "issue.md"
        meta, current_body = _parse_frontmatter(issue_path.read_text(encoding="utf-8"))
        _validate_issue_meta(
            meta,
            strict_slug=True,
            expected_id=_expected_id_from_dirname(issue_dir.name),
        )
        if title is not None:
            meta["title"] = title
        if add_labels or remove_labels:
            current = [label.name for label in self._labels_from_meta(meta.get("labels"))]
            updated = [label for label in current if label not in (remove_labels or [])]
            for label in add_labels or []:
                if label not in updated:
                    updated.append(label)
            meta["labels"] = updated
        new_body = body if body is not None else current_body
        _atomic_write(issue_path, self._build_issue_md(meta, new_body))
        return self._read_issue(issue_dir)

    def comment_issue(self, issue_id: str, body: str) -> Comment:
        """新しい comment を ``comments/<ts>-<machine>.md`` に書き込む。

        Issue local-pc5090-21: filename を seq 採番から compact ISO 8601
        timestamp (``YYYYMMDDTHHMMSSZ``) ベースに切替。worktree 間の seq race
        を原理的に解消する。

        ``O_CREAT | O_EXCL`` の atomic create に失敗した場合、filename 用
        timestamp を ``+1s`` ずつ加算して ``MAX_COMMENT_WRITE_RETRIES`` 回まで
        retry する。filename の timestamp は uniqueness 用であり、ordering の
        正本は frontmatter ``created_at`` （retry で乖離しても ordering には
        波及しない）。

        Phase 3-d preflight review Finding 2: write 系の一貫性として、comment
        付与前にも frontmatter validation を通す。slug は comment 自体が消費
        しないため ``strict_slug=False``。dirname と frontmatter id の乖離 /
        invalid state / invalid labels は本経路でも fail-fast する。
        """
        issue_dir = self._resolve_issue_dir(issue_id)
        # frontmatter の整合性を comment 付与前にも検証する。issue.md 不在は
        # IssueNotFoundError、parse / validation 失敗は LocalProviderError として
        # 上位層へ伝搬する（CLI dispatcher が exit 2 / 3 にマップ）。
        issue_path = issue_dir / "issue.md"
        if not issue_path.is_file():
            raise IssueNotFoundError(f"missing issue.md in {issue_dir}")
        meta, _ = _parse_frontmatter(issue_path.read_text(encoding="utf-8"))
        _validate_issue_meta(
            meta,
            strict_slug=False,
            expected_id=_expected_id_from_dirname(issue_dir.name),
        )
        cdir = issue_dir / "comments"
        cdir.mkdir(exist_ok=True)
        now = datetime.now(UTC)
        created_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        comment_meta: dict[str, object] = {"author": self.machine_id, "created_at": created_at}
        content = self._build_issue_md(comment_meta, body)
        last_attempted = ""
        for attempt in range(MAX_COMMENT_WRITE_RETRIES):
            ts = (now + timedelta(seconds=attempt)).strftime("%Y%m%dT%H%M%SZ")
            last_attempted = ts
            path = cdir / f"{ts}-{self.machine_id}.md"
            try:
                _atomic_write_new(path, content)
            except FileExistsError:
                continue
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    continue
                raise
            return Comment(
                author=self.machine_id,
                body=body,
                created_at=created_at,
                seq=ts,
                machine_id=self.machine_id,
            )
        raise LocalProviderError(
            f"failed to allocate unique comment filename in {cdir} after "
            f"{MAX_COMMENT_WRITE_RETRIES} retries (last attempted ts={last_attempted!r}). "
            f"Another process may be writing comments concurrently; retry later "
            f"or inspect the directory."
        )

    def close_issue(self, issue_id: str, reason: str | None = None) -> Issue:
        issue_dir = self._resolve_issue_dir(issue_id)
        issue_path = issue_dir / "issue.md"
        meta, current_body = _parse_frontmatter(issue_path.read_text(encoding="utf-8"))
        _validate_issue_meta(
            meta,
            strict_slug=True,
            expected_id=_expected_id_from_dirname(issue_dir.name),
        )
        meta["state"] = "closed"
        meta["closed_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta["closed_by"] = self.machine_id
        # Phase 3-d: default を "completed" に変更（design.md L985 / 状態遷移図
        # L1011-L1015 と整合）。明示値 ("not-planned" 等) はそのまま採用。
        meta["close_reason"] = reason if reason else "completed"
        _atomic_write(issue_path, self._build_issue_md(meta, current_body))
        return self._read_issue(issue_dir)

    def list_issues(
        self,
        *,
        state: str = "open",
        labels: list[str] | None = None,
        limit: int | None = None,
    ) -> list[Issue]:
        out: list[Issue] = []
        if self._issues_dir.exists():
            for entry in sorted(self._issues_dir.iterdir()):
                if not entry.is_dir():
                    continue
                if not entry.name.startswith("local-"):
                    continue
                try:
                    issue = self._read_issue(entry)
                except IssueNotFoundError:
                    continue
                if state != "all" and issue.state != state:
                    continue
                if labels:
                    names = {label.name for label in issue.labels}
                    if not all(label in names for label in labels):
                        continue
                out.append(issue)
                if limit is not None and len(out) >= limit:
                    return out
        # GitLab cache 由来 entry を末尾に append（issue ``local-p1-8``）。
        # 表示 state の決定は ``_list_cached_gitlab_issues`` 内で正規化する。
        out.extend(self._list_cached_gitlab_issues(state, labels))
        if limit is not None:
            out = out[:limit]
        return out

    def list_labels(self) -> list[Label]:
        # local mode はラベル定義の正本を持たない。実 Issue から union を返す。
        seen: dict[str, Label] = {}
        for issue in self.list_issues(state="all"):
            for label in issue.labels:
                seen.setdefault(label.name, label)
        return list(seen.values())

    # -------- IssueContext --------

    def resolve_issue_context(self, issue_id: str) -> IssueContext:
        issue_dir = self._resolve_issue_dir(issue_id)
        issue_path = issue_dir / "issue.md"
        meta, _ = _parse_frontmatter(issue_path.read_text(encoding="utf-8"))
        # context 解決経路: slug 必須 + branch_prefix の値域も fail-fast。
        # dirname / frontmatter id の乖離も identity check で fail-fast
        # （phase3d-preflight review Finding 1）。
        _validate_issue_meta(
            meta,
            strict_slug=True,
            expected_id=_expected_id_from_dirname(issue_dir.name),
        )
        slug = str(meta["slug"])
        prefix_value = meta.get("branch_prefix")
        fallback = False
        if isinstance(prefix_value, str) and prefix_value:
            prefix = prefix_value
        else:
            # local では type:* label からも導出を試みる
            from ._mappings import labels_to_branch_prefix

            label_names = [label.name for label in self._labels_from_meta(meta.get("labels"))]
            prefix, fallback = labels_to_branch_prefix(label_names)
            if fallback:
                prefix = DEFAULT_BRANCH_PREFIX
        return IssueContext(
            issue_id=issue_id,
            issue_ref=format_issue_ref(issue_id),
            issue_input=issue_id,
            slug=slug,
            branch_prefix=prefix,
            branch_name=build_branch_name(prefix, issue_id),
            worktree_dir=build_worktree_dir(prefix, issue_id, self.repo_root),
            design_path=build_design_path(issue_id, slug),
            provider_type="local",
            branch_prefix_fallback=fallback,
            default_branch=self.default_branch,
            git_remote=self.git_remote,
        )

    def resolve_pr_context(self, branch_name: str) -> PRContext | None:
        """no-op 実装。local mode に PR 概念は存在しない。

        ``provider.type='local'`` 配下では ``/i-pr`` / ``/pr-fix`` /
        ``/pr-verify`` が Step 0 で ABORT するため、本 method が呼ばれた
        時点で何かが間違っているが、`IssueProvider` Protocol 整合のため
        防御的に ``None`` を返す。
        """
        del branch_name
        return None

    # -------- remote cache reader (gh:N) --------

    def view_cached_issue(self, number: str) -> Issue:
        """``.kaji/cache/issues/<n>.json`` から read-only に Issue を組み立てる。

        cache fixture が無ければ明示エラー（phase3-design.md L78）。

        cache 自動 populate (`kaji sync from-github`) は残課題（forge 採用先
        確定時に再評価、`design.md` §残課題 参照）。2026-05-08 方針転換以降、
        検証期間中は forge 通信を行わない方針のため、本メソッドが呼ばれるのは
        user が手動で JSON を投入した場合に限られる。cache reader 自体は
        Phase 3-c で実装された既存契約として維持される
        （`tests/test_dispatcher.py:329-365` で検証済）。

        ``gl:`` cache reader は :meth:`view_cached_gitlab_issue` を参照
        （cache layout は ``.kaji/cache/gl-<iid>.json``、自動 populate は
        ``kaji sync from-gitlab`` 経由）。
        """
        if not _POS_INT_RE.match(number):
            raise ValueError(
                f"cached issue number must be a positive integer (no leading zero): {number!r}"
            )
        path = self._cache_dir / f"{number}.json"
        if not path.is_file():
            raise IssueNotFoundError(
                f"no cached issue at {path}. "
                f"Cache population (`kaji sync from-github`) is a remaining task, "
                f"re-evaluated when the forge migration target is decided. "
                f"During the local-mode validation period, manual `gh:` references "
                f"are not recommended (see docs/operations/local-mode-runbook.md). "
                f"If the JSON is intentionally pre-populated, ensure the file path "
                f"matches the cache layout."
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LocalProviderError(f"cache JSON malformed at {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise LocalProviderError(f"cache JSON at {path} must be an object")
        return _cached_issue_from_payload(payload)

    # -------- remote cache reader (gl:N) --------

    def view_cached_gitlab_issue(self, iid: str) -> Issue:
        """``.kaji/cache/gl-<iid>.json`` から read-only に Issue を組み立てる。

        cache fixture が無ければ ``IssueNotFoundError``（``kaji sync from-gitlab``
        を案内するメッセージ）。issue ``local-p1-8`` § インターフェース 3。
        """
        if not _POS_INT_RE.match(iid):
            raise ValueError(
                f"cached GitLab issue iid must be a positive integer (no leading zero): {iid!r}"
            )
        path = self._gitlab_cache_path(iid)
        if not path.is_file():
            raise IssueNotFoundError(
                f"no cached GitLab issue at {path}. "
                f"Run 'kaji sync from-gitlab' to populate the cache."
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LocalProviderError(f"cache JSON malformed at {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise LocalProviderError(f"cache JSON at {path} must be an object")
        return _cached_gitlab_issue_from_payload(payload)

    def _list_cached_gitlab_issues(self, state: str, labels: list[str] | None) -> list[Issue]:
        """``.kaji/cache/gl-*.json`` を読み state / labels filter 込みで返す。

        表示 state の決定ルール (issue ``local-p1-8`` 設計 § list_issues):

        - ``kaji_local.is_stale=true`` → 無条件 ``"closed"``（GitLab issue.state に依らない）
        - ``is_stale=false`` AND ``issue.state="opened"`` → ``"open"``
        - それ以外 → ``"closed"``

        旧 schema（``kaji_local`` field 不在）は ``is_stale=false`` 扱い（後方互換）。
        壊れた cache file は stderr に warning を書いて skip する。
        """
        cache_dir = self._cache_dir_root
        if not cache_dir.exists():
            return []
        out: list[Issue] = []
        for path in sorted(cache_dir.glob("gl-*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                sys.stderr.write(f"warning: skipping malformed cache entry {path.name}: {exc}\n")
                continue
            if not isinstance(payload, dict):
                sys.stderr.write(
                    f"warning: skipping malformed cache entry {path.name}: not a JSON object\n"
                )
                continue
            issue_payload = payload.get("issue") or {}
            if not isinstance(issue_payload, dict):
                continue
            kl = payload.get("kaji_local") or {}
            is_stale = bool(kl.get("is_stale", False)) if isinstance(kl, dict) else False
            gl_state = str(issue_payload.get("state", "") or "")
            if is_stale:
                display_state = "closed"
            elif gl_state == "opened":
                display_state = "open"
            else:
                display_state = "closed"
            if state != "all" and display_state != state:
                continue
            label_names_raw = issue_payload.get("labels") or []
            label_names: list[str] = []
            if isinstance(label_names_raw, list):
                for entry in label_names_raw:
                    if isinstance(entry, str):
                        label_names.append(entry)
                    elif isinstance(entry, dict):
                        label_names.append(str(entry.get("name", "") or ""))
            if labels:
                if not all(label in label_names for label in labels):
                    continue
            iid = issue_payload.get("iid")
            out.append(
                Issue(
                    id=f"gl:{iid}" if iid is not None else "",
                    title=str(issue_payload.get("title", "") or ""),
                    body=str(issue_payload.get("description", "") or ""),
                    state=display_state,
                    labels=[Label(name=name) for name in label_names if name],
                    comments=[],
                )
            )
        return out

    def is_readonly_id(self, resolved_kind: str) -> bool:
        """ID 種別ごとの read-only 判定。``remote_cache`` のみ True。

        provider 全体の `is_readonly` は False のままで、特定 ID 経路のみ
        write を拒む。CLI 層が ``rid.kind`` を見てから呼ぶ。
        """
        return resolved_kind == "remote_cache"


def _cached_issue_from_payload(payload: dict[str, object]) -> Issue:
    """remote cache JSON → Issue に整形（GitHubProvider と対称）。"""
    labels_raw = payload.get("labels", []) or []
    labels: list[Label] = []
    if isinstance(labels_raw, list):
        for entry in labels_raw:
            if isinstance(entry, dict):
                labels.append(
                    Label(
                        name=str(entry.get("name", "") or ""),
                        description=str(entry.get("description", "") or ""),
                        color=str(entry.get("color", "") or ""),
                    )
                )
            elif isinstance(entry, str):
                labels.append(Label(name=entry))
    comments_raw = payload.get("comments", []) or []
    comments: list[Comment] = []
    if isinstance(comments_raw, list):
        for entry in comments_raw:
            if isinstance(entry, dict):
                author_obj = entry.get("author")
                author = ""
                if isinstance(author_obj, dict):
                    author = str(author_obj.get("login", "") or "")
                elif isinstance(author_obj, str):
                    author = author_obj
                comments.append(
                    Comment(
                        author=author,
                        body=str(entry.get("body", "") or ""),
                        created_at=str(entry.get("createdAt", "") or ""),
                    )
                )
    return Issue(
        id=str(payload.get("number", "")),
        title=str(payload.get("title", "") or ""),
        body=str(payload.get("body", "") or ""),
        state=str(payload.get("state", "open") or "open").lower(),
        labels=labels,
        comments=comments,
    )


def _cached_gitlab_issue_from_payload(payload: dict[str, object]) -> Issue:
    """GitLab cache wrapper JSON → Issue に整形（``view_cached_gitlab_issue`` 用）。

    wrapper schema (issue ``local-p1-8`` 設計 § cache 配置):

    - ``payload["issue"]``: GitLab REST 由来 issue JSON
    - ``payload["kaji_local"]``: ``is_stale`` / ``last_seen_at`` / ``staled_at``

    state 正規化:

    - ``kaji_local.is_stale=true`` → ``"closed"``
    - ``is_stale=false`` AND ``issue.state="opened"`` → ``"open"``
    - それ以外 → ``"closed"``
    """
    issue_payload = payload.get("issue") or {}
    if not isinstance(issue_payload, dict):
        return Issue(id="", title="", body="", state="closed", labels=[], comments=[])
    kl = payload.get("kaji_local") or {}
    is_stale = bool(kl.get("is_stale", False)) if isinstance(kl, dict) else False
    gl_state = str(issue_payload.get("state", "") or "")
    if is_stale:
        state = "closed"
    elif gl_state == "opened":
        state = "open"
    else:
        state = "closed"
    label_names_raw = issue_payload.get("labels") or []
    labels: list[Label] = []
    if isinstance(label_names_raw, list):
        for entry in label_names_raw:
            if isinstance(entry, str):
                labels.append(Label(name=entry))
            elif isinstance(entry, dict):
                labels.append(Label(name=str(entry.get("name", "") or "")))
    iid = issue_payload.get("iid")
    return Issue(
        id=f"gl:{iid}" if iid is not None else "",
        title=str(issue_payload.get("title", "") or ""),
        body=str(issue_payload.get("description", "") or ""),
        state=state,
        labels=labels,
        comments=[],
    )
