"""Issue #407 / #508: attempt 固有 tmp dir の準備と 4 変数 env の生成。

``build_tmp_env`` / ``socket_path_budget_exceeded`` は純粋関数（small）、
``prepare_attempt_tmp_dir`` は実 FS を使う（medium）。

Issue #508: layout は ``<project_root>/tmp/kaji/<issue_id>/<NNNN>/``（Issue 内の通し番号）。
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from kaji_harness.errors import TmpDirPreparationError
from kaji_harness.runner import (
    build_tmp_env,
    prepare_attempt_tmp_dir,
    socket_path_budget_exceeded,
)

_CHROME_SUFFIX = "/com.google.Chrome.guSFOP/SingletonSocket"


@pytest.mark.small
class TestBuildTmpEnv:
    def test_returns_exactly_four_variables_with_same_value(self, tmp_path: Path) -> None:
        env = build_tmp_env(tmp_path / "x")
        assert list(env) == ["KAJI_TMP_DIR", "TMPDIR", "TMP", "TEMP"]
        assert set(env.values()) == {str(tmp_path / "x")}


@pytest.mark.small
class TestSocketPathBudgetExceeded:
    def test_chrome_suffix_length_matches_budget(self) -> None:
        assert len(_CHROME_SUFFIX) == 41

    @pytest.mark.parametrize(
        ("platform", "fits", "exceeds", "limit"),
        [("linux", 66, 67, 107), ("darwin", 62, 63, 103)],
    )
    def test_boundary(self, platform: str, fits: int, exceeds: int, limit: int) -> None:
        assert socket_path_budget_exceeded(Path("/" + "a" * (fits - 1)), platform=platform) is None
        assert socket_path_budget_exceeded(Path("/" + "a" * (exceeds - 1)), platform=platform) == (
            limit + 1,
            limit,
        )

    def test_other_platform_uses_linux_limit(self) -> None:
        assert socket_path_budget_exceeded(Path("/" + "a" * 66), platform="freebsd14") == (
            108,
            107,
        )

    def test_measures_encoded_bytes_not_characters(self) -> None:
        # "あ" は UTF-8 で 3 byte。文字数 23 でも byte 長は 67 になり超過する
        path = Path("/" + "あ" * 22)
        assert len(str(path)) == 23
        assert socket_path_budget_exceeded(path, platform="linux") == (
            len(os.fsencode(str(path))) + 41,
            107,
        )


def _issue_dir(project_root: Path, issue_id: str = "407") -> Path:
    return project_root.resolve() / "tmp" / "kaji" / issue_id


@pytest.mark.medium
class TestPrepareAttemptTmpDir:
    def test_first_and_second_allocation(self, tmp_path: Path) -> None:
        first = prepare_attempt_tmp_dir(tmp_path, issue_id="407")
        second = prepare_attempt_tmp_dir(tmp_path, issue_id="407")

        assert first == _issue_dir(tmp_path) / "0001"
        assert second == _issue_dir(tmp_path) / "0002"
        for result in (first, second):
            assert result.is_absolute()
            assert result.is_dir()
            assert os.path.normpath(result) == str(result)
            assert ".." not in result.parts
            assert "." not in result.parts

    def test_issues_are_numbered_independently(self, tmp_path: Path) -> None:
        prepare_attempt_tmp_dir(tmp_path, issue_id="407")
        assert prepare_attempt_tmp_dir(tmp_path, issue_id="508") == (
            _issue_dir(tmp_path, "508") / "0001"
        )

    def test_next_number_is_max_plus_one(self, tmp_path: Path) -> None:
        issue_dir = _issue_dir(tmp_path)
        (issue_dir / "0001").mkdir(parents=True)
        (issue_dir / "0005").mkdir()
        assert prepare_attempt_tmp_dir(tmp_path, issue_id="407") == issue_dir / "0006"

    def test_legacy_and_unrelated_entries_are_ignored(self, tmp_path: Path) -> None:
        issue_dir = _issue_dir(tmp_path)
        issue_dir.mkdir(parents=True)
        for name in ("261018213458", "261018213458-002", "abc", "123", "1234567"):
            (issue_dir / name).mkdir()
        (issue_dir / "0003").write_text("regular file, not a dir")
        assert prepare_attempt_tmp_dir(tmp_path, issue_id="407") == issue_dir / "0001"

    def test_six_digit_names_count(self, tmp_path: Path) -> None:
        issue_dir = _issue_dir(tmp_path)
        (issue_dir / "123456").mkdir(parents=True)
        assert prepare_attempt_tmp_dir(tmp_path, issue_id="407") == issue_dir / "123457"

    def test_widens_after_9999(self, tmp_path: Path) -> None:
        issue_dir = _issue_dir(tmp_path)
        (issue_dir / "9999").mkdir(parents=True)
        assert prepare_attempt_tmp_dir(tmp_path, issue_id="407") == issue_dir / "10000"

    def test_retries_next_number_on_file_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """並行 ``kaji run`` が同じ番号を先に作った競合を、mkdir 直前の作成で再現する。"""
        issue_dir = _issue_dir(tmp_path)
        original_mkdir = Path.mkdir
        raced: list[Path] = []

        def racing_mkdir(self: Path, *args: Any, **kwargs: Any) -> None:
            if self.parent == issue_dir and not raced:
                raced.append(self)
                original_mkdir(self)
                raise FileExistsError(str(self))
            original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", racing_mkdir)
        result = prepare_attempt_tmp_dir(tmp_path, issue_id="407")

        assert raced == [issue_dir / "0001"]
        assert result == issue_dir / "0002"

    def test_gitignore_created_with_star(self, tmp_path: Path) -> None:
        prepare_attempt_tmp_dir(tmp_path, issue_id="407")
        assert (tmp_path / "tmp" / "kaji" / ".gitignore").read_text() == "*\n"

    def test_existing_gitignore_is_not_overwritten(self, tmp_path: Path) -> None:
        base = tmp_path / "tmp" / "kaji"
        base.mkdir(parents=True)
        (base / ".gitignore").write_text("custom\n")
        prepare_attempt_tmp_dir(tmp_path, issue_id="407")
        assert (base / ".gitignore").read_text() == "custom\n"

    def test_tmp_is_regular_file_raises(self, tmp_path: Path) -> None:
        (tmp_path / "tmp").write_text("not a dir")
        with pytest.raises(TmpDirPreparationError) as excinfo:
            prepare_attempt_tmp_dir(tmp_path, issue_id="407")
        assert isinstance(excinfo.value.__cause__, OSError)

    @pytest.mark.parametrize(
        "bad",
        [
            "../other",
            "../../x",
            "/abs",
            "a/../design",
            "./design",
            "design/.",
            "design//x",
            "",
            ".",
            "..",
            "a/b",
            "bad\x00name",
        ],
    )
    def test_invalid_issue_id_rejected_without_side_effects(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(TmpDirPreparationError):
            prepare_attempt_tmp_dir(tmp_path, issue_id=bad)
        assert not (tmp_path / "tmp").exists()

    def test_tempfile_api_follows_returned_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = prepare_attempt_tmp_dir(tmp_path, issue_id="407")
        for key, value in build_tmp_env(target).items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(tempfile, "tempdir", None)
        assert tempfile.gettempdir() == str(target)


def _root_of_length(base: Path, length: int) -> Path:
    padding = length - len(os.fsencode(str(base))) - 1
    assert padding > 0, f"base {base} is too long for a {length}-byte root"
    root = base / ("r" * padding)
    root.mkdir()
    assert len(os.fsencode(str(root))) == length
    return root


@pytest.mark.medium
@pytest.mark.skipif(sys.platform != "linux", reason="sun_path 108 byte は Linux の値")
class TestChromeSocketRegression:
    """Issue #508 回帰: 47 byte の root なら Chrome 相当の socket を tmp dir 配下に作れる。

    workflow 内の ``tmp_path`` はリポジトリ内の長い path になり、外部 ``TMPDIR`` も長くなりうるため、
    ``TMPDIR`` に依存しない ``short_outside_project_tmp_path`` を起点に root の長さを固定する。
    """

    def test_socket_binds_under_tmp_dir_for_47_byte_root(
        self, short_outside_project_tmp_path: Path
    ) -> None:
        root = _root_of_length(short_outside_project_tmp_path, 47)
        tmp_dir = prepare_attempt_tmp_dir(root, issue_id="1831")
        assert len(os.fsencode(str(tmp_dir))) == 66
        assert socket_path_budget_exceeded(tmp_dir) is None

        socket_path = str(tmp_dir) + _CHROME_SUFFIX
        Path(socket_path).parent.mkdir()
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(socket_path)
        assert Path(socket_path).is_socket()

    def test_48_byte_root_exceeds_budget_and_bind_fails(
        self, short_outside_project_tmp_path: Path
    ) -> None:
        root = _root_of_length(short_outside_project_tmp_path, 48)
        tmp_dir = prepare_attempt_tmp_dir(root, issue_id="1831")
        assert socket_path_budget_exceeded(tmp_dir) == (108, 107)

        socket_path = str(tmp_dir) + _CHROME_SUFFIX
        Path(socket_path).parent.mkdir()
        with socket.socket(socket.AF_UNIX) as sock, pytest.raises(OSError):
            sock.bind(socket_path)
