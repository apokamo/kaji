"""Fixture: emit PASS verdict and exit 0."""

import os
import sys


def main() -> int:
    issue_id = os.environ.get("KAJI_ISSUE_ID", "(unset)")
    sys.stdout.write(
        "---VERDICT---\n"
        "status: PASS\n"
        "reason: |\n  fixture PASS\n"
        f"evidence: |\n  KAJI_ISSUE_ID={issue_id}\n"
        "suggestion: |\n  none\n"
        "---END_VERDICT---\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
