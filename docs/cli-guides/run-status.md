# Run status (`kaji status`)

[日本語版](run-status.ja.md)

`kaji status` reports the state of runs and series from local artifacts. Monitoring
tools call it instead of reading kaji's internal files (`run.log`, `session-state.json`,
`process.json`, `_series/*/state.json`, ...). The JSON output (`--json`) is a public
contract with a `schema_version`; the default table output is for people and is not a
contract.

```bash
kaji status 540 541                 # table
kaji status --json 540 541          # JSON (schema v1)
kaji status --json --series lane-a  # series state + one entry per member Issue
```

`kaji status` is read-only. It writes no files (artifacts, worktree, git index), calls no
GitHub API (it does not build a provider), and does not call Herdr. The only git command
it runs is `git worktree list --porcelain`, to find the artifacts directory the same way
`kaji run` does (a relative `paths.artifacts_dir` resolves against the main worktree, so
running from a feature worktree reads the same artifacts).

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Output written. A failed run, broken or missing artifacts, and a missing series `state.json` are all reported in the output with exit 0. |
| 2 | Argument error (both or neither of `<issue>` and `--series`, an Issue ID that is not `<number>` / `gh:<number>`, a series id that does not match `^[a-z0-9][a-z0-9-]{0,63}$`) or no `.kaji/config.toml`. |
| 3 | Unexpected error. |

## Schema compatibility

`schema_version` is an integer. kaji does not raise it when it adds an optional field;
consumers must ignore fields they do not know. kaji raises it when it removes a field,
renames it, changes its type, changes what `null` means, or adds or changes an enum value.
kaji always emits exactly one version; there is no option to request an older one.

## JSON document (schema v1)

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-11-12T04:05:09+00:00",
  "series": null,      // object only with --series
  "issues": [ ... ]    // IssueStatus, in argument order (member order with --series)
}
```

### `series` (with `--series`)

| Field | Type | `null` means |
|-------|------|--------------|
| `id` | str | — (the CLI argument) |
| `status` | `running` / `stopped` / `completed` \| null | `state.json` is missing or broken, or the value is not in the enum |
| `stop_reason` | str \| null | No stop reason, or unreadable |
| `updated_at` | str \| null | Unreadable |
| `members[]` | array | Never null; `[]` when unreadable |
| `read_errors[]` | ReadError | — |

Each member has `issue` (int), `workflow`, `status` (`pending` / `running` /
`interrupted` / `completed` / `failed`), `child_pid`, `run_id`, `exit_code`, `gate`,
`started_at`, `finished_at`. kaji validates each field on its own; a field that fails
validation is `null` and is listed in `read_errors` (`_series/<id>/state.json#members[0].status`).
`issues` lists only members whose `issue` is a positive integer.

### `IssueStatus`

| Field | Type | `null` means |
|-------|------|--------------|
| `issue` | str | — |
| `latest_run` | RunStatus \| null | The Issue has no run |
| `any_run_alive` | bool \| null | `true` if any run's `process.json` or any launch record is alive. `false` if none is alive (or there is no record). `null` if none is alive and at least one cannot be judged |
| `launching` | bool \| null | `true` if a live launch record has no `run_id` yet (a `kaji run` before its run dir exists, or a launcher whose child has no run dir yet). `false` if no live launch record. `null` if a launch record cannot be judged and none is `true` |
| `read_errors[]` | `{"path", "error": "missing" \| "unreadable" \| "invalid", "message"}` | — |

`path` is relative to the artifacts directory.

### `RunStatus`

The latest run is the run with the largest `run_id` in `<issue>/runs/` (string order;
`run_id` is local time `YYMMDDHHMMSS` plus a `-NNN` suffix within one second). kaji never
compares it with the caller's clock.

| Field | Type | `null` means |
|-------|------|--------------|
| `run_id` | str | — |
| `workflow` | str \| null | `workflow_start` is unreadable |
| `started_at` | str \| null | `workflow_start.ts` is unreadable |
| `process` | `{"pid", "alive", "basis"}` | `alive: null` = cannot be judged (see `basis`) |
| `termination` | `running` / `ended` / `vanished` / `unknown` \| null | `run.log` is unreadable and the process is not alive |
| `end_status` | str \| null | `termination != "ended"` (`COMPLETE` / `ABORT` / `ERROR`) |
| `ended_at` | str \| null | Same as above |
| `last_step` | `{"id", "attempt", "agent", "session_id"}` \| null | No step was dispatched |
| `failed_step` | str \| null | No failed step (running / COMPLETE) or it cannot be determined |
| `failure` | `{"cause", "source", "recoverability_hint"}` \| null | `termination` is not `ended` (ERROR / ABORT) or `vanished` |
| `worktree_dir` / `branch_name` | str \| null | Not in `session-state.json`, or unreadable |
| `pane` | `{"backend": "herdr", "pane_id", "origin_pane"}` \| null | The latest attempt of `last_step` has no `pane-metadata.json` |
| `recovery` | `{"root_run_id", "parent_run_id", "chain_index"}` \| null | Not a recovery child |

`termination`:

| `workflow_end` in run.log | `process.alive` | `termination` |
|---------------------------|-----------------|---------------|
| present | any (`true` = triage or wait after the run) | `ended` |
| absent (run.log readable) | `true` | `running` |
| absent (run.log readable) | `false` | `vanished` (killed, OOM, ...) |
| absent (run.log readable) | `null` | `unknown` |
| run.log unreadable | `true` | `running` |
| run.log unreadable | `false` / `null` | `null` (see `read_errors`) |

A `vanished` run has `failure.cause = "process_vanished"` (source `external`, hint
`unknown`) and `failed_step` = the last `step_start` that has no matching `step_end`.

`last_step.session_id` comes only from that run and attempt: the attempt's `result.json`
`session_id`, else the `step_start.session_id` of a `resume:` step, else `null` (the
session ID is not recorded yet). kaji does not use the Issue-wide `session-state.json`
sessions, which can hold IDs from other runs.

### `process.basis`

| basis | alive | Meaning |
|-------|-------|---------|
| `no_record` | null | No `process.json` (runs before this feature, or the write failed) |
| `record_unreadable` | null | `process.json` is broken |
| `other_host` | null | Written on another host (sharing artifacts between hosts is not supported) |
| `rebooted` | false | `boot_id` differs |
| `pid_absent` | false | The pid does not exist |
| `zombie` | false | `/proc/<pid>/stat` state is `Z` / `X` |
| `pid_verified` | true | The pid exists and its start time (`/proc/<pid>/stat` field 22) matches |
| `pid_reused` | false | The pid exists but started at a different time |
| `unverifiable` | null | The start time cannot be checked (no `/proc`) |
| `finished_by_log` | false | No usable record, but run.log shows the process finished (`COMPLETE`, a non-resume `recovery_decision`, or `recovery_attempt_end`) |

### Liveness records

`kaji run` writes a launch record (`<artifacts>/<issue>/launches/run-<pid>-<ticks>.json`)
before it resolves the Issue through the provider, sets its `run_id` once the run dir
exists, and deletes it when it exits. It also writes `runs/<run_id>/process.json`, which
it keeps. `kaji recover --retry-failed-step` and `kaji run-series` write a
`launcher-<pid>-<ticks>.json` record before they start a child `kaji run`. A record left
behind by a killed process is harmless: liveness probing reports it as not alive.
If `kaji run` cannot write its first launch record it exits 3 before it resolves the
Issue.
