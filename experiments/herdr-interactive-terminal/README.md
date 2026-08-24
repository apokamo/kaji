# Herdr interactive terminal experiments

Issue #396 の Herdr backend と agent-originated kaji launch を検証するための再現資産。

## Safety boundary

- 非破壊probeを先に実行する。
- Herdr内agentによるpane操作は `HERDR_ENV=1` と `HERDR_PANE_ID` を必須とする。
- focused paneを暗黙targetにしない。
- 作成responseから得たpane ID以外をcloseしない。
- server stop、session delete、process強制終了、managed pane pruneは後段の破壊的検証へ分離する。
- secrets、auth token、未redactのagent transcriptを保存しない。

## Tested baseline

- Date: 2026-08-20
- Herdr: 0.8.2 stable
- Protocol: 20
- Platform: Linux
- kaji base: `eb859c8e29a8870f30693e85cb26c96220dc9537`

## Reports

- [Pane lifecycle smoke test](reports/2026-08-20-pane-lifecycle-smoke.md)
- [Integration surface research](reports/2026-08-20-integration-surfaces.md)
- [Backend selection TDD](reports/2026-08-20-backend-selection-tdd.md)
- [Stateful fake CLI smoke test](reports/2026-08-20-fake-cli-smoke.md)
- [Implementation validation](reports/2026-08-20-implementation-validation.md)
- [Metadata ownership persistence](reports/2026-08-21-metadata-ownership-persistence.md)
- [Guarded live Herdr fake-agent smoke](reports/2026-08-21-live-herdr-fake-agent.md)

## Non-destructive fake CLI smoke

The fake executable writes only below a newly created temporary directory and never connects to the
real Herdr socket.

```bash
source .venv/bin/activate
PYTHONPATH=. python experiments/herdr-interactive-terminal/scripts/run_fake_herdr_smoke.py
```

## Guarded live Herdr pane smoke

Run this only from an agent or shell already inside Herdr. It creates one real pane, launches the
packaged wrapper with a fake Claude executable, writes a temporary verdict/snapshot, verifies exact
ownership, and closes that created pane. It does not start a real model or modify integrations.

```bash
source .venv/bin/activate
PYTHONPATH=. python experiments/herdr-interactive-terminal/scripts/run_live_herdr_fake_agent.py
```

## Planned assets

```text
scripts/   # non-destructive probes and report generators
fixtures/  # sanitized CLI/API responses
reports/   # dated findings
```

Product behavior is defined by the design, ADRs, tests, and user documentation. These experiments
are reproducibility evidence, not an implicit runtime contract.
