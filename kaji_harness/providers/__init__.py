"""Provider abstraction for kaji_harness.

Phase 3-ab で導入。`IssueProvider` Protocol と `IssueContext` を中心に
GitHub / local 両方を統一 interface で扱う。本パッケージは dispatcher
未切替の段階で導入されるため、`cli_main.py` / `prompt.py` への組み込みは
Phase 3-c で行う（design.md L226-244 参照）。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .base import IssueProvider
from .github import GitHubProvider
from .local import LocalProvider
from .models import Comment, Issue, IssueContext, Label

if TYPE_CHECKING:
    from ..config import KajiConfig

__all__ = [
    "Comment",
    "GitHubProvider",
    "Issue",
    "IssueContext",
    "IssueProvider",
    "Label",
    "LocalProvider",
    "ResolvedId",
    "get_provider",
    "normalize_id",
]


_PROVIDER_FALLBACK_WARNED = False


def _emit_provider_fallback_warning() -> None:
    """``[provider]`` 未設定時の WARN を 1 度のみ stderr に出す。

    Phase 3-c の段階。Phase 3-e で fallback は削除される（fail-fast）。
    """
    global _PROVIDER_FALLBACK_WARNED
    if _PROVIDER_FALLBACK_WARNED:
        return
    _PROVIDER_FALLBACK_WARNED = True
    print(
        "WARNING: [provider] section is not configured in .kaji/config.toml. "
        "Falling back to provider.type='github' for backward compatibility. "
        "This fallback will be removed in a future release; add the following "
        "to your .kaji/config.toml:\n"
        "    [provider]\n"
        '    type = "github"\n\n'
        "    [provider.github]\n"
        '    repo = "<owner>/<repo>"\n'
        "For local-first development, run 'kaji local init'.",
        file=sys.stderr,
    )


def get_provider(config: KajiConfig) -> IssueProvider | None:
    """`KajiConfig` から provider インスタンスを構築する。

    Phase 3-c の挙動:

    - ``config.provider`` が ``None`` → WARN を出して ``None`` を返す。
      ``kaji issue`` / ``kaji pr`` の passthrough、`build_prompt` の
      Phase 2-B 互換動作、いずれも ``None`` 受領時は legacy 経路で動く
    - ``provider.type == "github"`` → GitHubProvider
    - ``provider.type == "local"`` → LocalProvider

    Phase 3-e で fallback 経路は削除される（phase3-design.md § 4 ロールアウト）。
    """
    if config.provider is None:
        _emit_provider_fallback_warning()
        return None

    if config.provider.type == "github":
        if not config.provider.github.repo:
            raise ValueError(
                "provider.type='github' requires provider.github.repo (e.g. 'owner/name')."
            )
        return GitHubProvider(repo=config.provider.github.repo, repo_root=config.repo_root)
    if config.provider.type == "local":
        local_cfg = config.provider.local
        if not local_cfg.machine_id:
            raise ValueError(
                "provider.type='local' requires provider.local.machine_id. "
                "Run 'kaji local init' or set machine_id in "
                ".kaji/config.local.toml."
            )
        return LocalProvider(
            repo_root=config.repo_root,
            machine_id=local_cfg.machine_id,
            default_branch=local_cfg.default_branch,
        )
    raise ValueError(f"unknown provider.type: {config.provider.type!r}")


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
