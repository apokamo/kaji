# Stateful fake Herdr CLI smoke test

Issue: #396

## Purpose

Exercise the product backend across a real subprocess and JSON boundary without connecting to or
mutating a live Herdr session. The fake implements only the protocol-20 response shapes used by the
smoke path and stores its state in a fresh temporary directory.

Assets:

- `experiments/herdr-interactive-terminal/scripts/herdr`
- `experiments/herdr-interactive-terminal/scripts/run_fake_herdr_smoke.py`

## Initial run and environment correction

The first invocation used:

```bash
source .venv/bin/activate
python experiments/herdr-interactive-terminal/scripts/run_fake_herdr_smoke.py
```

It failed before any fake Herdr call:

```text
TypeError: execute_interactive_terminal() got an unexpected keyword argument 'backend'
```

Cause: this feature worktree intentionally reuses the main worktree's `.venv`. Because a script's
directory is `sys.path[0]`, the editable installation resolved `kaji_harness` from main rather than
the feature worktree. The reproducible command must explicitly put the current checkout first.

## Successful command

```bash
source .venv/bin/activate
PYTHONPATH=. python experiments/herdr-interactive-terminal/scripts/run_fake_herdr_smoke.py
```

Observed summary:

```json
{
  "call_count": 9,
  "fake_closed": true,
  "session_id_present": true,
  "terminal_log": "fake interactive agent screen\n",
  "verdict_present": true
}
```

The generated metadata confirmed:

- backend `herdr`, version `0.8.2`
- origin `w1:p1`, response-derived pane `w1:p2`
- marker confirmation before cleanup
- first-pane right split with no prune
- rendered recent-unwrapped snapshot revision 7
- `truncated=false`

## Safety

The fake requires both `KAJI_FAKE_HERDR=1` and an explicit `KAJI_FAKE_HERDR_STATE` path. It never
opens the real Herdr socket. All prompt, verdict, transcript, metadata, and state files were created
inside `TemporaryDirectory` and removed automatically after the process ended. No live pane,
session, agent, repository config, or user integration was changed.
