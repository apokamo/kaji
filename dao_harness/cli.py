"""CLI execution for dao_harness.

Handles subprocess management, streaming, and argument building.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from .adapters import ADAPTERS, CLIEventAdapter
from .errors import CLIExecutionError, CLINotFoundError, StepTimeoutError
from .models import CLIResult, CostInfo, Step

DEFAULT_TIMEOUT = 1800  # 30 minutes


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
) -> CLIResult:
    """CLI を実行し、結果を返す。"""
    args = build_cli_args(step, prompt, workdir, session_id, execution_policy)
    adapter = ADAPTERS[step.agent]
    timeout = step.timeout or DEFAULT_TIMEOUT

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
        raise CLIExecutionError(step.id, process.returncode, result.stderr)
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
                    print(f"[{step_id}] {text}")

            c = adapter.extract_cost(event)
            if c:
                cost = c

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
