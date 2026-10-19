"""Issue #515: recorder の terminal.log と tmux ``pipe-pane`` のログを同じ実行で突き合わせる。

tmux backend を削除する前に、private tmux server の pane で recorder を起動し、pane の出力を
``pipe-pane`` でも記録する。recorder の log が ``pipe.log`` の末尾と byte 一致すること
（差分は recorder 起動前の入力 echo だけ）を確認する。

Usage:
    python experiments/herdr-interactive-terminal/scripts/compare_recorder_pipe_pane.py \\
        --out <dir> [--claude]
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import textwrap
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RECORDER = REPO / "kaji_harness" / "interactive_terminal_recorder.py"
SOCKET = "kaji515-cmp"

FAKE_TUI = textwrap.dedent(
    """
    import sys
    out = sys.stdout
    for i in range(3000):
        out.write(f"BULK-{i:04d}\\n")
    out.write("\\x1b[?1049h")
    for i in range(200):
        out.write(f"ALT-{i:03d}\\n")
    out.write("\\x1b[?1049l\\x1b[3J")
    out.write("AFTER-CLEAR\\n")
    out.write("API Error: 529 overloaded_error\\n")
    out.flush()
    sys.exit(7)
    """
)


def tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", "-L", SOCKET, "-f", "/dev/null", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def wait_for(predicate: object, timeout: float, what: str) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():  # type: ignore[operator]
            return
        time.sleep(0.2)
    raise TimeoutError(what)


def pane_dead() -> bool:
    result = tmux("display-message", "-p", "-t", "cmp", "#{pane_dead}", check=False)
    return result.stdout.strip() == "1"


def run_case(name: str, argv: list[str], out: Path, *, claude: bool) -> dict[str, object]:
    case = out / name
    case.mkdir(parents=True, exist_ok=True)
    rec_log, pipe_log = case / "rec.log", case / "pipe.log"
    for path in (rec_log, pipe_log):
        path.unlink(missing_ok=True)
    inner = shlex.join([sys.executable, "-I", str(RECORDER), str(rec_log), "--", *argv])
    tmux("kill-server", check=False)
    tmux(
        "new-session",
        "-d",
        "-s",
        "cmp",
        "-x",
        "160",
        "-y",
        "48",
        "-c",
        str(REPO),
        f"sh -c {shlex.quote('read _; exec ' + inner)}",
    )
    tmux("set-option", "-w", "-t", "cmp", "remain-on-exit", "on")
    tmux("pipe-pane", "-o", "-t", "cmp", f"cat >> {shlex.quote(str(pipe_log))}")
    started = time.monotonic()
    tmux("send-keys", "-t", "cmp", "Enter")
    if claude:
        wait_for(
            lambda: rec_log.exists() and b"PROBE-OK" in rec_log.read_bytes().split(b"exactly")[-1],
            150,
            "claude reply",
        )
        time.sleep(2)
        tmux("send-keys", "-t", "cmp", "-l", "/exit")
        time.sleep(0.5)
        tmux("send-keys", "-t", "cmp", "Enter")
    wait_for(pane_dead, 120, f"{name} exit")
    status = tmux("display-message", "-p", "-t", "cmp", "#{pane_dead_status}").stdout.strip()
    time.sleep(1)
    tmux("kill-server", check=False)
    rec, pipe = rec_log.read_bytes(), pipe_log.read_bytes()
    prefix = pipe[: len(pipe) - len(rec)] if pipe.endswith(rec) else None
    return {
        "case": name,
        "argv": argv,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "exit_status": status,
        "rec_bytes": len(rec),
        "pipe_bytes": len(pipe),
        "rec_is_suffix_of_pipe": prefix is not None,
        "pipe_only_prefix": prefix.decode("latin-1") if prefix is not None else None,
        "bulk_lines": rec.count(b"BULK-"),
        "alt_lines": rec.count(b"ALT-"),
        "after_clear": rec.split(b"\x1b[3J", 1)[-1][:40].decode("latin-1")
        if b"\x1b[3J" in rec
        else None,
        "state": (case / "rec.log.state").read_text().strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--claude", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    fake = args.out / "fake_tui.py"
    fake.write_text(FAKE_TUI, encoding="utf-8")
    results = [run_case("A-fake-tui", [sys.executable, str(fake)], args.out, claude=False)]
    if args.claude:
        results.append(
            run_case(
                "B-claude",
                ["claude", "--model", "haiku", "Reply with exactly PROBE-OK"],
                args.out,
                claude=True,
            )
        )
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0 if all(r["rec_is_suffix_of_pipe"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
