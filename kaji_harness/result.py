"""Attempt result artifact for kaji_harness.

Issue #222: 各 step attempt の終了情報（``status`` / ``exit_code`` / ``signal`` /
時刻 / ``duration_ms`` / ``session_id``）を attempt 単位の構造化ファイル
``result.json`` として保存する。``verdict.py`` の ``write_verdict_yaml`` と並列の
構造を持つ（attempt path 自体が現在の run / step / attempt を表すため、result.json
にも run_id は保存しない）。

異常終了（143 / SIGTERM / timeout / interruption）でも best-effort で
``exit_code`` / ``signal`` を残せるよう、``derive_signal`` で returncode から
signal 名を導出する。
"""

from __future__ import annotations

import json
import signal as signal_module
from dataclasses import asdict, dataclass
from pathlib import Path

RESULT_FILE = "result.json"


def derive_signal(exit_code: int | None) -> str | None:
    """subprocess の ``returncode`` から終了 signal 名を導出する。

    導出規則（``subprocess.Popen.returncode`` と shell 慣例の両方を許容する）:

    - ``None`` → ``None``（取得不能）。
    - 負値 ``-N`` → signal ``N`` 名（POSIX: signal 終了は負の returncode）。
    - ``> 128`` → signal ``N - 128`` 名（shell 慣例 ``128 + signum``。Claude Code
      CLI が SIGTERM を trap し ``143`` で exit する経路を含む）。
    - clean exit（``0``）/ それ以外の正値 / 未知 signal 番号 → ``None``。

    Args:
        exit_code: subprocess の ``returncode``。取得不能なら ``None``。

    Returns:
        ``"SIGTERM"`` のような signal 名。signal 由来でなければ ``None``。
    """
    if exit_code is None:
        return None
    if exit_code < 0:
        signum = -exit_code
    elif exit_code > 128:
        signum = exit_code - 128
    else:
        return None
    try:
        return signal_module.Signals(signum).name
    except ValueError:
        return None


@dataclass
class AttemptResult:
    """attempt 単位の終了情報（``result.json`` の直列化対象）。

    ``status`` は正常終了では解決済み verdict の status、異常終了では ``"ABORT"``。
    ``exit_code`` / ``signal`` / ``session_id`` / ``error`` は取得不能なら ``None``。

    Issue #288: ``synthetic`` は ABORT record が runner 生成（dispatch / verdict の
    except 経路）か、agent の正規 ABORT verdict かを ``result.json`` 単体で区別する
    ための直交属性。末尾に default 付きで追加するため、旧形式 ``result.json``
    （``synthetic`` キーなし）の読み込みは default ``False`` で互換を保つ。
    """

    step_id: str
    attempt: int
    status: str
    exit_code: int | None
    signal: str | None
    started_at: str
    ended_at: str
    duration_ms: int
    session_id: str | None
    dispatch: str
    error: str | None = None
    synthetic: bool = False


def write_result_json(path: Path, result: AttemptResult) -> None:
    """``AttemptResult`` を pure JSON で書き出す。

    親ディレクトリは必要なら作成する。``write_verdict_yaml`` と同じく attempt path
    配下に保存する前提（run_id / step_id / attempt は path が表す）。

    Args:
        path: 書き込み先（通常 ``attempt-NNN/result.json``）。
        result: 直列化する ``AttemptResult``。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
