"""Fixture: emit 1 MiB of stderr THEN PASS verdict on stdout, exit 0.

OS pipe capacity (典型値 64 KiB) を超える stderr を verdict 前に書き、
script_exec が stderr を並行に drain しないと child が stderr write で
ブロックして timeout する状況を再現する。
"""

import sys


def main() -> int:
    sys.stderr.write("X" * (1024 * 1024))
    sys.stderr.flush()
    sys.stdout.write(
        "---VERDICT---\n"
        "status: PASS\n"
        "reason: |\n  emitted after large stderr\n"
        "evidence: |\n  stderr_bytes=1048576\n"
        "suggestion: |\n  none\n"
        "---END_VERDICT---\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
