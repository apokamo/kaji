"""Configuration discovery and loading for kaji_harness.

Discovers .kaji/config.toml by walking up from a start directory.
The directory containing .kaji/ is the repo root.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import ConfigLoadError, ConfigNotFoundError


@dataclass(frozen=True)
class PathsConfig:
    """Path-related configuration."""

    artifacts_dir: str = ""  # Required. Empty string = not set.
    skill_dir: str = ""  # Required. Empty string = not set.
    worktree_prefix: str = ""  # Optional. Empty string = not set (→ "kaji" fallback).


@dataclass(frozen=True)
class ExecutionConfig:
    """Execution-related configuration.

    Issue #288: ``failure_triage`` は default 有効。triage は Issue コメントという
    可視証跡を残すだけで destructive 操作を含まないため。``auto_recover`` は child run
    起動という強い副作用を持つため default 無効（opt-in）。
    """

    default_timeout: int  # Required. No default.
    agent_runner: Literal["headless", "interactive_terminal"] = "headless"
    interactive_terminal_close_on_verdict: bool = True
    failure_triage: bool = True
    auto_recover: bool = False


@dataclass(frozen=True)
class LocalProviderConfig:
    """``[provider.local]`` セクション。"""

    machine_id: str = ""
    default_branch: str = "main"
    git_remote: str = "origin"


@dataclass(frozen=True)
class GitHubProviderConfig:
    """``[provider.github]`` セクション。"""

    repo: str = ""
    default_branch: str = "main"
    git_remote: str = "origin"


@dataclass(frozen=True)
class ProviderConfig:
    """``[provider]`` 設定（Phase 3-c で導入、optional）。

    Phase 3-c では未設定でも動作する（``get_provider`` 側で WARN + github
    fallback）。Phase 3-e で必須化される（fail-fast）。
    """

    type: Literal["github", "local"]
    local: LocalProviderConfig
    github: GitHubProviderConfig


@dataclass(frozen=True)
class KajiConfig:
    """Top-level kaji configuration.

    Attributes:
        provider_overlay_present: ``True`` if the current worktree's
            ``.kaji/config.local.toml`` overlay file exists. ``git worktree add``
            does not copy the gitignored overlay into a new worktree, so a
            feature worktree typically has this ``False``. Used by
            ``provider_overlay_divergence_warning`` to detect a silent
            provider-resolution divergence.
    """

    repo_root: Path
    paths: PathsConfig
    execution: ExecutionConfig
    provider: ProviderConfig | None = None
    provider_overlay_present: bool = False

    @property
    def artifacts_dir(self) -> Path:
        """Absolute path to artifacts directory."""
        expanded = Path(self.paths.artifacts_dir).expanduser()
        if expanded.is_absolute():
            return expanded
        return self.repo_root / self.paths.artifacts_dir

    @classmethod
    def discover(cls, start_dir: Path | None = None) -> KajiConfig:
        """Walk up from start_dir (or CWD) to find .kaji/config.toml."""
        current = (start_dir or Path.cwd()).resolve()
        while True:
            candidate = current / ".kaji" / "config.toml"
            if candidate.is_file():
                return cls._load(candidate)
            parent = current.parent
            if parent == current:
                raise ConfigNotFoundError(start_dir or Path.cwd())
            current = parent

    @classmethod
    def _load(cls, path: Path) -> KajiConfig:
        """Parse a .kaji/config.toml file and build KajiConfig."""
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigLoadError(path, f"invalid TOML: {e}") from e
        paths_data = data.get("paths", {})
        if not isinstance(paths_data, dict):
            raise ConfigLoadError(path, "[paths] must be a table")
        artifacts_raw = paths_data.get("artifacts_dir")
        if artifacts_raw is None or (isinstance(artifacts_raw, str) and not artifacts_raw.strip()):
            raise ConfigLoadError(path, "paths.artifacts_dir is required")
        if not isinstance(artifacts_raw, str):
            raise ConfigLoadError(
                path, f"paths.artifacts_dir must be a string, got {type(artifacts_raw).__name__}"
            )
        cls._validate_artifacts_dir(path, artifacts_raw)
        skill_dir_raw = paths_data.get("skill_dir")
        if skill_dir_raw is None:
            raise ConfigLoadError(path, "paths.skill_dir is required")
        if not isinstance(skill_dir_raw, str):
            raise ConfigLoadError(
                path, f"paths.skill_dir must be a string, got {type(skill_dir_raw).__name__}"
            )
        cls._validate_skill_dir(path, skill_dir_raw)
        wt_prefix_raw = paths_data.get("worktree_prefix", "")
        if not isinstance(wt_prefix_raw, str):
            raise ConfigLoadError(
                path,
                f"paths.worktree_prefix must be a string, got {type(wt_prefix_raw).__name__}",
            )
        cls._validate_worktree_prefix(path, wt_prefix_raw)
        paths = PathsConfig(
            **{k: v for k, v in paths_data.items() if k in PathsConfig.__dataclass_fields__}
        )

        # Read the gitignored ``config.local.toml`` overlay once and share it
        # between [execution] and [provider] parsing (both overlay top-level
        # section keys with the same merge granularity).
        local_overlay_path = path.parent / "config.local.toml"
        overlay_data, overlay_present = cls._read_overlay(local_overlay_path)

        execution = cls._parse_execution(path, data, overlay_data, local_overlay_path)

        repo_root = path.parent.parent
        provider = cls._parse_provider(path, data, overlay_data, local_overlay_path, repo_root)

        return cls(
            repo_root=repo_root,
            paths=paths,
            execution=execution,
            provider=provider,
            provider_overlay_present=overlay_present,
        )

    @staticmethod
    def _read_overlay(local_overlay_path: Path) -> tuple[dict[str, object] | None, bool]:
        """Read ``config.local.toml`` if present.

        Returns:
            ``(overlay_data, overlay_present)``. ``overlay_data`` is the parsed
            TOML table (``None`` when the file does not exist); ``overlay_present``
            mirrors the file's existence regardless of which sections it defines.
        """
        if not local_overlay_path.is_file():
            return None, False
        try:
            with open(local_overlay_path, "rb") as f:
                overlay = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigLoadError(local_overlay_path, f"invalid TOML: {e}") from e
        return overlay, True

    @classmethod
    def _parse_execution(
        cls,
        path: Path,
        data: dict[str, object],
        overlay_data: dict[str, object] | None,
        local_overlay_path: Path,
    ) -> ExecutionConfig:
        """Parse ``[execution]`` (tracked) overlaid with ``config.local.toml``.

        Merge granularity is per-key inside the ``[execution]`` table: a key set
        in the overlay's ``[execution]`` overrides the same key in the tracked
        ``[execution]`` (same precedence as the ``[provider]`` overlay). The
        merged result is then validated.
        """
        execution_data = data.get("execution")
        if execution_data is None or not isinstance(execution_data, dict):
            raise ConfigLoadError(path, "[execution] section is required")

        overlay_execution: dict[str, object] | None = None
        if overlay_data is not None:
            oe = overlay_data.get("execution")
            if oe is not None:
                if not isinstance(oe, dict):
                    raise ConfigLoadError(local_overlay_path, "[execution] must be a table")
                overlay_execution = oe

        merged: dict[str, object] = dict(execution_data)
        if overlay_execution is not None:
            merged.update(overlay_execution)

        def source(key: str) -> Path:
            # Point validation errors at the file that actually defined the key.
            if overlay_execution is not None and key in overlay_execution:
                return local_overlay_path
            return path

        raw_timeout = merged.get("default_timeout")
        if raw_timeout is None:
            raise ConfigLoadError(path, "execution.default_timeout is required")
        if not isinstance(raw_timeout, int) or isinstance(raw_timeout, bool):
            raise ConfigLoadError(
                source("default_timeout"),
                f"execution.default_timeout must be an integer, got {type(raw_timeout).__name__}",
            )
        if raw_timeout <= 0:
            raise ConfigLoadError(
                source("default_timeout"),
                f"execution.default_timeout must be a positive integer, got {raw_timeout}",
            )

        raw_agent_runner = merged.get("agent_runner", "headless")
        if not isinstance(raw_agent_runner, str):
            raise ConfigLoadError(
                source("agent_runner"),
                f"execution.agent_runner must be a string, got {type(raw_agent_runner).__name__}",
            )
        if raw_agent_runner not in {"headless", "interactive_terminal"}:
            raise ConfigLoadError(
                source("agent_runner"),
                "execution.agent_runner must be 'headless' or 'interactive_terminal', "
                f"got {raw_agent_runner!r}",
            )

        raw_close = merged.get("interactive_terminal_close_on_verdict", True)
        if not isinstance(raw_close, bool):
            raise ConfigLoadError(
                source("interactive_terminal_close_on_verdict"),
                "execution.interactive_terminal_close_on_verdict must be a boolean, "
                f"got {type(raw_close).__name__}",
            )

        def parse_bool(key: str, default: bool) -> bool:
            raw = merged.get(key, default)
            if not isinstance(raw, bool):
                raise ConfigLoadError(
                    source(key),
                    f"execution.{key} must be a boolean, got {type(raw).__name__}",
                )
            return raw

        return ExecutionConfig(
            default_timeout=raw_timeout,
            agent_runner=raw_agent_runner,  # type: ignore[arg-type]
            interactive_terminal_close_on_verdict=raw_close,
            failure_triage=parse_bool("failure_triage", True),
            auto_recover=parse_bool("auto_recover", False),
        )

    @staticmethod
    def _parse_provider(
        path: Path,
        data: dict[str, object],
        overlay_data: dict[str, object] | None,
        local_overlay_path: Path,
        repo_root: Path,
    ) -> ProviderConfig | None:
        """Parse the optional ``[provider]`` section + ``config.local.toml`` overlay.

        Phase 3-c: optional. Missing both tracked and overlay → returns ``None``;
        ``get_provider`` issues a WARN and falls back to GitHub. Phase 3-e
        tightens this to fail-fast.

        Overlay rules（phase3c review #4 で拡張）:

        - ``.kaji/config.local.toml`` の ``[provider]`` 全体をマージできる
        - ``type`` / ``[provider.github]`` / ``[provider.local]`` のいずれも
          上書き可能（tracked が ``type=github`` でも overlay で ``type=local``
          に切替えられる）
        - tracked と overlay の双方が無いときのみ ``None`` を返す

        ``overlay_data`` は ``_read_overlay`` が事前に読んだ ``config.local.toml``
        の全 table（不在なら ``None``）。``[execution]`` overlay と同じ file を
        二重読みしないよう呼び出し側で読み込んで渡す。

        Returns:
            解決した ``ProviderConfig``、または tracked / overlay の双方に
            ``[provider]`` が無い場合は ``None``。
        """
        provider_data = data.get("provider")
        if provider_data is not None and not isinstance(provider_data, dict):
            raise ConfigLoadError(path, "[provider] must be a table")

        # ---- overlay 抽出 ----
        overlay_provider: dict[str, object] | None = None
        if overlay_data is not None:
            op = overlay_data.get("provider")
            if op is not None:
                if not isinstance(op, dict):
                    raise ConfigLoadError(local_overlay_path, "[provider] must be a table")
                overlay_provider = op

        if provider_data is None and overlay_provider is None:
            return None

        # ---- tracked + overlay の deep-1 merge ----
        merged: dict[str, object] = {}
        if isinstance(provider_data, dict):
            merged = dict(provider_data)
        if overlay_provider is not None:
            for k, v in overlay_provider.items():
                if k in {"github", "local"} and isinstance(v, dict):
                    base_sub = merged.get(k) or {}
                    if not isinstance(base_sub, dict):
                        base_sub = {}
                    merged[k] = {**base_sub, **v}
                else:
                    merged[k] = v

        # ---- 検証 ----
        ptype_raw = merged.get("type")
        if ptype_raw is None or not isinstance(ptype_raw, str):
            raise ConfigLoadError(path, "provider.type is required (string)")
        ptype = ptype_raw.strip()
        if ptype not in {"github", "local"}:
            raise ConfigLoadError(
                path,
                f"provider.type must be 'github' or 'local', got {ptype!r}",
            )

        github_raw = merged.get("github") or {}
        if not isinstance(github_raw, dict):
            raise ConfigLoadError(path, "[provider.github] must be a table")
        repo_raw = github_raw.get("repo")
        if repo_raw is not None and not isinstance(repo_raw, str):
            raise ConfigLoadError(path, "provider.github.repo must be a string")
        gh_default_branch_raw = github_raw.get("default_branch", "main") or "main"
        if not isinstance(gh_default_branch_raw, str):
            raise ConfigLoadError(path, "provider.github.default_branch must be a string")
        gh_git_remote_raw = github_raw.get("git_remote", "origin") or "origin"
        if not isinstance(gh_git_remote_raw, str):
            raise ConfigLoadError(path, "provider.github.git_remote must be a string")
        github_cfg = GitHubProviderConfig(
            repo=str(repo_raw or ""),
            default_branch=gh_default_branch_raw,
            git_remote=gh_git_remote_raw,
        )

        local_raw = merged.get("local") or {}
        if not isinstance(local_raw, dict):
            raise ConfigLoadError(path, "[provider.local] must be a table")
        machine_id = local_raw.get("machine_id", "") or ""
        if not isinstance(machine_id, str):
            raise ConfigLoadError(path, "provider.local.machine_id must be a string")
        if machine_id:
            # Phase 3-e: 手書き overlay の罠を構造で防ぐ。`type=github` + 空 [provider.local]
            # は default で空文字を残す正常ケースなので non-empty 時のみ検証する。
            from .providers.local import validate_machine_id as _validate_machine_id

            try:
                _validate_machine_id(machine_id)
            except ValueError as exc:
                # 修正先 path は machine_id の出所を優先: overlay が定義していれば
                # overlay path、そうでなければ tracked path。`kaji local init` で
                # 作る overlay を直接編集させたいケースを誤誘導しない。
                overlay_local = (
                    overlay_provider.get("local") if isinstance(overlay_provider, dict) else None
                )
                source_path = (
                    local_overlay_path
                    if isinstance(overlay_local, dict) and "machine_id" in overlay_local
                    else path
                )
                raise ConfigLoadError(
                    source_path,
                    f"provider.local.machine_id {machine_id!r} is invalid: {exc}",
                ) from exc
        default_branch = local_raw.get("default_branch", "main") or "main"
        if not isinstance(default_branch, str):
            raise ConfigLoadError(path, "provider.local.default_branch must be a string")
        local_git_remote_raw = local_raw.get("git_remote", "origin") or "origin"
        if not isinstance(local_git_remote_raw, str):
            raise ConfigLoadError(path, "provider.local.git_remote must be a string")
        local_cfg = LocalProviderConfig(
            machine_id=machine_id,
            default_branch=default_branch,
            git_remote=local_git_remote_raw,
        )

        del repo_root  # reserved for future cross-checks
        return ProviderConfig(
            type=ptype,  # type: ignore[arg-type]
            local=local_cfg,
            github=github_cfg,
        )

    @staticmethod
    def _validate_artifacts_dir(config_path: Path, artifacts_dir: str) -> None:
        """Validate artifacts_dir path."""
        from pathlib import PurePosixPath

        try:
            expanded = Path(artifacts_dir).expanduser()
        except RuntimeError as e:
            raise ConfigLoadError(
                config_path,
                f"paths.artifacts_dir: failed to expand '~': {e}",
            ) from e
        if expanded.is_absolute():
            return  # absolute paths (including ~ expanded) are allowed
        # relative paths: disallow '..' to prevent repo root escape
        p = PurePosixPath(artifacts_dir)
        if ".." in p.parts:
            raise ConfigLoadError(
                config_path,
                f"paths.artifacts_dir must not escape repo root (contains '..'): {artifacts_dir}",
            )

    @staticmethod
    def _validate_skill_dir(config_path: Path, skill_dir: str) -> None:
        """Validate skill_dir path (relative only, no '..' or absolute)."""
        from pathlib import PurePosixPath

        if Path(skill_dir).is_absolute():
            raise ConfigLoadError(
                config_path,
                f"paths.skill_dir must be a relative path, got absolute: {skill_dir}",
            )
        p = PurePosixPath(skill_dir)
        if ".." in p.parts:
            raise ConfigLoadError(
                config_path,
                f"paths.skill_dir must not contain '..': {skill_dir}",
            )

    @staticmethod
    def _validate_worktree_prefix(config_path: Path, worktree_prefix: str) -> None:
        """Validate worktree_prefix: empty = unset; non-empty must be a single safe segment.

        ``worktree_prefix`` は worktree dir 名の先頭 path segment として
        ``f"{prefix}-{branch_prefix}-{issue_id}"`` に展開される（Issue #215）。
        separator / whitespace / ``..`` / absolute を含むと worktree 算出規約を壊す
        ため、単一の安全な segment（``[A-Za-z0-9._-]+``）のみ許可する。
        """
        if not worktree_prefix:
            return  # 未設定（"kaji" fallback）
        if not re.fullmatch(r"[A-Za-z0-9._-]+", worktree_prefix) or worktree_prefix in {".", ".."}:
            raise ConfigLoadError(
                config_path,
                f"paths.worktree_prefix must be a single safe path segment "
                f"([A-Za-z0-9._-], no separators/whitespace/'..'): {worktree_prefix!r}",
            )
