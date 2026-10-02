"""Issue #407: attempt 固有 tmp dir の準備と 4 変数 env の生成。

``build_tmp_env`` は純粋関数（small）、``prepare_attempt_tmp_dir`` は実 FS を使う（medium）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from kaji_harness.errors import TmpDirPreparationError
from kaji_harness.runner import build_tmp_env, prepare_attempt_tmp_dir

_KWARGS = {
    "issue_id": "407",
    "run_id": "261003050715",
    "step_id": "design",
    "attempt_name": "attempt-001",
}


@pytest.mark.small
class TestBuildTmpEnv:
    def test_returns_exactly_four_variables_with_same_value(self, tmp_path: Path) -> None:
        env = build_tmp_env(tmp_path / "x")
        assert list(env) == ["KAJI_TMP_DIR", "TMPDIR", "TMP", "TEMP"]
        assert set(env.values()) == {str(tmp_path / "x")}


@pytest.mark.medium
class TestPrepareAttemptTmpDir:
    def test_creates_normalized_absolute_layout(self, tmp_path: Path) -> None:
        result = prepare_attempt_tmp_dir(tmp_path, **_KWARGS)

        expected = tmp_path.resolve() / "tmp" / "kaji" / "407" / "261003050715" / "design"
        expected = expected / "attempt-001"
        assert result == expected
        assert result.is_absolute()
        assert result.is_dir()
        assert os.path.normpath(result) == str(result)
        assert ".." not in result.parts
        assert "." not in result.parts

    def test_gitignore_created_with_star(self, tmp_path: Path) -> None:
        prepare_attempt_tmp_dir(tmp_path, **_KWARGS)
        assert (tmp_path / "tmp" / "kaji" / ".gitignore").read_text() == "*\n"

    def test_existing_gitignore_is_not_overwritten(self, tmp_path: Path) -> None:
        base = tmp_path / "tmp" / "kaji"
        base.mkdir(parents=True)
        (base / ".gitignore").write_text("custom\n")
        prepare_attempt_tmp_dir(tmp_path, **_KWARGS)
        assert (base / ".gitignore").read_text() == "custom\n"

    def test_different_run_step_attempt_give_different_paths(self, tmp_path: Path) -> None:
        base = prepare_attempt_tmp_dir(tmp_path, **_KWARGS)
        other_attempt = prepare_attempt_tmp_dir(
            tmp_path, **{**_KWARGS, "attempt_name": "attempt-002"}
        )
        other_step = prepare_attempt_tmp_dir(tmp_path, **{**_KWARGS, "step_id": "implement"})
        other_run = prepare_attempt_tmp_dir(tmp_path, **{**_KWARGS, "run_id": "261003050716"})
        assert len({base, other_attempt, other_step, other_run}) == 4

    def test_existing_target_raises(self, tmp_path: Path) -> None:
        prepare_attempt_tmp_dir(tmp_path, **_KWARGS)
        with pytest.raises(TmpDirPreparationError):
            prepare_attempt_tmp_dir(tmp_path, **_KWARGS)

    def test_tmp_is_regular_file_raises(self, tmp_path: Path) -> None:
        (tmp_path / "tmp").write_text("not a dir")
        with pytest.raises(TmpDirPreparationError) as excinfo:
            prepare_attempt_tmp_dir(tmp_path, **_KWARGS)
        assert isinstance(excinfo.value.__cause__, OSError)

    @pytest.mark.parametrize("field", ["issue_id", "run_id", "step_id", "attempt_name"])
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
    def test_invalid_component_rejected_without_side_effects(
        self, tmp_path: Path, field: str, bad: str
    ) -> None:
        with pytest.raises(TmpDirPreparationError):
            prepare_attempt_tmp_dir(tmp_path, **{**_KWARGS, field: bad})
        assert not (tmp_path / "tmp").exists()

    def test_tempfile_api_follows_returned_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = prepare_attempt_tmp_dir(tmp_path, **_KWARGS)
        for key, value in build_tmp_env(target).items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(tempfile, "tempdir", None)
        assert tempfile.gettempdir() == str(target)
