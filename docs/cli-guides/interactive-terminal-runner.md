# Interactive Terminal Runner

Language: English | [日本語](interactive-terminal-runner.ja.md)

A runner that executes `kaji run` agent steps through normal `claude`, `codex`,
or Antigravity (`agy`) interactive CLIs inside **tmux or Herdr panes**, instead of the headless CLI
path (Issues 224, 230, and 396). When an agent writes
`verdict.yaml` in the attempt directory, kaji reads the verdict through the
artifact-primary path ([ADR 005](../adr/005-artifact-primary-verdict.md)) and
advances to the next step.

`interactive_terminal_backend = "tmux"` remains the default. Selecting `"herdr"` uses Herdr's
release-matched CLI and explicit pane IDs. In either backend, kaji starts inside the selected
terminal session and adds agent panes beside its origin pane. This lets you see
`kaji run` output and the agent side by side even in displayless environments
(WSL2 / SSH / headless). Pane placement is: first pane to the right of the
origin pane, later panes split vertically within the right column. Kaji keeps at
most **two** managed agent panes in the right column (Issue 238). Cleanup uses
`tmux kill-pane`, transcripts use `tmux pipe-pane`, and there is no `/proc` scan
or util-linux `script(1)` dependency, so the behavior is the same on Linux and
macOS. The Herdr backend instead stores a rendered `recent-unwrapped` snapshot and explicit
truncation metadata; it does not claim raw transcript parity with `tmux pipe-pane`.

For the technical selection rationale, see [ADR 007](../adr/007-interactive-terminal-runner.md).
For runner dispatch placement, see [ARCHITECTURE](../ARCHITECTURE.md) section
Runner backend dispatch.

## When to use it

- You want to advance a workflow in a shape closer to normal console use rather
  than the metered headless path.
- You want to see `kaji run` output and the agent in the same screen even in a
  displayless environment (WSL2 / SSH / headless).
- You want each step to continue Claude / Codex sessions through `--resume` /
  `codex resume`.
- You want to run a single Antigravity step through `agy -i` while keeping
  kaji's artifact completion contract. Antigravity does not support `resume:`.
- You want to keep the agent's final state visible in the pane after the verdict
  (`close_on_verdict = false`). Even when panes remain, the right column is kept
  to the latest two panes, so its width does not shrink on every step.

When `agent_runner` is unset, the existing `headless` runner is used. Existing
workflows and CI behavior do not change.

## Prerequisites

Choose one terminal backend explicitly; there is no auto-detection or fallback.

### tmux

- **Run `kaji run` inside a tmux session**. The runner checks `$TMUX` and fails
  fast as a step failure when it is unset. There is no automatic fallback or
  search for another terminal. Use `--agent-runner headless` if you want to run
  outside tmux. This failure is treated as a known user precondition error and
  never opens an incident Issue (the triage comment and run artifacts are kept;
  Issue #322 / [failure-recovery.md](./failure-recovery.md) § Incident recording
  exemption).
- `$TMUX_PANE` must be set (it is used as the `split -t` target and is set
  automatically inside a tmux pane).
- `tmux` (**>= 3.1**) must be on PATH. Kaji identifies managed panes through
  pane user options (`set-option -p @kaji_interactive_terminal`), added in tmux
  3.1 (raised from 3.0 in Issue 238). Missing or older tmux fails fast.
- The selected `claude`, `codex`, or `agy` CLI must be on PATH.
- Transcripts (`terminal.log`) are **always recorded** with `tmux pipe-pane`
  (no OS branch).
- Before launching the agent, the wrapper unsets `NO_COLOR` and sets
  `COLORTERM=truecolor`. Even if the parent shell has `NO_COLOR=1`, the agent
  inside the interactive terminal runner can use truecolor output.

### Herdr

- Herdr **>= 0.8.2** must be on PATH and its client/server protocol must be compatible. When
  `HERDR_BIN_PATH` points to an executable, kaji prefers that release-matched CLI over PATH lookup.
- Run `kaji run` inside Herdr. `HERDR_ENV=1` and `HERDR_PANE_ID` are required; an outside caller
  raises `HerdrSessionRequiredError` and never controls the UI-focused session. Preflight also runs
  `herdr status` and requires `pane current --current` to match `HERDR_PANE_ID` exactly.
- Kaji invokes the installed CLI with argv lists, parses JSON response IDs, and never predicts a
  pane ID. A raw socket client, plugin, and Herdr agent integration are not required.
- Created panes carry `kaji_origin`, `kaji_run`, and `kaji_step` metadata tokens. Cleanup re-reads
  the exact pane and requires current origin/run ownership before closing it.
- `terminal.log` is a rendered `recent-unwrapped` diagnostic snapshot. `pane-metadata.json` records
  its kind, availability, and revision. Herdr 0.8.2's `pane read` CLI emits plain text without its
  structured truncation flag, so `transcript_truncated` is `null` (unknown), not a guessed boolean.

## Configuration

`[execution]` belongs in the **repository config**, not workflow YAML. See the
[Configuration Reference](../reference/configuration.md#discovery-rule) for the
`.kaji/config.toml` discovery rule.

Tracked defaults live in `.kaji/config.toml`. For a personal environment-only
switch, write the gitignored `.kaji/config.local.toml`.

```toml
# .kaji/config.toml or .kaji/config.local.toml
[execution]
default_timeout = 2400
agent_runner = "interactive_terminal"            # "headless" (default) | "interactive_terminal"
interactive_terminal_backend = "tmux"            # "tmux" (default) | "herdr"
interactive_terminal_close_on_verdict = true     # default true
```

If `agent_runner` is not an allowed value, config loading fails fast with
`ConfigLoadError`. The exhaustive specification for `[execution]` keys (type /
default / validation) is the
[Configuration Reference](../reference/configuration.md#execution).

### Antigravity execution policy

The runner passes workflow `execution_policy` to the wrapper as its ninth
argument. The Antigravity case maps it as follows:

| policy | `agy -i` flag | permission behavior |
|--------|---------------|---------------------|
| `auto` | `--dangerously-skip-permissions` | tool requests are auto-approved |
| `sandbox` | `--sandbox` | containment is enabled; TUI approval remains active |
| `interactive` | none | AGY defaults and TUI approval |

Sandbox and permission are separate AGY controls. The `sandbox` mapping never
adds the permission bypass flag.

### Overlay

`[execution]` in `.kaji/config.local.toml` overrides the same key in
`.kaji/config.toml` **key by key** (see the
[Configuration Reference](../reference/configuration.md#overlay-merge-rule)).
Even if the tracked config has `agent_runner = "headless"`, writing one key in
the overlay switches only your personal environment while keeping
`default_timeout` from the tracked config.

```toml
# .kaji/config.local.toml (gitignored, personal environment)
[execution]
agent_runner = "interactive_terminal"
```

## CLI options (`kaji run`)

```bash
--agent-runner <headless|interactive-terminal>
--interactive-terminal-backend <tmux|herdr>
--interactive-terminal-close-on-verdict
--no-interactive-terminal-close-on-verdict
```

- `--agent-runner interactive-terminal`: use the interactive terminal runner for
  this run only (normalized to config value `interactive_terminal`; the public
  CLI value is hyphen-separated only).
- `--agent-runner headless`: use headless for this run only, even if config uses
  interactive terminal.
- `--interactive-terminal-backend`: select the pane implementation for this run only. It does not
  implicitly enable the interactive runner.
- `--interactive-terminal-close-on-verdict` / `--no-...`: override
  close-on-verdict for this run only. If neither is specified, the config value
  is retained.

### Fixed precedence

1. `kaji run` CLI option
2. `.kaji/config.local.toml` `[execution]`
3. `.kaji/config.toml` `[execution]`
4. Built-in default (`agent_runner = "headless"`, `interactive_terminal_backend = "tmux"`,
   `interactive_terminal_close_on_verdict = true`)

### Examples

```bash
# Always run inside a tmux session ($TMUX missing fails fast)
tmux new-session            # or use an existing tmux session

# Use repository config where interactive_terminal is the default
kaji run .kaji/wf/official/dev.yaml 224

# Use interactive terminal for this run and keep panes open
kaji run .kaji/wf/official/dev.yaml 224   --agent-runner interactive-terminal   --no-interactive-terminal-close-on-verdict

# Use the Herdr backend from inside Herdr
kaji run .kaji/wf/official/dev.yaml 396 \
  --agent-runner interactive-terminal \
  --interactive-terminal-backend herdr

# Use headless for this run only (tmux not required)
kaji run .kaji/wf/official/dev.yaml 224 --agent-runner headless
```

## Launcher-console progress (Issue 235)

With the interactive terminal runner, agent work appears in the pane, so the
launcher console (the original pane that ran `kaji run`) can otherwise feel
silent. Since Issue 235, the harness writes timestamped `[kaji]` progress to the
launcher console through stdlib `logging`.

```text
[2026-06-07T12:34:57] [kaji] workflow start: dev issue #224
[2026-06-07T12:34:57] [kaji] step start: design attempt-001 dispatch=agent agent=claude model=opus
[2026-06-07T12:35:02] [kaji] pane launched: step=design agent=claude pane=%12 timeout=1800s verdict=/.../steps/design/attempt-001/verdict.yaml
[2026-06-07T12:42:10] [kaji] verdict detected: design source=artifact status=PASS
[2026-06-07T12:42:10] [kaji] step end: design status=PASS duration=433000ms next=review-design
[2026-06-07T12:42:10] [kaji] workflow end: status=COMPLETE duration=...ms
```

- `INFO` and below go to stdout; `WARNING` and above go to stderr.
- `--log-level {DEBUG,INFO,WARNING,ERROR}` (default `INFO`) controls the display threshold.
- `--quiet` suppresses agent/exec stdout streaming (pane or exec relay), but it
  does not affect `[kaji]` progress; the two are independent. Use
  `--log-level WARNING` if you only want to suppress harness progress.
- Deterministic exec steps such as `review-poll` do not open panes. During
  polling, they flush a `[review-poll]` heartbeat every `POLL_INTERVAL_SEC`
  (10s) to the launcher console, including elapsed seconds, PR number,
  abbreviated head, observed state, and timeout remaining. This lets you
  distinguish waiting, stopping, and errors without opening `run.log`.

```bash
# Show only warnings/errors from harness progress
kaji run .kaji/wf/official/dev.yaml 224 --log-level WARNING
```

## Behavior

The following numbered lifecycle describes the tmux implementation. Herdr uses the same wrapper,
verdict, and session-state contracts with the backend-specific differences below.

1. Before launch, the runner lists kaji-managed agent panes in the same window
   using `tmux list-panes` and pane user option
   `@kaji_interactive_terminal` matching `origin=<origin pane>`, then decides
   placement (Issue 238):
   - 0 managed panes: create a right column by splitting the origin pane with
     `tmux split-window -d -h -t "$TMUX_PANE"`.
   - 1 managed pane: split that agent pane vertically with `-d -v`, creating a
     second pane in the right column.
   - 2 or more managed panes: in ascending `pane_top` order, kill oldest panes
     until one remains, then split the remaining newest pane with `-d -v`. The
     right column is therefore always equivalent to the latest two panes, and
     the width does not keep shrinking step after step.

   Every split uses `-d` so focus is not stolen, and `-P -F '#{pane_id}'` returns
   the created pane id for lifecycle handling. Immediately after creation, kaji
   marks the pane with `tmux set-option -p -t <pane> @kaji_interactive_terminal
   origin=<origin pane>`, so only panes created by kaji are later pruned (user
   panes and panes from another origin are not closed by mistake). If marker
   setup fails, kaji best-effort kills the created pane and then fails loud.
   `list-panes` and pane lookup failures also fail loud to avoid unsafe cleanup
   in broken tmux state. If a user manually activates an agent pane and that
   pane is later pruned, tmux may move the active pane normally; kaji only
   promises not to steal focus during automatic creation.
2. The runner records pane output to `terminal.log` in the attempt directory via
   `tmux pipe-pane -o -t %id 'cat >> terminal.log'`.
3. The wrapper first runs `cd <workdir>` (trusted project worktree; not `/tmp` or
   the attempt directory), then launches normal `claude` / `codex` / `agy`
   (Codex also receives `--cd <workdir>`).
4. The wrapper does not embed the full prompt. It only tells the agent to read
   `prompt.txt` and write pure YAML to `verdict.yaml`.
5. The runner polls `verdict.yaml`; once it appears, it resolves the verdict via
   artifact-primary and continues the workflow. The **only completion trigger is
   the appearance of `verdict.yaml`**. The runner does not wait for the agent
   process to exit naturally. Pane liveness is checked with `#{pane_dead}`
   (lookup failure also counts as dead).
6. If `interactive_terminal_close_on_verdict = true`, verdict detection is
   followed by best-effort pane cleanup with `tmux kill-pane`. This is cleanup,
   not a latency contract such as "kill within <=N seconds after polling"; a
   kill after the next step starts is allowed. Timeout paths also best-effort
   kill the pane and then fail loud. An operator Ctrl-C (`KeyboardInterrupt`) is
   the exception: the pane is **not** killed (Issue #403). The runner records the
   orphan `pane_id` in `pane-metadata.json` and re-raises so the agent state stays
   inspectable; clean it up manually with `tmux kill-pane -t <pane_id>`.
7. If `interactive_terminal_close_on_verdict = false`, the runner sets
   `tmux set-option -p -t %id remain-on-exit on` before polling and does not kill
   the pane after the verdict. After the agent exits naturally, the pane remains
   as `[dead]` (`#{pane_dead}=1`) so the user can inspect it later. On the next
   step launch, that pane is still detected as a kaji-marked pane, and the right
   column is pruned to at most two panes (compare the latest and previous agent
   steps).

> **Pane metadata (diagnostics)**: At verdict detection time, the runner snapshots
> `#{pane_dead}` and related values to `pane-metadata.json` in the attempt
> directory. Under the verdict-trigger contract, the agent CLI is usually still
> alive when the verdict is detected, so the snapshot is normally
> `#{pane_dead}=0`. The later transition to `[dead]` is the final state
> guaranteed by `remain-on-exit on`, separate from the metadata snapshot. Since
> Issue 238, the snapshot also records placement diagnostics:
> `layout_target_pane`, `split_target_pane`, `split_direction` (`horizontal` /
> `vertical`), `kaji_agent_panes_before`, and `kaji_agent_panes_pruned`.

### Herdr behavior

1. Preflight validates `HERDR_ENV=1` and `HERDR_PANE_ID` before resolving the binary, then validates Herdr >= 0.8.2 and an exact `pane current --current` match.
2. Kaji reads token-owned panes and origin layout. It opens the first pane to the right, later panes
   downward, and keeps at most two. Prune re-reads current origin/run tokens before closing. A stale
   candidate with a missing run token/layout or changed ownership is skipped; kaji logs a warning and
   records its ID in `kaji_agent_panes_skipped` instead of blocking every later step.
3. Split uses explicit cwd and `--no-focus`. Kaji marks the response-derived pane, atomically publishes
   a private (`0700`) `herdr-launcher.sh` in the attempt directory, and sends only the short
   `<launcher path>` child command once. The shell remains the pane's shell process for liveness
   checks, while the inherited PATH and long wrapper argv stay in the launcher. Correctness therefore
   does not depend on a fresh shell's unobservable input mode. The launcher atomically publishes a
   private start marker before the wrapper exec; kaji waits for it within a fixed bound and does not
   count pre-marker shell-only observations as agent exit. Launcher creation, pane-run, or start
   confirmation failure cleans up the owned pane and fails loud. Ownership marker failure leaves the
   unowned pane untouched.
4. `verdict.yaml` is the only completion trigger. Foreground process observations only detect a
   command that returned to its shell early; output/status text never completes a step. Missing or
   malformed optional process fields are treated as unknown and never count as a confirmed shell return.
5. At verdict, pane-run failure, early exit, or timeout, kaji saves a best-effort rendered snapshot.
   Verdict cleanup obeys `interactive_terminal_close_on_verdict`; failure cleanup remains
   ownership-checked. A close failure is a warning recorded as `close_error` and never replaces the
   verdict, the original failure/timeout, or its resolved session. Early-exit metadata also includes
   the structured `terminal_diagnostic`; its message warns that a rendered snapshot may be incomplete.
6. An operator Ctrl-C leaves the agent pane open, records its ownership and rendered snapshot in
   `pane-metadata.json`, and re-raises `KeyboardInterrupt`. If snapshot persistence fails, the
   original interrupt still wins. Close the orphan manually with `herdr pane close <pane_id>` after
   inspection.

### Launch kaji from Codex or Claude Code

Use repository skill `herdr-kaji-launch` from an agent already running inside Herdr. Claude and
Codex share the canonical instructions through `.claude/skills/herdr-kaji-launch` and the
`.agents/skills/herdr-kaji-launch` symlink. The skill loads the release-matched `herdr --skill`,
splits from the explicit caller pane, preserves cwd/focus, and runs kaji as an ordinary interactive
command. It never uses Claude Code `-p` / print mode or Herdr `agent start`, and leaves the kaji pane
open. Plugins remain an optional human launcher UX, not a core dependency.

### Session continuation

- **Claude**: fresh runs generate a UUID and pass it as `--session-id`; the same
  UUID is stored in session state. Resume steps pass the stored id to `--resume`.
- **Codex**: after a fresh run, the runner extracts `codex resume <uuid>` from
  `terminal.log`. If unavailable, it scans `CODEX_HOME/sessions/**/*.jsonl`, then
  `~/.codex/sessions/**/*.jsonl`, in descending mtime and adopts the UUID from a
  rollout file that contains the attempt's `prompt.txt` / `verdict.yaml` path.
  Resume steps launch `codex resume <uuid>`. The tmux backend may wait through a
  collection grace period (<=5s) and re-scan after cleanup. The Herdr backend deliberately performs
  one rendered snapshot plus the session-store fallback without that grace: this avoids adding up to
  five seconds to every Herdr verdict, and the real Herdr 0.8.2 Codex run resolved its session with
  this path.
- **Antigravity**: no public session ID is collected. The result always carries
  `session_id=None`, and workflow `resume:` is rejected before launch.

#### Abnormal exit paths (timeout / pane-dead) (Issue #403)

An attempt that ends without a verdict still records a session ID in
`result.json` when one can be **verifiably** tied to that attempt (for diagnosis
and manual resume decisions; nothing is auto-resumed). The rules differ from the
verdict-detection path. These rules apply to both terminal backends; Herdr's confirmed return to
the shell uses the `pane-dead` row because the launched agent process has ended even though the
container pane remains available until ownership-checked cleanup.

| Agent | Resume input | Path | Resolution |
|---|---|---|---|
| codex | none / present | timeout / pane-dead | Adopted only when exactly one session-store rollout matches. Zero / multiple / unreadable resolves to `null` (never falls back to the parent id) |
| claude | none | timeout | The runner-minted `--session-id` UUID |
| claude | none | pane-dead | `null` (a dead pane includes launch failure, so the UUID may name a session that never existed) |
| claude | present | timeout / pane-dead | The resume input id (`--resume` continues the same session) |
| antigravity | — | all | `null` |

- The codex scan is limited to `CODEX_HOME/sessions/**/*.jsonl` entries modified
  **at or after the `prompt.txt` mtime**, counting those whose body contains the
  attempt marker (the absolute `prompt.txt` / `verdict.yaml` paths). On a `resume:`
  step the parent rollout also contains the marker but stopped being updated
  before the attempt started, so it is excluded.
- Abnormal exit paths never read `terminal.log` (on timeout the Codex resume line
  has not been printed yet, and the transcript can be very large) and add no grace
  wait.
- The **verdict-detection path keeps its existing rule** (first match in
  descending mtime). Codex `resume` creates a new rollout that inherits history,
  so the same marker can legitimately match more than once; forcing uniqueness
  there would regress existing `resume:` steps with `MissingResumeSessionError`.

### Effort note (Codex)

Codex `reasoning.effort = minimal` conflicts with the current tool configuration
(`image_gen` / `web_search`), so the practical minimum is `low`. The runner /
wrapper pass effort through; choosing the minimum value is the responsibility of
workflow steps and manual verification.

## Manual verification procedure (real tmux + real Claude / Codex / Antigravity)

> Automated tests cover behavior with fake binaries + real tmux at Large
> (`large_local`) and fake tmux at Medium. Live connectivity with real `claude` /
> `codex` / `agy` is **intentionally not automated** because it may incur API charges and
> interactive CLIs are hard to automate. Verify it manually as follows.

Run verification **inside a project worktree** and **inside a tmux session**. Do
not use `/tmp` or an attempt directory as cwd.

Verification models / effort (low-cost choices):

- Claude: `haiku` / `low`
- Codex: `gpt-5.4-mini` / `low`
- Antigravity: an available model / `low`

Procedure:

1. Start a tmux session (`tmux new-session`, or use an existing session).
2. Set `[execution] agent_runner = "interactive_terminal"` in
   `.kaji/config.local.toml` (or pass `kaji run ... --agent-runner interactive-terminal`).
3. Start a minimal workflow with `kaji run`, and confirm a pane opens on the
   right side of the current window and launches normal `claude`.
4. Confirm kaji advances to the next step after the agent writes `verdict.yaml`
   (**Claude fresh**).
5. Confirm the resume step starts with the same session id (`--resume <uuid>`)
   (**Claude resume**).
6. Repeat for Codex: fresh writes `verdict.yaml` (**Codex fresh**), then resume
   starts with `codex resume` (**Codex resume**).
7. Confirm panes disappear after verdict with
   `interactive_terminal_close_on_verdict = true` (`kill-pane`), and remain as
   `[dead]` with `false` (`--no-interactive-terminal-close-on-verdict`).
8. Confirm `terminal.log` is recorded in the attempt directory (always recorded,
   regardless of OS).
9. With `--no-interactive-terminal-close-on-verdict`, run **three or more agent
   steps** consecutively and confirm kaji-managed panes in the right column stay
   at **at most two**, and the `pane_width` of the origin pane and right-column
   agent panes does not keep narrowing on every step. Also confirm manually
   created unrelated panes are not closed by mistake.

## Troubleshooting

| Symptom | Cause / action |
|---------|----------------|
| Immediate exit with `CLI 'tmux' not found` | Add `tmux` to PATH or run with `--agent-runner headless` |
| Immediate exit with `requires tmux. Run kaji run inside tmux` | You are outside a tmux session. Rerun inside `tmux new-session` or use `--agent-runner headless` |
| Immediate exit with `requires tmux >= 3.1` | tmux is too old. Upgrade to 3.1 or newer (`set-option -p` pane user option is 3.1; `#{pane_dead}` / `split-window -P -F` require 3.0) |
| Immediate exit with `TMUX_PANE is not set` | You are not inside a tmux pane. Normal tmux sessions set it automatically |
| Immediate exit with `CLI 'herdr' not found` | Install Herdr >= 0.8.2 or select the tmux backend |
| Immediate exit with `must run inside Herdr` | Start kaji inside a Herdr pane; external focused-session control is intentionally refused |
| `HERDR_PANE_ID is not set` | Restart the command in a normal Herdr pane so caller identity is explicit |
| Herdr `terminal.log` lacks older lines | It is a rendered snapshot, not a raw transcript. In Herdr 0.8.2 metadata, `transcript_truncated=null` means the plain-text CLI did not expose whether older rows were omitted; use the revision as snapshot identity, not completeness proof |
| Step times out | The agent did not write `verdict.yaml`. Check the prompt's verdict-writing instruction and path |
| Pane disappears before verdict / `tmux pane exited before writing verdict.yaml` | Agent launch failed. Check the tail of `terminal.log` attached to the error |
| No color | The wrapper unsets `NO_COLOR` and sets `COLORTERM=truecolor`; also check terminal color support and agent-side settings |
| Codex resume does not work | No resume line in `terminal.log`, and session-store fallback did not match markers. Check `CODEX_HOME` |
| CLI stops at trust / permission confirmation | cwd is outside the project (`/tmp`, etc.). Pin workdir to the project worktree |
