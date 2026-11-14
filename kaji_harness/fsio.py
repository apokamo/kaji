"""atomic な filesystem 書き込み helper (foundation 層)。

baseline artifact / review-poll 証跡 / series 状態などの書き込みが使う。kaji 固有の
概念を含まない純粋な filesystem utility であり、`kaji_harness` 内部への import を
一切持たない。

Issue #285: 以前は provider 実装の private 関数だったため package 境界を越える
private import が生じていた。foundation 層へ切り出して利用者が下位層を見る形に
是正した（ADR 009）。
"""

from __future__ import annotations

import contextlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PublishOutcome:
    """``atomic_publish_new`` の結果。

    Attributes:
        path: 公開したファイルのパス。
        cleanup_error: 公開確定（``os.link`` 成功）後に一時ファイルを削除できなかった場合の
            ``OSError``。公開自体は有効なため送出せずにここへ入れる。
    """

    path: Path
    cleanup_error: OSError | None = None


def _write_all(fd: int, data: bytes) -> None:
    """short write に備えて ``data`` を書ききる。"""
    written = 0
    while written < len(data):
        n = os.write(fd, data[written:])
        if n <= 0:
            raise OSError(f"os.write returned non-positive count {n}")
        written += n


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


_TEMP_NAME_ATTEMPTS = 100


def _create_unique_temp(path: Path) -> tuple[int, str]:
    """``path`` と同じディレクトリに一意名の一時ファイルを新規作成し、fd と名前を返す。

    ``O_CREAT | O_EXCL`` と mode ``0o644`` で作るため、権限には呼び出し元の umask が
    そのまま適用される（``atomic_write`` / ``atomic_write_new`` と同じ）。作成後に
    ``fchmod`` で権限を設定し直すと umask が効かず、``umask 077`` の利用者の内容を
    他ユーザーへ読める状態で公開してしまうため行わない（Issue #568 MF1）。
    ``O_EXCL`` はシンボリックリンクを含む既存エントリで失敗するので、名前の衝突時だけ
    別の名前で作り直す。
    """
    for _ in range(_TEMP_NAME_ATTEMPTS):
        tmp_name = str(path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            fd = os.open(tmp_name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        return fd, tmp_name
    # FileExistsError にすると呼び出し側が「公開先が既にある」と誤認するため、素の OSError にする
    raise OSError(f"could not create a unique temporary file next to {path}")


def atomic_publish_new(path: Path, content: str) -> PublishOutcome:
    """完成した内容を、上書きせず 1 回の操作で ``path`` に公開する。

    同じディレクトリに一意名の一時ファイル（``.<name>.<random>.tmp``、権限は umask に従う）を
    作り、write → fsync → ``os.link(tmp, path)`` → 一時ファイルの unlink の順に行う。``os.link`` は宛先が
    既にあれば ``EEXIST`` で失敗し、成功すると書き込み済みの inode が一度に見える（POSIX
    ``link(2)``）。そのため途中状態は外から見えず、既存ファイルも上書きしない。

    ``os.link`` の成功を公開の確定点とする。link 前の失敗では一時ファイルを削除して例外を
    送出し、``path`` は作らない。link 後の一時ファイル削除の失敗は送出せず
    ``PublishOutcome.cleanup_error`` に入れて返す（公開済みの ``path`` には触れない）。

    Args:
        path: 公開先。親ディレクトリは存在している必要がある。
        content: UTF-8 で書き込む完成済みの内容。

    Returns:
        公開結果。

    Raises:
        FileExistsError: ``path`` が既に存在する。
        OSError: link 前の一時ファイル作成・書き込み・fsync・link に失敗した（hard link を
            作れないファイルシステムを含む）。
    """
    fd, tmp_name = _create_unique_temp(path)
    try:
        try:
            _write_all(fd, content.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.link(tmp_name, path)
    except BaseException:
        # 公開前の失敗。元の例外を優先し、一時ファイルの削除失敗では置き換えない
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    try:
        os.unlink(tmp_name)
    except OSError as exc:
        return PublishOutcome(path=path, cleanup_error=exc)
    return PublishOutcome(path=path)
