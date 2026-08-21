# Antigravity CLI Guide

Language: English

kaji supports Antigravity CLI as the public workflow agent `antigravity`
(executable: `agy`). Support is intentionally limited to single-run headless
and interactive steps because AGY v1.1.6 does not expose a new conversation ID
or machine-readable event stream through its public stdout contract.

## Prerequisites

- Install and authenticate Antigravity CLI by following the
  [official installation guide](https://antigravity.google/docs/cli/install).
- Ensure `agy` is on PATH.
- Use `tmux` 3.1 or newer when selecting kaji's interactive terminal runner.

Verify the local CLI:

```bash
agy --version
agy --help
```

## Workflow configuration

Use `agent: antigravity` for a single workflow step:

```yaml
name: antigravity-example
execution_policy: auto
cycles:
  implementation:
    entry: implement
    loop: [implement]
    max_iterations: 3
    on_exhaust: ABORT
steps:
  - id: implement
    skill: issue-implement
    agent: antigravity
    model: gemini-3-pro
    effort: high
    on:
      PASS: end
      RETRY: implement
      ABORT: end
```

`model` is passed through to `--model`. Supported `effort` values are `low`,
`medium`, and `high`; kaji rejects other values while parsing the workflow.

The `RETRY: implement` self-edge needs the matching one-step `cycles` entry.
kaji counts iterations only for steps that belong to a `cycles.*.loop` tail, so
a self-`RETRY` edge without a cycle would re-dispatch the step without any cap.
With the cycle above, the fourth entry into `implement` yields the synthetic
`on_exhaust` verdict (`ABORT`) instead of looping forever.

## Headless execution

The default runner executes:

```text
agy -p <prompt> [--model <model>] [--effort <effort>] [<policy flag>]
```

AGY stdout is plain text. kaji preserves all lines, including JSON-looking
lines, blank lines, and leading or trailing spaces (only line terminators are
removed when building `CLIResult.full_output`). stderr and the process exit code
are retained; a non-zero exit raises `CLIExecutionError`.

AGY does not provide JSONL progress or public token/cost metadata on this path.
Accordingly, kaji returns:

- `session_id=None`
- `cost=None`
- `terminal_seen=False`
- `terminal_failure=False`

An exit code of zero with no answer can occur when a headless tool request is
not approved. kaji does not invent a successful verdict for this soft-deny
case: if stdout contains no verdict, normal verdict resolution fails loud.

## Interactive terminal execution

Set the repository execution backend to interactive terminal:

```toml
[execution]
default_timeout = 2400
agent_runner = "interactive_terminal"
interactive_terminal_backend = "tmux"  # or "herdr"
interactive_terminal_close_on_verdict = true
```

Run `kaji run` inside tmux. The wrapper launches:

```text
agy [<policy flag>] [--model <model>] [--effort <effort>] -i <initial prompt>
```

The completion trigger is the attempt's `verdict.yaml`, not AGY process exit or
stdout parsing. Pane lifecycle, timeout, transcript, and cleanup follow the
[Interactive Terminal Runner](interactive-terminal-runner.md) contract.
Interactive Antigravity results also use `session_id=None`.

## Permission and sandbox policy

AGY treats permission approval and sandbox containment as separate controls.
kaji uses the same mapping for headless and interactive execution:

| `execution_policy` | AGY flag | Behavior |
|--------------------|----------|----------|
| `auto` | `--dangerously-skip-permissions` | Auto-approve tool permission requests |
| `sandbox` | `--sandbox` | Enable containment without bypassing approval |
| `interactive` | none | Use AGY defaults; TUI approval is available only in interactive terminal mode |

Headless `sandbox` and `interactive` runs may soft-deny tools because no human
is present to answer an approval request.

## Resume is not supported

Do not set `resume:` on an Antigravity step:

```yaml
steps:
  - id: implement
    skill: issue-implement
    agent: antigravity
    resume: design
    on:
      PASS: end
```

Both `kaji validate` and run preflight reject this before AGY starts. The error
identifies the step, `antigravity` agent, and unsupported `resume` capability.
kaji never silently ignores the field or delays the failure to session lookup.

AGY exposes conversation continuation options, but kaji cannot safely associate
a newly started workflow step with a public conversation ID. Diagnostic
`--log-file` output and internal conversation storage are not part of kaji's
supported session contract and are not scraped.

## Capability matrix

| Capability | Support |
|------------|---------|
| Headless single run (`agy -p`) | Yes |
| Interactive terminal (`agy -i`) | Yes |
| Plain stdout verdict | Yes |
| `model` passthrough | Yes |
| `effort` validation | `low` / `medium` / `high` |
| Permission / sandbox mapping | Yes |
| Workflow `resume:` | No; validation error |
| Session ID collection | No |
| JSONL progress | No |
| Token / cost collection | No |

## Failure diagnosis

| Symptom | Action |
|---------|--------|
| `CLI 'agy' not found` | Install Antigravity CLI and ensure `agy` is on PATH |
| Non-zero exit | Inspect the captured stderr and `stderr.log` |
| Exit 0 with empty output | Check AGY permission rules; use `auto` only when automatic approval is acceptable |
| Interactive timeout | Confirm the agent wrote pure YAML to the exact `verdict.yaml` path from the prompt |
| Resume validation error | Remove `resume:` and use a single-run Antigravity step |
