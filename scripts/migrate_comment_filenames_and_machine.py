#!/usr/bin/env python3
"""Issue local-pc5090-21 移行スクリプト: comment filename timestamp 化 + pc5090 → p1。

Phase（``--phase``）ごとに分割実行できる。一括実行は ``--phase=all``。

Phase 一覧:
    rename-comments  : .kaji/issues/local-pc5090-{1..20,21}-*/comments/*.md を
                       新形式 <YYYYMMDDTHHMMSSZ>-<machine>.md に rename。
                       1〜20 は machine=p1、21 は machine=pc5090 を維持。
                       同一 issue dir 内で同秒衝突する場合は元 seq 昇順で +1s。
                       frontmatter は無変更。
    rename-dirs      : .kaji/issues/local-pc5090-{1..20}-*/ を
                       local-p1-{1..20}-*/ に rename し、issue.md frontmatter
                       id と本文中の cross-ref（N=1..20 のみ）を書き換える。
                       21 は対象外。
    config           : .kaji/counters/pc5090.txt → p1.txt（中身保全）、
                       .kaji/config.local.toml の machine_id = "p1"。

dry-run は ``--dry-run`` で全 phase に対して plan のみ出力する。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

OLD_MACHINE = "pc5090"
NEW_MACHINE = "p1"
BOUNDARY_ISSUE_N = 21  # 21 は移行対象外（dir / id / cross-ref 据置）
MIGRATE_RANGE = range(1, BOUNDARY_ISSUE_N)  # 1..20

OLD_FILENAME_RE = re.compile(r"^(?P<seq>\d{4})-(?P<machine>[a-z0-9]{1,16})$")
NEW_FILENAME_RE = re.compile(r"^(?P<ts>\d{8}T\d{6}Z)-(?P<machine>[a-z0-9]{1,16})$")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
CREATED_AT_RE = re.compile(r"^created_at:\s*['\"]?(?P<v>[^'\"\n]+?)['\"]?\s*$", re.MULTILINE)


@dataclass
class CommentRename:
    old: Path
    new: Path
    issue_n: int


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _parse_created_at(path: Path) -> datetime:
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SystemExit(f"missing frontmatter in {path}")
    fm = m.group(1)
    cm = CREATED_AT_RE.search(fm)
    if not cm:
        raise SystemExit(f"missing 'created_at' in frontmatter of {path}")
    raw = cm.group("v").strip()
    # 例: "2026-05-10T07:09:36Z"。fromisoformat は Python 3.11+ で "Z" を解釈する
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"invalid 'created_at' {raw!r} in {path}: {exc}") from exc


def _compact_ts(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _issue_n_from_dirname(dirname: str) -> int | None:
    m = re.match(rf"^local-{re.escape(OLD_MACHINE)}-([1-9]\d*)(?:-|$)", dirname)
    return int(m.group(1)) if m else None


def _plan_comment_renames(repo: Path) -> list[CommentRename]:
    issues_dir = repo / ".kaji" / "issues"
    plans: list[CommentRename] = []
    for issue_dir in sorted(issues_dir.iterdir()):
        if not issue_dir.is_dir():
            continue
        n = _issue_n_from_dirname(issue_dir.name)
        if n is None:
            continue
        cdir = issue_dir / "comments"
        if not cdir.is_dir():
            continue

        # 既存形式の comment を seq 昇順で収集
        old_comments: list[tuple[int, Path]] = []
        for path in sorted(cdir.iterdir()):
            if path.suffix != ".md":
                continue
            m = OLD_FILENAME_RE.match(path.stem)
            if m is None:
                # 既に新形式 → スキップ（idempotent）
                if NEW_FILENAME_RE.match(path.stem):
                    continue
                raise SystemExit(f"unrecognized filename in old layout: {path}")
            old_comments.append((int(m.group("seq")), path))
        old_comments.sort(key=lambda x: x[0])

        # filename uniqueness 用の +1s 衝突解消
        used_ts: dict[str, int] = defaultdict(int)
        target_machine = OLD_MACHINE if n == BOUNDARY_ISSUE_N else NEW_MACHINE
        for _, path in old_comments:
            base_dt = _parse_created_at(path)
            offset = 0
            while True:
                from datetime import timedelta

                ts = _compact_ts(base_dt + timedelta(seconds=offset))
                key = f"{ts}-{target_machine}"
                if used_ts[key] == 0:
                    used_ts[key] = 1
                    break
                offset += 1
            new_path = cdir / f"{ts}-{target_machine}.md"
            plans.append(CommentRename(old=path, new=new_path, issue_n=n))
    return plans


def _phase_rename_comments(repo: Path, dry_run: bool) -> None:
    plans = _plan_comment_renames(repo)
    print(f"[rename-comments] {len(plans)} files")
    for p in plans:
        rel_old = p.old.relative_to(repo)
        rel_new = p.new.relative_to(repo)
        print(f"  {rel_old} -> {rel_new}")
        if dry_run:
            continue
        if p.new.exists():
            raise SystemExit(f"target already exists: {p.new}")
        _git(repo, "mv", str(rel_old), str(rel_new))


def _phase_rename_dirs(repo: Path, dry_run: bool) -> None:
    issues_dir = repo / ".kaji" / "issues"
    targets: list[tuple[Path, Path, int]] = []
    for issue_dir in sorted(issues_dir.iterdir()):
        n = _issue_n_from_dirname(issue_dir.name)
        if n is None or n not in MIGRATE_RANGE:
            continue
        new_name = issue_dir.name.replace(f"local-{OLD_MACHINE}-", f"local-{NEW_MACHINE}-", 1)
        targets.append((issue_dir, issue_dir.with_name(new_name), n))

    print(f"[rename-dirs] {len(targets)} dirs (issues 1..20 only; 21 stays put)")
    # cross-ref rewrite: N=1..20 only. 21 references protected.
    cross_ref_re = re.compile(rf"local-{re.escape(OLD_MACHINE)}-(?P<n>[1-9]|1[0-9]|20)\b")

    # Step 1: rewrite frontmatter id + body cross-refs in *all* issue.md files
    # （1..20 + 21 自身の本文中で 1..20 へ言及がある可能性）。21 への参照は
    # cross_ref_re が N=1..20 のみマッチするので保護される。
    all_issue_dirs = [
        d
        for d in sorted(issues_dir.iterdir())
        if d.is_dir() and _issue_n_from_dirname(d.name) is not None
    ]
    for d in all_issue_dirs:
        issue_md = d / "issue.md"
        if not issue_md.is_file():
            continue
        text = issue_md.read_text(encoding="utf-8")
        new_text = cross_ref_re.sub(lambda m: f"local-{NEW_MACHINE}-{m.group('n')}", text)
        if new_text == text:
            continue
        rel = issue_md.relative_to(repo)
        print(f"  rewrite cross-refs: {rel}")
        if not dry_run:
            issue_md.write_text(new_text, encoding="utf-8")

    # Step 2: git mv each migrated dir
    for old_dir, new_dir, _ in targets:
        rel_old = old_dir.relative_to(repo)
        rel_new = new_dir.relative_to(repo)
        print(f"  {rel_old} -> {rel_new}")
        if dry_run:
            continue
        if new_dir.exists():
            raise SystemExit(f"target dir already exists: {new_dir}")
        _git(repo, "mv", str(rel_old), str(rel_new))


def _resolve_repo_main(repo: Path) -> Path:
    """worktree から main repo の root を解決する。

    優先順:
        1. ``.kaji/config.local.toml`` が symlink ならその実体の親の親
           （symlink target = ``<main-repo>/.kaji/config.local.toml``）
        2. ``git rev-parse --git-common-dir`` の親
        3. ``repo`` 自身（main repo として実行された場合）

    解決不能な場合は ``SystemExit`` で fail-fast。完了条件
    （``.kaji/counters/p1.txt`` 存在 / ``pc5090.txt`` 削除 / config 切替）
    を保証するため、warning でなく error にする。
    """
    config_link = repo / ".kaji" / "config.local.toml"
    if config_link.is_symlink():
        target = config_link.resolve()
        # target = <main-repo>/.kaji/config.local.toml
        if target.parent.name == ".kaji":
            return target.parent.parent

    # git common dir 経由（worktree → main repo の git metadata）
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return repo
    common = Path(out)
    if common.name == ".git":
        return common.parent
    # bare repo: common == bare repo dir。main worktree dir は別途設定が必要。
    # 既知の構成では config.local.toml symlink で解決できるはずなので、ここは
    # fail-fast して --repo-main 明示を促す。
    return repo


def _phase_config(repo_main: Path, dry_run: bool) -> None:
    """gitignored な config / counter の rename / 書き換え。

    .kaji/config.local.toml と .kaji/counters/ は worktree では symlink /
    不在のため、main repo (``repo_main``) 上で操作する必要がある。

    完了条件保証のため、counter file が見つからない / config に旧値が残る
    場合は ``SystemExit`` で fail-fast する（warning では migration の
    完了確認が漏れるため）。
    """
    counter_old = repo_main / ".kaji" / "counters" / f"{OLD_MACHINE}.txt"
    counter_new = repo_main / ".kaji" / "counters" / f"{NEW_MACHINE}.txt"
    config = repo_main / ".kaji" / "config.local.toml"

    print(f"[config] repo_main={repo_main}")
    if counter_old.is_file():
        print(
            f"  rename {counter_old.relative_to(repo_main)} -> {counter_new.relative_to(repo_main)}"
        )
        if not dry_run:
            counter_new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(counter_old), str(counter_new))
    elif counter_new.is_file():
        print(f"  counter already at {counter_new.relative_to(repo_main)} (idempotent)")
    else:
        raise SystemExit(
            f"no counter file found at {counter_old} or {counter_new}. "
            f"Pass --repo-main pointing to the directory containing "
            f"'.kaji/counters/{OLD_MACHINE}.txt'."
        )

    if not config.is_file():
        raise SystemExit(
            f"no config at {config}. Pass --repo-main pointing to the directory "
            f"containing '.kaji/config.local.toml'."
        )
    text = config.read_text(encoding="utf-8")
    new_text = re.sub(
        rf'machine_id\s*=\s*"{re.escape(OLD_MACHINE)}"',
        f'machine_id = "{NEW_MACHINE}"',
        text,
    )
    if new_text != text:
        print(f"  set machine_id = {NEW_MACHINE!r} in {config.relative_to(repo_main)}")
        if not dry_run:
            config.write_text(new_text, encoding="utf-8")
    else:
        # 既に p1 になっているか、そもそも machine_id 行が無いかを区別する
        if f'"{NEW_MACHINE}"' in text:
            print(f"  config already migrated (idempotent): {config}")
        else:
            raise SystemExit(
                f"config at {config} has no 'machine_id = \"{OLD_MACHINE}\"' line "
                f"and no 'machine_id = \"{NEW_MACHINE}\"' either. Inspect manually."
            )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--phase",
        required=True,
        choices=["rename-comments", "rename-dirs", "config", "all"],
    )
    p.add_argument("--repo", default=os.getcwd(), help="worktree (or repo) path")
    p.add_argument(
        "--repo-main",
        default=None,
        help="main repo path for gitignored .kaji/counters and config.local.toml. "
        "Auto-detected from .kaji/config.local.toml symlink or "
        "'git rev-parse --git-common-dir' when omitted.",
    )
    p.add_argument("--dry-run", action="store_true")
    ns = p.parse_args(argv)

    repo = Path(ns.repo).resolve()
    repo_main = Path(ns.repo_main).resolve() if ns.repo_main else _resolve_repo_main(repo)

    phases = ["rename-comments", "rename-dirs", "config"] if ns.phase == "all" else [ns.phase]
    for phase in phases:
        if phase == "rename-comments":
            _phase_rename_comments(repo, ns.dry_run)
        elif phase == "rename-dirs":
            _phase_rename_dirs(repo, ns.dry_run)
        elif phase == "config":
            _phase_config(repo_main, ns.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
