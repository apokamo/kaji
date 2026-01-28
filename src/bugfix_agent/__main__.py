"""Entry point for python -m bugfix_agent.

Enables running the CLI via:
    python -m bugfix_agent design https://github.com/owner/repo/issues/123
    python -m bugfix_agent bugfix https://github.com/owner/repo/issues/123
    python -m bugfix_agent https://github.com/owner/repo/issues/123  # backward compat
"""

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
