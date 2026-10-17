"""``kaji validate`` subcommand（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import KajiConfig
from ..errors import (
    ConfigLoadError,
    ConfigNotFoundError,
    ProviderTypeRemovedError,
    WorkflowValidationError,
)
from ..preflight import preflight_workflow
from ..workflow import load_workflow, validate_workflow
from .exit_codes import EXIT_CONFIG_NOT_FOUND, EXIT_OK, EXIT_VALIDATION_ERROR


def _resolve_project_root_for_validate(explicit_root: Path | None, yaml_path: Path) -> Path:
    """Resolve project root for validate command.

    Priority:
    1. Explicit --project-root if provided
    2. .kaji/config.toml discovery from YAML file's directory
    3. Walk up from YAML file's directory looking for pyproject.toml
    4. Fall back to YAML file's parent directory
    """
    if explicit_root is not None:
        return explicit_root.resolve()
    # Try .kaji/config.toml
    try:
        config = KajiConfig.discover(start_dir=yaml_path.resolve().parent)
        return config.repo_root
    except ConfigNotFoundError:
        pass
    except ConfigLoadError:
        raise
    # Fallback: pyproject.toml
    current = yaml_path.resolve().parent
    while True:
        if (current / "pyproject.toml").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return yaml_path.resolve().parent


def cmd_validate(args: argparse.Namespace) -> int:
    """Execute the `validate` subcommand.

    ``provider.type = "local"``（削除済み provider）の設定エラーが 1 件でもあれば、
    他ファイルの検証失敗（exit 1）より優先して ``EXIT_CONFIG_NOT_FOUND``（2）を返す。
    """
    failed = 0
    removed_local_provider = False
    total = len(args.files)

    for path in args.files:
        config_error: ConfigNotFoundError | ConfigLoadError | None = None
        # Appended to every failure of this file so the removed-provider diagnostic
        # survives even when the workflow itself is missing or fails to load.
        removed_provider_errors: list[str] = []
        try:
            try:
                project_root = _resolve_project_root_for_validate(args.project_root, path)
                config = KajiConfig.discover(start_dir=project_root)
            except (ConfigNotFoundError, ConfigLoadError) as e:
                config_error = e
                if isinstance(e, ProviderTypeRemovedError):
                    removed_local_provider = True
                    removed_provider_errors = [str(e)]
            if not path.exists():
                _print_error(path, ["File not found", *removed_provider_errors])
                failed += 1
                continue
            workflow = load_workflow(path)
            if config_error is not None:
                # L3 needs project config; without it still surface L2 diagnostics,
                # which are more actionable than the config error itself.
                validate_workflow(workflow)
                _print_error(path, [str(config_error)])
                failed += 1
                continue
            result = preflight_workflow(
                workflow,
                project_root=project_root,
                skill_dir=config.paths.skill_dir,
            )
            _print_warnings(path, result.warnings)
            if result.errors:
                _print_error(path, result.errors)
                failed += 1
                continue
            _print_success(path)
        except WorkflowValidationError as e:
            _print_error(path, [*e.errors, *removed_provider_errors])
            failed += 1
        except OSError as e:
            _print_error(path, [str(e), *removed_provider_errors])
            failed += 1

    if failed > 0 and total > 1:
        print(
            f"Validation failed: {failed} of {total} files had errors.",
            file=sys.stderr,
        )

    if removed_local_provider:
        return EXIT_CONFIG_NOT_FOUND
    return EXIT_VALIDATION_ERROR if failed > 0 else EXIT_OK


def _print_warnings(path: Path, warnings: list[str]) -> None:
    """Print non-fatal warnings to stderr, one line each.

    ``path`` comes from user-supplied CLI args and may contain line-breaking
    characters (e.g. newline, U+2028); escape it via ``repr()`` so it can never
    split a single warning across multiple stderr lines (Issue #381).
    """
    for warning in warnings:
        print(f"⚠ {str(path)!r}: {warning}", file=sys.stderr)


def _print_success(path: Path) -> None:
    """Print success message to stdout."""
    print(f"✓ {path}")


def _print_error(path: Path, errors: list[str]) -> None:
    """Print error messages to stderr."""
    print(f"✗ {path}", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
