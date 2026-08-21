# Herdr metadata ownership persistence

Issue: #396

## Question

Does a source-scoped ownership token reported by a short-lived `herdr` CLI process remain available
to a later `pane get` call? Kaji depends on that behavior to re-read exact ownership immediately
before closing or pruning a pane.

## Environment

- Date: 2026-08-21
- Installed Herdr: 0.8.2
- Protocol baseline: 20
- Caller process: outside Herdr; no live pane was modified

## Evidence

The current official CLI reference states that `--ttl-ms` makes metadata expire automatically and
that omitting it retains metadata until it is replaced, cleared, or the pane closes. It also states
that TTL applies independently to token keys updated by that call:

- <https://herdr.dev/docs/cli-reference/#panes>
- <https://herdr.dev/docs/integrations/#custom-status-labels>

Local read-only checks matched that contract:

```text
$ herdr --version
herdr 0.8.2

$ herdr pane report-metadata --help
...
      --token <NAME=VALUE>
      --clear-token <NAME>
      --seq <N>
      --ttl-ms <N>
```

The installed protocol schema reports `ttl_ms` as nullable and optional, constrained to
`1..86400000` only when supplied:

```bash
herdr api schema --json \
  | jq '.schemas.request["$defs"].PaneReportMetadataParams.properties.ttl_ms'
```

```json
{
  "format": "uint64",
  "maximum": 86400000,
  "minimum": 1,
  "type": ["integer", "null"]
}
```

Kaji's marker builder deliberately emits no `--ttl-ms`:

```text
herdr pane report-metadata w1:p2 --source kaji \
  --token kaji_origin=w1:p1 \
  --token kaji_run=run-123 \
  --token kaji_step=design
```

The focused command-contract test asserts the complete argv and explicitly rejects the accidental
addition of `--ttl-ms`.

## Decision

- Keep ownership metadata persistent for the lifetime of the managed pane by omitting `--ttl-ms`.
- Continue to re-read `pane get` immediately before close/prune and require both `kaji_origin` and
  `kaji_run` to match.
- Do not infer ownership from pane position, focus, title, or a predicted pane ID.
- A cold Herdr restart can discard ephemeral server metadata. Missing tokens therefore fail closed:
  kaji leaves the pane untouched rather than attempting cleanup.

No implementation change to the marker argv was required; documentation and a regression assertion
were added to make the persistence choice explicit.
