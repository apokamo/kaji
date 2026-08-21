# Herdr 0.8.2 pane lifecycle smoke test

Issue: #396

## Purpose

Before implementing a Herdr backend, verify the minimum non-destructive control surface:

- explicit pane creation
- cwd and focus control
- foreground process inspection
- rendered output reads
- read-only terminal observation
- exact-target cleanup

No coding agent or kaji workflow was launched.

## Environment

```text
client: Herdr 0.8.2 stable, protocol 20
server: Herdr 0.8.2, protocol 20, compatible
origin pane: w1:p1
origin cwd: /home/aki
test pane: w1:p2
test cwd: /home/aki/dev/kaji/main
```

Pane IDs are historical evidence from the test session and must not be reused by later scripts.

## Commands

The test used the following command shapes. A rerun must parse the newly created pane ID from the
split JSON response rather than copying `w1:p2`.

```bash
herdr status
herdr pane current
herdr pane split w1:p1 \
  --direction right \
  --ratio 0.5 \
  --cwd /home/aki/dev/kaji/main \
  --no-focus

herdr pane process-info --pane w1:p2
herdr pane run w1:p2 \
  "printf 'KAJI_HERDR_SMOKE_START\\n'; pwd; sleep 4; printf 'KAJI_HERDR_SMOKE_DONE\\n'"
herdr pane process-info --pane w1:p2
herdr pane wait-output w1:p2 \
  --regex '^KAJI_HERDR_SMOKE_DONE$' \
  --source recent-unwrapped \
  --lines 200 \
  --timeout 10000
herdr pane read w1:p2 --source recent-unwrapped --lines 200
herdr terminal session observe w1:p2 --cols 100 --rows 30
```

Before cleanup, `pane get` and `pane list` were used to verify that the target was the created test
pane with the expected cwd. Only then was it closed:

```bash
herdr pane get w1:p2
herdr pane list
herdr pane close w1:p2
herdr pane list
```

## Results

### Split and cwd

The split returned the new pane at `.result.pane.pane_id`. `--cwd` and `--no-focus` worked as
documented. The origin retained focus.

### Foreground process

Before the probe, `pane process-info` reported the pane shell (`bash`). During `sleep 4`, it reported
`sleep`; after completion, it returned to `bash`. Process information can distinguish an ordinary
foreground command from an available shell.

### Output matching

A literal `--match KAJI_HERDR_SMOKE_DONE` matched the shell-echoed command line before the actual
completion line. Anchoring a regex to the complete line matched the emitted marker correctly.

Decision: kaji must not use output matching as the workflow completion authority. The existing
filesystem `verdict.yaml` trigger remains authoritative.

### Rendered reads

`pane read --source recent-unwrapped` returned plain rendered text containing the command, markers,
cwd, and prompt.

### Terminal observer

`terminal session observe` emitted newline-delimited `terminal.frame` JSON. The initial record had
`full: true`; later records had `full: false`. Each `bytes` field contained base64-encoded ANSI screen
state or deltas. This is not a raw PTY byte stream and cannot be appended directly as the tmux
`pipe-pane` equivalent.

### Cleanup

The read-only observer was stopped with Ctrl-C. The test pane was verified and closed. The origin
pane remained. The Herdr server/session was not stopped or deleted.

## Conclusions

- Herdr has sufficient primitives for explicit pane lifecycle management.
- `verdict.yaml` must remain the completion trigger.
- `recent-unwrapped` is suitable for a rendered diagnostic snapshot, not a guaranteed complete raw
  transcript.
- Cleanup must require both a response-derived pane ID and a kaji ownership marker.
- Agent lifecycle and alternate-screen transcript behavior still require real-agent tests.
