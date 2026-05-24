"""Artifacts directory resolution for ``kaji run``.

Issue #177: ``KajiConfig.artifacts_dir`` は ``repo_root`` 基準で相対パスを解決する
ため、feature worktree 内で ``kaji run`` すると artifacts が feature worktree 配下
に書かれ、``git worktree remove`` でログが消失する。本モジュールは ``kaji run``
専用の helper として main worktree 基準で artifacts path を解決する。

``KajiConfig.discover()`` には副作用を入れず、``cli_main._cmd_run`` のみが
``resolve_artifacts_dir(config)`` を経由する設計（非-run callsite で
``git worktree list --porcelain`` の subprocess を起動しないため）。
"""

from __future__ import annotations

from pathlib import Path

from .config import KajiConfig


def resolve_artifacts_dir(config: KajiConfig) -> Path:
    """``kaji run`` の artifacts 書き込み先を main worktree 基準で解決する。

    挙動:

    - ``paths.artifacts_dir`` が絶対パス / ``~`` 展開後絶対パス → そのまま返す
      （PR #102 互換）
    - 相対パス + main worktree 解決成功 → ``<main_worktree>/<artifacts_dir>``
    - 相対パス + main worktree 解決失敗（非 git / ``default_branch`` 未 checkout /
      git CLI 不在 / ``provider`` 未設定）→ legacy fallback として
      ``config.artifacts_dir`` を返す（= ``repo_root / 相対パス``）
    """
    expanded = Path(config.paths.artifacts_dir).expanduser()
    if expanded.is_absolute():
        return expanded
    main_root = _try_resolve_main_worktree(config)
    if main_root is None:
        return config.artifacts_dir
    return main_root / config.paths.artifacts_dir


def _try_resolve_main_worktree(config: KajiConfig) -> Path | None:
    """provider 情報から main worktree を best-effort 解決する。失敗時 ``None``。

    ``provider_overlay_divergence_warning`` (gl:28) と同じ best-effort パターン:
    git CLI 不在 / ``default_branch`` 未 checkout / ``provider is None`` のいずれも
    raise せず ``None`` を返す。
    """
    if config.provider is None:
        return None
    default_branch: str = getattr(config.provider, config.provider.type).default_branch
    from .providers._worktree import resolve_main_worktree
    from .providers.local import LocalProviderError

    try:
        return resolve_main_worktree(
            start_dir=config.repo_root,
            default_branch=default_branch,
        )
    except LocalProviderError:
        return None
