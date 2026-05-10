.PHONY: check lint format typecheck test test-small test-medium test-large \
        test-large-local test-large-gitlab verify-docs verify-packaging setup help

SOURCES := kaji_harness/ tests/

check: lint format typecheck test

lint:
	ruff check $(SOURCES)

format:
	ruff format $(SOURCES)

typecheck:
	mypy kaji_harness/

# Default test run excludes `large_gitlab` (requires real GitLab API + glab auth +
# KAJI_TEST_GITLAB_REPO). Use `make test-large-gitlab` to run that suite.
test:
	pytest -m "not large_gitlab"

test-small:
	pytest -m small

test-medium:
	pytest -m medium

test-large:
	pytest -m large

test-large-local:
	pytest -m large_local

# Provider=gitlab E2E. Prerequisites:
#   - `glab` CLI on PATH
#   - GITLAB_TOKEN env OR `glab auth status` succeeds
#   - KAJI_TEST_GITLAB_REPO=<group>/<project> (a dedicated test fixture project)
#   - Optional: KAJI_TEST_GITLAB_DEFAULT_BRANCH (default: main)
# Without these, individual tests skip with explanatory messages.
test-large-gitlab:
	pytest -m large_gitlab

verify-docs:
	python3 scripts/check_doc_links.py docs/ README.md CLAUDE.md .claude/skills/

verify-packaging:
	@scripts/verify-packaging.sh

setup:
	uv sync

help:
	@echo "Common targets:"
	@echo "  make check               - lint + format + typecheck + test (excludes large_gitlab)"
	@echo "  make test                - pytest, excluding large_gitlab"
	@echo "  make test-small          - pytest -m small"
	@echo "  make test-medium         - pytest -m medium"
	@echo "  make test-large          - pytest -m large"
	@echo "  make test-large-local    - pytest -m large_local"
	@echo "  make test-large-gitlab   - pytest -m large_gitlab (requires glab auth +"
	@echo "                             GITLAB_TOKEN or glab auth status, and"
	@echo "                             KAJI_TEST_GITLAB_REPO=<group>/<project>)"
	@echo "  make verify-docs         - run doc link checker"
	@echo "  make verify-packaging    - isolated uv install + metadata check"
