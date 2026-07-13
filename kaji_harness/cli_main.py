"""kaji console entrypoint（`kaji_harness.cli_main:main` / `python -m kaji_harness.cli_main`）。

実装の実体は kaji_harness.commands 配下（#283/#286 で分割・分離、#284 で shim 撤去）。
"""

from __future__ import annotations

import sys

from kaji_harness.commands.main import main

__all__ = ["main"]


if __name__ == "__main__":
    sys.exit(main())
