.PHONY: check lint format typecheck test test-small test-medium test-large \
        test-large-local verify-docs verify-packaging setup

SOURCES := kaji_harness/ tests/

check: lint format typecheck test

lint:
	ruff check $(SOURCES)

format:
	ruff format $(SOURCES)

typecheck:
	mypy kaji_harness/

test:
	pytest

test-small:
	pytest -m small

test-medium:
	pytest -m medium

test-large:
	pytest -m large

test-large-local:
	pytest -m large_local

verify-docs:
	python3 scripts/check_doc_links.py docs/ README.md CLAUDE.md .claude/skills/

verify-packaging:
	@scripts/verify-packaging.sh

setup:
	uv sync
