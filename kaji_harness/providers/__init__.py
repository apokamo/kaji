"""Provider abstraction for kaji_harness.

Phase 3-ab で導入。`IssueProvider` Protocol と `IssueContext` を中心に
GitHub / local 両方を統一 interface で扱う。本パッケージは dispatcher
未切替の段階で導入されるため、`cli_main.py` / `prompt.py` への組み込みは
Phase 3-c で行う（design.md L226-244 参照）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .base import IssueProvider
from .models import Comment, Issue, IssueContext, Label

__all__ = [
    "Comment",
    "Issue",
    "IssueContext",
    "IssueProvider",
    "Label",
    "ResolvedId",
    "normalize_id",
]


ResolvedKind = Literal["github", "local", "remote_cache"]


@dataclass(frozen=True)
class ResolvedId:
    """正規化済み Issue ID。

    Attributes:
        kind: ID の所属。``github`` は active な GitHub provider 経路、
            ``local`` は LocalProvider 経路、``remote_cache`` は
            ``provider=local`` 配下で ``gh:N`` として読み出される
            キャッシュ参照（read-only）。
        value: provider 内部表現での ID 文字列。
            github → 数値文字列（``"153"``）、
            local → ``local-<machine>-<n>``（``"local-pc1-3"``）、
            remote_cache → 数値文字列（``"153"``、cache JSON 上の番号）。
        raw: 入力された原文字列（デバッグ・エラーメッセージ用）。
    """

    kind: ResolvedKind
    value: str
    raw: str


# Issue 番号は 1 始まり整数。leading zero（``007``）や ``0`` は拒否する。
_POS_INT = r"[1-9]\d*"
_GH_PREFIX_RE = re.compile(rf"^gh:({_POS_INT})$")
_LOCAL_FULL_RE = re.compile(rf"^local-([a-z0-9]{{1,16}})-({_POS_INT})$")
_LOCAL_SHORT_RE = re.compile(rf"^([a-z0-9]{{1,16}})-({_POS_INT})$")
_NUMERIC_RE = re.compile(rf"^{_POS_INT}$")
_MACHINE_ID_RE = re.compile(r"^[a-z0-9]{1,16}$")


def normalize_id(
    raw: str,
    *,
    provider_name: str,
    machine_id: str | None,
) -> ResolvedId:
    """user 入力の Issue ID を `ResolvedId` に正規化する。

    受理する形式は以下:

    - ``"153"``           — provider に応じて github / local どちらにも解釈
    - ``"gh:153"``        — provider=local 配下では remote_cache、
                            provider=github では github（``gh:`` を剥がす）
    - ``"local-pc1-3"``   — local（machine_id まで明示）
    - ``"pc1-3"``         — local（短縮形、machine_id を埋めて拡張）

    Args:
        raw: ユーザー入力。
        provider_name: ``"github"`` または ``"local"``。
        machine_id: provider=local 時の machine_id。``"153"`` や
            ``"pc1-3"`` を解釈する際に必要。

    Raises:
        ValueError: 入力が空 / 文法不一致 / machine_id 欠落 等。
    """
    if not isinstance(raw, str) or not raw:
        raise ValueError("issue id must be a non-empty string")
    if provider_name not in {"github", "local"}:
        raise ValueError(f"unknown provider: {provider_name!r}")

    # gh:N — provider 非依存のキャッシュ参照
    m = _GH_PREFIX_RE.match(raw)
    if m:
        if provider_name == "github":
            return ResolvedId(kind="github", value=m.group(1), raw=raw)
        return ResolvedId(kind="remote_cache", value=m.group(1), raw=raw)

    # local-<machine>-<n>
    m = _LOCAL_FULL_RE.match(raw)
    if m:
        if provider_name != "local":
            raise ValueError(
                f"local-form id {raw!r} requires provider.type='local' "
                f"(current: {provider_name!r}). "
                f"Use 'gh:N' to read GitHub-cached issues from local mode."
            )
        return ResolvedId(kind="local", value=raw, raw=raw)

    # 数値のみ
    if _NUMERIC_RE.match(raw):
        if provider_name == "github":
            return ResolvedId(kind="github", value=raw, raw=raw)
        # provider=local + 数値 → 自分の machine_id を補完
        if not machine_id:
            raise ValueError(
                f"numeric id {raw!r} under provider.type='local' requires "
                f"provider.local.machine_id to be configured. "
                f"Run 'kaji local init' or set [provider.local] machine_id "
                f"in .kaji/config.local.toml."
            )
        if not _MACHINE_ID_RE.match(machine_id):
            raise ValueError(f"invalid machine_id {machine_id!r}: must match [a-z0-9]{{1,16}}")
        return ResolvedId(kind="local", value=f"local-{machine_id}-{raw}", raw=raw)

    # <machine>-<n> 短縮形
    m = _LOCAL_SHORT_RE.match(raw)
    if m:
        if provider_name != "local":
            raise ValueError(
                f"short-form id {raw!r} requires provider.type='local' (current: {provider_name!r})"
            )
        return ResolvedId(kind="local", value=f"local-{raw}", raw=raw)

    raise ValueError(
        f"invalid issue id {raw!r}: expected one of "
        f"'<number>', 'gh:<number>', 'local-<machine>-<n>', '<machine>-<n>'"
    )
