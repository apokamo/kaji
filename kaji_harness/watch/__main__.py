"""Internal loop entrypoint: ``python -m kaji_harness.watch`` (Issue #543 D3).

公開の CLI ではない。``kaji watch start`` が ``kaji-watch`` タブで起動する。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kaji_harness.config import KajiConfig
from kaji_harness.console_log import configure_console_logging
from kaji_harness.errors import ConfigLoadError, ConfigNotFoundError, WatchPreconditionError
from kaji_harness.watch.preflight import check_watch_preconditions
from kaji_harness.watch.service import default_herdr, make_deps, run_loop


def main(argv: list[str] | None = None) -> int:
    """監視の loop を実行し、exit code を返す（0 = 終了・重複起動、2 = 前提違反）。"""
    parser = argparse.ArgumentParser(prog="python -m kaji_harness.watch")
    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    configure_console_logging()
    try:
        config = KajiConfig.discover(start_dir=args.workdir.resolve())
        context = check_watch_preconditions(config, require_herdr=True)
    except (ConfigNotFoundError, ConfigLoadError, WatchPreconditionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(run_loop(make_deps(context, default_herdr(context)), pane_id=context.herdr_pane_id))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
