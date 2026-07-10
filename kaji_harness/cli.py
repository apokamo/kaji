"""CLI execution for kaji_harness.

Handles subprocess management, streaming, and argument building.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .adapters import ADAPTERS, CLIEventAdapter
from .errors import CLIExecutionError, CLINotFoundError, StepTimeoutError
from .models import CLIResult, CostInfo, Step
from .result import derive_signal

logger = logging.getLogger(__name__)

# Retry constants for transient CLI errors (Bug 4)
_MAX_RETRIES = 3
_BASE_DELAY = 30.0
# terminal success event 観測後、プロセスが自発 exit するのを待つ猶予（秒）。
# stdout EOF と OS によるプロセス reap の間には僅かなラグがあり、EOF 直後に
# poll() を呼ぶと自発 exit 途中のプロセスを誤って "生存中" と判定しうる。この
# 猶予内に exit すればその returncode を attempt の真の終了として保持し、超過時は
# CLI ハングと見なして kaji が terminate する（その returncode は SIGTERM ノイズ）。
_TERMINAL_SELF_EXIT_GRACE = 2.0
_TRANSIENT_PATTERNS = [
    "at capacity",
    "rate limit",
    "overloaded",
    "try again",
    "`thinking` or `redacted_thinking` blocks in the latest assistant message cannot be modified",
    "thinking or redacted_thinking blocks in the latest assistant message cannot be modified",
]


def is_transient_error_text(text: str | None) -> bool:
    """エラー文字列が一時的障害（retry する価値がある）かを判定する。

    Issue #288: attempt-level retry（``execute_cli``）と run-level recovery classifier
    が同一 pattern list を参照するための公開 helper。二重実装を作らないため、
    ``_is_transient`` はこの関数へ委譲する。

    Args:
        text: 判定対象。``None`` / 空文字は ``False``。

    Returns:
        ``_TRANSIENT_PATTERNS`` のいずれかを（大小文字無視で）含めば ``True``。
    """
    if not text:
        return False
    lowered = text.lower()
    return any(p in lowered for p in _TRANSIENT_PATTERNS)


def _is_transient(error: CLIExecutionError) -> bool:
    """Return True if the error is likely transient and worth retrying."""
    return is_transient_error_text(error.stderr or str(error))


def _now_stamp() -> str:
    """現在時刻を ISO 8601 形式（秒精度、タイムゾーンなし）で返す。"""
    return datetime.now().isoformat(timespec="seconds")


def build_cli_args(
    step: Step,
    prompt: str,
    workdir: Path,
    session_id: str | None,
    execution_policy: str,
) -> list[str]:
    """CLI 実行引数を構築する。"""
    match step.agent:
        case "claude":
            return _build_claude_args(step, prompt, workdir, session_id, execution_policy)
        case "codex":
            return _build_codex_args(step, prompt, workdir, session_id, execution_policy)
        case "gemini":
            return _build_gemini_args(step, prompt, workdir, session_id, execution_policy)
        case _:
            raise ValueError(f"Unknown agent: {step.agent}")


def execute_cli(
    step: Step,
    prompt: str,
    workdir: Path,
    session_id: str | None,
    log_dir: Path,
    execution_policy: str,
    verbose: bool = True,
    *,
    default_timeout: int,
) -> CLIResult:
    """CLI を実行し、結果を返す。一時的エラー時はバックオフ付きリトライする。"""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return _execute_cli_once(
                step,
                prompt,
                workdir,
                session_id,
                log_dir,
                execution_policy,
                verbose,
                default_timeout=default_timeout,
            )
        except CLIExecutionError as e:
            if attempt == _MAX_RETRIES or not _is_transient(e):
                raise
            delay = _BASE_DELAY * (2**attempt)
            logger.warning(
                "Step '%s' transient error (attempt %d/%d): %s. Retrying in %.0fs...",
                step.id,
                attempt + 1,
                _MAX_RETRIES + 1,
                e,
                delay,
            )
            time.sleep(delay)
    # unreachable, satisfies type checker
    raise RuntimeError("unreachable")  # pragma: no cover


def _execute_cli_once(
    step: Step,
    prompt: str,
    workdir: Path,
    session_id: str | None,
    log_dir: Path,
    execution_policy: str,
    verbose: bool,
    *,
    default_timeout: int,
) -> CLIResult:
    """CLI を 1 回実行する（リトライなし）。"""
    # execute_cli は agent 必須 step 専用。exec_script 経路は execute_script を使う。
    assert step.agent is not None, f"execute_cli requires step.agent (step={step.id})"
    args = build_cli_args(step, prompt, workdir, session_id, execution_policy)
    adapter = ADAPTERS[step.agent]
    timeout = step.timeout if step.timeout is not None else default_timeout

    try:
        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=workdir
        )
    except FileNotFoundError as e:
        raise CLINotFoundError(f"CLI '{args[0]}' not found. Is it installed?") from e

    timed_out = threading.Event()
    timer = threading.Timer(timeout, _kill_process, args=[process, timed_out])
    timer.start()
    # terminal event 観測後にプロセスが自発 exit せず、kaji が後始末で terminate した
    # かを記録する。その場合の returncode は kaji 由来の SIGTERM であって attempt の
    # 終了コードではない（result.exit_code への取り込みを抑止する根拠）。
    harness_terminated = False
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        result = stream_and_log(process, adapter, step.id, log_dir, verbose)
        if result.terminal_seen:
            # terminal event を観測した時点で timer を disarm（race 防止の核）。
            timer.cancel()
            # 自発 exit の猶予を与えてから生存判定する。poll() を即チェックすると
            # stdout EOF と reap のラグで自発 exit 途中のプロセスを生存中と誤認し、
            # 本来保持すべき真の returncode を harness terminate のノイズで潰してしまう。
            try:
                process.wait(timeout=_TERMINAL_SELF_EXIT_GRACE)
            except subprocess.TimeoutExpired:
                harness_terminated = True
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            # 後始末完了後に stderr を読み出す（stream_and_log では blocking を避けるため未読）。
            if process.stderr:
                stderr_tail = process.stderr.read()
                if stderr_tail:
                    result.stderr = stderr_tail
                    (log_dir / "stderr.log").write_text(stderr_tail, encoding="utf-8")
        else:
            process.wait()
    finally:
        timer.cancel()

    # Issue #222: 終了情報を CLIResult へ運ぶ。terminal success を観測した後に kaji が
    # 後始末で terminate した場合（harness_terminated=True）、process.returncode は
    # kaji 発の SIGTERM 起因（143 / 137 / -15 等）であって attempt の終了ではない。
    # これは異常終了シグナルではなく routine cleanup なので result.exit_code / signal に
    # 残さない（result.json の exit_code / signal は timeout / crash 等の真の異常終了を
    # 表す枠であり、harness cleanup のノイズで成功 attempt を signal 終了に見せない）。
    # プロセスが自発 exit した場合はその returncode が attempt の真の終了。失敗経路は
    # CLIExecutionError.returncode が、timeout 経路は StepTimeoutError.returncode が運ぶ。
    if harness_terminated:
        result.exit_code = None
        result.signal = None
    else:
        result.exit_code = process.returncode
        result.signal = derive_signal(process.returncode)

    if timed_out.is_set() and not result.terminal_seen:
        raise StepTimeoutError(step.id, timeout, returncode=process.returncode)
    # 失敗判定:
    #  - terminal event を観測したら、その event を真実とする。kaji が後始末で撃った
    #    terminate の returncode は、CLI の SIGTERM ハンドリング方式（-15 / 143 / 137 等）
    #    に依らず失敗根拠にしない。Claude Code CLI は SIGTERM を trap し shell 慣例の
    #    正値（128+15=143）で exit するため、returncode > 0 を失敗根拠にすると成功
    #    ステップを誤って例外化する。
    #  - 失敗は (1) terminal event 自体の failure シグナル
    #    (`adapter.is_terminal_failure`) または (2) adapter が
    #    `treats_stream_error_as_failure()=True` を返す場合の `error_messages` non-empty
    #    で判定する。Codex は (2) を False とし、stream-level `error` event は
    #    recoverable 通知（reconnection 等）として扱う (Issue #196)。
    #    `error_messages` は detail メッセージのフォールバック材料としては全 adapter で
    #    利用する。
    if result.terminal_seen:
        fail = result.terminal_failure
        if not fail and adapter.treats_stream_error_as_failure():
            fail = bool(result.error_messages)
        if fail:
            detail = result.stderr or "\n".join(result.error_messages[-3:]) or "terminal failure"
            rc = process.returncode if process.returncode is not None else -1
            raise CLIExecutionError(step.id, rc, detail)
        return result
    # terminal event なし: 従来どおり returncode で判定
    if process.returncode != 0:
        detail = result.stderr or "\n".join(result.error_messages[-3:])
        raise CLIExecutionError(step.id, process.returncode, detail)
    return result


def stream_and_log(
    process: subprocess.Popen[str],
    adapter: CLIEventAdapter,
    step_id: str,
    log_dir: Path,
    verbose: bool,
) -> CLIResult:
    """行単位で読み取り、ログ書き出し・デコード・ターミナル表示を同時実行。"""
    session_id: str | None = None
    cost: CostInfo | None = None
    texts: list[str] = []
    error_messages: list[str] = []
    terminal_seen = False
    terminal_failure = False

    with (
        open(log_dir / "stdout.log", "a", encoding="utf-8") as f_raw,
        open(log_dir / "console.log", "a", encoding="utf-8") as f_con,
    ):
        assert process.stdout is not None
        for line in process.stdout:
            f_raw.write(line)
            f_raw.flush()

            try:
                event: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                # Collect non-JSON lines (VERDICT may appear as plain text,
                # e.g. Codex mcp_tool_call mode). V5/V6 restoration.
                stripped = line.strip()
                if stripped:
                    texts.append(stripped)
                    f_con.write(stripped + "\n")
                    f_con.flush()
                    if verbose:
                        print(f"[{_now_stamp()}] [{step_id}] {stripped}")
                continue

            sid = adapter.extract_session_id(event)
            if sid:
                session_id = sid

            text = adapter.extract_text(event)
            if text:
                texts.append(text)
                f_con.write(text + "\n")
                f_con.flush()
                if verbose:
                    print(f"[{_now_stamp()}] [{step_id}] {text}")

            c = adapter.extract_cost(event)
            if c:
                cost = c

            # Collect provider-specific failure detail for better CLIExecutionError messages.
            error_message = adapter.extract_error_message(event)
            if error_message:
                error_messages.append(error_message)

            if adapter.is_terminal_event(event):
                terminal_seen = True
                terminal_failure = adapter.is_terminal_failure(event)
                break

    # terminal_seen で early-break した場合、process がまだ生きているため
    # process.stderr.read() は EOF を待って blocking する。後始末（terminate）後に
    # 呼び出し側で stderr を読むため、ここでは EOF 経路でのみ読む。
    stderr = ""
    if not terminal_seen and process.stderr:
        stderr = process.stderr.read()
    if stderr:
        (log_dir / "stderr.log").write_text(stderr, encoding="utf-8")

    return CLIResult(
        full_output="\n".join(texts),
        session_id=session_id,
        cost=cost,
        stderr=stderr,
        error_messages=error_messages,
        terminal_seen=terminal_seen,
        terminal_failure=terminal_failure,
    )


def _kill_process(process: subprocess.Popen[str], timed_out: threading.Event) -> None:
    """タイムアウト時のプロセス強制終了。"""
    timed_out.set()
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _build_claude_args(
    step: Step,
    prompt: str,
    workdir: Path,
    session_id: str | None,
    execution_policy: str,
) -> list[str]:
    args = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    if step.model:
        args += ["--model", step.model]
    if step.effort:
        args += ["--effort", step.effort]
    if step.max_budget_usd:
        args += ["--max-budget-usd", str(step.max_budget_usd)]
    if session_id:
        args += ["--resume", session_id]
    if execution_policy == "auto":
        args += ["--permission-mode", "bypassPermissions"]
    args.append(prompt)
    return args


def _build_codex_args(
    step: Step,
    prompt: str,
    workdir: Path,
    session_id: str | None,
    execution_policy: str,
) -> list[str]:
    if session_id:
        args = ["codex", "exec", "resume", session_id, "--json"]
    else:
        args = ["codex", "exec", "--json", "-C", str(workdir)]
    if step.model:
        args += ["-m", step.model]
    if step.effort:
        args += ["-c", f'model_reasoning_effort="{step.effort}"']
    match execution_policy:
        case "auto":
            args.append("--dangerously-bypass-approvals-and-sandbox")
        case "sandbox":
            args += ["-s", "workspace-write"]
    args.append(prompt)
    return args


def _build_gemini_args(
    step: Step,
    prompt: str,
    workdir: Path,
    session_id: str | None,
    execution_policy: str,
) -> list[str]:
    args = ["gemini", "-p", prompt, "-o", "stream-json"]
    if step.model:
        args += ["-m", step.model]
    if session_id:
        args += ["-r", session_id]
    match execution_policy:
        case "auto":
            args += ["--approval-mode", "yolo"]
        case "sandbox":
            args.append("-s")
    return args
