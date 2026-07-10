"""Fixture: emit PASS verdict but exit 1 (must trigger ScriptExecutionError)."""

import sys


def main() -> int:
    sys.stdout.write(
        "---VERDICT---\n"
        "status: PASS\n"
        "reason: |\n  fixture PASS but non-zero exit\n"
        "evidence: |\n  must fail-loud\n"
        "suggestion: |\n  none\n"
        "---END_VERDICT---\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
