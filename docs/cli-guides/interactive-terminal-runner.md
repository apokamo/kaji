# Interactive Terminal Runner

Language: English | [日本語](interactive-terminal-runner.ja.md)

A runner that executes `kaji run` agent steps through normal `claude`, `codex`,
or Antigravity (`agy`) interactive CLIs inside **Herdr panes**, instead of the headless CLI
path (Issues 224, 230, 396, and 515). When an agent writes
`verdict.yaml` in the attempt directory, kaji reads the verdict through the
artifact-primary path ([ADR 005](../adr/005-artifact-primary-verdict.md)) and
advances to the next step.

Herdr is the only interactive terminal backend. The tmux backend was removed in Issue 515 after its
deprecation in 0.23.0 (see [Migrating from tmux](#migrating-from-tmux)). Kaji uses Herdr's
release-matched CLI and explicit pane IDs, starts inside a Herdr pane, and adds agent panes beside
its origin pane. This lets you see `kaji run` output and the agent side by side even in displayless
environments (WSL2 / SSH to a host where Herdr runs). Pane placement is: first pane to the right of
the origin pane, later panes split vertically within the right column. Kaji keeps at most **two**
managed agent panes in the right column (Issue 238).

Each agent is launched through a kaji-owned Python PTY recorder, so `terminal.log` holds the raw
PTY bytes the agent wrote, including alternate-screen output (Issue 515). See
[Terminal log (`terminal.log`)](#terminal-log-terminallog).

For the technical selection rationale, see [ADR 007](../adr/007-interactive-terminal-runner.md).
For runner dispatch placement, see [ARCHITECTURE](../ARCHITECTURE.md) section
Runner backend dispatch.

## When to use it

- You want to advance a workflow in a shape closer to normal console use rather
  than the metered headless path.
- You want to see `kaji run` output and the agent in the same screen even in a
  displayless environment (WSL2 / SSH to a host where Herdr runs).
- You want each step to continue Claude / Codex sessions through `--resume` /
  `codex resume`.
- You want to run a single Antigravity step through `agy -i` while keeping
  kaji's artifact completion contract. Antigravity does not support `resume:`.
- You want to keep the agent's final state visible in the pane after the verdict
  (`close_on_verdict = false`). Even when panes remain, the right column is kept
  to the latest two panes, so its width does not shrink on every step.

When `agent_runner` is unset, the existing `headless` runner is used. Existing
workflows and CI behavior do not change.

## Migrating from tmux

The tmux backend was **removed** (Issue 515, parent Issue 466; deprecated in 0.23.0, Issue 475).
Herdr is the only interactive terminal backend; the headless runner stays.

### What changed

- `interactive_terminal_backend = "tmux"` in `.kaji/config.toml` or `.kaji/config.local.toml`
  (after the per-key merge) fails config loading with `ConfigLoadError` and names the file that
  defines the value. This happens for every `kaji run`, **including headless runs**.
  `--agent-runner headless` / `--interactive-terminal-backend herdr` **cannot override it**; you
  must edit the file.
- `--interactive-terminal-backend tmux` is rejected as a usage error (exit 2) with migration
  guidance. Other unknown values fail with argparse's `invalid choice`.
- With `agent_runner = "interactive_terminal"` and no backend set, the default is now `herdr`, so
  `kaji run` must run inside a Herdr pane. Running outside Herdr raises `HerdrSessionRequiredError`;
  there is no fallback to headless and no terminal auto-detection.

### Am I affected?

Search both config files and your scripts:

```bash
grep -nE 'interactive_terminal_backend|agent_runner' .kaji/config.toml .kaji/config.local.toml
grep -rnE -- '--interactive-terminal-backend|--agent-runner' <your-scripts>
```

These searches are a heuristic (quoting or dynamically built arguments may hide matches). A
representative `kaji run` confirms the result: an affected config fails at config loading and names
the file to edit.

### How to migrate

- **Herdr**: install Herdr (see [Prerequisites](#prerequisites)), set
  `interactive_terminal_backend = "herdr"` (or remove the key), and start `kaji run` inside a Herdr
  pane.
- **Headless**: set `agent_runner = "headless"` (or remove `agent_runner`) and remove
  `interactive_terminal_backend = "tmux"`. No terminal multiplexer is needed.
- In both cases, remove or change `"tmux"` in **both** the tracked `.kaji/config.toml` **and** the
  gitignored `.kaji/config.local.toml`, and drop `--interactive-terminal-backend tmux` from scripts.
  The error names the file that currently defines the value; fixing only that file can reveal the
  same value in the other file on the next run.

Past tmux run artifacts stay readable for diagnosis: a historical `TmuxSessionRequiredError` in
`run.log` is still classified as a user precondition error, and an old tmux `pane-metadata.json`
(`pane_id: "%N"`) is still read for the orphan pane ID.

## tmux backend deprecation

The tmux backend was deprecated in 0.23.0 and has since been removed. See
[Migrating from tmux](#migrating-from-tmux).

## Prerequisites

- Herdr **>= 0.8.2** must be on PATH and its client/server protocol must be compatible. When
  `HERDR_BIN_PATH` points to an executable, kaji prefers that release-matched CLI over PATH lookup.
- Run `kaji run` inside Herdr. `HERDR_ENV=1` and `HERDR_PANE_ID` are required; an outside caller
  raises `HerdrSessionRequiredError` and never controls the UI-focused session. Preflight also runs
  `herdr status` and requires `pane current --current` to match `HERDR_PANE_ID` exactly. This
  failure is treated as a known user precondition error and never opens an incident Issue (the
  triage comment and run artifacts are kept; [failure-recovery.md](./failure-recovery.md) §
  Incident recording exemption).
- The selected `claude`, `codex`, or `agy` CLI must be on PATH.
- Kaji invokes the installed CLI with argv lists, parses JSON response IDs, and never predicts a
  pane ID. A raw socket client, plugin, and Herdr agent integration are not required.
- Created panes carry `kaji_origin`, `kaji_run`, and `kaji_step` metadata tokens. Cleanup re-reads
  the exact pane and requires current origin/run ownership before closing it.
- Before launching the agent, the wrapper unsets `NO_COLOR` and sets
  `COLORTERM=truecolor`. Even if the parent shell has `NO_COLOR=1`, the agent
  inside the interactive terminal runner can use truecolor output.
- The PTY recorder uses only the Python standard library (`pty`, `termios`, ...) and runs with kaji's
  own interpreter in isolated mode (`-I`). It is Unix-only; Linux (including WSL2) is verified, and
  real-machine verification on macOS is deferred.

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
interactive_terminal_backend = "herdr"           # "herdr" only (default); "tmux" is an error
interactive_terminal_close_on_verdict = true     # default true
```

If `agent_runner` or `interactive_terminal_backend` is not an allowed value, config loading fails
fast with `ConfigLoadError`. The exhaustive specification for `[execution]` keys (type /
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

### Codex skill invocation

The runner passes a tenth wrapper argument, `skill_invocation`. For Codex it is
the `$<skill> を実行してください。` line (the same line `build_prompt` puts at the top
of `prompt.txt`); for Claude and Antigravity it is an empty string. When it is
non-empty, the wrapper prepends it, followed by a blank line, to the initial
message it hands to the agent, because Codex resolves a `$<skill>` mention only
in the user's input and not inside `prompt.txt`. An empty value keeps the
original initial message unchanged.

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
--interactive-terminal-backend <herdr>
--interactive-terminal-close-on-verdict
--no-interactive-terminal-close-on-verdict
```

- `--agent-runner interactive-terminal`: use the interactive terminal runner for
  this run only (normalized to config value `interactive_terminal`; the public
  CLI value is hyphen-separated only).
- `--agent-runner headless`: use headless for this run only, even if config uses
  interactive terminal.
- `--interactive-terminal-backend`: accepts only `herdr`. It does not implicitly enable the
  interactive runner. `tmux` is rejected with migration guidance.
- `--interactive-terminal-close-on-verdict` / `--no-...`: override
  close-on-verdict for this run only. If neither is specified, the config value
  is retained.

### Fixed precedence

1. `kaji run` CLI option
2. `.kaji/config.local.toml` `[execution]`
3. `.kaji/config.toml` `[execution]`
4. Built-in default (`agent_runner = "headless"`, `interactive_terminal_backend = "herdr"`,
   `interactive_terminal_close_on_verdict = true`)

A `"tmux"` value in either config file fails config loading before CLI options are applied.

### Examples

```bash
# Run inside a Herdr pane (HERDR_ENV / HERDR_PANE_ID missing fails fast)

# Use repository config where interactive_terminal is the default
kaji run .kaji/wf/official/dev.yaml 224

# Use interactive terminal for this run and keep panes open
kaji run .kaji/wf/official/dev.yaml 224 \
  --agent-runner interactive-terminal \
  --no-interactive-terminal-close-on-verdict

# Use headless for this run only (Herdr not required)
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
[2026-06-07T12:35:02] [kaji] pane launched: step=design agent=claude pane=w1-3 timeout=1800s verdict=/.../steps/design/attempt-001/verdict.yaml
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

1. Preflight validates `HERDR_ENV=1` and `HERDR_PANE_ID` before resolving the binary, then validates
   Herdr >= 0.8.2 and an exact `pane current --current` match. It also checks that the packaged
   wrapper, kaji's Python interpreter, and the recorder module exist before any pane is created.
2. Kaji reads token-owned panes and origin layout. It opens the first pane to the right, later panes
   downward, and keeps at most two. Prune re-reads current origin/run tokens before closing. A stale
   candidate with a missing run token/layout or changed ownership is skipped; kaji logs a warning and
   records its ID in `kaji_agent_panes_skipped` instead of blocking every later step.
3. Split uses explicit cwd and `--no-focus`. Kaji marks the response-derived pane, atomically publishes
   a private (`0700`) `herdr-launcher.sh` in the attempt directory, and sends only the short
   `<launcher path>` child command once. The shell remains the pane's shell process for liveness
   checks, while the inherited PATH and long argv stay in the launcher. Correctness therefore
   does not depend on a fresh shell's unobservable input mode. The launcher atomically publishes a
   private start marker before it execs the recorder; kaji waits for it within a fixed bound and does
   not count pre-marker shell-only observations as agent exit. Launcher creation, pane-run, or start
   confirmation failure cleans up the owned pane and fails loud. Ownership marker failure leaves the
   unowned pane untouched.
4. The launcher runs `HERDR_AGENT=<claude|codex|agy> <python> -I <recorder> <terminal.log> --
   <wrapper> <10 args>`. `HERDR_AGENT` is set on the recorder process only (kaji's `antigravity` maps
   to Herdr's `agy` manifest), so Herdr's agent detection (idle / working / blocked / done) behaves
   as with a direct launch. The recorder removes `HERDR_AGENT` from the child environment, so it does
   not leak into nested kaji runs.
5. The wrapper first runs `cd <workdir>` (trusted project worktree; not `/tmp` or
   the attempt directory), then launches normal `claude` / `codex` / `agy`
   (Codex also receives `--cd <workdir>`). The wrapper does not embed the full prompt. It only tells
   the agent to read `prompt.txt` and write pure YAML to `verdict.yaml`.
6. `verdict.yaml` is the only completion trigger. Foreground process observations only detect a
   command that returned to its shell early; output/status text and the recorder's exit code never
   complete a step. Missing or malformed optional process fields are treated as unknown and never
   count as a confirmed shell return.
7. Verdict cleanup obeys `interactive_terminal_close_on_verdict`; failure cleanup remains
   ownership-checked. A close failure is a warning recorded as `close_error` and never replaces the
   verdict, the original failure/timeout, or its resolved session. Early-exit and timeout metadata
   also include the structured `terminal_diagnostic`, built from the recorder's `terminal.log`.
8. An operator Ctrl-C in the kaji pane (`KeyboardInterrupt`) leaves the agent pane open, records its
   ownership in `pane-metadata.json`, and re-raises. If metadata persistence fails, the original
   interrupt still wins. Close the orphan manually with `herdr pane close <pane_id>` after
   inspection. A Ctrl-C typed inside the agent pane is a separate path: the recorder forwards it as
   `\x03` to the agent's PTY, so the agent receives SIGINT as with a direct launch.

### Terminal log (`terminal.log`)

- **Format**: the raw bytes the child (wrapper → agent) wrote to its PTY, appended unchanged. This
  includes ANSI escapes, alternate-screen content (after `\e[?1049h`), and output after `\e[3J`. It
  is the same shape as the former tmux `pipe-pane` log. The wrapper banner is recorded too.
- **Writes**: each chunk is appended unbuffered (`O_APPEND`) before it is mirrored to the pane, so
  chunks already read survive a forced close of the recorder or pane. The log is opened before the
  agent starts; an agent exec failure is written to the log through the PTY (exit code 127).
- **No size limit or rotation.** With `close_on_verdict = false`, the log keeps growing while the
  agent stays alive after the step.
- **Sensitive data**: a raw PTY recording contains everything the agent printed to the screen. The
  file follows the process umask, as before. Treat attempt directories accordingly.
- **Recorder state** (`terminal.log.state`, internal): the recorder writes `recording` before the
  agent starts, `complete` after the child's EOF, and `incomplete <ERRNO>` (e.g. `ENOSPC`) when a
  log write fails; it then stops recording and keeps relaying the pane. If it cannot overwrite the
  state, it deletes the file so the harness reports `unknown`. Residual risk: if both the overwrite
  and the delete fail, `recording` remains even though bytes were lost.

### Pane metadata (diagnostics)

`pane-metadata.json` in the attempt directory records ownership, placement diagnostics
(`layout_target_pane`, `split_target_pane`, `split_direction`, `kaji_agent_panes_before`,
`kaji_agent_panes_pruned`, `kaji_agent_panes_skipped`), and transcript metadata:

| Key | Meaning |
|-----|---------|
| `transcript_kind` | `"raw_pty_recording"` |
| `transcript_available` | `terminal.log` exists as a regular file when the metadata is written |
| `transcript_recorder_state` | `"recording"` / `"complete"` / `"incomplete"` / `"unknown"` (state file missing, unreadable, or invalid) |
| `transcript_truncated` | `false` for `recording` / `complete` (no known loss), `true` for `incomplete`, `null` for `unknown`. Omitted when no log exists |
| `transcript_error` | Only for `incomplete`: the errno name (e.g. `ENOSPC`); never transcript content |
| `transcript_revision` | Always `null` (the recorder has no revision concept). Omitted when no log exists |
| `transcript_bytes` | Size of `terminal.log` in bytes at metadata time. Omitted when no log exists |

When the recorder state is `incomplete` or `unknown`, the abnormal-exit error detail adds
`; transcript may be incomplete (recorder state: <state>)`.

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
  Resume steps launch `codex resume <uuid>`. There is no collection grace period: the verdict path
  reads `terminal.log` once and then falls back to the session store.
- **Antigravity**: no public session ID is collected. The result always carries
  `session_id=None`, and workflow `resume:` is rejected before launch.

#### Abnormal exit paths (timeout / pane-dead) (Issue #403)

An attempt that ends without a verdict still records a session ID in
`result.json` when one can be **verifiably** tied to that attempt (for diagnosis
and manual resume decisions; nothing is auto-resumed). The rules differ from the
verdict-detection path. Herdr's confirmed return to the shell uses the `pane-dead` row because the
launched agent process has ended even though the container pane remains available until
ownership-checked cleanup.

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
- Abnormal exit paths do not read `terminal.log` for the session ID (on timeout the Codex resume
  line has not been printed yet, and the transcript can be very large) and add no grace wait.
- The **verdict-detection path keeps its existing rule** (first match in
  descending mtime). Codex `resume` creates a new rollout that inherits history,
  so the same marker can legitimately match more than once; forcing uniqueness
  there would regress existing `resume:` steps with `MissingResumeSessionError`.

### Effort note (Codex)

Codex `reasoning.effort = minimal` conflicts with the current tool configuration
(`image_gen` / `web_search`), so the practical minimum is `low`. The runner /
wrapper pass effort through; choosing the minimum value is the responsibility of
workflow steps and manual verification.

## Manual verification procedure (real Herdr + real Claude / Codex / Antigravity)

> Automated tests cover the recorder on a real PTY and the launcher → recorder → wrapper chain with
> fake agents at Large (`large_local`), and the Herdr backend with a fake `herdr` CLI at Medium.
> Live connectivity with real Herdr and real `claude` / `codex` / `agy` is **intentionally not
> automated** because it needs a Herdr pane, agent authentication, and may incur API charges.
> Verify it manually as follows.

Run verification **inside a project worktree** and **inside a Herdr pane**. Do
not use `/tmp` or an attempt directory as cwd.

Verification models / effort (low-cost choices):

- Claude: `haiku` / `low`
- Codex: `gpt-5.4-mini` / `low`
- Antigravity: an available model / `low`

Procedure:

1. Start Herdr and open a pane (locally, under WSL2, or on a host reached over SSH).
2. Set `[execution] agent_runner = "interactive_terminal"` in
   `.kaji/config.local.toml` (or pass `kaji run ... --agent-runner interactive-terminal`).
3. Start a minimal workflow with `kaji run`, and confirm a pane opens on the
   right side of the origin pane and launches normal `claude`.
4. Confirm kaji advances to the next step after the agent writes `verdict.yaml`
   (**Claude fresh**).
5. Confirm the resume step starts with the same session id (`--resume <uuid>`)
   (**Claude resume**).
6. Repeat for Codex: fresh writes `verdict.yaml` (**Codex fresh**), then resume
   starts with `codex resume` (**Codex resume**).
7. Confirm panes close after verdict with `interactive_terminal_close_on_verdict = true`, and remain
   with `false` (`--no-interactive-terminal-close-on-verdict`).
8. Confirm `terminal.log` in the attempt directory contains the conversation (including
   alternate-screen output) and `pane-metadata.json` shows `transcript_kind = "raw_pty_recording"`.
9. With `--no-interactive-terminal-close-on-verdict`, run **three or more agent
   steps** consecutively and confirm kaji-managed panes in the right column stay
   at **at most two**. Also confirm manually created unrelated panes are not closed by mistake.

Record the Herdr and agent versions with the result.

## Troubleshooting

| Symptom | Cause / action |
|---------|----------------|
| `config error (...): execution.interactive_terminal_backend = 'tmux' is no longer supported` | The tmux backend was removed. Edit the named file (and check the other config file); see [Migrating from tmux](#migrating-from-tmux). CLI options cannot override it |
| `argument --interactive-terminal-backend: the tmux backend was removed` (exit 2) | Use `herdr`, or `--agent-runner headless` |
| Immediate exit with `CLI 'herdr' not found` | Install Herdr >= 0.8.2 or use the headless runner |
| Immediate exit with `must run inside Herdr` (`HerdrSessionRequiredError`) | Start kaji inside a Herdr pane, or set `agent_runner = "headless"`; external focused-session control is intentionally refused |
| `HERDR_PANE_ID is not set` | Restart the command in a normal Herdr pane so caller identity is explicit |
| `transcript may be incomplete (recorder state: ...)` | The recorder could not write part of `terminal.log` (`incomplete`, see `transcript_error`, e.g. disk full) or its state could not be confirmed (`unknown`). The agent run itself is unaffected |
| Step times out | The agent did not write `verdict.yaml`. Check the prompt's verdict-writing instruction and path |
| `Herdr pane returned to its shell before writing verdict.yaml` | Agent launch failed or the agent exited early. Check the tail of `terminal.log` attached to the error |
| No color | The wrapper unsets `NO_COLOR` and sets `COLORTERM=truecolor`; also check terminal color support and agent-side settings |
| Codex resume does not work | No resume line in `terminal.log`, and session-store fallback did not match markers. Check `CODEX_HOME` |
| CLI stops at trust / permission confirmation | cwd is outside the project (`/tmp`, etc.). Pin workdir to the project worktree |
