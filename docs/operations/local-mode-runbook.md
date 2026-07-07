# Local Mode Operations Runbook (GitHub Outage / Emergency Fallback)

Language: English | [日本語](local-mode-runbook.ja.md)

Normal operation uses the GitHub provider (`.kaji/wf/dev.yaml`,
`dev-thorough.yaml`, and `docs.yaml`). This practical runbook explains how to
switch to kaji local mode as an emergency fallback when GitHub is down or
unreachable, covering multi-PC operation, code synchronization strategy, and the
criteria for returning to the forge.

## 1. Role of this document

- **Normal operation**: GitHub provider is the SoT. Issues, PRs, and reviews run
  on GitHub (`.kaji/wf/dev.yaml`, `dev-thorough.yaml`, and `docs.yaml`).
- **Scope of this runbook**: emergency fallback steps for temporarily moving to
  local mode when GitHub outages, connectivity failures, rate limits, or similar
  problems prevent GitHub operation.
- **Return**: once GitHub recovers, return to normal GitHub operation. The user
  decides how local issues / commits accumulated during fallback are reflected
  back to GitHub.

Reference: `draft/design/local-mode/design.md` sections Overview, Verification
strategy prerequisites, and Remaining work.

## 2. Setup

### 2.1 Single-PC setup

Generate the overlay from the repository root. **Do not rewrite the tracked
`.kaji/config.toml` (`type = "github"`)**. Use the gitignored overlay for the
switch so a personal provider choice is not committed for the whole repository:

```bash
kaji local init
```

- `.kaji/config.local.toml` (gitignored) is written with `[provider] type = "local"`,
  `machine_id`, and `default_branch`.
- `machine_id` must match `[a-z0-9]{1,16}` (no hyphen). Pass
  `--machine-id pc1`, etc. to set it explicitly.
- `kaji local init` adds `.kaji/config.local.toml` to `.gitignore` (check the
  `.kaji/counters/` line manually).
- If config is invalid, `kaji issue`, `kaji pr`, and `kaji run` stop with exit 2
  and print guidance for the needed fix.

See [Local Mode CLI Guide](../cli-guides/local-mode.md) section 2 for generated
overlay contents and `machine_id` resolution order.

### 2.2 Multi-PC setup

Always set a **different `machine_id`** on each PC. `.kaji/config.local.toml` is
gitignored, so per-PC settings do not flow through git.

| PC | Example machine_id | counter dir |
|----|--------------------|-------------|
| pc1 (main) | `pc1` | `.kaji/counters/pc1.txt` |
| pc2 (laptop) | `pc2` | `.kaji/counters/pc2.txt` |
| mac1 (mobile) | `mac1` | `.kaji/counters/mac1.txt` |

- Counter dirs are independent per PC (gitignored), so they do not collide on
  `git pull`.
- When cloning an existing repo on another PC, create `.kaji/config.local.toml`
  and set a new `machine_id`. Even if the counter is absent, `next_local_id()`
  automatically corrects from the maximum existing `.kaji/issues/local-<machine>-*`.

### 2.3 Post-setup smoke check

```bash
kaji config provider-type      # -> local
kaji issue list --state open   # Empty is fine; no error means OK
```

## 3. Daily operation

### 3.1 Issue lifecycle (`/issue-create` -> `/issue-close`)

During emergency fallback, choose the local workflow by **type label**:

| type | workflow YAML | Skill series |
|------|---------------|--------------|
| type:feature | `dev-local.yaml` | issue-design / issue-implement / issue-review-* / issue-close |
| type:docs | `docs-local.yaml` | i-doc-update / i-doc-review / i-doc-fix / i-doc-verify / i-doc-final-check / issue-close |
| GitHub workflows (`dev.yaml`, etc.) | - | **Do not use during GitHub outage / disconnection** because they communicate with the forge. Use them after returning to normal operation |

Invocation example:

```bash
# Manual prerequisites
/issue-create   # Create issue (Skill)
/issue-start    # Create worktree (Skill)

# Automatic continuous run (kaji run requires a file path; it does not search by basename)
kaji run .kaji/wf/dev-local.yaml local-pc1-1
# or
kaji run .kaji/wf/docs-local.yaml   local-pc1-2
```

### 3.1a Manual operation for docs-only issues (without `kaji run`)

Alternative procedure when running skills manually instead of `docs-local.yaml`:

1. `/i-doc-update [issue_id]`
2. `/i-doc-review [issue_id]`
3. On RETRY, repeat `/i-doc-fix [issue_id]` -> `/i-doc-verify [issue_id]` until it converges
4. `/i-doc-final-check [issue_id]`
5. `/issue-close [issue_id]`

> Do **not** use `/i-pr`. The local (bare) provider has no PR concept, so
> `kaji pr create` exits 2 through the bare-provider guard (see
> [Local Mode CLI Guide](../cli-guides/local-mode.md) section 8).

### 3.2 Multi-PC parallel operation

- Each PC allocates only inside its own `local-<machine>-<n>` number space, so
  the machine prefix structurally prevents collisions.
- One cycle: `git pull` -> work -> commit -> `git push`.
- Issue / counter / config tracked state:
  - tracked: `.kaji/issues/`, `.kaji/config.toml`
  - gitignored: `.kaji/config.local.toml`, `.kaji/counters/`

### 3.3 Conflict resolution

| Case | Action |
|------|--------|
| Multiple PCs edit the same issue | Resolve as a normal git merge conflict |
| Counter inconsistency (fresh clone / after cleanup) | `next_local_id()` automatically corrects from the maximum `.kaji/issues/local-<machine>-*`; no special action needed |
| Duplicate issue dir detected | `resolve_issue_dir` stops with a duplicate glob error. Manually remove the duplicate dir (usually from a merge accident) |

## 4. Code synchronization

Even during fallback, git itself is used normally. Synchronize code as usual
with `git push origin main` (if GitHub push is unavailable too, push after
recovery). Long outages that require an alternative remote such as a LAN bare
repo are outside this runbook and should be decided separately.

## 5. Return to GitHub operation

After GitHub recovers, return to normal operation. See the
[GitHub Mode CLI Guide](../cli-guides/github-mode.md) for GitHub provider setup
and authentication.

1. Delete `.kaji/config.local.toml`, or rewrite its `[provider] type` to
   `"github"`.
2. Confirm `kaji config provider-type` returns `github`.
3. Decide how to handle local issues accumulated during fallback
   (`local-<m>-<n>`): manually copy them to GitHub and close the local side, or
   finish them as local issues with `dev-local.yaml` / `docs-local.yaml`.
4. Commits and branches from the fallback period are retained in git remote, so
   history is not lost after returning.
5. If you need read-only references to GitHub Issues, refresh the cache with
   `kaji sync from-github` (`gh:N` references; see
   [Local Mode CLI Guide](../cli-guides/local-mode.md) section 10).

## 6. Troubleshooting

### 6.1 "provider.type cannot be resolved" errors

- `[provider] section is required in .kaji/config.toml.` - neither the tracked
  config nor the overlay has a `[provider]` section. Generate an overlay with
  `kaji local init` if switching to local fallback.
- `Error loading <path>: provider.type is required (string)` - the `[provider]`
  section exists but `type` is missing or invalid. If switching through an
  overlay, check the `[provider]` block in `.kaji/config.local.toml`.

There is no legacy passthrough that falls through to `gh` without config;
`type` must be explicit.

### 6.2 machine_id collision

Using the same `machine_id` on two PCs makes the `local-<machine>-<n>` number
space collide. Recovery:

1. On one PC, rename the duplicate dir with `git mv` (for example,
   `local-pc1-3-foo` -> `local-pc1-99-foo`).
2. Reset `.kaji/config.local.toml` to a new `machine_id` (existing dirs'
   `machine` portion must be renamed manually).
3. Manually resequence the counter file if needed.

### 6.3 counter / dir inconsistency

If `.kaji/counters/` is deleted by `make clean` or similar, the next
`kaji issue create` automatically corrects `next_local_id()` from the maximum
`.kaji/issues/local-<machine>-*`. No manual action is needed.

### 6.4 worktree removal failure

If `/issue-close` fails to remove the worktree, the issue state is already
closed (cleanup failure does not roll back issue close). Manual cleanup:

```bash
git worktree list   # Check remaining worktrees
git worktree remove <path>
git branch -d <branch>
```

## 7. References

- Design: `draft/design/local-mode/design.md` (especially Remaining work / History)
- Phase 5 design: `draft/design/local-mode/phase5-design.md`
- CLI Guide: `docs/cli-guides/local-mode.md`
- Workflow Guide: `docs/dev/workflow_guide.md`
- Workflow Authoring: `docs/dev/workflow-authoring.md`
- Skill Authoring: `docs/dev/skill-authoring.md`
