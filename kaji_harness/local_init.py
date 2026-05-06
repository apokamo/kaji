"""``kaji local init`` 実装。

Phase 3-d で導入。``provider=local`` 主運用に必要な初期化（machine_id 生成 /
overlay TOML 作成 / `.gitignore` 整備）を 1 コマンドにまとめる。

phase3d-design.md § 3 の上書き仕様に従い、active provider 値（`type` /
`machine_id` / `default_branch`）はすべて `.kaji/config.local.toml`
(gitignored) に書く。tracked `.kaji/config.toml` は touch しない。
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
from pathlib import Path

from .providers.local import validate_machine_id

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_OVERLAY_EXISTS = 3

_GITIGNORE_LINE = ".kaji/config.local.toml"
_LOCAL_DIR_RE = re.compile(r"^local-([a-z0-9]+)-\d+(?:-.*)?$")

# git の branch 命名規則（git check-ref-format --branch 相当）の保守的な
# サブセット。許容: 英数字 / `_` / `.` / `/` / `-`。これらだけでも
# `feat/foo`, `release-1.2`, `main`, `develop` 等の一般的な branch 名は表現できる。
# TOML 文字列の安全性も同時に保証する（quote / 制御文字 / 改行を排除）。
_DEFAULT_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_DEFAULT_BRANCH_MAX_LEN = 255


def validate_default_branch(value: str) -> None:
    """``provider.{local,github}.default_branch`` の値域を検証する。

    git の ``check-ref-format`` ルールの保守的なサブセットを採用する。
    `_DEFAULT_BRANCH_RE` を通り、かつ git の禁止規則
    （先頭末尾 ``.`` / ``/``、``..``、``//``、``.lock`` 末尾、空文字、
    255 文字超）に該当しないことを確認する。

    Raises:
        ValueError: 上記いずれかに違反したとき。
    """
    if not isinstance(value, str) or not value:
        raise ValueError("default_branch must be a non-empty string")
    if len(value) > _DEFAULT_BRANCH_MAX_LEN:
        raise ValueError(f"default_branch too long (max {_DEFAULT_BRANCH_MAX_LEN}): {value!r}")
    if not _DEFAULT_BRANCH_RE.match(value):
        raise ValueError(
            f"invalid default_branch {value!r}: only [A-Za-z0-9._/-] are allowed "
            f"(no whitespace, quotes, control chars, or other shell/TOML metacharacters)"
        )
    if value.startswith(("/", ".", "-")):
        raise ValueError(f"default_branch must not start with '/' / '.' / '-': {value!r}")
    if value.endswith(("/", ".")):
        raise ValueError(f"default_branch must not end with '/' or '.': {value!r}")
    if ".." in value or "//" in value:
        raise ValueError(f"default_branch must not contain '..' or '//': {value!r}")
    if value.endswith(".lock"):
        raise ValueError(f"default_branch must not end with '.lock': {value!r}")


def register_subcommand(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``kaji local`` parent + ``init`` child subcommand."""
    p_local = subparsers.add_parser(
        "local",
        help="Local-mode utilities (init etc.)",
    )
    local_subparsers = p_local.add_subparsers(dest="local_command", required=True)
    p_init = local_subparsers.add_parser("init", help="Initialize local mode for this repo")
    p_init.add_argument(
        "--machine-id",
        dest="machine_id",
        default=None,
        help="machine_id を明示する。未指定なら hostname sanitize → pcN fallback で生成。",
    )
    p_init.add_argument(
        "--default-branch",
        dest="default_branch",
        default="main",
        help="provider.local.default_branch に書く branch 名（既定: main）",
    )
    p_init.add_argument(
        "--non-interactive",
        action="store_true",
        help="stdin を読まない（CI / automation 用途）",
    )
    p_init.add_argument(
        "--repo-root",
        dest="repo_root",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )


def cmd_local(args: argparse.Namespace) -> int:
    """Dispatcher for ``kaji local <subcommand>``."""
    if args.local_command == "init":
        return cmd_local_init(args)
    return EXIT_INVALID_INPUT


def cmd_local_init(args: argparse.Namespace) -> int:
    """Execute ``kaji local init``.

    Returns:
        exit code（0: 正常 / 2: machine_id 不正 / 3: 既存 overlay）。
    """
    repo_root = (args.repo_root or Path.cwd()).resolve()
    kaji_dir = repo_root / ".kaji"
    config_path = kaji_dir / "config.toml"
    overlay_path = kaji_dir / "config.local.toml"
    issues_dir = kaji_dir / "issues"
    gitignore_path = repo_root / ".gitignore"

    # Step 1 & 5 (overlay existence check first to avoid wasted side-effects)
    if overlay_path.exists():
        print(
            f"ERROR: {overlay_path} already exists. To regenerate, remove it manually "
            f"first (kaji local init has no --force flag in Phase 3-d).",
            file=sys.stderr,
        )
        return EXIT_OVERLAY_EXISTS

    # Step 1: collect existing machine_ids from issue dirs (for duplicate warning)
    existing_ids = _collect_existing_machine_ids(issues_dir)

    # Step 2a: validate --default-branch（TOML escape / git ref-format 安全性）。
    # bad"branch / 改行 / 制御文字 を early に弾くことで壊れた overlay を生成しない。
    default_branch_raw = args.default_branch or "main"
    try:
        validate_default_branch(default_branch_raw)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    # Step 2b: resolve machine_id
    try:
        machine_id = _resolve_machine_id(args, existing_ids)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    # Step 3: warn on duplicate
    if machine_id in existing_ids:
        existing_dirs = sorted(
            d.name
            for d in issues_dir.iterdir()
            if d.is_dir() and d.name.startswith(f"local-{machine_id}-")
        )
        sample = ", ".join(existing_dirs[:5])
        print(
            f"WARNING: machine_id {machine_id!r} is already used by existing issues: "
            f"{sample}. Continue only if you intend to share the namespace.",
            file=sys.stderr,
        )

    # Step 4: don't touch tracked .kaji/config.toml (per phase3d-design § 3)

    # Step 5: write overlay
    default_branch = default_branch_raw
    kaji_dir.mkdir(parents=True, exist_ok=True)
    overlay_content = _build_overlay_toml(machine_id=machine_id, default_branch=default_branch)
    overlay_path.write_text(overlay_content, encoding="utf-8")

    # Step 6: ensure .gitignore line
    gitignore_added = _ensure_gitignore_line(gitignore_path, _GITIGNORE_LINE)

    # Step 7: summary
    issue_count = _count_local_issues(issues_dir, machine_id)
    print(f"kaji local init: machine_id={machine_id} default_branch={default_branch}")
    print(f"  overlay: {overlay_path}")
    if gitignore_added:
        print(f"  .gitignore: added {_GITIGNORE_LINE!r}")
    if not config_path.exists():
        print(
            "  note: .kaji/config.toml does not yet exist; create it with "
            "[paths] / [execution] / [provider] sections "
            "(see docs/cli-guides/local-mode.md)."
        )
    print(f"  existing issues for {machine_id}: {issue_count}")
    return EXIT_OK


def _collect_existing_machine_ids(issues_dir: Path) -> set[str]:
    if not issues_dir.is_dir():
        return set()
    out: set[str] = set()
    for entry in issues_dir.iterdir():
        if not entry.is_dir():
            continue
        m = _LOCAL_DIR_RE.match(entry.name)
        if m:
            out.add(m.group(1))
    return out


def _count_local_issues(issues_dir: Path, machine_id: str) -> int:
    if not issues_dir.is_dir():
        return 0
    count = 0
    prefix = f"local-{machine_id}-"
    for entry in issues_dir.iterdir():
        if entry.is_dir() and entry.name.startswith(prefix):
            count += 1
    return count


def _resolve_machine_id(args: argparse.Namespace, existing_ids: set[str]) -> str:
    """Resolve machine_id from CLI args / hostname / pcN fallback.

    Raises:
        ValueError: ``--machine-id`` の値が文法違反のとき。
    """
    if args.machine_id is not None:
        machine_id: str = args.machine_id
        validate_machine_id(machine_id)
        return machine_id

    # hostname sanitize
    raw = socket.gethostname()
    candidate = re.sub(r"[^a-z0-9]", "", raw.lower())[:16]
    if candidate and candidate not in existing_ids:
        validate_machine_id(candidate)
        return candidate

    # pcN fallback
    n = 1
    while True:
        candidate = f"pc{n}"
        if candidate not in existing_ids:
            validate_machine_id(candidate)
            return candidate
        n += 1


def _build_overlay_toml(*, machine_id: str, default_branch: str) -> str:
    """Compose the ``config.local.toml`` content for the overlay.

    `provider.type = "local"`、`provider.local.machine_id`、
    `provider.local.default_branch` の 3 値を書く（phase3d-design.md § 3）。
    """
    return (
        "# kaji local mode overlay (gitignored).\n"
        "# Generated by `kaji local init`. Edit machine_id / default_branch as needed.\n"
        "[provider]\n"
        'type = "local"\n'
        "\n"
        "[provider.local]\n"
        f'machine_id = "{machine_id}"\n'
        f'default_branch = "{default_branch}"\n'
    )


def _ensure_gitignore_line(gitignore_path: Path, line: str) -> bool:
    """Append ``line`` to ``.gitignore`` if not already present.

    Returns:
        True if a new line was appended, False if no change was needed.
    """
    if gitignore_path.exists():
        existing = gitignore_path.read_text(encoding="utf-8")
        for raw in existing.splitlines():
            if raw.strip() == line:
                return False
        # append (ensure trailing newline before adding)
        if existing and not existing.endswith("\n"):
            existing += "\n"
        existing += f"{line}\n"
        gitignore_path.write_text(existing, encoding="utf-8")
        return True
    gitignore_path.write_text(f"{line}\n", encoding="utf-8")
    return True
