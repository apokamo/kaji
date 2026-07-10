"""Fixture: emit ABORT verdict and exit 0 (deterministic-script contract)."""

import sys


def main() -> int:
    sys.stdout.write(
        "---VERDICT---\n"
        "status: ABORT\n"
        "reason: |\n  fixture ABORT\n"
        "evidence: |\n  business failure expressed as verdict, not exit code\n"
        "suggestion: |\n  none\n"
        "---END_VERDICT---\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
