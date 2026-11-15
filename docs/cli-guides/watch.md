# Series monitoring (`kaji watch`)

[日本語版](watch.ja.md)

`kaji watch` monitors Issues queued in a series without an LLM. Every poll it recomputes
the `state:*` label of each Issue from facts and fixes only the difference on GitHub.
When a run stops, it posts a report comment, sends an external notification, and opens
the `/watch-triage` skill in a new Herdr tab.

The feature is **opt-in**: it works only in repositories that declare `[watch]` in the
tracked `.kaji/config.toml`. Without the declaration every existing behaviour (including
`issue-close`) is unchanged.

Run and series facts come only from `kaji status` (`kaji_harness.runstate.query`, the
same path as [`kaji status --json`](run-status.md)); `kaji watch` does not read kaji's
internal run files.

## Configuration

```toml
[watch]                                         # the table itself is the opt-in (may be empty)
notify_command = ["scripts/notify-discord.sh"]  # optional argv; unset = no notification
```

- Only the tracked `.kaji/config.toml` is read. A `[watch]` table in `config.local.toml`
  neither enables nor disables monitoring (it is ignored with a one-line warning).
- `provider.type` must be `github`.
- The six contract labels (`state:planned` / `state:running` / `state:aborted` /
  `state:done` / `state:hold` / `state:needs-maintainer`) must exist on GitHub. kaji does
  not create them; sync them from `.github/labels.yml`.
- Periods and thresholds are fixed constants (`kaji_harness/watch/constants.py`): poll
  every 180 s, `pane_idle` after 2 non-`working` polls, `log_stall` after 90 minutes
  without progress, exit after 2 consecutive empty polls.

## Commands

```bash
kaji watch start                 # start the loop in the `kaji-watch` Herdr tab (idempotent)
kaji watch status [--json]       # read-only view of monitored Issues
kaji watch once [--dry-run]      # run one poll; --dry-run prints the plan and writes nothing
kaji watch record --event FILE --outcome OUTCOME [--comment-url URL] [--summary TEXT]
kaji watch events [--json]       # events and triage records (read-only)
```

All subcommands accept `--workdir DIR`. There is no `stop`.

| Subcommand | Requires Herdr | Behaviour |
|---|---|---|
| `start` | yes | Exits 0 with `watch already running (pane <id>)` when the lock is held. Otherwise reuses the `kaji-watch` tab when its pane is back at a shell, or creates it with `herdr tab create --label kaji-watch --no-focus`, and runs the internal loop `python -m kaji_harness.watch` there. |
| `status` | no | Facts, current label, target, hold reason, triage tab and suppression per Issue. Writes nothing to GitHub or artifacts. |
| `once` | yes (not with `--dry-run`) | One poll under the lock (exit 2 `watch is running` when held). `--dry-run` computes targets, diffs and events and prints JSON without writing anything (GitHub, state files, Herdr, notifications). Outside Herdr `--dry-run` reports `"herdr": "unavailable"` and omits `pane_idle` candidates. |
| `record` | no | Appends a triage outcome to `records.jsonl`. `--outcome handoff` also sends a `handoff` notification. |
| `events` | no | Lists events and records. |

`OUTCOME` is one of `handoff`, `still-working` (only for `pane_idle` events),
`not-stalled` (only for `log_stall` events) and `recovered`.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (including the loop ending after two empty polls, and `start` when already running). |
| 2 | Argument error, no `.kaji/config.toml`, no `[watch]`, missing contract labels, non-github provider, `start` / `once` (without `--dry-run`) outside Herdr, lock held by `once`, invalid event file or outcome. |
| 3 | Unexpected error. |

## Label contract

| Label | Meaning | Added by | Removed by |
|---|---|---|---|
| `state:planned` | queued in a series, no run yet | `/run-watched`, `kaji watch` | `kaji watch` |
| `state:running` | a `kaji run` for the Issue is alive (or launching) | `kaji watch` | `kaji watch` |
| `state:aborted` | the latest run since queueing failed or vanished, or it COMPLETEd while the Issue is still open | `kaji watch` | `kaji watch`, `issue-close` |
| `state:done` | closed as completed | `issue-close`, `kaji watch` | — |
| `state:hold` | on hold; do not hand to unattended workflows | people | people, `issue-close` |
| `state:needs-maintainer` | waiting for the maintainer | people, `issue-close` (follow-up) | people, `issue-close` |

- At most one `state:*` per Issue. `kaji watch` adds the target first and then removes the
  others; a crash in between leaves two labels for one poll.
- `kaji watch` never touches an Issue with `state:hold` / `state:needs-maintainer`, or with
  two or more `state:*` labels that it did not create itself (`status` shows `conflict`).
- An Issue counts runs from its anchor: the run of its member in the most recently updated
  series, or (outside series) the latest run when `kaji watch` first saw it as
  `state:planned`. Run IDs are compared, not GitHub timestamps.
- `wf:<name>` (people only, optional) names the default workflow for `/run-watched`;
  `<name>` is the workflow YAML `name:`.

## Events and triage

| Kind | Condition | Key |
|---|---|---|
| `aborted` | target is `state:aborted` because the latest run since the anchor ended `ABORT` / `ERROR` or vanished, and the key is not yet in the ledger | `<issue>:aborted:<run_id>` |
| `pane_idle` | the run is alive, has a pane (not headless) and the step pane was not `working` for 2 polls | `<issue>:pane_idle:<run_id>:<step>:<attempt>:<episode>` |
| `log_stall` | the run is alive and its progress (`run_id`, step, attempt, termination) did not change for 90 minutes | `<issue>:log_stall:<run_id>:<step>:<attempt>:<recheck>` |

Detection uses facts and the ledger, not label transitions, so a second failed run on an
Issue that is already `state:aborted` is still detected. Each event is persisted before any
label change and launched at most once (`detected` → `launching` → `launched`, or
`launch_failed` after 3 attempts; then start `/watch-triage <event-file>` by hand).
No triage tab is launched while a `watch-triage-<issue>` tab is open, or after a `handoff`
record until the Issue's progress changes. While the tab is open, `pane_idle` and `log_stall`
are not raised, but a new failed run is still recorded as an `aborted` event and reported;
its triage tab is launched after the open tab is closed. At most one triage tab is launched
per Issue per poll. Tab panes are resolved by joining `herdr tab list` with `herdr pane list`
(Herdr's tab rows carry no pane ID).

For each `aborted` event the Issue gets one comment whose first line is
`<!-- kaji-watch: aborted key=<key> -->` (an existing marker comment is reused), then one
`aborted` notification.

State lives under `<artifacts>/_watch/` (`state.json`, `events/`, `records.jsonl`,
`labels.jsonl`, `lock`). These are internal formats; the event file carries
`schema_version: 1` because `/watch-triage` reads it.

## JSON (schema v1)

`kaji watch status --json`, `kaji watch events --json` and the notification payload carry
`schema_version: 1`, versioned with the rules of
[run status § Schema compatibility](run-status.md#schema-compatibility).

The notification payload is written to the standard input of `notify_command`:

```jsonc
{
  "schema_version": 1,
  "kind": "aborted",            // aborted | handoff | watch_exit
  "repo": "owner/name",         // or null
  "issue": 101,                 // null for watch_exit
  "summary": "run 261115113853 ended ERROR at step implement",
  "comment_url": "https://github.com/owner/name/issues/101#issuecomment-1",  // or null
  "pane_id": "p_9",             // or null
  "event_key": "101:aborted:261115113853",  // or null
  "generated_at": "2026-11-15T03:00:00+00:00"
}
```

The command runs with a 30 s timeout in the repository root, with no extra arguments
(keep secrets in the environment). Failures are logged with `argv[0]` and the exit code
only and are not retried.

```bash
#!/usr/bin/env bash
set -euo pipefail
payload=$(cat)
[ -n "${DISCORD_WEBHOOK_URL:-}" ] || exit 0
jq -n --arg c "$(jq -r '"[\(.kind)] #\(.issue // "-") \(.summary) \(.comment_url // "")"' <<<"$payload")" '{content: $c}' \
  | curl -fsS -H 'Content-Type: application/json' -d @- "$DISCORD_WEBHOOK_URL" >/dev/null
```

## `/run-watched`

`/run-watched` is the entry point that turns an explicit request ("run #101, #102 with dev in
sequence") into a launched, watched series. The skill only makes judgements (workflow choice,
order); everything deterministic is done by `kaji_harness.scripts.run_watched`:

```bash
python -m kaji_harness.scripts.run_watched plan --issues 101,102 [--workflow dev | --member 101=dev --member 102=docs] [--chosen-by-skill] [--order-ambiguous]
python -m kaji_harness.scripts.run_watched execute --draft <draft-id>
```

- `plan` reads the Issues, `kaji status` facts (`any_run_alive` / `launching`), worktrees and
  open PRs, classifies them, and saves a draft under `<artifacts>/_watch/run-watched/<draft-id>.json`.
  It never writes to GitHub. The workflow comes from the argument (stem, file name or path),
  then the Issue's `wf:*` label; otherwise the Issue is listed in `needs_selection`.
  Excluded Issues carry a reason (`closed`, `state_label`, `state_conflict`,
  `workflow_mismatch`, `wf_unresolvable`, `wf_ambiguous`, `run_alive_or_unknown`,
  `worktree_or_pr`). `approval_required` is true with `--chosen-by-skill`,
  `--order-ambiguous` or any exclusion. No member → exit 2.
- `execute` runs, in order: preconditions (`[watch]`, labels, github provider, Herdr; main
  checkout on the default branch, no staged change other than the series YAML, `HEAD` equal to
  the fetched remote) → series YAML (`.kaji/series/rw-<first>-<last>-<YYMMDDHHMM>.yaml`, never
  overwriting a file with different content) → `kaji validate-series` and
  `kaji run-series --dry-run` → commit of the YAML only and `git push <remote> <sha>:refs/heads/<default>`
  (no force) → `state:planned` on each member → the `kaji-series-<id>` Herdr tab running
  `kaji run-series <yaml>` → `kaji watch start`. Issue state (open/closed and the `state:*`
  and `wf:*` labels saved by `plan`) is re-checked before each side effect; any change other
  than our own `state:planned` stops with exit 2.
- Every stage is decided by comparing external state, with receipts saved to the draft before
  (intent) and after (result) each side effect. Re-running `execute` with the same draft after
  a crash skips finished stages: no empty commit, double push, double label, double tab or
  double launch. A commit made just before a crash is recognised by its parent, the single
  added YAML, its blob and its exact message; any other commit ahead of the remote is refused.
- Exit codes: 0 success (prints `series_id`, `yaml`, `commit`, `series_pane`, `watch`); 2
  precondition failure or external change (fix it and re-run the same draft); 3 the remote
  moved past our parent or the push failed (no rebase or force push; resolve by hand, e.g.
  `git reset --soft <parent>`), or an unexpected error.

## `issue-close`

`issue-close` calls `python -m kaji_harness.scripts.watch_labels mark-done <issue>` after
closing and `mark-follow-up <follow-up>` after creating a follow-up Issue. Without
`[watch]` the script prints `watch labels: skipped (no [watch])` and exits 0. With
`[watch]` but missing labels it exits 2; a failed GitHub write exits 3. `mark-done` adds
`state:done` and removes every other `state:*`.
