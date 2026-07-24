"""Large-local: 実 `gh --version` binary に対する parse 前提の検証（Issue #372）。

ネットワーク疎通は不要。`shutil.which("gh") is None` なら skip する。
`gh_version_probe` marker を module 単位で宣言し、conftest の autouse stub
(`_stub_gh_version`) を opt-out する。これが無いと `_detect_gh_version` が
固定値に置換され、実 binary を 1 度も起動しないまま Green になり、本 file の
存在意義が消える。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from kaji_harness.providers.github import _MIN_GH_VERSION, GitHubProvider

pytestmark = [
    pytest.mark.large,
    pytest.mark.large_local,
    pytest.mark.gh_version_probe,
]


@pytest.fixture
def provider(tmp_path: Path) -> GitHubProvider:
    return GitHubProvider(repo="owner/name", repo_root=tmp_path / "main")


@pytest.mark.skipif(shutil.which("gh") is None, reason="gh CLI not on PATH")
class TestRealGhVersion:
    def test_detect_matches_independent_probe(self, provider: GitHubProvider) -> None:
        proc = subprocess.run(["gh", "--version"], check=False, capture_output=True, text=True)
        first_line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
        independent_version = tuple(
            int(part) for part in first_line.removeprefix("gh version ").split(" ")[0].split(".")
        )
        detected = provider._detect_gh_version()
        assert detected == independent_version

    def test_detect_returns_int_tuple(self, provider: GitHubProvider) -> None:
        detected = provider._detect_gh_version()
        assert detected is not None
        assert len(detected) == 3
        assert all(isinstance(part, int) for part in detected)

    def test_ensure_version_does_not_raise_when_supported(self, provider: GitHubProvider) -> None:
        detected = provider._detect_gh_version()
        assert detected is not None
        if detected < _MIN_GH_VERSION:
            pytest.skip(f"local gh {detected} is older than required {_MIN_GH_VERSION}")
        provider._ensure_gh_version()
