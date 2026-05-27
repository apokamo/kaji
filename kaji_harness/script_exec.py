"""Deterministic script dispatch for kaji_harness.

`exec_script` を持つ skill を ``python -m <module>`` として subprocess 実行する。
LLM agent を介さない決定論的 dispatch 経路の核。

- subprocess は ``shell=False`` で呼び、``exec_script`` 値は呼び出し側で
  Python identifier 正規表現により validate 済みである前提（``skill.py``）。
- exit code != 0 は ``ScriptExecutionError`` で fail-loud。stdout の verdict
  有無を問わない（決定論性確保のため、Issue #204 § exit code と verdict の
  優先順位の正本契約）。
- stdout は line-buffered で ``stdout.log`` / ``console.log`` に書き出しつつ
  ``CLIResult.full_output`` に蓄積する（既存 ``execute_cli`` と同方針）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from .errors import CLINotFoundError, ScriptExecutionError, StepTimeoutError
from .models import CLIResult, Step


def _now_stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def execute_script(
    *,
    step: Step,
    module: str,
    env: Mapping[str, str],
    workdir: Path,
    log_dir: Path,
    timeout: int,
    verbose: bool = True,
) -> CLIResult:
    """``python -m <module>`` を subprocess 実行し ``CLIResult`` を返す。

    Args:
        step: 実行対象 step（id をログラベルに使用）
        module: ``python -m`` の引数。Python identifier dotted path を前提とする
        env: context 追加 env（``os.environ`` に merge される）
        workdir: subprocess の cwd
        log_dir: ``stdout.log`` / ``console.log`` / ``stderr.log`` の出力先
        timeout: 秒単位の hard timeout
        verbose: ``True`` で stdout を逐次 console に echo する

    Raises:
        CLINotFoundError: ``python`` 実行体が見つからない（通常は到達不能）
        StepTimeoutError: timeout 超過
        ScriptExecutionError: subprocess が non-zero exit（stdout の verdict 有無
            を問わない）
    """
    args = [sys.executable, "-m", module]
    full_env = {**os.environ, **env}

    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(workdir),
            env=full_env,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise CLINotFoundError(f"python '{args[0]}' not found") from exc

    timed_out = threading.Event()

    def _kill() -> None:
        timed_out.set()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    timer = threading.Timer(timeout, _kill)
    timer.start()

    texts: list[str] = []
    stderr_chunks: list[str] = []

    def _drain_stderr() -> None:
        # stdout 読了を待たずに stderr pipe を逐次消費する。
        # 大量の stderr (>OS pipe capacity, 通常 64 KiB) を出す script で
        # child が stderr write でブロックされ、stdout verdict に到達できず
        # timeout する事故を防ぐ。
        assert process.stderr is not None
        try:
            for line in process.stderr:
                stderr_chunks.append(line)
        except ValueError:
            # pipe が timeout の kill により閉じられた場合
            pass

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    try:
        assert process.stdout is not None
        with (
            open(log_dir / "stdout.log", "a", encoding="utf-8") as f_raw,
            open(log_dir / "console.log", "a", encoding="utf-8") as f_con,
        ):
            for line in process.stdout:
                f_raw.write(line)
                f_raw.flush()
                stripped = line.rstrip("\n")
                texts.append(stripped)
                f_con.write(stripped + "\n")
                f_con.flush()
                if verbose:
                    print(f"[{_now_stamp()}] [{step.id}] {stripped}")
        process.wait()
        stderr_thread.join(timeout=5)
        stderr = "".join(stderr_chunks)
        if stderr:
            (log_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    finally:
        timer.cancel()

    if timed_out.is_set():
        raise StepTimeoutError(step.id, timeout)

    if process.returncode != 0:
        raise ScriptExecutionError(step.id, module, process.returncode, stderr or "(no stderr)")

    return CLIResult(
        full_output="\n".join(texts),
        session_id=None,
        cost=None,
        stderr=stderr,
    )
