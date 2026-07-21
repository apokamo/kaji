---
name: series-create
description: Create a validated sequential Issue series plan from an explicitly ordered GitHub Issue list. Use when a maintainer wants to generate or update an ID-named YAML file under .kaji/series, select standard workflows from Issue type metadata and workflow descriptions, or preview a series without starting it.
---

# Series Create

Generate a deterministic series YAML, validate it, show its dry-run plan, and stop before execution.

## Input

Accept this manual form:

```text
/series-create <issue>... --id <series-id> [--parent <issue>]
  [--workflow <issue>=<repo-relative-path>]... [--update]
```

Preserve Issue order exactly. Treat `--workflow` as a member-specific override. Require `--id` and at
least one positive integer Issue number.

## Workflow

1. Confirm `kaji config provider-type` returns `github`; otherwise stop.
2. Read each Issue without mutation:

   ```bash
   kaji issue view <issue> --json labels,title,state
   ```

3. Read every `.kaji/wf/official/**/*.yaml` and `.kaji/wf/custom/**/*.yaml` `description` and `requires_provider` value. For each member without
   an override, select candidates whose description says they are the standard series auto-selection
   target for the Issue's single `type:` label and whose provider is `github` or `any`.
4. Auto-select only when exactly one candidate remains. Display the Issue type and matching
   description sentence as the reason. If the Issue has zero or multiple `type:` labels, or the
   candidate count is not one, stop and request `--workflow <issue>=<path>` for that member.
5. Invoke the deterministic generator. Do not write YAML manually:

   ```bash
   python -m kaji_harness.scripts.series_generate \
     --id <series-id> [--parent <parent>] \
     --member <issue>=<workflow>... \
     --output .kaji/series/<series-id>.yaml [--update]
   ```

6. If the target exists without `--update`, stop. Never retry with `--update` implicitly.
7. Run both checks and stop if either fails:

   ```bash
   kaji validate-series .kaji/series/<series-id>.yaml
   kaji run-series .kaji/series/<series-id>.yaml --dry-run
   ```

8. Report the generated path, ordered members, workflow selection reasons, validation result, and
   dry-run plan. Do not start `kaji run-series` without `--dry-run`.

## Output

- One `.kaji/series/<id>.yaml` containing only `id`, optional `parent_issue`, `strategy`, ordered
  `members` with resolved workflow paths, and `on_failure`.
- A console summary of selection reasons and the validated dry-run plan.
- No Issue, PR, label, sub-issue, state, lock, or member-run mutation.

## Stop Conditions

- Provider is not GitHub.
- Input is malformed or Issue type metadata is missing/ambiguous.
- Workflow selection is not unique and no override was supplied.
- An override path fails `validate-series`.
- The output exists without explicit `--update`.
- Generation, validation, or dry-run returns nonzero.

## Non-goals

- Discovering members from an EPIC, parent body, or sub-issue relation.
- Reordering members or inferring dependencies.
- Editing Issue metadata or external state.
- Duplicating schema validation or YAML serialization in the agent.
- Starting the actual series execution.
