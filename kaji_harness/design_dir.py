"""設計書 directory（``[paths].design_dir``）の規約と lexical 検証。

foundation 層: ``kaji_harness`` 内部への依存を持たない。``config``（application 層）の
loader 検証と ``providers``（provider 層）の ``build_design_path`` の双方から共有する
（provider → application の import は層方向違反になるため、共有点をここに置く）。
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath

# 設計書 directory の legacy default（``[paths].design_dir`` 未設定時の実効値）。
LEGACY_DESIGN_DIR = "draft/design"

_DESIGN_DIR_SEGMENT_RE = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._-]*")


def validate_design_dir(value: str) -> None:
    """``[paths].design_dir`` の lexical 規則（V2〜V6）を検証する。違反は ``ValueError``。

    ``design_path`` は skill 内の shell command にそのまま展開されるため、絶対 path・
    traversal・空 segment・``.git``・shell metachar / 先頭 ``-`` / ``~`` を構文的に排除する。
    filesystem を参照する検査（symlink escape, V7）は ``KajiConfig`` loader が行う。

    Args:
        value: repository root からの相対 POSIX path（``/`` 区切り）。
    """
    if (
        PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or PureWindowsPath(value).drive
    ):
        raise ValueError(f"design_dir must be a relative path, got absolute: {value!r}")
    for seg in value.split("/"):
        if seg == "":
            raise ValueError(f"design_dir must not contain empty segments: {value!r}")
        if seg in {".", ".."}:
            raise ValueError(f"design_dir must not contain '.' or '..' segments: {value!r}")
        if seg.lower() == ".git":
            raise ValueError(f"design_dir must not contain a '.git' segment: {value!r}")
        if not _DESIGN_DIR_SEGMENT_RE.fullmatch(seg):
            raise ValueError(
                f"design_dir segments must match [A-Za-z0-9._][A-Za-z0-9._-]* "
                f"(no whitespace/shell metacharacters/leading '-'/'~'): {value!r}"
            )
