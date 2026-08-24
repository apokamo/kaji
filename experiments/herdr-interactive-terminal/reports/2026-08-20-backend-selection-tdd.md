# Backend selection TDD baseline

Issue: #396

## Purpose

Fix the public selection contract before implementing Herdr pane control:

- config default remains `tmux`
- config accepts only `tmux` or `herdr`
- `config.local.toml` can override the backend
- `kaji run --interactive-terminal-backend` has the highest precedence
- `WorkflowRunner` passes the resolved backend to the interactive runner

## Command

```bash
source .venv/bin/activate
pytest -q \
  tests/test_config.py::TestExecutionRunnerConfig \
  tests/test_config.py::TestExecutionOverlay \
  tests/test_run_execution_overrides.py \
  tests/test_runner_interactive_dispatch.py::TestRunnerBackendDispatch::test_interactive_terminal_config_routes_to_interactive_runner \
  tests/test_runner_interactive_dispatch.py::TestRunnerBackendDispatch::test_herdr_backend_is_threaded_to_interactive_runner
```

## Result before product implementation

```text
28 collected
12 passed
16 failed
```

Every failure was expected and belonged to one of these missing surfaces:

- `ExecutionConfig.interactive_terminal_backend`
- config validation for the new key
- argparse option `--interactive-terminal-backend`
- CLI override application
- runner-to-interactive-runner parameter threading

There were no live Herdr calls and no pane/session/process mutations in this test.

## Decision

Implement the selection seam first. Keep `tmux` as the built-in default and require explicit
`herdr` selection; do not perform environment-based auto-detection or fallback.

## Result after selection-seam implementation

The same command was rerun after adding config parsing, CLI override handling, and runner
parameter threading:

```text
28 passed in 0.81s
```

This validates only backend selection and propagation. It does not claim that Herdr pane lifecycle
is implemented yet.

## Herdr lifecycle TDD red baseline

`tests/test_interactive_terminal_herdr.py` was then added for preflight, explicit-ID argv,
ownership markers, JSON failures, verdict-driven snapshot, and marker-failure cleanup safety.

Initial collection stopped with the expected missing symbol:

```text
ImportError: cannot import name 'HerdrSessionRequiredError' from 'kaji_harness.errors'
```

No Herdr command was executed by either test run.

## Herdr lifecycle initial green

The initial Herdr module and its user-precondition classification were implemented, then the test
scope was expanded to the full existing tmux suite and the adjacent config/dispatch/recovery suites:

```bash
source .venv/bin/activate
pytest -q \
  tests/test_interactive_terminal.py \
  tests/test_interactive_terminal_herdr.py \
  tests/test_runner_interactive_dispatch.py \
  tests/test_config.py::TestExecutionRunnerConfig \
  tests/test_config.py::TestExecutionOverlay \
  tests/test_run_execution_overrides.py \
  tests/test_recovery_classify.py \
  tests/test_recovery_plan.py
```

Result:

```text
207 passed in 7.82s
```

Static checks at this checkpoint:

```text
ruff check: passed
ruff format --check: initially requested two mechanical reformats
ruff format: reformatted the two new Herdr test/module files
ruff check after formatting: passed
mypy (five affected source modules): passed
```

These tests use mocks only. They validate command construction, response parsing, ownership checks,
artifact-driven completion, transcript metadata, and error classification without controlling the
live Herdr session. Real-pane lifecycle and real-agent behavior remain separate validations.
