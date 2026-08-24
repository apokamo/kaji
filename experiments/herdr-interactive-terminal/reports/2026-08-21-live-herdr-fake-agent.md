# Guarded live Herdr fake-agent smoke

Issue: #396

## Execution conditions

- Date: 2026-08-21
- Worktree: `/home/aki/dev/kaji/kaji-feat-396`
- Branch: `feat/396`
- HEAD: `eb859c8e29a8870f30693e85cb26c96220dc9537`
- Installed Herdr: `0.8.2`
- `HERDR_ENV=1`
- Origin pane: `w1:p1`
- Release-matched `herdr --skill`: read completely before pane operations
- Destructive checks: not performed
- Herdr integrations/plugins: not installed or changed

## Command

```bash
PYTHONPATH=. python experiments/herdr-interactive-terminal/scripts/run_live_herdr_fake_agent.py
```

## Result

The command exited with code 1 before the packaged wrapper or fake Claude command was launched.
The failure occurred while kaji marked the newly split pane with source-scoped ownership metadata:

```text
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
...
kaji_harness.errors.CLIExecutionError: Step 'interactive_terminal' CLI exited with code 1:
Herdr returned invalid JSON: Expecting value: line 1 column 1 (char 0)
```

## Pane observations

| Item | Observed value |
|------|----------------|
| Origin pane | `w1:p1` |
| Response-derived created pane | `w1:p3` |
| Created pane focus | `false` |
| Created pane process | `/bin/bash` at its prompt |
| Created pane cwd | `/tmp/kaji-live-herdr-0cebmaku (deleted)` |
| `kaji_origin` token | `w1:p1` |
| `kaji_run` token | `9ac78ef2-0062-40b6-89bf-9534552532aa` |
| `kaji_step` token | `live-herdr-fake` |
| Verdict artifact | not created |
| Terminal snapshot artifact | not created |

The adapter only attempts JSON decoding after the Herdr subprocess returns exit code 0. A later
explicit `herdr pane get w1:p3` showed all three requested tokens on the created pane. Therefore the
`pane report-metadata` request succeeded and mutated the intended pane, but its successful CLI call
emitted an empty stdout stream rather than the JSON envelope expected by `_run_herdr_json`.

The temporary directory was removed when the Python context manager unwound. Because the marker
confirmation path raised, the fail-closed implementation deliberately did not close `w1:p3`.
No later command inferred ownership from its position or title, and no destructive cleanup was
attempted.

## Agent-to-interactive-kaji launch

After recording the first failure on Issue #396, the repository-native `herdr-kaji-launch` skill
was used from origin pane `w1:p1`. The workflow definition passed validation first:

```text
$ kaji validate .kaji/wf/official/dev.yaml
✓ .kaji/wf/official/dev.yaml
```

The caller was split explicitly with `--direction right --ratio 0.5 --no-focus`; the split response
identified the new launch pane as `w1:p4`. The exact shell command submitted there was:

```bash
PYTHONPATH=. .venv/bin/kaji run .kaji/wf/official/dev.yaml 396 \
  --workdir /home/aki/dev/kaji/kaji-feat-396 \
  --agent-runner interactive-terminal \
  --interactive-terminal-backend herdr
```

This was a real interactive kaji command. It did not use Herdr `agent start`, Claude Code `-p`,
print mode, or a headless runner. The pane stayed unfocused and retained the requested cwd.

Kaji started the `dev` workflow and attempted `review-ready`, but the nested Herdr backend hit the
same metadata-response incompatibility and ended with `ERROR` after 642 ms:

```text
workflow start: dev issue #396
step start: review-ready attempt-001 dispatch=agent agent=codex model=gpt-5.6-sol effort=medium
ERROR: workflow error: CLIExecutionError: Step 'interactive_terminal' CLI exited with code 1:
Herdr returned invalid JSON: Expecting value: line 1 column 1 (char 0)
workflow end: status=ERROR duration=642ms
```

The launch pane `w1:p4` returned to its bash prompt and was left open. Its nested split produced
`w1:p5`, whose retained metadata was `kaji_origin=w1:p4`,
`kaji_run=0db33b78-5bac-4349-8a8e-0ffd3bdea810`, and `kaji_step=review-ready`. `w1:p5` also remained
at a bash prompt and was not closed. Kaji's failure triage classified the run as a synthetic
`dispatch_failure`, `not_resumable`, and posted Issue comment `5357834314`; no automatic recovery
was attempted.

## Decision

- **Live lifecycle verdict: FAIL / ABORT.** The real Herdr backend did not reach wrapper launch,
  fake-agent execution, verdict polling, terminal snapshot, or ownership-checked close.
- **Agent-to-kaji launch verdict: PARTIAL PASS.** A Herdr-hosted Codex successfully created a
  response-identified sibling pane and launched real interactive kaji with the required backend
  options. End-to-end agent dispatch remains blocked by the same backend compatibility gap.
- **Confirmed compatibility gap:** Herdr 0.8.2's successful `pane report-metadata` behavior is not
  compatible with the implementation's blanket assumption that every successful Herdr command
  returns a JSON object.
- Treat command response contracts individually. For `report-metadata`, success must be recognized
  without weakening non-zero exit handling, and ownership must still be confirmed with an explicit
  `pane get` before the pane is considered managed.
- Add a regression test for exit 0 plus empty stdout on `report-metadata`, then repeat this same live
  smoke only after the implementation is corrected. Do not retry the lifecycle unchanged.
- Leave `w1:p3` open for explicit human disposition; the failed run did not complete the ownership
  confirmation path, and this validation excludes destructive cleanup.
- Leave launch pane `w1:p4` and nested pane `w1:p5` open. Neither pane ID was reused or closed, and
  the caller focus remained on `w1:p1`.

## Notes and friction

- The safety boundary worked: marker-path failure did not automatically close the new pane.
- The temporary cwd makes the retained shell inconvenient to reuse; it must not be reused as a kaji
  launch target.
- `kaji validate .kaji/wf/official/dev.yaml` completed successfully before the agent-to-kaji launch.

## Compatibility fix prepared after the failed live run

The implementation was corrected after preserving the failure evidence above:

- JSON remains mandatory for commands whose response contract returns an object.
- `pane report-metadata` accepts exit 0 with empty stdout, matching Herdr 0.8.2.
- A non-empty metadata response must still be a typed `ok` JSON envelope.
- Non-zero metadata exits still raise before any ownership confirmation.
- After every successful metadata report, kaji performs an explicit `pane get` and requires the
  response pane ID plus `kaji_origin`, `kaji_run`, and `kaji_step` to match exactly.
- The stateful fake Herdr now emits the same empty-success response as the installed Herdr.

TDD first reproduced the live failure:

```text
2 failed: both stopped at Herdr returned invalid JSON: Expecting value
```

After the correction and mechanical formatting:

```text
focused Herdr tests: 22 passed
stateful fake CLI smoke: 10 calls, marker confirmed, verdict/snapshot present, owned pane closed
ruff check: passed
ruff format --check: 3 files already formatted
mypy interactive_terminal_herdr.py: passed
git diff --check: passed
```

This is implementation evidence only. The guarded live Herdr smoke must be repeated from a process
with `HERDR_ENV=1` before the earlier FAIL / ABORT verdict can be superseded.

The complete repository check then passed with the compatibility fix included:

```text
ruff check: passed
ruff format --check: 219 files already formatted
mypy: Success: no issues found in 78 source files
workflow validation: all listed workflows passed
pytest: 2747 passed, 10 skipped in 190.14s
```

## Live re-run after the metadata compatibility fix

The earlier FAIL / ABORT evidence above is retained. The corrected worktree was exercised once
from the same Herdr-hosted Codex process at `2026-08-21T02:02:21+09:00`.

### Re-run conditions

- Worktree: `/home/aki/dev/kaji/kaji-feat-396`
- Branch: `feat/396`
- HEAD: `eb859c8e29a8870f30693e85cb26c96220dc9537`
- Python: `3.12.3`
- Installed Herdr: `0.8.2`
- `HERDR_ENV=1`
- Origin pane: `w1:p1`
- Release-matched `herdr --skill`: read completely before pane operations
- Live panes immediately before the re-run: `w1:p1` only
- Destructive checks: not performed
- Herdr integrations/plugins/server/session: not changed

The requested command was run exactly once:

```bash
PYTHONPATH=. python experiments/herdr-interactive-terminal/scripts/run_live_herdr_fake_agent.py
```

### Re-run result

The command exited with code 1. The metadata compatibility fix was exercised successfully: the
backend parsed the split response, obtained new pane `w1:p6`, reported ownership metadata, and
continued past the explicit `pane get` confirmation. It then stopped at the next mutation,
`pane run`, because Herdr 0.8.2 again returned exit 0 with empty stdout while the adapter required a
JSON `ok` envelope:

```text
kaji_harness.errors.CLIExecutionError: Step 'interactive_terminal' CLI exited with code 1:
Herdr returned invalid JSON: Expecting value: line 1 column 1 (char 0)
```

The traceback stopped in `_run_herdr_pane_command`, not `_mark_herdr_pane`. This proves that the
new `report-metadata` empty-success handling and its subsequent ownership readback both completed.
The fake-agent lifecycle did not reach a reportable success summary.

### Pane, ownership, snapshot, and close evidence

| Item | Re-run observation |
|------|--------------------|
| Origin pane | `w1:p1` |
| Response-derived created pane | `w1:p6` |
| `kaji_origin` confirmation | exact match to `w1:p1` |
| `kaji_run` confirmation | exact match to the generated run UUID; value was only in the temporary metadata and was not retained after unwind |
| `kaji_step` confirmation | exact match to `live-herdr-fake` |
| Ownership verdict before launch | confirmed |
| Process verdict | FAIL / ABORT, exit code 1 |
| `verdict.yaml` | no durable artifact or successful summary; temporary directory was removed during unwind |
| Rendered snapshot | best-effort capture path ran before cleanup, but its temporary artifact and metadata were removed during unwind, so content/revision are not durable evidence |
| Cleanup ownership check | origin and run token re-read and exact-matched before close |
| Close target | `w1:p6` only |
| Herdr close result | server recorded `pane.close` outcome `ok`; pane 6 exited with Hangup |
| Adapter close result | successful empty stdout was parsed as required JSON and internally treated as an unconfirmed close result |
| Post-close live panes | `w1:p1` only |

The Herdr server log independently recorded the response-created pane and close lifecycle:

```text
2026-08-20T17:02:21.115207Z pane.spawn.start pane_id=6
2026-08-20T17:02:21.116393Z pane.spawned outcome=ok pane_id=6
2026-08-20T17:02:21.126854Z api.request.start method="pane.close"
2026-08-20T17:02:21.127206Z pane.exit pane_id=6 signal=Hangup
2026-08-20T17:02:21.128240Z api.request.complete method="pane.close" outcome=ok
```

No command in this re-run targeted `w1:p3`, `w1:p4`, or `w1:p5`. They were absent from the live
pane list before the command, and this validation did not issue `get`, `run`, or `close` requests
for any of those IDs.

### Re-run decision

- **Corrected metadata path: PASS.** Empty-success metadata reporting was accepted and all three
  ownership tokens were confirmed through explicit `pane get` before launch.
- **End-to-end live lifecycle: FAIL / ABORT.** The fake-agent verdict and durable rendered snapshot
  were not confirmed because `pane run` hit the same successful-empty-response compatibility class.
- **Scoped cleanup: operationally PASS, adapter contract FAIL.** Exact ownership gated the cleanup,
  and Herdr closed only `w1:p6`; however, the adapter could not recognize the successful empty close
  response.
- Successful-empty response handling must be defined per mutating command. Add RED/GREEN coverage
  for real Herdr 0.8.2 `pane run` and `pane close` before another live re-run. Non-zero handling and
  typed non-empty response validation must remain strict.
- Do not repeat the unchanged live command. The next run should occur only after the newly observed
  response-contract gap is corrected and covered by regression tests.

### Notes and friction

- The fail-closed ownership boundary worked and prevented any unrelated pane from being targeted.
- TemporaryDirectory cleanup makes exception-path snapshot and run UUID evidence ephemeral. A
  future live harness should copy sanitized failure metadata to a durable report fixture before
  unwind, without retaining prompt contents or secrets.
- No destructive server/session/process validation was performed.

## Pane run / close compatibility fix before the next live re-run

The second live failure was reproduced with command-specific RED tests before changing the
adapter. The response contract is now explicit:

- Herdr read/query commands still require a typed JSON object response.
- `pane report-metadata`, `pane run`, and `pane close` accept either exit 0 with empty stdout or a
  non-empty typed `ok` envelope.
- Non-zero exits always raise `CLIExecutionError` at the mutation boundary.
- Non-empty malformed JSON, non-object JSON, and non-`ok` result types are rejected.
- `pane close` re-reads the response-derived pane ID and requires exact `kaji_origin` and
  `kaji_run` tokens before issuing the close.
- After a successful close response, the adapter reads `pane list --workspace <workspace-id>`
  using the owned pane's returned workspace ID and requires the exact close target to be absent.
  Other pane IDs are read-only observations and are never close or reuse targets.

The initial focused RED selection produced the expected boundary failures:

```text
13 selected: 8 failed, 5 passed
```

After the implementation and one formatter-only correction:

```text
command-contract selection: 13 passed
focused Herdr tests: 35 passed
stateful fake CLI smoke: 11 calls, verdict/snapshot/owned close/post-close absence confirmed
ruff check: passed
ruff format --check: 4 files already formatted
mypy interactive_terminal_herdr.py: passed
git diff --check: passed
```

The stateful fake Herdr now returns the installed 0.8.2 behavior for all three mutations: exit 0
with empty stdout for metadata, run, and close. The extra eleventh call is the workspace-scoped
post-close confirmation.

The complete worktree quality gate also passed:

```text
ruff check: passed
ruff format --check: 219 files already formatted
mypy: Success: no issues found in 78 source files
workflow validation: all listed workflows passed
pytest: 2760 passed, 10 skipped in 190.06s
```

This section records non-live readiness only. The earlier live FAIL / ABORT remains authoritative
until the guarded live fake-agent command is run once against this corrected adapter.

## Guarded live run after the run / close compatibility fix

The corrected adapter was exercised once from the Herdr-hosted Codex process. The command was not
retried:

```bash
PYTHONPATH=. python experiments/herdr-interactive-terminal/scripts/run_live_herdr_fake_agent.py
```

Immediately before the command, a workspace-scoped pane list contained only origin `w1:p1`.
The run passed split, metadata reporting and readback, and `pane run`; it then waited for the
filesystem verdict until the 30-second deadline:

```text
StepTimeoutError: Step 'live-herdr-fake' timed out after 30s
```

This is materially different from both earlier JSON failures. Reaching the verdict polling loop
proves that the installed Herdr accepted the empty-success `pane run` response. The timeout cleanup
then re-read exact ownership, accepted the empty-success `pane close` response, and completed the
workspace-scoped target-absence confirmation. The final exception remained the original timeout,
not a close error. A post-run workspace list again contained only `w1:p1`; no unrelated pane was
closed or reused.

### Live-run decision

- **`pane run` empty-success compatibility: PASS on installed Herdr 0.8.2.**
- **`pane close` empty-success compatibility: PASS on installed Herdr 0.8.2.**
- **Exact ownership and post-close target-absence confirmation: PASS.**
- **End-to-end fake verdict and rendered snapshot: FAIL / ABORT due to a fixture environment gap.**
- **Agent-to-interactive-kaji revalidation: not run because the required live smoke did not pass.**

### Fixture environment gap

The live script prepended `scripts/fake-bin` to the child Python process's `PATH`. The Herdr backend
did not pass that caller PATH through `pane split --env`, so the server-created shell did not have a
durable route to the fake Claude executable. The backend observed a non-shell foreground process
until timeout; a missing command would instead have returned to the shell and triggered the early
exit guard. It is therefore likely that the ordinary shell PATH resolved a real Claude process.
This is an inference from the process lifecycle and source inspection: exception unwind removed the
temporary transcript and metadata, so the launched executable name is not durable evidence.

## Caller PATH fix after the live timeout

The split contract was extended to preserve a non-empty caller `PATH` as one explicit Herdr argv
token:

```text
--env PATH=<caller-process-PATH>
```

It is not concatenated into the shell command. The explicit split target, caller cwd, no-focus
behavior, origin marker, and response-derived pane ID remain unchanged.

TDD and non-live validation:

```text
RED: 1 failed; split argv omitted the expected PATH env
GREEN focused Herdr tests: 35 passed
stateful fake CLI: 11 calls, split_path_preserved=true
verdict/snapshot/owned close/post-close absence: passed
ruff check: passed
ruff format --check: 4 files already formatted
mypy interactive_terminal_herdr.py: passed
git diff --check: passed
```

The final complete quality gate after the PATH fix passed:

```text
ruff check: passed
ruff format --check: 219 files already formatted
mypy: Success: no issues found in 78 source files
workflow validation: all listed workflows passed
pytest: 2760 passed, 10 skipped in 189.99s
```

No additional live command was run. The run/close contracts now have direct installed-Herdr
evidence, while fake-agent verdict and snapshot remain unverified after the PATH correction.

## Shell startup PATH diagnosis and command-level pinning

An additional response-derived diagnostic pane showed that Herdr did preserve the split `PATH`,
but interactive shell startup prepended `/home/aki/.local/bin`. Consequently `command -v claude`
resolved the real Claude executable before the experiment fake. Only the diagnostic pane was read
and closed; the post-check inventory contained origin `w1:p1` only.

The wrapper launch contract was therefore tightened with TDD. `_run_herdr_pane_command` now prefixes
the already shell-quoted wrapper command with a shell-quoted `env PATH=<caller-path>` assignment.
This applies after shell startup and preserves the caller's executable resolution order. Empty or
unset PATH remains omitted.

```text
RED: 2 failed; pane run command lacked command-level PATH assignment
GREEN focused Herdr tests: 35 passed
stateful fake CLI: 11 calls
ruff / format / targeted mypy / git diff check: passed
```

## Herdr 0.8.2 plain-text pane read contract

The next live attempt reached the fake verdict and owned close, but exposed that installed Herdr
0.8.2 returns rendered text directly from `pane read`, not a JSON envelope. A RED test reproduced
the JSON failure with plain stdout. The adapter now treats read stdout as the rendered snapshot,
then performs `pane get` on the same explicit ID to obtain and validate the integer revision.

Herdr 0.8.2 does not expose a structured truncation flag through this CLI response, so metadata
records `transcript_truncated: null` rather than inventing a value.

```text
RED: 1 failed; plain rendered stdout was rejected as invalid JSON
GREEN focused Herdr tests: 36 passed
stateful fake CLI: 12 calls, revision 7, truncated null
ruff / format / targeted mypy / git diff check: passed
```

## Final guarded fake-agent live PASS

After both PATH corrections and the plain-text read contract were covered, the guarded live script
completed successfully against installed Herdr 0.8.2:

```bash
PYTHONPATH=. python experiments/herdr-interactive-terminal/scripts/run_live_herdr_fake_agent.py
```

| Item | Observation |
|------|-------------|
| Origin | `w1:p1` |
| Response-created pane | `w1:pB` |
| Ownership marker readback | exact; `marker_confirmed=true` |
| Verdict | `PASS`, written by experiment fake Claude |
| Rendered snapshot | present; revision 2 |
| Truncation | `null` (unknown under the 0.8.2 plain-text contract) |
| Close | exact ownership recheck, close target `w1:pB` only |
| Post-close inventory | `w1:p1` only |

This supersedes the earlier end-to-end FAIL / ABORT decisions while retaining them above as the
compatibility-gap history.

## Non-destructive real-agent matrix

`scripts/run_live_herdr_real_agents.py` constrained each real interactive agent to writing only its
temporary verdict artifact. Every attempt used a response-derived pane, a 180-second timeout, and
`close_on_verdict=true`.

| Agent | Phase | Result | Session evidence | Snapshot evidence |
|-------|-------|--------|------------------|-------------------|
| Claude Code 2.1.237 | fresh | PASS | present | 1405 chars, revision 4 |
| Codex CLI 0.147.0 | fresh | PASS | present | 5454 chars, revision 3 |
| Antigravity 1.1.6 | fresh | PASS | unsupported / none as designed | 1726 chars, revision 2 |
| Claude Code 2.1.237 | resume | PASS | input session ID exact match | 1911 chars, revision 3 |
| Codex CLI 0.147.0 | resume | PASS | input session ID exact match | 6440 chars, revision 3 |

All five runs recorded `marker_confirmed=true`, `transcript_available=true`, and
`transcript_truncated=null`, then passed exact ownership close and post-close target absence. The
suite ended with `w1:p1` only and did not modify repository files. Codex supplied evidence that an
alternate-screen UI can yield a rendered snapshot; completeness beyond the available 0.8.2 screen
history is deliberately not claimed.

## Retained-pane ownership verification

The fake harness's guarded `--retain` mode exercised `close_on_verdict=false`. It created only
response pane `w1:pH`, completed with a PASS verdict, and left the pane present. An explicit
`pane get w1:pH` then confirmed its pane ID, `kaji_origin=w1:p1`, and exact run token
from the harness result. The temporary full UUID was not copied into this sanitized report. Cleanup
subsequently targeted only `w1:pH`; the final inventory again contained only `w1:p1`.

## Agent-to-interactive-kaji final validation

The repository `herdr-kaji-launch` skill was exercised again only after the guarded fake lifecycle
passed. To avoid repository or Issue mutation by a full development workflow, the validated
experiment workflow `fixtures/herdr-kaji-launch-smoke.yaml` contains one fake-Claude step.

The caller split response identified launch pane `w1:pJ`; cwd was the feature worktree, focus stayed
on `w1:p1`, and the real interactive command was:

```bash
env PATH=/home/aki/dev/kaji/kaji-feat-396/experiments/herdr-interactive-terminal/scripts/fake-bin:/home/aki/.local/bin:/usr/local/bin:/usr/bin:/bin \
  PYTHONPATH=. .venv/bin/kaji run \
  experiments/herdr-interactive-terminal/fixtures/herdr-kaji-launch-smoke.yaml 396 \
  --workdir /home/aki/dev/kaji/kaji-feat-396 \
  --agent-runner interactive-terminal \
  --interactive-terminal-backend herdr
```

The nested backend created response pane `w1:pK`. Run `260821024949` advanced on its filesystem
artifact—not terminal matching—and recorded:

```text
verdict.yaml: PASS
result.json: status PASS, session ID present
run.log: verdict_source=artifact, workflow_end status=COMPLETE
pane-metadata.json: origin=w1:pJ, pane=w1:pK, marker_confirmed=true,
                    snapshot revision=2, truncated=null
```

The backend closed only `w1:pK` after exact ownership confirmation. Per the launch skill, outer
launch pane `w1:pJ` remains open for interaction and was not closed or reused. No `agent start`,
Claude `-p`, plugin installation, server/session mutation, or destructive operation was used.

## Final scope decision

- Fake lifecycle, real supported-agent fresh/resume matrix, retained-pane behavior, and
  agent-to-interactive-kaji workflow are **PASS**.
- Herdr 0.8.2 mutation empty-success and plain-text read contracts are covered by TDD, the stateful
  fake, and installed-Herdr evidence.
- Server stop, session delete, forced process termination, prune, and similar destructive checks
  remain intentionally unexecuted under this validation's safety conditions.
- Real prune is therefore not claimed. The implemented prune selection remains covered by
  non-live exact-ownership tests.

## Final repository quality gate

After all implementation, test, fixture, script, documentation, report, and live-validation
changes were present in the worktree, the complete repository gate passed:

```text
ruff check kaji_harness/ tests/ experiments/: passed
ruff format --check: 220 files already formatted
mypy: Success: no issues found in 78 source files
workflow validation: all listed workflows passed
pytest: 2761 passed, 10 skipped in 190.02s
```

The immediately preceding focused gate also passed with 36 Herdr tests and the 12-call stateful
fake CLI smoke.

## PR-lifecycle boundary

The final-check audit synchronized the draft design with the installed-Herdr discoveries and
confirmed that its lasting decisions are already integrated into ADR 007, architecture,
configuration, interactive-runner, recovery, and agent-session documentation. A new ADR promotion
is therefore unnecessary. `make verify-docs` passed after that synchronization.

Formal `issue-review-code` and `i-dev-final-check` verdicts are intentionally not fabricated in
this uncommitted state. Their repository workflow requires an implementation commit, a mandatory
Pre-Handoff Review comment tied to that commit, and an independent review approval. The user has
not authorized commit, push, or PR creation. These are PR-lifecycle gates, not remaining Herdr
runtime or compatibility tests.

## Resume pre-commit audit and final guard completion (2026-08-22)

The handoff audit found three preflight/timeout contracts that the earlier live success did not
exercise: release-matched `HERDR_BIN_PATH` preference, exact `pane current --current` caller
confirmation after `herdr status`, and a bounded timeout for every Herdr subprocess request. The
JSON response boundary was also moved behind a strict Pydantic envelope before field-specific
checks, matching the repository external-input rule.

TDD and non-destructive evidence after the fix:

```text
RED: 4 failed, 36 passed
GREEN focused Herdr tests: 42 passed
stateful fake CLI: 14 calls
split_path_preserved=true
marker_confirmed=true
verdict_present=true
rendered snapshot revision=7, truncated=null
exact owned close + workspace-scoped absence=true
real Herdr 0.8.2 read-only preflight: origin w1:p1 exact match
```

The stateful fake gained explicit `status` and `pane current --current` responses. A permanent
Medium pytest now drives the executable lifecycle, and managed-pane discovery is constrained to
the origin workspace and right-column `rect.x`; no destructive real-Herdr test was repeated. The
recovered deterministic baseline at the unchanged base commit
`eb859c8e29a8870f30693e85cb26c96220dc9537` was clean (`2716 passed, 10 skipped`). The current
worktree then passed the full repository gates:

```text
ruff check: passed
ruff format --check: 220 files already formatted
mypy: 78 source files passed
workflow validation: passed
pytest: 2767 passed, 10 skipped in 189.99s
make verify-docs: 131 files, all Markdown links valid
git diff --check: passed
```
