# Failure Triage / Recovery CLI

Language: English | [日本語](failure-recovery.ja.md)

CLI reference for the failure triage and auto recovery layer (Issue #288). The triage comment is
posted through the active provider (`provider.type = "github"`).

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

### Interactive terminal: transient provider errors buried in the transcript (Issue #296)

When `agent_runner = "interactive_terminal"` and the agent in a Herdr pane exits (its pane returns
to the shell) before writing `verdict.yaml`, kaji scans the **entire** `terminal.log` transcript
(the raw PTY recording written by kaji's recorder, including alternate-screen output) (not just the last 2000
characters) for a known transient pattern (`"at capacity"`, `"rate limit"`, `"overloaded"`, …
— the same list `execute_cli()` uses). TUI redraw can bury a one-line provider error deep in a
transcript far larger than the old tail window, so a full-transcript scan is required to avoid
misclassifying a transient capacity error as non-recoverable.

Only the matched pattern **literal** is placed in `CLIExecutionError` / `result.json.error`
(e.g. `"...transient provider error detected (pattern: 'at capacity')"`) — never a transcript
substring. This keeps unrelated text on the same physical line (such as a `Token usage:`
telemetry line) out of the classifier/sensitive-gate input, so it cannot accidentally trip the
credential-leak gate. The full ANSI-stripped excerpt and tail are kept for humans only, in
`pane-metadata.json`'s `terminal_diagnostic` key (`kind`: `provider_error` / `no_pattern` /
`no_log` / `empty`), which the classifier never reads.

The resulting `dispatch_failure` classification and `--auto-recover` behavior (candidate →
resume after 10 minutes, or `comment_only` when auto recovery is off) follow the same rules as
any other transient dispatch failure described below.

### Verdict YAML forbidden control characters no longer trigger re-execution (Issue #298)

Previously, a raw control character outside the YAML 1.2 printable range (e.g. ESC / `U+001B`)
inside a `verdict.yaml` evidence/reason field made `_parse_yaml_fields` raise `VerdictParseError`,
even when the step's external side effects (commit, push, comment) had already completed. The
runner converted this into a `verdict_exception` failure event and a synthetic `ABORT`, and
recovery classified it as `verdict_resolution_failure` — presenting the same, already-completed
step as the resume point.

`_parse_yaml_fields` now sanitizes YAML-forbidden control characters (replacing them with
`U+FFFD`) before parsing, so the artifact's real verdict resolves normally and this failure mode
no longer reaches recovery at all. TAB / LF / CR and other characters YAML permits are left
untouched. Sanitized codepoints and their position are recorded in `run.log` as a
`verdict_sanitization` event (see [logging reference](../reference/python/logging.md) §
`verdict_sanitization`) — never the raw control character itself. Verdict resolution failures
unrelated to control characters (e.g. a missing `status` field) still raise `VerdictParseError`
and are classified as `verdict_resolution_failure` exactly as before.

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
kaji run .kaji/wf/official/dev.yaml 288
# → on ERROR: triage comment on the Issue, recovery.json saved, summary on stderr. exit 3

# 2. Opt in to auto recovery
kaji run .kaji/wf/official/dev.yaml 288 --auto-recover
# → decision: resume starts a child run after 10 minutes. The parent's exit code is the child's

# 3. The command the handler itself runs (you normally do not type this)
kaji run .kaji/wf/official/dev.yaml 288 --from review-code \
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
- Re-running it against a run that already performed an auto recovery yields `decision: exhausted`.
  The budget is one resume per recovery chain; `recovery.json` and the child run directory are the
  inputs to that decision.
- For runs produced **before** this feature (their `run.log` `workflow_start` carries no
  `schema_version`), a missing `failure_event` is not treated as a harness contradiction, so no bug
  issue is filed.

```bash
kaji recover .kaji/wf/official/dev.yaml 288
kaji recover .kaji/wf/official/dev.yaml 288 --run-id 260710120000
```

## `kaji recover --retry-failed-step` (Issue #540)

Retries one failed run once, from its failed step, after kaji itself checks the safety
gates. It does not use the triage decision: a failure that triage does not mark `resume`
(a usage limit longer than the step timeout, an OOM kill, a transient provider error) can
be retried. The caller does not rebuild `kaji run --from` or check the gates.

```
kaji recover <workflow.yaml> <issue> --retry-failed-step [--run-id <run_id>] [--workdir <dir>]
```

- `--retry-failed-step` and `--auto-recover` are mutually exclusive.
- `--run-id` defaults to the newest run. A run without `workflow_end` can be retried when
  kaji can tell its process is gone (`termination: vanished`, see
  [Run status](run-status.md)); the failed step is then the last step that started and
  did not end.
- The recovery budget (one auto resume per chain) does not apply. The run you name must be
  the newest run, so each run can be retried at most once. A recovery child that failed
  can itself be retried.
- When the gates pass, kaji starts the child run in the foreground without waiting:
  `kaji run <workflow> <issue> --from <step> --recovery-root <root> --recovery-parent <run_id>
  --recovery-trigger retry_failed_step`. A step with `resume:` restarts from the step that
  created the session (the stored session is discarded), as in triage.
- kaji writes nothing to GitHub (no Issue comment, no bug Issue, no incident). It records the
  request in the target run's `recovery.json` (`retries[]`) and `run.log`
  (`retry_requested` / `retry_refused` / `retry_child_end`). The child run's own triage on
  failure follows the usual `kaji run` settings; because the child belongs to the recovery
  chain, its auto recovery is `exhausted`.

Exit codes:

| Situation | Exit code |
|-----------|-----------|
| Workflow missing / invalid, no config, Issue cannot be resolved, `--run-id` dir missing, both flags given | `2` |
| A record could not be written before launch (launch lock, launcher record, `recovery.json` / `run.log`), or the child could not be started. Nothing was started | `3` |
| Refused by a safety gate. Nothing was started | `4` |
| The child run was started | the child's exit code |

On refusal, stderr has one summary line and one `retry-gate:` line per refusing gate
(`^retry-gate: (?P<name>[a-z_]+)(?: (?P<detail>.*))?$`):

```
Error: retry refused by safety gate(s): run_alive, branch_mismatch
retry-gate: run_alive pid=12345 basis=pid_verified
retry-gate: branch_mismatch worktree=feat/9 state=feat/540
```

### Safety gates

kaji evaluates every gate and lists all that refuse, in this order (`concurrent_launch`
alone is reported without evaluating the others). The names are part of the public
contract; `kaji run-series --resume` uses the same gates.

| Name | Refuses when |
|------|--------------|
| `concurrent_launch` | Another retry / series resume is deciding for the same Issue (the Issue's launch lock is held) |
| `no_target_run` | The Issue has no run |
| `newer_run_detected` | The target run is not the newest run |
| `run_alive` | The target run's process (or a launch record pointing at it) is alive |
| `liveness_unknown` | The target run's liveness cannot be judged (no record, other host, no `/proc`) |
| `issue_run_alive` | Another run of the Issue, or a launch record not pointing at the target run, is alive |
| `issue_liveness_unknown` | None of those is alive but at least one cannot be judged |
| `run_not_failed` | The run ended with a status other than `ERROR` / `ABORT` |
| `artifact_unreadable` | A required artifact is missing or broken — unreadable, not UTF-8, invalid JSON, or (for `result.json`) a missing required key / wrong type (run.log, `result.json` of an attempt that has `step_end`, `session-state.json`, `recovery-chain.json`, `recovery.json`) |
| `workflow_mismatch` | The run's workflow name differs from `<workflow.yaml>` |
| `unknown_failed_step` | The failed step cannot be determined or is not in the workflow |
| `non_resumable_skill` | The failed step or the actual restart step runs `issue-start` / `i-pr` / `issue-close` |
| `agent_declared_abort` / `cycle_exhausted` / `user_interrupted` / `ambiguous_worktree_abort` | The triage cause is one of these |
| `sensitive_failure_pattern` | The error text matches an auth / permission pattern |
| `worktree_unavailable` / `branch_mismatch` | The worktree in `session-state.json` is missing / its branch differs |
| `provider_unavailable` | The provider cannot be resolved (shared with triage; for retry this is normally an exit 2 before the gates) |
| `agent_pane_alive` | The failed step's latest Herdr pane is owned by kaji and still has a foreground agent process |
| `agent_pane_unverifiable` | That pane cannot be checked (`pane-metadata.json` broken or missing `pane_id` / `origin_pane` / `kaji_run`, Herdr query failed or returned incomplete ownership tokens, or liveness unknown) |

The triage `recoverability_hint` never refuses a retry; causes such as
`kaji_bug_suspected` or `dispatch_failure` pass.

```bash
kaji recover .kaji/wf/official/dev.yaml 540 --retry-failed-step
case $? in
  4) echo "refused by a gate: read the retry-gate: lines" ;;
  0) echo "child completed" ;;
  *) echo "child failed or input error" ;;
esac
```

## Exit codes

The existing map (`0 = OK`, `1 = ABORT`, `2 = definition error`, `3 = runtime error`) is unchanged.

| Situation | Exit code |
|-----------|-----------|
| `kaji run` with triage only (no child run) | the original failure's exit code |
| `kaji run` that started a child run | the child's exit code (the chain's final result) |
| `kaji recover`, triage completed (any decision) | `0` |
| `kaji recover`, run not found / still in progress / flag mismatch / `requires_provider` mismatch | `2` |
| `kaji recover`, handler internal error | `3` |
| `kaji recover --retry-failed-step`, refused by a safety gate | `4` |
| `kaji recover --retry-failed-step`, child run started | the child's exit code |
| `kaji run`, launch record cannot be written (before the Issue is resolved) | `3` |

## Artifacts

| Path | Content |
|------|---------|
| `runs/<run_id>/recovery.json` | `RecoveryDecision` (`schema_version: 1`), overwritten on every decision update. Carries `incident_suppressed` / `incident_suppression_reason` when incident recording was skipped (Issue #322) |
| `runs/<run_id>/recovery-chain.json` | `{root_run_id, parent_run_id}`, written by a recovery child run at startup |
| `runs/<run_id>/run.log` | `failure_event`, `recovery_decision`, `recovery_scheduled`, `recovery_attempt_start`, `recovery_attempt_end`, `incident_recorded`, `incident_recording_failed`, `incident_suppressed` |
| `incidents/occurrences.jsonl` | Local occurrence log of the incident layer (directly under `<artifacts_dir>`, append-only). One line per failure, **except for the exempted causes below** |
| Issue comment | Machine-generated triage report (posted before the child launch) plus a follow-up result comment when an auto recovery ran. No kaji-verdict marker (it is not a step verdict) |
| stderr | A short `--- failure triage ---` summary printed after the existing terminal message |

The `comment:` line of the stderr summary shows `Comment.ref`: the created comment URL for the
GitHub provider, and `n/a` when the reference could not be captured.

### Incident recording exemption (Issue #322 / #403 / #405)

Failures classified as `user_precondition_error`, `user_interrupted`, `agent_declared_abort`, or
`cycle_exhausted` never enter the incident layer: no incident Issue is opened, no occurrence
comment is posted, and nothing is appended to `incidents/occurrences.jsonl`. These are known
user-originated endings that need no investigation, or contractually normal terminations, and
promoting them to incidents would drown out the real failure signal.

| Cause | Case | Decision input |
|---|---|---|
| `user_precondition_error` | The interactive terminal runner was started outside a Herdr session (`HerdrSessionRequiredError`). `TmuxSessionRequiredError` is still classified here for past tmux run artifacts; the tmux backend itself was removed (Issue #515) | `failure_event.exception_type` |
| `user_interrupted` | The operator interrupted `kaji run` with Ctrl-C | `failure_event.kind == "interrupted"` |
| `agent_declared_abort` | The agent returned a legitimate ABORT verdict (safe stop / manual confirmation requested) | `failure_event.kind == "agent_abort"` |
| `cycle_exhausted` | A cycle reached `max_iterations` (safety valve worked as designed) | `failure_event.kind == "cycle_exhausted"` |

All four decisions key off the structured `failure_event` recorded in `run.log`, never off the raw
error message. A missing backend binary, an insufficient backend version, and every other
`CLINotFoundError` keep their existing incident recording behavior.

`agent_declared_abort` and `cycle_exhausted` end without an exception, so the identity signature's
canonical input is always empty and the fingerprint degenerates to a per-cause constant. `cause` is
itself part of the match key, so the two causes never collapse into each other. Without this
exemption, unrelated safe stops that share a cause — regardless of their step or actual stop
reason — would all collapse into a single incident Issue per cause (Issue #405).

An interrupted run ends with `workflow_end status=ERROR`, so `kaji recover` can select it as a triage
target (`user_interrupted` maps to the `comment_only` decision; it is never auto-resumed). The
interruption is recorded at run level only — no `result.json` is written for the in-flight attempt.
The interactive terminal runner leaves the agent pane alive and surfaces its `pane_id` in the triage
evidence (see the [interactive terminal runner guide](./interactive-terminal-runner.md) § session
continuation). Only an in-flight attempt — one without a `result.json` — contributes orphan pane
evidence; a completed attempt's pane has already been cleaned up. Without that distinction an
interruption that lands before the next attempt directory exists would report the previous,
already-killed pane as an orphan.

Even when incident recording is suppressed, the console error, the run artifacts, and the triage
comment on the originating Issue are all preserved. The suppression itself is auditable from the
`incident_suppressed` event in `run.log` (`cause` / `exception_type` / `failed_step` / `reason`) and
from the two `recovery.json` fields above.

## Related documents

- [Workflow guide](../dev/workflow_guide.md) — operational rules, non-resumable cases
- [Configuration reference](../reference/configuration.md) — `[execution] failure_triage` / `auto_recover`
- [Architecture](../ARCHITECTURE.md) — recovery layer and `kaji_harness/recovery/` package
