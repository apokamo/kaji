# Failure Triage / Recovery CLI

Language: English | [日本語](failure-recovery.ja.md)

CLI reference for the failure triage and auto recovery layer (Issue #288). It applies to both
`provider.type = "github"` and `provider.type = "local"`; the triage comment is posted through the
active provider.

For the operational rules (what is and is not resumable, why the wait exists), see
[Workflow guide](../dev/workflow_guide.md) § failure triage と自動再開. For the config keys, see
[Configuration reference](../reference/configuration.md) § `[execution]`.

## What runs when

`kaji run` classifies the failure and records evidence when the workflow process ends with `ERROR`,
or with an `ABORT` that is eligible for triage. Failures that happen **before the run directory is
created** (config discovery, workflow validation, `IssueContext` resolution) are not triaged: there
is no artifact to reason about, and an evidence-free Issue comment is worse than none.

Two layers exist and do not overlap:

| Layer | Scope | Timescale | Where |
|-------|-------|-----------|-------|
| attempt retry | transient CLI failure inside one step dispatch | seconds to minutes, in-process | `execute_cli()` |
| run recovery | `ERROR` / triage-eligible `ABORT` at the end of the workflow process | fixed 10-minute wait, then a new `kaji run` | this document |

## `kaji run` options

| flag | Default | Meaning |
|------|---------|---------|
| `--failure-triage` / `--no-failure-triage` | config (`true`) | Classify the failure, post the triage comment, write `recovery.json` / `run.log`, print the stderr summary |
| `--auto-recover` / `--no-auto-recover` | config (`false`) | Start one child run per recovery chain when the decision is `resume` |
| `--recovery-root <run_id>` | — | Root run_id of the recovery chain (normally added by the handler) |
| `--recovery-parent <run_id>` | — | Direct parent run_id. Requires `--recovery-root`; alone it exits `2` |

Precedence matches `--agent-runner`: CLI flag > `.kaji/config.local.toml` > `.kaji/config.toml`.
`--no-failure-triage` also forces `auto_recover` off, because the handler that would start the child
run never executes.

```bash
# 1. Normal operation: triage on, auto recovery off (defaults)
kaji run .kaji/wf/dev.yaml 288
# → on ERROR: triage comment on the Issue, recovery.json saved, summary on stderr. exit 3

# 2. Opt in to auto recovery
kaji run .kaji/wf/dev.yaml 288 --auto-recover
# → decision: resume starts a child run after 10 minutes. The parent's exit code is the child's

# 3. The command the handler itself runs (you normally do not type this)
kaji run .kaji/wf/dev.yaml 288 --from review-code \
  --recovery-root 260710120000 --recovery-parent 260710120000
```

## `kaji recover`

Runs the same handler against an already-failed run's artifacts. Use it to investigate, to re-render
the triage report after a provider outage, or to opt into a resume after the fact.

```
kaji recover <workflow.yaml> <issue> [--run-id <run_id>] [--auto-recover] [--workdir <dir>]
```

- `--run-id` defaults to the newest run under `<artifacts_dir>/<issue>/runs/`.
- If the target run has no `workflow_end` event, `kaji recover` refuses with `2`. This prevents
  interfering with a run that is still executing.
- If the target run ended with a status other than `ERROR` / `ABORT`, it also exits `2`.
- `<workflow.yaml>` is used to resolve the resume point and to build the resume command; pointing at
  a different workflow than the one the run used is the operator's responsibility (the workflow path
  is recorded in `recovery.json`).

```bash
kaji recover .kaji/wf/dev.yaml 288
kaji recover .kaji/wf/dev.yaml 288 --run-id 260710120000
```

## Exit codes

The existing map (`0 = OK`, `1 = ABORT`, `2 = definition error`, `3 = runtime error`) is unchanged.

| Situation | Exit code |
|-----------|-----------|
| `kaji run` with triage only (no child run) | the original failure's exit code |
| `kaji run` that started a child run | the child's exit code (the chain's final result) |
| `kaji recover`, triage completed (any decision) | `0` |
| `kaji recover`, run not found / still in progress / flag mismatch | `2` |
| `kaji recover`, handler internal error | `3` |

## Artifacts

| Path | Content |
|------|---------|
| `runs/<run_id>/recovery.json` | `RecoveryDecision` (`schema_version: 1`), overwritten on every decision update |
| `runs/<run_id>/recovery-chain.json` | `{root_run_id, parent_run_id}`, written by a recovery child run at startup |
| `runs/<run_id>/run.log` | `failure_event`, `recovery_decision`, `recovery_scheduled`, `recovery_attempt_start`, `recovery_attempt_end` |
| Issue comment | Machine-generated triage report. No kaji-verdict marker (it is not a step verdict) |
| stderr | A short `--- failure triage ---` summary printed after the existing terminal message |

The `comment:` line of the stderr summary shows `Comment.ref`: the created comment URL for the
GitHub provider, the repo-root-relative comment file path for the local provider, and `n/a` when the
reference could not be captured.

## Related documents

- [Workflow guide](../dev/workflow_guide.md) — operational rules, non-resumable cases
- [Configuration reference](../reference/configuration.md) — `[execution] failure_triage` / `auto_recover`
- [Architecture](../ARCHITECTURE.md) — recovery layer and `kaji_harness/recovery/` package
