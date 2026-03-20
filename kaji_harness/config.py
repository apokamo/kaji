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

    artifacts_dir: str = "~/.kaji/artifacts"


@dataclass(frozen=True)
class ExecutionConfig:
    """Execution-related configuration."""

    default_timeout: int  # Required. No default.


@dataclass(frozen=True)
class KajiConfig:
    """Top-level kaji configuration."""

    repo_root: Path
    paths: PathsConfig
    execution: ExecutionConfig

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
        artifacts_raw = paths_data.get("artifacts_dir", PathsConfig.artifacts_dir)
        if not isinstance(artifacts_raw, str):
            raise ConfigLoadError(
                path, f"paths.artifacts_dir must be a string, got {type(artifacts_raw).__name__}"
            )
        cls._validate_artifacts_dir(path, artifacts_raw)
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

        return cls(repo_root=path.parent.parent, paths=paths, execution=execution)

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
