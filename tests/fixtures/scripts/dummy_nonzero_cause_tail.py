"""Fixture: emit long pytest-like stderr ending with a cause line, exit 1 (Issue #516).

stderr の先頭は原因と無関係な banner で、最後の行だけが失敗原因を示す
（``baseline_precheck`` の失敗と同じ形）。
"""

import sys


def main() -> int:
    sys.stderr.write("=" * 29 + " test session starts " + "=" * 30 + "\n")
    sys.stderr.write("platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0\n" * 5)
    sys.stderr.write("OSError: [Errno 30] Read-only file system: '/x'\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
