"""Provider abstraction for kaji_harness.

Phase 3-ab で導入。`IssueProvider` Protocol と `IssueContext` を中心に
GitHub / local 両方を統一 interface で扱う。本パッケージは dispatcher
未切替の段階で導入されるため、`cli_main.py` / `prompt.py` への組み込みは
Phase 3-c で行う（design.md L226-244 参照）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .base import IssueProvider
from .github import GitHubProvider
from .gitlab import GitLabProvider
from .local import LocalProvider
from .models import Comment, Issue, IssueContext, Label, PRContext

if TYPE_CHECKING:
    from ..config import KajiConfig

__all__ = [
    "Comment",
    "GitHubProvider",
    "GitLabProvider",
    "Issue",
    "IssueContext",
    "IssueProvider",
    "Label",
    "LocalProvider",
    "PRContext",
    "ResolvedId",
    "actual_provider_type",
    "get_provider",
    "normalize_id",
]


def actual_provider_type(config: KajiConfig) -> str:
    """``get_provider(config)`` 成功後に ``provider.type`` を取り出す helper。

    Phase 3-e 以降、``get_provider(config)`` が成功すれば ``config.provider``
    は必ず非 ``None``。本 helper は型 narrowing と「provider 確定後に呼ぶ」
    契約を呼出側に強制する役割を持つ。

    Args:
        config: ``KajiConfig`` インスタンス。``get_provider()`` で事前に
            検証済みであることが前提。

    Returns:
        ``"github"`` / ``"local"`` / ``"gitlab"`` のいずれか。

    Raises:
        ValueError: ``config.provider`` が ``None``（``get_provider()`` を
            経由せず本 helper を呼んだ場合の防御的ガード）。
    """
    if config.provider is None:
        raise ValueError(
            "actual_provider_type() called before get_provider(); config.provider is None"
        )
    return config.provider.type


def get_provider(config: KajiConfig) -> IssueProvider:
    """`KajiConfig` から provider インスタンスを構築する。

    Phase 3-e で fallback 経路を削除。``config.provider`` が ``None`` であれば
    ``ValueError`` を raise し、呼出側に `[provider]` セクションを必須として
    要求する。

    - ``provider.type == "github"`` → GitHubProvider
    - ``provider.type == "local"`` → LocalProvider
    - ``provider.type == "gitlab"`` → GitLabProvider
    """
    if config.provider is None:
        raise ValueError(
            "[provider] section is required in .kaji/config.toml.\n\n"
            "You must add the following to your .kaji/config.toml:\n"
            "    [provider]\n"
            '    type = "github"\n\n'
            "    [provider.github]\n"
            '    repo = "<owner>/<repo>"\n\n'
            "For GitHub-independent (local-first) development, run "
            "`kaji local init`.\n"
            "See `docs/cli-guides/local-mode.md`."
        )

    if config.provider.type == "github":
        if not config.provider.github.repo:
            raise ValueError(
                "provider.type='github' requires provider.github.repo (e.g. 'owner/name')."
            )
        return GitHubProvider(
            repo=config.provider.github.repo,
            repo_root=config.repo_root,
            default_branch=config.provider.github.default_branch,
        )
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
    if config.provider.type == "gitlab":
        if not config.provider.gitlab.repo:
            raise ValueError(
                "provider.type='gitlab' requires provider.gitlab.repo (e.g. 'group/project')."
            )
        return GitLabProvider(
            repo=config.provider.gitlab.repo,
            repo_root=config.repo_root,
            default_branch=config.provider.gitlab.default_branch,
        )
    raise ValueError(f"unknown provider.type: {config.provider.type!r}")


ResolvedKind = Literal["github", "local", "remote_cache", "gitlab"]


@dataclass(frozen=True)
class ResolvedId:
    """正規化済み Issue ID。

    Attributes:
        kind: ID の所属。``github`` は active な GitHub provider 経路、
            ``local`` は LocalProvider 経路、``gitlab`` は active な
            GitLab provider 経路、``remote_cache`` は
            ``provider=local`` 配下で ``gh:N`` / ``gl:N`` として読み出される
            キャッシュ参照（read-only）。
        value: provider 内部表現での ID 文字列。
            github → 数値文字列（``"153"``）、
            local → ``local-<machine>-<n>``（``"local-pc1-3"``）、
            gitlab → 数値文字列（``"42"``、project-local IID）、
            remote_cache → 数値文字列（cache JSON 上の番号）。
        raw: 入力された原文字列（デバッグ・エラーメッセージ用）。
    """

    kind: ResolvedKind
    value: str
    raw: str


# Issue 番号は 1 始まり整数。leading zero（``007``）や ``0`` は拒否する。
_POS_INT = r"[1-9]\d*"
_GH_PREFIX_RE = re.compile(rf"^gh:({_POS_INT})$")
_GL_PREFIX_RE = re.compile(rf"^gl:({_POS_INT})$")
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

    - ``"153"``           — provider に応じて github / local / gitlab どれにも解釈
    - ``"gh:153"``        — provider=local 配下では remote_cache、
                            provider=github では github（``gh:`` を剥がす）、
                            provider=gitlab では cross-provider 参照不可で reject
    - ``"gl:42"``         — provider=local 配下では remote_cache、
                            provider=gitlab では gitlab（``gl:`` を剥がす）、
                            provider=github では cross-provider 参照不可で reject
    - ``"local-pc1-3"``   — local（machine_id まで明示）
    - ``"pc1-3"``         — local（短縮形、machine_id を埋めて拡張）

    Args:
        raw: ユーザー入力。
        provider_name: ``"github"`` / ``"local"`` / ``"gitlab"`` のいずれか。
        machine_id: provider=local 時の machine_id。``"153"`` や
            ``"pc1-3"`` を解釈する際に必要。

    Raises:
        ValueError: 入力が空 / 文法不一致 / machine_id 欠落 / cross-provider
            参照（``gh:N`` × gitlab / ``gl:N`` × github）等。
    """
    if not isinstance(raw, str) or not raw:
        raise ValueError("issue id must be a non-empty string")
    if provider_name not in {"github", "local", "gitlab"}:
        raise ValueError(f"unknown provider: {provider_name!r}")

    # gl:N — provider=gitlab で gitlab、provider=local で remote_cache、それ以外 reject
    m = _GL_PREFIX_RE.match(raw)
    if m:
        if provider_name == "gitlab":
            return ResolvedId(kind="gitlab", value=m.group(1), raw=raw)
        if provider_name == "local":
            return ResolvedId(kind="remote_cache", value=m.group(1), raw=raw)
        raise ValueError(
            f"invalid issue id {raw!r}: 'gl:N' requires provider.type in "
            f"{{'gitlab', 'local'}} (current: {provider_name!r})."
        )

    # gh:N — provider=github で github、provider=local で remote_cache、provider=gitlab は reject
    m = _GH_PREFIX_RE.match(raw)
    if m:
        if provider_name == "github":
            return ResolvedId(kind="github", value=m.group(1), raw=raw)
        if provider_name == "local":
            return ResolvedId(kind="remote_cache", value=m.group(1), raw=raw)
        raise ValueError(
            f"invalid issue id {raw!r}: 'gh:N' requires provider.type in "
            f"{{'github', 'local'}} (current: {provider_name!r})."
        )

    # local-<machine>-<n>
    m = _LOCAL_FULL_RE.match(raw)
    if m:
        if provider_name != "local":
            raise ValueError(
                f"local-form id {raw!r} requires provider.type='local' "
                f"(current: {provider_name!r}). "
                f"Use 'gh:N' / 'gl:N' to read cached issues from local mode."
            )
        return ResolvedId(kind="local", value=raw, raw=raw)

    # 数値のみ
    if _NUMERIC_RE.match(raw):
        if provider_name == "github":
            return ResolvedId(kind="github", value=raw, raw=raw)
        if provider_name == "gitlab":
            return ResolvedId(kind="gitlab", value=raw, raw=raw)
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
        f"'<number>', 'gh:<number>', 'gl:<number>', 'local-<machine>-<n>', '<machine>-<n>'"
    )
