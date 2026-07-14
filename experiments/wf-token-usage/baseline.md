# Workflow token usage baseline

Issue #325 established this record for comparing changes to the `issue-implement`
workflow step. Measurements are meaningful only between rows whose complete series key
`(workflow, step_id, agent, model, effort)` matches.

## Fixed instruction input

Measured on 2026-07-14 at main commit `9f1b3c5` (`chore(release): v0.15.0`). The fixed
`issue-implement` input is the skill, its five prerequisite documents, the `type:chore`
fallback guide, and the shared unrelated-issue rule.

| Input | Characters | Bytes |
|---|---:|---:|
| `.claude/skills/issue-implement/SKILL.md` | 17,776 | 29,080 |
| `docs/dev/development_workflow.md` | 9,254 | 13,172 |
| `docs/dev/workflow_completion_criteria.md` | 7,689 | 13,635 |
| `docs/dev/documentation_update_criteria.md` | 1,320 | 2,570 |
| `docs/dev/testing-convention.md` | 6,707 | 12,411 |
| `docs/reference/python/python-style.md` | 4,487 | 7,091 |
| `.claude/skills/_shared/implement-by-type/feat.md` | 2,032 | 3,650 |
| `.claude/skills/_shared/report-unrelated-issues.md` | 630 | 1,430 |
| **Total** | **49,895** | **83,039** |

Reproduce the count from the repository root:

```bash
wc -c -m \
  .claude/skills/issue-implement/SKILL.md \
  docs/dev/development_workflow.md \
  docs/dev/workflow_completion_criteria.md \
  docs/dev/documentation_update_criteria.md \
  docs/dev/testing-convention.md \
  docs/reference/python/python-style.md \
  .claude/skills/_shared/implement-by-type/feat.md \
  .claude/skills/_shared/report-unrelated-issues.md
```

Documents loaded only when needed, including other files under `docs/reference/python/`,
are variable input and are intentionally excluded from this fixed count.

### Issue 326 progressive-disclosure comparison

Measured on 2026-07-15 from the `docs/326` worktree. For a like-for-like
`type:chore` startup comparison, the new fixed input is the compact skill, its
quick reference, the unchanged feature fallback guide, and the shared
unrelated-issue rule. Phase-specific references and the completion-report
template are excluded because the skill now loads them only at their named
steps rather than before reading the design.

| Input | Characters | Bytes |
|---|---:|---:|
| `.claude/skills/issue-implement/SKILL.md` | 6,522 | 10,762 |
| `docs/dev/implement-quickref.md` | 1,913 | 3,041 |
| `.claude/skills/_shared/implement-by-type/feat.md` | 2,032 | 3,650 |
| `.claude/skills/_shared/report-unrelated-issues.md` | 630 | 1,430 |
| **Total after Issue 326** | **11,097** | **18,883** |
| **Reduction from baseline** | **77.8%** | **77.3%** |

Reproduce the post-change count:

```bash
wc -c -m \
  .claude/skills/issue-implement/SKILL.md \
  docs/dev/implement-quickref.md \
  .claude/skills/_shared/implement-by-type/feat.md \
  .claude/skills/_shared/report-unrelated-issues.md
```

The Issue 326 workflow attempt runs the `doc-update` step, so it does not have
the same complete series key as the Issue 323 `implement` baseline. Calls,
cache-read tokens, wall time, and review quality are therefore not compared;
doing so would violate the series-isolation rule below.

## Recent run baseline

Measured on 2026-07-14 from Issue #323 run `260714213832`. The artifacts were resolved
through `KajiConfig.discover()` and `resolve_artifacts_dir()`; the Codex transcript was the
local rollout whose session ID matches the attempt's `result.json`.

```bash
uv run python experiments/wf-token-usage/measure_wf_usage.py 323 \
  --run 260714213832 --step implement --format json
```

Series key:

```text
(dev-thorough-fable, implement, codex, gpt-5.6-sol, high)
```

| Metric | Baseline |
|---|---:|
| Calls (`token_count` events inside the attempt interval) | 146 |
| Output tokens (attempt cumulative delta) | 48,130 |
| Cache-read tokens (attempt cumulative delta) | 18,990,848 |
| Max context (`last_token_usage.input_tokens`) | 195,582 |
| Wall time | 1,682,755 ms (1,682.755 s) |
| Attempt verdict | PASS |
| review-code executions / RETRY / BACK | 1 / 1 / 0 |
| final-check executions / RETRY / BACK | 1 / 0 / 0 |

The complete Codex session contains 148 token-count events. Two fall outside the
attempt's inclusive `[started_at, ended_at]` interval, so the attempt baseline is 146 calls.

## Usage and comparison rules

- Compare only identical complete series keys. Do not average different workflows, steps,
  agents, models, or effort levels.
- A record with `usage_status=missing` is excluded from token totals and medians. Never
  replace missing token data with zero; inspect `missing_reason` and the series' missing
  counts instead.
- Read RETRY and BACK counts alongside token changes. Lower token use with worse review or
  final-check outcomes is not an improvement.
- Claude streaming rows are deduplicated by `message.id`, with the final row used for each
  ID. Codex output and cache-read values are cumulative deltas across the attempt interval.
- A Codex attempt killed before its final token-count event may be undercounted because its
  last cumulative value was never written.
- Agent transcripts are machine-local. Runs copied from another machine remain visible as
  records but normally report `transcript_not_found`.

The tool reads local files only and calls no external API. File-system integration is
covered by Medium tests; a Large test would add machine-specific transcript dependence
without exercising a distinct external-service path, so no Large test is maintained.
