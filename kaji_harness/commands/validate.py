"""``kaji validate`` subcommand（#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import KajiConfig
from ..errors import (
    ConfigLoadError,
    ConfigNotFoundError,
    WorkflowValidationError,
)
from ..preflight import preflight_workflow
from ..workflow import load_workflow, validate_workflow
from .exit_codes import EXIT_OK, EXIT_VALIDATION_ERROR


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
    """Execute the `validate` subcommand."""
    failed = 0
    total = len(args.files)

    for path in args.files:
        if not path.exists():
            _print_error(path, ["File not found"])
            failed += 1
            continue
        try:
            workflow = load_workflow(path)
            # Preserve definition diagnostics even when project config discovery fails.
            # The shared preflight repeats L2 later so it can aggregate L2 and L3 errors.
            validate_workflow(workflow)
            project_root = _resolve_project_root_for_validate(args.project_root, path)
            config = KajiConfig.discover(start_dir=project_root)
            result = preflight_workflow(
                workflow,
                project_root=project_root,
                skill_dir=config.paths.skill_dir,
            )
            if result.errors:
                _print_error(path, result.errors)
                failed += 1
                continue
            _print_success(path)
        except WorkflowValidationError as e:
            _print_error(path, e.errors)
            failed += 1
        except (ConfigNotFoundError, ConfigLoadError) as e:
            _print_error(path, [str(e)])
            failed += 1
        except OSError as e:
            _print_error(path, [str(e)])
            failed += 1

    if failed > 0 and total > 1:
        print(
            f"Validation failed: {failed} of {total} files had errors.",
            file=sys.stderr,
        )

    return EXIT_VALIDATION_ERROR if failed > 0 else EXIT_OK


def _print_success(path: Path) -> None:
    """Print success message to stdout."""
    print(f"✓ {path}")


def _print_error(path: Path, errors: list[str]) -> None:
    """Print error messages to stderr."""
    print(f"✗ {path}", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
