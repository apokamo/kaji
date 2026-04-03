#!/usr/bin/env bash
set -euo pipefail

TMPDIR_PKG=$(mktemp -d)
trap 'rm -rf "$TMPDIR_PKG"' EXIT

echo "==> Creating temporary venv in $TMPDIR_PKG/venv ..."
python3 -m venv "$TMPDIR_PKG/venv"

echo "==> Installing package in editable mode ..."
"$TMPDIR_PKG/venv/bin/pip" install -e . --quiet

echo "==> Checking entry point (kaji --help) ..."
"$TMPDIR_PKG/venv/bin/kaji" --help > /dev/null

echo "==> Checking package metadata ..."
"$TMPDIR_PKG/venv/bin/python" -c "
import importlib.metadata
meta = importlib.metadata.metadata('kaji')
print(f\"  Name: {meta['Name']}\")
print(f\"  Version: {meta['Version']}\")
"

echo "==> verify-packaging: OK"
