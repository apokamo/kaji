# Herdr backend implementation validation

Issue: #396

## Implemented surfaces

- explicit config/overlay/CLI backend selection with tmux default
- Herdr 0.8.2 caller-context preflight
- explicit split/run/read/process/metadata/close CLI requests
- response-derived pane IDs and source-scoped ownership tokens
- first-right/later-down placement and maximum-two owned panes
- artifact-driven completion, early shell-return detection, timeout, and ownership-checked cleanup
- rendered snapshot metadata and existing provider-error diagnostic reuse
- failure-recovery user-precondition classification
- repository `herdr-kaji-launch` skill for Claude Code and Codex
- English/Japanese configuration, guide, architecture, ADR, and recovery documentation

## Focused validation

The focused Herdr suite reached:

```text
18 passed
ruff check: passed
mypy kaji_harness/interactive_terminal_herdr.py: passed
```

The stateful fake CLI smoke also passed after the documented `PYTHONPATH=.` correction. It crossed
the actual subprocess/JSON boundary and observed nine requests through ownership-checked close.

## Module-boundary validation

The first layer-fitness run found the new module was not yet classified:

```text
ValueError: unclassified module: kaji_harness.interactive_terminal_herdr
```

After adding it to the application-layer map:

```text
37 passed
```

## Full check

The first `make check` stopped at format-check only:

```text
ruff check: passed
ruff format --check: 3 files would be reformatted
```

After applying the mechanical formatting, the same command completed:

```text
ruff check: passed
ruff format --check: 218 files already formatted
mypy: Success: no issues found in 78 source files
workflow validation: all listed workflows passed
pytest: 2743 passed, 10 skipped in 190.83s
```

`git diff --check` and the skill-creator `quick_validate.py` also passed. The Codex compatibility
symlink resolves to the canonical Claude skill.

## Full check rerun after experiment additions

The complete check was rerun on 2026-08-21 after adding the guarded live smoke, fake Claude,
metadata persistence report, and explicit no-TTL ownership assertion:

```text
ruff check kaji_harness/ tests/ experiments/: passed
ruff format --check: 219 files already formatted
mypy: Success: no issues found in 78 source files
workflow validation: all listed workflows passed
pytest: 2743 passed, 10 skipped in 189.99s
```

This rerun exercised every committed and untracked Python experiment under the same repository check.

## Not yet validated

- real Herdr pane lifecycle against this implementation
- real Claude/Codex fresh and resume behavior in Herdr
- real alternate-screen rendered snapshot behavior
- real pane retention and prune
- a Codex/Claude process inside Herdr invoking `herdr-kaji-launch`
- destructive server/session/process tests

The current agent process is outside Herdr and therefore cannot perform these operations under the
release-matched Herdr skill guardrail. They must be initiated from an agent with `HERDR_ENV=1`.
