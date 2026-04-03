#!/usr/bin/env python3
"""Markdown link validator.

Checks that relative Markdown links resolve to existing files
and that fragment identifiers reference existing headings.

Usage:
    python3 scripts/check_doc_links.py              # Check docs/ directory
    python3 scripts/check_doc_links.py <path>...     # Check specific files/dirs
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

MARKDOWN_EXT = ".md"
DEFAULT_TARGET = "docs"

# Matches [text](target) but NOT ![text](target)
LINK_PATTERN = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+(?:\s+\"[^\"]*\")?)\)")

HEADING_PATTERN = re.compile(r"^ {0,3}(#{1,6})\s+(.*)$")

EXTERNAL_PREFIXES = ("https://", "http://", "mailto:", "tel:", "ftp://")


def main() -> int:
    args = sys.argv[1:]
    repo_root = Path.cwd()

    if args:
        md_files, breakdown = collect_from_args(args, repo_root)
    else:
        default_files = collect_from_directory(repo_root / DEFAULT_TARGET)
        md_files = default_files
        breakdown = {DEFAULT_TARGET: len(default_files)}

    if not md_files:
        print("No Markdown files to check.", file=sys.stderr)
        return 0

    errors = validate_all(md_files, repo_root)

    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        return 1

    detail = ", ".join(f"{arg}: {count}" for arg, count in breakdown.items())
    print(f"All Markdown links valid ({len(md_files)} file(s) checked: {detail}).")
    return 0


def collect_from_args(
    args: list[str], repo_root: Path
) -> tuple[list[Path], dict[str, int]]:
    files: list[Path] = []
    breakdown: dict[str, int] = {}
    for arg in args:
        p = Path(arg)
        if not p.is_absolute():
            p = repo_root / p
        before = len(files)
        if p.is_dir():
            files.extend(collect_from_directory(p))
        elif p.is_file() and p.suffix == MARKDOWN_EXT:
            files.append(p)
        breakdown[arg] = len(files) - before
    return files, breakdown


def collect_from_directory(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        p
        for p in directory.rglob(f"*{MARKDOWN_EXT}")
        if not _is_hidden(p.relative_to(directory))
    )


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def validate_all(files: list[Path], repo_root: Path) -> list[str]:
    heading_cache: dict[Path, set[str]] = {}
    errors: list[str] = []

    for filepath in files:
        content = filepath.read_text(encoding="utf-8")
        lines = content.split("\n")

        for match in LINK_PATTERN.finditer(content):
            raw_target = match.group(1).split()[0]
            line_num = _index_to_line(match.start(), lines)
            err = validate_link(filepath, raw_target, line_num, repo_root, heading_cache)
            if err:
                rel = filepath.relative_to(repo_root)
                errors.append(f"{rel}:{line_num}: {err}")

    return errors


def validate_link(
    source: Path,
    raw_target: str,
    line: int,
    repo_root: Path,
    heading_cache: dict[Path, set[str]],
) -> str | None:
    target = raw_target.strip()
    if not target or _is_external(target):
        return None

    fragment = ""
    hash_idx = target.find("#")
    if hash_idx != -1:
        fragment = target[hash_idx + 1 :]
        target = target[:hash_idx]

    if target.startswith("?"):
        return None

    if target == "" or target == "#":
        resolved = source
    elif target.startswith("/"):
        resolved = repo_root / target.lstrip("/")
    else:
        resolved = (source.parent / target).resolve()

    # Reject links that resolve outside the repo root
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        return f"link resolves outside repository: {raw_target}"

    resolved = _resolve_path(resolved)
    if resolved is None:
        return f"broken link: {raw_target}"

    if fragment and resolved.is_file() and resolved.suffix == MARKDOWN_EXT:
        slugs = _get_headings(resolved, heading_cache)
        if fragment not in slugs:
            return f"missing anchor '{fragment}' in {resolved.relative_to(repo_root)}"

    return None


def _resolve_path(candidate: Path) -> Path | None:
    if candidate.exists():
        return candidate
    md_candidate = candidate.with_suffix(MARKDOWN_EXT)
    if md_candidate.exists():
        return md_candidate
    readme = candidate / "README.md"
    if readme.exists():
        return readme
    return None


def _is_external(target: str) -> bool:
    return any(target.startswith(prefix) for prefix in EXTERNAL_PREFIXES)


def _get_headings(filepath: Path, cache: dict[Path, set[str]]) -> set[str]:
    if filepath in cache:
        return cache[filepath]

    content = filepath.read_text(encoding="utf-8")
    slugs: set[str] = set()
    slug_counts: dict[str, int] = {}

    for line in content.split("\n"):
        m = HEADING_PATTERN.match(line)
        if not m:
            continue
        text = m.group(2).strip()
        text = re.sub(r"\s+#+\s*$", "", text).strip()
        if not text:
            continue
        slugs.add(_slugify(text, slug_counts))

    cache[filepath] = slugs
    return slugs


def _slugify(text: str, slug_counts: dict[str, int]) -> str:
    slug = text.strip().lower()
    # Remove control characters
    slug = re.sub(r"[\x00-\x1f]", "", slug)
    # Remove punctuation and symbols
    slug = "".join(
        c for c in slug if not unicodedata.category(c).startswith(("P", "S"))
    )
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")

    if not slug:
        slug = "section"

    count = slug_counts.get(slug, 0)
    slug_counts[slug] = count + 1
    return slug if count == 0 else f"{slug}-{count}"


def _index_to_line(index: int, lines: list[str]) -> int:
    total = 0
    for i, line in enumerate(lines):
        total += len(line) + 1
        if index < total:
            return i + 1
    return len(lines)


if __name__ == "__main__":
    sys.exit(main())
