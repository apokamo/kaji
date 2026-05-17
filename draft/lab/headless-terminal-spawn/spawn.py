#!/usr/bin/env python3
"""Driver: spawn a Windows Terminal window from WSL2 and run claude / codex
inside it, then collect output via sentinel-file polling.

Usage:
    python spawn.py claude
    python spawn.py codex
    python spawn.py claude --timeout 300

Outputs a structured summary on stdout and saves all artifacts under
/tmp/kaji-spawn/<run_id>/.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

BASE = Path("/tmp/kaji-spawn")
LAB_DIR = Path(__file__).resolve().parent
WRAPPER = LAB_DIR / "wrapper.sh"
PROMPT_TEMPLATE = LAB_DIR / "prompt_template.txt"

WT_EXE = "/mnt/c/Users/eq787/AppData/Local/Microsoft/WindowsApps/wt.exe"
ZUTTY = "/usr/bin/zutty"
MLTERM = "/usr/bin/mlterm"
XFCE4 = "/usr/bin/xfce4-terminal"
KITTY = "/usr/bin/kitty"
# HackGen Console NF — Japanese/ASCII unified monospace (standalone TTF).
# zutty matches by filename basename in -fontpath, not via fontconfig.
ZUTTY_FONT = "HackGenConsoleNF"
ZUTTY_FONT_PATH = str(Path.home() / ".local" / "share" / "fonts")


def render_prompt(agent: str, run_id: str, run_dir: Path) -> str:
    tmpl = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    return tmpl.format(
        run_id=run_id,
        agent=agent,
        agent_upper=agent.upper(),
        run_dir=str(run_dir),
    )


def spawn_terminal(
    run_dir: Path, agent: str, terminal: str
) -> subprocess.Popen[bytes]:
    """Spawn a terminal window with the wrapper. Returns the Popen handle.

    terminal: "wt" → Windows Terminal via wt.exe; "zutty" → Linux X terminal via WSLg.
    """
    if terminal == "wt":
        # wt.exe: `-w new` opens a new window; `new-tab` is the subcommand;
        # remaining args become the command line for the inner shell.
        # NOTE: `new-window` is NOT a valid subcommand — using it makes wt.exe
        # fall back to bare-command mode and concatenate everything into the
        # inline-command name, failing with 0x80070002.
        cmd = [
            WT_EXE,
            "-w",
            "new",
            "new-tab",
            "--title",
            f"kaji-spawn-{agent}-{run_dir.name}",
            "wsl.exe",
            "bash",
            "-lc",
            f"{WRAPPER} {run_dir} {agent}",
        ]
    elif terminal == "zutty":
        cmd = [
            ZUTTY,
            "-font",
            ZUTTY_FONT,
            "-dwfont",
            ZUTTY_FONT,
            "-fontpath",
            ZUTTY_FONT_PATH,
            "-title",
            f"kaji-spawn-{agent}-{run_dir.name}",
            "-e",
            "bash",
            "-lc",
            f"{WRAPPER} {run_dir} {agent}",
        ]
    elif terminal == "mlterm":
        # mlterm uses fontconfig — no font path / font name needed for CJK.
        cmd = [
            MLTERM,
            "--title",
            f"kaji-spawn-{agent}-{run_dir.name}",
            "-e",
            "bash",
            "-lc",
            f"{WRAPPER} {run_dir} {agent}",
        ]
    elif terminal == "xfce4":
        # xfce4-terminal uses pango → handles CJK + emoji + ligatures natively.
        # --hold keeps the window open after the inner command exits.
        cmd = [
            XFCE4,
            "--title",
            f"kaji-spawn-{agent}-{run_dir.name}",
            "--hold",
            "-e",
            f"bash -lc '{WRAPPER} {run_dir} {agent}'",
        ]
    elif terminal == "kitty":
        # kitty is cross-platform (Linux / macOS native / WSLg). Single binary.
        # Syntax: kitty [options] program args...   (no -e flag like xterm)
        cmd = [
            KITTY,
            "--title",
            f"kaji-spawn-{agent}-{run_dir.name}",
            "--hold",
            "bash",
            "-lc",
            f"{WRAPPER} {run_dir} {agent}",
        ]
    else:
        raise ValueError(f"unknown terminal: {terminal}")
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_sentinel(sentinel: Path, timeout: float, poll: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sentinel.exists():
            return True
        time.sleep(poll)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("agent", choices=["claude", "codex"])
    parser.add_argument(
        "--terminal",
        choices=["wt", "zutty", "mlterm", "xfce4", "kitty"],
        default="wt",
        help="Terminal emulator to spawn (default: wt = Windows Terminal).",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove run_dir after completion.",
    )
    parser.add_argument(
        "--kill-on-done",
        action="store_true",
        help="SIGTERM the spawned terminal after sentinel arrives.",
    )
    args = parser.parse_args()

    if args.terminal == "wt" and not Path(WT_EXE).exists():
        print(f"ERROR: wt.exe not found at {WT_EXE}", file=sys.stderr)
        return 2
    if args.terminal == "zutty" and not Path(ZUTTY).exists():
        print(f"ERROR: zutty not found at {ZUTTY}", file=sys.stderr)
        return 2
    if args.terminal == "mlterm" and not Path(MLTERM).exists():
        print(f"ERROR: mlterm not found at {MLTERM}", file=sys.stderr)
        return 2
    if args.terminal == "xfce4" and not Path(XFCE4).exists():
        print(f"ERROR: xfce4-terminal not found at {XFCE4}", file=sys.stderr)
        return 2
    if args.terminal == "kitty" and not Path(KITTY).exists():
        print(f"ERROR: kitty not found at {KITTY}", file=sys.stderr)
        return 2
    if not WRAPPER.exists():
        print(f"ERROR: wrapper.sh not found at {WRAPPER}", file=sys.stderr)
        return 2

    # Ensure wrapper is executable
    os.chmod(WRAPPER, 0o755)

    run_id = uuid.uuid4().hex[:8]
    run_dir = BASE / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    prompt = render_prompt(args.agent, run_id, run_dir)
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    sentinel = run_dir / "sentinel"
    output_file = run_dir / "output.txt"
    status_file = run_dir / "status.json"

    print(f"[driver] run_id={run_id} agent={args.agent}")
    print(f"[driver] run_dir={run_dir}")
    print(f"[driver] spawning Windows Terminal...")

    started = time.monotonic()
    proc = spawn_terminal(run_dir, args.agent, args.terminal)
    print(f"[driver] terminal={args.terminal} PID={proc.pid}")

    print(f"[driver] watching sentinel (timeout={args.timeout}s)...")
    ok = wait_for_sentinel(sentinel, args.timeout)
    elapsed = time.monotonic() - started

    summary: dict[str, object] = {
        "run_id": run_id,
        "agent": args.agent,
        "run_dir": str(run_dir),
        "elapsed_seconds": round(elapsed, 2),
        "sentinel_seen": ok,
    }

    if not ok:
        summary["status"] = "TIMEOUT"
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 1

    summary["status"] = "OK"
    if output_file.exists():
        content = output_file.read_text(encoding="utf-8").strip()
        summary["output"] = content
        expected = f"HELLO_FROM_{args.agent.upper()}_{run_id}"
        summary["expected"] = expected
        summary["match"] = content == expected
    else:
        summary["output"] = None
        summary["match"] = False

    if status_file.exists():
        try:
            summary["agent_status"] = json.loads(status_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            summary["agent_status_error"] = str(e)

    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if args.kill_on_done and proc.poll() is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass

    if args.cleanup:
        shutil.rmtree(run_dir, ignore_errors=True)

    return 0 if summary.get("match") else 1


if __name__ == "__main__":
    sys.exit(main())
