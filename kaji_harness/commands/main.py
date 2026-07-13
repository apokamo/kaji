"""``main()`` dispatch + exit code mapping（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

from .config import cmd_config_artifacts_dir, cmd_config_provider_type
from .exit_codes import EXIT_ABORT
from .issue import _handle_issue
from .parser import create_parser
from .pr import _handle_pr
from .recover import cmd_recover
from .run import cmd_run
from .series import cmd_run_series, cmd_validate_series
from .sync import cmd_sync_from_github, cmd_sync_status
from .validate import cmd_validate


def main(argv: list[str] | None = None) -> int:
    """Main entrypoint."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.command == "recover":
        return cmd_recover(args)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "validate-series":
        return cmd_validate_series(args)
    if args.command == "run-series":
        return cmd_run_series(args)
    if args.command == "issue":
        return _handle_issue(args.args)
    if args.command == "pr":
        return _handle_pr(args.args)
    if args.command == "config":
        if args.config_command == "provider-type":
            return cmd_config_provider_type(args)
        if args.config_command == "artifacts-dir":
            return cmd_config_artifacts_dir(args)
        parser.print_help()
        return EXIT_ABORT
    if args.command == "sync":
        if args.sync_command == "from-github":
            return cmd_sync_from_github(args)
        if args.sync_command == "status":
            return cmd_sync_status(args)
        parser.print_help()
        return EXIT_ABORT
    if args.command == "local":
        from ..local_init import cmd_local

        return cmd_local(args)

    parser.print_help()
    return EXIT_ABORT
