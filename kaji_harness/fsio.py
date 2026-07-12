"""atomic な filesystem 書き込み helper (foundation 層)。

`providers.local`（Issue ファイル / コメントファイルの書き込み）と `sync`
（GitHub cache の書き込み）の双方が使う。kaji 固有の概念を含まない純粋な
filesystem utility であり、`kaji_harness` 内部への import を一切持たない。

Issue #285: 以前は `providers/local.py` の private 関数だったため
`sync.py -> providers.local._atomic_write` という package 境界を越える
private import が生じていた。foundation 層へ切り出して両者が下位層を見る形に
是正した（ADR 009）。
"""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    """``*.tmp`` → ``os.replace`` による atomic な text 書き込み。

    部分書き込みが残らないため、git の add/commit 段で中間状態を取り込まない
    （phase3-design.md § Issue ファイル / コメントファイルの atomic 書き込み）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_new(path: Path, content: str) -> None:
    """``O_CREAT | O_EXCL`` で新規ファイルとして atomic に書き込む。

    既存ファイルがある場合は ``FileExistsError`` を投げる。``path.open("x")`` は
    buffering / kill 時の 0 byte file 懸念があるため、``os.open`` で fd を作って
    bytes を loop で書ききる（phase3d-preflight-design § 5）。

    POSIX ``write(2)`` は short write を許す契約なので、返り値が要求 byte 数より
    少ない場合に備えて残バイトを再 write する。``n <= 0`` は通常起きないが、
    EINTR を裸で晒さないため最低限の defensive な扱いとする。

    既存 ``atomic_write()`` は edit / close 等の上書き用として残す。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(path, flags, 0o644)
    try:
        data = content.encode("utf-8")
        written = 0
        while written < len(data):
            n = os.write(fd, data[written:])
            if n <= 0:
                raise OSError(f"os.write returned non-positive count {n}")
            written += n
    finally:
        os.close(fd)
