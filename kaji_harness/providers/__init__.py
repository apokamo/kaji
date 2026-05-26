"""Provider abstraction for kaji_harness.

Phase 3-ab で導入。`IssueProvider` Protocol と `IssueContext` を中心に
GitHub / local 両方を統一 interface で扱う。本パッケージは dispatcher
未切替の段階で導入されるため、`cli_main.py` / `prompt.py` への組み込みは
Phase 3-c で行う（design.md L226-244 参照）。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ._worktree import resolve_main_worktree
from .base import IssueProvider
from .github import GitHubProvider
from .local import LocalProvider, LocalProviderError
from .models import Comment, Issue, IssueContext, Label, PRContext

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
    "PRContext",
    "ResolvedId",
    "actual_provider_type",
    "get_provider",
    "normalize_id",
    "provider_overlay_divergence_warning",
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
        ``"github"`` / ``"local"`` のいずれか。

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
            git_remote=config.provider.github.git_remote,
        )
    if config.provider.type == "local":
        local_cfg = config.provider.local
        if not local_cfg.machine_id:
            raise ValueError(
                "provider.type='local' requires provider.local.machine_id. "
                "Run 'kaji local init' or set machine_id in "
                ".kaji/config.local.toml."
            )
        # cwd 起点 discover では feature worktree が repo_root になりうるため、
        # LocalProvider の I/O 正本を ``default_branch`` を checkout している worktree
        # (= main worktree) に固定する。GitHubProvider は外部 API 経由なので
        # repo_root は cwd 起点のまま。
        main_root = resolve_main_worktree(
            start_dir=config.repo_root,
            default_branch=local_cfg.default_branch,
        )
        return LocalProvider(
            repo_root=main_root,
            machine_id=local_cfg.machine_id,
            default_branch=local_cfg.default_branch,
            git_remote=local_cfg.git_remote,
        )
    raise ValueError(f"unknown provider.type: {config.provider.type!r}")


def _read_overlay_provider_type(overlay_path: Path) -> str | None:
    """``config.local.toml`` overlay の ``[provider].type`` を読み出す。

    ファイル不在・読み取り不能・TOML 不正・``[provider]`` テーブル不在・
    ``type`` が文字列でない場合はいずれも ``None`` を返す（best-effort）。

    Args:
        overlay_path: 読み出す ``config.local.toml`` の絶対パス。

    Returns:
        overlay が指定する ``provider.type``。判定不能なら ``None``。
    """
    try:
        with open(overlay_path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    provider = data.get("provider")
    if not isinstance(provider, dict):
        return None
    ptype = provider.get("type")
    if not isinstance(ptype, str):
        return None
    return ptype.strip() or None


def provider_overlay_divergence_warning(config: KajiConfig) -> str | None:
    """worktree 間で provider overlay が継承されない沈黙のズレを検出する。

    ``git worktree add`` は gitignored な ``.kaji/config.local.toml`` overlay を
    新規 worktree にコピーしない。現 worktree に overlay が無く、かつ main worktree
    の overlay が異なる ``provider.type`` を選んでいる場合、provider 解決は tracked
    ``.kaji/config.toml`` の値に**沈黙で**フォールバックする。本関数はそのケースで
    WARN 文言を返し、ズレが無ければ ``None`` を返す。

    検出は best-effort。非 git / git CLI 不在 / main worktree 未構成では
    ``resolve_main_worktree`` が ``LocalProviderError`` を送出するが、握り潰して
    ``None`` を返す（コマンド自体は壊さない）。

    overlay 非継承は ``git worktree add`` 由来の linked worktree でのみ起きる構造
    的問題のため、現 worktree が linked worktree でなければ ``resolve_main_worktree``
    の subprocess を起動せず即 ``None`` を返す（設計「subprocess コストの局所化」）。

    Args:
        config: 現 worktree から discover 済みの ``KajiConfig``。

    Returns:
        ズレ検出時は stderr 向け WARN 文言、無ければ ``None``。
    """
    if config.provider is None:
        return None
    if config.provider_overlay_present:
        # 現 worktree 自身に overlay あり → per-worktree で意図的に異なる
        # provider を使う正当な運用。WARN は出さない。
        return None
    if not (config.repo_root / ".git").is_file():
        # linked worktree（`git worktree add` 由来）では `.git` が gitdir を指す
        # *ファイル*、通常の clone / main worktree では *ディレクトリ*。ファイル
        # でなければ overlay 非継承は構造的に起きないため、`git worktree list`
        # subprocess を起動せず即 None を返す。
        return None
    current_type = config.provider.type
    default_branch: str = getattr(config.provider, current_type).default_branch
    try:
        main_root = resolve_main_worktree(
            start_dir=config.repo_root,
            default_branch=default_branch,
        )
    except LocalProviderError:
        return None
    if main_root == config.repo_root:
        return None  # 自分が main worktree
    main_type = _read_overlay_provider_type(main_root / ".kaji" / "config.local.toml")
    if main_type is None or main_type == current_type:
        return None  # main にも overlay 無し / type 上書き無し / type 一致
    return (
        "WARNING: this worktree has no .kaji/config.local.toml overlay; "
        f"provider.type resolved to {current_type!r} from tracked "
        f".kaji/config.toml, but the main worktree's overlay selects "
        f"{main_type!r}. Copy .kaji/config.local.toml from the main worktree "
        "or run 'kaji local init' here. See docs/guides/git-worktree.md."
    )


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
            remote_cache → 数値文字列（cache JSON 上の番号）。
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
        provider_name: ``"github"`` / ``"local"`` のいずれか。
        machine_id: provider=local 時の machine_id。``"153"`` や
            ``"pc1-3"`` を解釈する際に必要。

    Raises:
        ValueError: 入力が空 / 文法不一致 / machine_id 欠落 等。
    """
    if not isinstance(raw, str) or not raw:
        raise ValueError("issue id must be a non-empty string")
    if provider_name not in {"github", "local"}:
        raise ValueError(f"unknown provider: {provider_name!r}")

    # gh:N — provider=github で github、provider=local で remote_cache
    m = _GH_PREFIX_RE.match(raw)
    if m:
        if provider_name == "github":
            return ResolvedId(kind="github", value=m.group(1), raw=raw)
        # provider_name == "local"
        return ResolvedId(kind="remote_cache", value=m.group(1), raw=raw)

    # local-<machine>-<n>
    m = _LOCAL_FULL_RE.match(raw)
    if m:
        if provider_name != "local":
            raise ValueError(
                f"local-form id {raw!r} requires provider.type='local' "
                f"(current: {provider_name!r}). "
                f"Use 'gh:N' to read cached issues from local mode."
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
