"""Small tests: ``[watch]`` config section (Issue #543 D1)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kaji_harness.config import KajiConfig, WatchConfig
from kaji_harness.errors import ConfigLoadError

pytestmark = pytest.mark.small

_BASE = (
    '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji-artifacts"\n\n'
    "[execution]\ndefault_timeout = 1800\n\n"
    '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "owner/name"\n'
)


def _write(tmp_path: Path, watch_body: str | None, local_body: str | None = None) -> Path:
    kaji = tmp_path / ".kaji"
    kaji.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
    text = _BASE
    if watch_body is not None:
        # table の外の key（``watch = ...``）は先頭に置かないと直前の table に入る
        text = watch_body + "\n" + text if watch_body.startswith("watch") else text + watch_body
    path = kaji / "config.toml"
    path.write_text(text, encoding="utf-8")
    if local_body is not None:
        (kaji / "config.local.toml").write_text(local_body, encoding="utf-8")
    return path


def test_watch_absent_is_none(tmp_path: Path) -> None:
    assert KajiConfig._load(_write(tmp_path, None)).watch is None


def test_empty_table_enables_watch(tmp_path: Path) -> None:
    config = KajiConfig._load(_write(tmp_path, "\n[watch]\n"))
    assert config.watch == WatchConfig(notify_command=None)


def test_notify_command_is_parsed(tmp_path: Path) -> None:
    config = KajiConfig._load(
        _write(tmp_path, '\n[watch]\nnotify_command = ["scripts/notify.sh", "--x"]\n')
    )
    assert config.watch is not None
    assert config.watch.notify_command == ("scripts/notify.sh", "--x")


def test_watch_not_a_table_is_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigLoadError, match="watch"):
        KajiConfig._load(_write(tmp_path, 'watch = "yes"'))


@pytest.mark.parametrize(
    "body",
    [
        "notify_command = []",
        'notify_command = [""]',
        'notify_command = ["  "]',
        "notify_command = [1]",
        'notify_command = "scripts/notify.sh"',
        "unknown = 1",
    ],
)
def test_invalid_watch_values(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigLoadError, match="watch"):
        KajiConfig._load(_write(tmp_path, f"\n[watch]\n{body}\n"))


def test_overlay_watch_is_ignored_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="kaji.config"):
        config = KajiConfig._load(_write(tmp_path, None, local_body="[watch]\n"))
    assert config.watch is None
    assert any("[watch]" in record.message for record in caplog.records)


def test_overlay_watch_does_not_disable(tmp_path: Path) -> None:
    config = KajiConfig._load(
        _write(tmp_path, "\n[watch]\n", local_body='[watch]\nnotify_command = ["x"]\n')
    )
    assert config.watch == WatchConfig(notify_command=None)
