"""Configuration discovery and loading for kaji_harness.

Discovers .kaji/config.toml by walking up from a start directory.
The directory containing .kaji/ is the repo root.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigNotFoundError


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
        with open(path, "rb") as f:
            data = tomllib.load(f)
        paths_data = data.get("paths", {})
        paths = PathsConfig(
            **{k: v for k, v in paths_data.items() if k in PathsConfig.__dataclass_fields__}
        )
        return cls(repo_root=path.parent.parent, paths=paths)
