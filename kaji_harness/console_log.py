"""起動コンソール向け human-readable progress logging の設定（Issue #235）。

``run.log``（``RunLogger`` が書く JSONL 機械可読ログ）とは **別系統** の、
``kaji run`` 起動コンソール向け表示層。stdlib ``logging`` の二ハンドラで
``INFO`` 以下を stdout、``WARNING`` 以上を stderr に振り分ける。

各モジュールは ``logging.getLogger("kaji.<module>")`` を使い、``kaji`` ルート
logger に設定したハンドラへ伝播させる。``RunLogger`` の JSONL 契約には一切
影響しない（``docs/reference/python/logging.md`` の機械可読ログとは責務が異なる）。
"""

from __future__ import annotations

import logging
import sys

# local time。``script_exec`` の relay 行 ``[ts] [step_id] ...`` が
# ``datetime.now()``（local time）を使うため、同一タイムラインに並べるには
# console も local time にそろえる。
ISO_LOCAL = "%Y-%m-%dT%H:%M:%S"

# console progress 用ルート logger 名。``RunLogger`` (``kaji_harness.*``) とは
# 別ツリーにして混線を避ける。
ROOT_LOGGER_NAME = "kaji"


def configure_console_logging(level: int = logging.INFO) -> None:
    """``kaji`` ルート logger に stdout / stderr の二ハンドラを設定する。

    stdout handler は ``levelno < WARNING`` の Filter で INFO 以下のみ通す。
    stderr handler は ``setLevel(WARNING)`` で WARNING 以上のみ通す。冪等：
    再呼び出し時は既存の kaji handler を除去してから張り直すため、ハンドラが
    重複登録されない（テスト / resume で複数回呼ばれても安全）。

    Args:
        level: ルート logger の閾値。default ``logging.INFO``。
    """
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(level)
    root.propagate = False
    # 冪等化: 既存 kaji handler を除去してから張り直す。
    for handler in [h for h in root.handlers if getattr(h, "_kaji", False)]:
        root.removeHandler(handler)

    out = logging.StreamHandler(sys.stdout)
    out.addFilter(lambda record: record.levelno < logging.WARNING)
    out.setFormatter(logging.Formatter("[%(asctime)s] [kaji] %(message)s", ISO_LOCAL))
    out._kaji = True  # type: ignore[attr-defined]

    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.WARNING)
    err.setFormatter(
        logging.Formatter("[%(asctime)s] [kaji] %(levelname)s: %(message)s", ISO_LOCAL)
    )
    err._kaji = True  # type: ignore[attr-defined]

    root.addHandler(out)
    root.addHandler(err)
