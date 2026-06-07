"""Deterministic script dispatch for kaji_harness.

LLM agent を介さない決定論的 dispatch 経路の核。2 つの entrypoint を持つ:

- ``execute_script``: ``exec_script`` を持つ skill を ``python -m <module>`` として
  実行する（Issue #204）。``exec_script`` 値は呼び出し側で Python identifier
  正規表現により validate 済みである前提（``skill.py``）。
- ``execute_exec``: workflow.yaml の ``exec:`` step を任意 argv として実行する
  （Issue #205）。argv は parse 境界（``workflow.py``）で正規化済み（非空 list[str]、
  全要素非空 str）である前提。

両者は subprocess 起動 / timeout / stdout・stderr ドレイン / ログ書き出しの本体を
private helper ``_run_argv`` で共有する。共通の不変条件:

- subprocess は ``shell=False`` で呼ぶ。シェルメタ文字の展開・injection は
  構造的に発生しない。
- exit code != 0 は ``ScriptExecutionError`` で fail-loud。stdout の verdict
  有無を問わない（決定論性確保のため、Issue #204 § exit code と verdict の
  優先順位の正本契約）。
- stdout は line-buffered で ``stdout.log`` / ``console.log`` に書き出しつつ
  ``CLIResult.full_output`` に蓄積する（既存 ``execute_cli`` と同方針）。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from .errors import CLINotFoundError, ScriptExecutionError, StepTimeoutError
from .models import CLIResult, Step
from .result import derive_signal

# Issue #235: 起動コンソール向け progress logger（kaji.* 名前空間）。
# RunLogger の JSONL とは別系統で、人間向け表示のみを担う。
_console = logging.getLogger("kaji.script_exec")

# exec start 行で表示する argv 文字列の最大長。これを超えたら末尾を省略する。
_ARGV_DISPLAY_LIMIT = 200

# skill-authoring.md の env 契約上「未解決なら未注入」となる reserved 変数。
# parent process に古い値が残っていた場合に subprocess へ漏れて
# review_poll_entry 等が無関係 PR を polling する事故を防ぐため、
# 現在 step の env に含まれない限り os.environ からも除外する。
_OPTIONAL_RESERVED_ENV = frozenset({"KAJI_PR_ID", "KAJI_PR_REF"})


def _now_stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _format_argv(args: list[str]) -> str:
    """argv を 1 行表示用に join する。長大なら末尾を省略する。

    exec / exec_script 共通の表示作法。``command_label`` ではなく実 ``args`` を
    join することで、両 dispatch で同じ作り方の argv 文字列を一貫表示する
    （設計書 § exec start の argv 表示位置）。
    """
    joined = " ".join(args)
    if len(joined) > _ARGV_DISPLAY_LIMIT:
        return joined[:_ARGV_DISPLAY_LIMIT] + "…"
    return joined


def _run_argv(
    *,
    step: Step,
    args: list[str],
    env: Mapping[str, str],
    workdir: Path,
    log_dir: Path,
    timeout: int,
    verbose: bool,
    command_label: str,
) -> CLIResult:
    """任意 argv を ``shell=False`` で subprocess 実行し ``CLIResult`` を返す。

    ``execute_script`` / ``execute_exec`` が共有する subprocess コア。
    subprocess 起動・timeout・stdout/stderr ドレイン・ログ書き出しの本体を
    1 箇所に集約する（Issue #205 § 方針 3）。

    Args:
        step: 実行対象 step（id をログラベルに使用）
        args: subprocess に渡す argv（``shell=False``）。非空である前提
        env: context 追加 env（``os.environ`` に merge される）
        workdir: subprocess の cwd
        log_dir: ``stdout.log`` / ``console.log`` / ``stderr.log`` の出力先
        timeout: 秒単位の hard timeout
        verbose: ``True`` で stdout を逐次 console に echo する
        command_label: 失敗時の ``ScriptExecutionError`` に載せる調査用ラベル

    Raises:
        CLINotFoundError: 実行体が見つからない
        StepTimeoutError: timeout 超過
        ScriptExecutionError: subprocess が non-zero exit（stdout の verdict 有無
            を問わない）
    """
    base_env = {k: v for k, v in os.environ.items() if k not in _OPTIONAL_RESERVED_ENV or k in env}
    full_env = {**base_env, **env}

    log_dir.mkdir(parents=True, exist_ok=True)

    # Issue #235: 起動コンソールへ exec start progress を出す。実 argv を知るのは
    # この共有コアのみ（runner は exec_script の module 名しか持たない）ため、
    # exec / exec_script いずれもここで 1 箇所だけ発火し表示粒度を揃える。
    _console.info("exec start: %s", _format_argv(args))

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
        raise CLINotFoundError(f"command '{args[0]}' not found") from exc

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
        raise StepTimeoutError(step.id, timeout, returncode=process.returncode)

    if process.returncode != 0:
        raise ScriptExecutionError(
            step.id, command_label, process.returncode, stderr or "(no stderr)"
        )

    # Issue #222: 正常終了の exit_code / signal を CLIResult へ運ぶ。
    return CLIResult(
        full_output="\n".join(texts),
        session_id=None,
        cost=None,
        stderr=stderr,
        exit_code=process.returncode,
        signal=derive_signal(process.returncode),
    )


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
    return _run_argv(
        step=step,
        args=[sys.executable, "-m", module],
        env=env,
        workdir=workdir,
        log_dir=log_dir,
        timeout=timeout,
        verbose=verbose,
        command_label=module,
    )


def execute_exec(
    *,
    step: Step,
    argv: list[str],
    env: Mapping[str, str],
    workdir: Path,
    log_dir: Path,
    timeout: int,
    verbose: bool = True,
) -> CLIResult:
    """workflow.yaml の ``exec:`` step（任意 argv）を subprocess 実行する。

    ``argv`` は parse 境界（``workflow.py`` の ``_normalize_exec``）で
    非空 list[str]・全要素非空 str に正規化済みである前提。``execute_script`` と
    subprocess コア（``_run_argv``）を共有し、dispatch 副作用（ログ・timeout・
    fail-loud）は完全に同等となる（Issue #205）。

    Args:
        step: 実行対象 step（id をログラベルに使用）
        argv: subprocess に渡す argv。``shell=False`` で起動する
        env: context 追加 env（``os.environ`` に merge される）
        workdir: subprocess の cwd
        log_dir: ``stdout.log`` / ``console.log`` / ``stderr.log`` の出力先
        timeout: 秒単位の hard timeout
        verbose: ``True`` で stdout を逐次 console に echo する

    Raises:
        CLINotFoundError: argv[0] の実行体が見つからない
        StepTimeoutError: timeout 超過
        ScriptExecutionError: subprocess が non-zero exit（stdout の verdict 有無
            を問わない）
    """
    return _run_argv(
        step=step,
        args=argv,
        env=env,
        workdir=workdir,
        log_dir=log_dir,
        timeout=timeout,
        verbose=verbose,
        command_label=" ".join(argv),
    )
