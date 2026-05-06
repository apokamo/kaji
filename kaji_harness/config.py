"""Configuration discovery and loading for kaji_harness.

Discovers .kaji/config.toml by walking up from a start directory.
The directory containing .kaji/ is the repo root.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigLoadError, ConfigNotFoundError


@dataclass(frozen=True)
class PathsConfig:
    """Path-related configuration."""

    artifacts_dir: str = ""  # Required. Empty string = not set.
    skill_dir: str = ""  # Required. Empty string = not set.


@dataclass(frozen=True)
class ExecutionConfig:
    """Execution-related configuration."""

    default_timeout: int  # Required. No default.


@dataclass(frozen=True)
class LocalProviderConfig:
    """``[provider.local]`` セクション。"""

    machine_id: str = ""
    default_branch: str = "main"


@dataclass(frozen=True)
class GitHubProviderConfig:
    """``[provider.github]`` セクション。"""

    repo: str = ""
    default_branch: str = "main"


@dataclass(frozen=True)
class ProviderConfig:
    """``[provider]`` 設定（Phase 3-c で導入、optional）。

    Phase 3-c では未設定でも動作する（``get_provider`` 側で WARN + github
    fallback）。Phase 3-e で必須化される（fail-fast）。
    """

    type: str  # "github" or "local"
    local: LocalProviderConfig
    github: GitHubProviderConfig


@dataclass(frozen=True)
class KajiConfig:
    """Top-level kaji configuration."""

    repo_root: Path
    paths: PathsConfig
    execution: ExecutionConfig
    provider: ProviderConfig | None = None

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
        paths = PathsConfig(
            **{k: v for k, v in paths_data.items() if k in PathsConfig.__dataclass_fields__}
        )

        # Parse [execution] section (required)
        execution_data = data.get("execution")
        if execution_data is None or not isinstance(execution_data, dict):
            raise ConfigLoadError(path, "[execution] section is required")
        raw_timeout = execution_data.get("default_timeout")
        if raw_timeout is None:
            raise ConfigLoadError(path, "execution.default_timeout is required")
        if not isinstance(raw_timeout, int) or isinstance(raw_timeout, bool):
            raise ConfigLoadError(
                path,
                f"execution.default_timeout must be an integer, got {type(raw_timeout).__name__}",
            )
        if raw_timeout <= 0:
            raise ConfigLoadError(
                path,
                f"execution.default_timeout must be a positive integer, got {raw_timeout}",
            )
        execution = ExecutionConfig(default_timeout=raw_timeout)

        repo_root = path.parent.parent
        provider = cls._parse_provider(path, data, repo_root)

        return cls(repo_root=repo_root, paths=paths, execution=execution, provider=provider)

    @staticmethod
    def _parse_provider(
        path: Path,
        data: dict[str, object],
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
        """
        provider_data = data.get("provider")
        if provider_data is not None and not isinstance(provider_data, dict):
            raise ConfigLoadError(path, "[provider] must be a table")

        # ---- overlay 読み込み ----
        local_overlay_path = path.parent / "config.local.toml"
        overlay_provider: dict[str, object] | None = None
        if local_overlay_path.is_file():
            try:
                with open(local_overlay_path, "rb") as f:
                    overlay = tomllib.load(f)
            except tomllib.TOMLDecodeError as e:
                raise ConfigLoadError(local_overlay_path, f"invalid TOML: {e}") from e
            op = overlay.get("provider")
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
            raise ConfigLoadError(path, f"provider.type must be 'github' or 'local', got {ptype!r}")

        github_raw = merged.get("github") or {}
        if not isinstance(github_raw, dict):
            raise ConfigLoadError(path, "[provider.github] must be a table")
        repo_raw = github_raw.get("repo")
        if repo_raw is not None and not isinstance(repo_raw, str):
            raise ConfigLoadError(path, "provider.github.repo must be a string")
        gh_default_branch_raw = github_raw.get("default_branch", "main") or "main"
        if not isinstance(gh_default_branch_raw, str):
            raise ConfigLoadError(path, "provider.github.default_branch must be a string")
        github_cfg = GitHubProviderConfig(
            repo=str(repo_raw or ""),
            default_branch=gh_default_branch_raw,
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
        local_cfg = LocalProviderConfig(machine_id=machine_id, default_branch=default_branch)

        del repo_root  # reserved for future cross-checks
        return ProviderConfig(type=ptype, local=local_cfg, github=github_cfg)

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
