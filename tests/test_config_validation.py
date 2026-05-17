"""Phase 3-e: `_parse_provider` で `validate_machine_id` を呼ぶことを検証する。

config load 時点で invalid machine_id を `ConfigLoadError` で停止し、
`kaji issue` / `kaji run` 段で初めて発覚する trace の暗黒化を防ぐ。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaji_harness.config import KajiConfig
from kaji_harness.errors import ConfigLoadError


def _write_repo(
    tmp_path: Path,
    *,
    tracked_provider: str = "",
    overlay_provider: str = "",
) -> Path:
    repo = tmp_path / "repo"
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir(parents=True)
    (kaji_dir / "config.toml").write_text(
        "[paths]\n"
        'artifacts_dir = ".kaji-artifacts"\n'
        'skill_dir = ".claude/skills"\n'
        "\n"
        "[execution]\n"
        "default_timeout = 1800\n" + tracked_provider
    )
    if overlay_provider:
        (kaji_dir / "config.local.toml").write_text(overlay_provider)
    return repo


@pytest.mark.medium
class TestMachineIdConfigValidation:
    def test_valid_machine_id_accepted(self, tmp_path: Path) -> None:
        repo = _write_repo(
            tmp_path,
            tracked_provider=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
            ),
        )
        cfg = KajiConfig.discover(start_dir=repo)
        assert cfg.provider is not None
        assert cfg.provider.local.machine_id == "pc1"

    def test_uppercase_machine_id_rejected_at_config_load(self, tmp_path: Path) -> None:
        repo = _write_repo(
            tmp_path,
            tracked_provider=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "PC1"\n'
            ),
        )
        with pytest.raises(ConfigLoadError, match="machine_id"):
            KajiConfig.discover(start_dir=repo)

    def test_hyphen_machine_id_rejected_at_config_load(self, tmp_path: Path) -> None:
        repo = _write_repo(
            tmp_path,
            tracked_provider=(
                '\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc-1"\n'
            ),
        )
        with pytest.raises(ConfigLoadError, match="machine_id"):
            KajiConfig.discover(start_dir=repo)

    def test_too_long_machine_id_rejected_at_config_load(self, tmp_path: Path) -> None:
        long_id = "a" * 17
        repo = _write_repo(
            tmp_path,
            tracked_provider=(
                f'\n[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "{long_id}"\n'
            ),
        )
        with pytest.raises(ConfigLoadError, match="machine_id"):
            KajiConfig.discover(start_dir=repo)

    def test_overlay_invalid_machine_id_rejected_with_overlay_path(self, tmp_path: Path) -> None:
        """overlay 由来の違反は overlay ファイルを修正先として指す（実運用導線）。"""
        repo = _write_repo(
            tmp_path,
            tracked_provider='\n[provider]\ntype = "local"\n',
            overlay_provider='[provider.local]\nmachine_id = "PC1"\n',
        )
        with pytest.raises(ConfigLoadError, match="machine_id") as exc_info:
            KajiConfig.discover(start_dir=repo)
        # ConfigLoadError.path が overlay (config.local.toml) を指していること
        assert exc_info.value.path.name == "config.local.toml"

    def test_empty_machine_id_accepted_for_github_provider(self, tmp_path: Path) -> None:
        # type=github + 空 [provider.local] が default ケース。validation は走らない。
        repo = _write_repo(
            tmp_path,
            tracked_provider=(
                '\n[provider]\ntype = "github"\n\n[provider.github]\nrepo = "owner/name"\n'
            ),
        )
        cfg = KajiConfig.discover(start_dir=repo)
        assert cfg.provider is not None
        assert cfg.provider.local.machine_id == ""
