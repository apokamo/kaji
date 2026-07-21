.PHONY: check lint format fmt typecheck validate-workflows test test-small test-medium \
        test-large test-large-local verify-docs verify-packaging setup help

SOURCES := kaji_harness/ tests/ experiments/

check: lint format typecheck validate-workflows test

lint:
	ruff check $(SOURCES)

format:
	ruff format --check $(SOURCES)

fmt:
	ruff format $(SOURCES)

typecheck:
	mypy kaji_harness/

validate-workflows:
	@files=$$(git ls-files -- '.kaji/wf' | grep '\.yaml$$'); \
	if [ -z "$$files" ]; then \
	  echo "no tracked workflow YAML under .kaji/wf/" >&2; exit 1; \
	fi; \
	kaji validate $$files

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
	python3 scripts/check_doc_links.py docs/ README.md README.ja.md CLAUDE.md .claude/skills/ AGENTS.md

verify-packaging:
	@scripts/verify-packaging.sh

setup:
	uv sync

help:
	@echo "Common targets:"
	@echo "  make check               - lint + format(--check) + typecheck + workflow validation + test (non-mutating gate)"
	@echo "  make fmt                 - apply ruff format (mutating)"
	@echo "  make validate-workflows  - validate tracked official + custom workflows with L1/L2/L3 preflight"
	@echo "  make test                - pytest (all markers)"
	@echo "  make test-small          - pytest -m small"
	@echo "  make test-medium         - pytest -m medium"
	@echo "  make test-large          - pytest -m large"
	@echo "  make test-large-local    - pytest -m large_local"
	@echo "  make verify-docs         - run doc link checker"
	@echo "  make verify-packaging    - isolated uv install + metadata check"
