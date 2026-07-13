"""Deterministic series YAML generation with explicit overwrite control."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import SeriesConfig


def generate_series_yaml(config: SeriesConfig, output: Path, *, update: bool = False) -> Path:
    """Write normalized YAML, refusing implicit replacement of an existing file."""
    if output.exists() and not update:
        raise FileExistsError(f"series file already exists: {output}; pass --update to replace it")
    payload = config.model_dump(mode="json", exclude_none=True)
    content = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    return output
