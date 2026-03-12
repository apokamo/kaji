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

    artifacts_dir: str = ".kaji-artifacts"


@dataclass(frozen=True)
class KajiConfig:
    """Top-level kaji configuration."""

    repo_root: Path
    paths: PathsConfig

    @property
    def artifacts_dir(self) -> Path:
        """Absolute path to artifacts directory."""
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
        return cls(repo_root=path.parent.parent, paths=paths)

    @staticmethod
    def _validate_artifacts_dir(config_path: Path, artifacts_dir: str) -> None:
        """Validate artifacts_dir is a relative path within repo root."""
        from pathlib import PurePosixPath

        p = PurePosixPath(artifacts_dir)
        if p.is_absolute():
            raise ConfigLoadError(
                config_path,
                f"paths.artifacts_dir must be a relative path, got absolute: {artifacts_dir}",
            )
        if ".." in p.parts:
            raise ConfigLoadError(
                config_path,
                f"paths.artifacts_dir must not escape repo root (contains '..'): {artifacts_dir}",
            )
