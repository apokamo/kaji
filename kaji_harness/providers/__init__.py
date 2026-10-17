"""Provider abstraction for kaji_harness.

Phase 3-ab で導入。`IssueProvider` Protocol と `IssueContext` を中心に
provider を統一 interface で扱う（現行の実装は GitHub provider のみ）。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import MainWorktreeResolutionError
from ._mappings import DEFAULT_BRANCH_PREFIX, LABEL_TO_PREFIX
from ._worktree import resolve_main_worktree
from .base import IssueProvider
from .github import GitHubProvider
from .models import Comment, Issue, IssueContext, Label, PRContext

if TYPE_CHECKING:
    from ..config import KajiConfig

# package の public API。`_mappings` / `_worktree` は private module のまま維持し、
# package 外が必要とするシンボルだけを本 facade 経由で公開する（ADR 009）。
# `labels_to_branch_prefix` は package 外に消費者がいないため公開しない。
#
# `__all__` の実行時効果は `from kaji_harness.providers import *` の制御に限られ、
# `from kaji_harness.providers._mappings import X` のような named import を禁止する
# 強制力は持たない。境界の強制は tests/test_private_imports.py の fitness test が担う。
__all__ = [
    "DEFAULT_BRANCH_PREFIX",
    "LABEL_TO_PREFIX",
    "Comment",
    "GitHubProvider",
    "Issue",
    "IssueContext",
    "IssueProvider",
    "Label",
    "PRContext",
    "actual_provider_type",
    "get_provider",
    "normalize_id",
    "provider_overlay_divergence_warning",
    "resolve_main_worktree",
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
        ``"github"``。

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
    """
    if config.provider is None:
        raise ValueError(
            "[provider] section is required in .kaji/config.toml.\n\n"
            "You must add the following to your .kaji/config.toml:\n"
            "    [provider]\n"
            '    type = "github"\n\n'
            "    [provider.github]\n"
            '    repo = "<owner>/<repo>"\n\n'
            "See `docs/cli-guides/github-mode.md`."
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
            worktree_prefix=config.paths.worktree_prefix,
            design_dir=config.paths.design_dir,
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
    ``resolve_main_worktree`` が ``MainWorktreeResolutionError`` を送出するが、握り潰して
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
    default_branch = config.provider.github.default_branch
    try:
        main_root = resolve_main_worktree(
            start_dir=config.repo_root,
            default_branch=default_branch,
        )
    except MainWorktreeResolutionError:
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
        "to this worktree. See docs/guides/git-worktree.md."
    )


# Issue 番号は 1 始まり整数。leading zero（``007``）や ``0`` は拒否する。
_POS_INT = r"[1-9]\d*"
_GH_PREFIX_RE = re.compile(rf"^gh:({_POS_INT})$")
_NUMERIC_RE = re.compile(rf"^{_POS_INT}$")


def normalize_id(raw: str) -> str:
    """user 入力の Issue ID を GitHub Issue 番号文字列に正規化する。

    受理する形式は ``"153"`` と ``"gh:153"``（``gh:`` を剥がす）のみ。

    Args:
        raw: ユーザー入力。

    Returns:
        正規化済み Issue 番号（例: ``"153"``）。

    Raises:
        ValueError: 入力が空、または受理形式に一致しない。
    """
    if not isinstance(raw, str) or not raw:
        raise ValueError("issue id must be a non-empty string")
    m = _GH_PREFIX_RE.match(raw)
    if m:
        return m.group(1)
    if _NUMERIC_RE.match(raw):
        return raw
    raise ValueError(f"invalid issue id {raw!r}: expected '<number>' or 'gh:<number>'")
