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

logger = logging.getLogger(__name__)

# Retry constants for transient CLI errors (Bug 4)
_MAX_RETRIES = 3
_BASE_DELAY = 30.0
_TRANSIENT_PATTERNS = ["at capacity", "rate limit", "overloaded", "try again"]


def _is_transient(error: CLIExecutionError) -> bool:
    """Return True if the error is likely transient and worth retrying."""
    msg = str(error).lower()
    return any(p in msg for p in _TRANSIENT_PATTERNS)


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
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        result = stream_and_log(process, adapter, step.id, log_dir, verbose)
        process.wait()
    finally:
        timer.cancel()

    if timed_out.is_set():
        raise StepTimeoutError(step.id, timeout)
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

            # Collect error event messages for Bug 3: better CLIExecutionError messages
            event_type = event.get("type")
            if event_type == "error":
                msg = event.get("message", "")
                if msg:
                    error_messages.append(msg)
            elif event_type == "turn.failed":
                msg = (event.get("error") or {}).get("message", "")
                if msg:
                    error_messages.append(msg)

    stderr = ""
    if process.stderr:
        stderr = process.stderr.read()
    if stderr:
        (log_dir / "stderr.log").write_text(stderr, encoding="utf-8")

    return CLIResult(
        full_output="\n".join(texts),
        session_id=session_id,
        cost=cost,
        stderr=stderr,
        error_messages=error_messages,
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
    if step.max_turns:
        args += ["--max-turns", str(step.max_turns)]
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
