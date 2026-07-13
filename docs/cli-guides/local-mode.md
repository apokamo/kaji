# Local Mode CLI Guide

Language: English | [日本語](local-mode.ja.md)

A minimal guide for operating `kaji` without GitHub. Create an overlay with
`kaji local init`, then run local-only workflows (`dev-local.yaml` /
`docs-local.yaml`).

## When to use it

- GitHub is unavailable, or you do not want to create issues / PRs for personal development.
- Longer local operation over weeks or months is also expected.

## 1. Install

```bash
uv sync
source .venv/bin/activate
```

## 2. Initialize (`kaji local init`)

### Prerequisite: tracked `.kaji/config.toml`

`kaji local init` **only creates the overlay (`.kaji/config.local.toml`)**.
In repositories without a tracked `.kaji/config.toml`, `kaji issue`, `kaji pr`,
and `kaji run` all stop with `.kaji/config.toml not found`, so you must commit
a minimal base config once before generating the overlay.

The `[provider]` section is also required. If neither the tracked config nor the
overlay defines it, kaji stops with exit 2. There is no legacy passthrough that
falls through to `gh` when config is missing. Syntax violations such as an
invalid `machine_id` also fail fast as `ConfigLoadError` during config loading.

Minimal template:

```toml
# .kaji/config.toml (tracked)
[paths]
artifacts_dir = ".kaji-artifacts"
skill_dir = ".claude/skills"
# worktree_prefix = "kaji"          # Optional. See the configuration reference for defaults and effective behavior.

[execution]
default_timeout = 1800
# agent_runner = "headless"          # Optional. "headless" (default) | "interactive_terminal"
# interactive_terminal_close_on_verdict = true   # Optional

[provider]
type = "local"
```

`agent_runner = "interactive_terminal"` is a runner backend that launches normal
`claude` / `codex` sessions inside tmux panes. See the
[Interactive Terminal Runner guide](./interactive-terminal-runner.md) for setup,
CLI options, and manual verification.

The exhaustive type / default / validation specification for each key
(`[paths]`, `[execution]`, `[provider.local]`, and so on) is defined by the
[Configuration Reference](../reference/configuration.md). This guide is scoped
to local-mode operational how-to. For `type = "github"` setup, see the
[GitHub Mode CLI Guide](github-mode.md).

### Generate the overlay

From the repository root:

```bash
kaji local init
```

Behavior:

- Creates `.kaji/config.local.toml` (aborts with exit 3 if it already exists).
- Adds `.kaji/config.local.toml` to `.gitignore` (no-op if already present).
- **Does not touch** the tracked `.kaji/config.toml` (personal choices are not committed).

`machine_id` is resolved in this order:

1. Explicit `--machine-id <name>` (violations of `[a-z0-9]{1,16}` exit 2).
2. Sanitized `socket.gethostname()` (lowercase + alphanumeric + truncated to 16 characters).
3. Fallback to `pc1`, `pc2`, ... (the smallest value that does not collide with existing `.kaji/issues/local-*`).

Passing `--default-branch <branch>` writes the value to
`provider.local.default_branch` in the overlay (default: `main`).

### Generated overlay example

```toml
# .kaji/config.local.toml (gitignored)
[provider]
type = "local"

[provider.local]
machine_id = "pc1"
default_branch = "main"
```

`kaji local init` writes only the three values above. Add `git_remote` manually
if needed (the remote used by `git push` / `git fetch` inside skills, default
`"origin"`; see section 6).

## 3. Provider switching

`.kaji/config.toml` (committed) keeps the repository default, and
`.kaji/config.local.toml` (gitignored) overrides personal choices as an overlay.

| State | Effect |
|-------|--------|
| No overlay | The `[provider]` from `.kaji/config.toml` is used as-is |
| Overlay exists | The overlay's `[provider]` section is merged |
| Overlay has `type = "local"` | LocalProvider path |
| Overlay removed | Returns to the tracked default |

> **Warning: overlays are per worktree instance**. Because the overlay is
> gitignored, new worktrees do **not** inherit it and fall back to the tracked
> `config.toml` `[provider]` (kaji emits a WARN to stderr when it detects the
> mismatch). Copy `config.local.toml` from the main repository or rerun
> `kaji local init` inside that worktree. See the Git Worktree guide's provider
> overlay section for details.

## 4. Issues / workflows

```bash
# Create an issue (--body or --body-file is required. The slug is derived from
# the title automatically; pass --slug to choose it explicitly.)
kaji issue create   --title "do something"   --body "describe the work"   --label type:feature

# Read the body from a file
kaji issue create --title "do something" --body-file issue-body.md --label type:feature

# List issues
kaji issue list

# Resolve context (thin wrapper over provider.resolve_issue_context(), for skills / automation scripts)
kaji issue context local-pc1-1 --json branch_prefix,branch_name,worktree_dir
# -> {"branch_prefix":"feat","branch_name":"feat/local-pc1-1","worktree_dir":"/abs/.../kaji-feat-local-pc1-1"}

# Deterministically prepend Worktree metadata (> [!NOTE] block) to the body (for /issue-start)
kaji issue prepend-note local-pc1-1 --worktree kaji-feat-local-pc1-1 --branch feat/local-pc1-1 --commit

# Start a workflow (local-only. Use docs-local.yaml for docs-only issues.)
kaji run .kaji/wf/dev-local.yaml local-pc1-1
```

`kaji issue prepend-note <id> --worktree <basename> --branch <branch> [--commit]`
is a provider-common subcommand that composes a `> [!NOTE]` metadata block at
the start of the issue body. The composition (NOTE block + **exactly one blank
line** + existing body) is handled by deterministic Python code inside `kaji`,
so it does not depend on an agent's multiline fidelity and guarantees the blank
line (Issue 200). `--commit` atomically commits `issue.md` for the local
provider. GitHub has no `gh issue prepend-note`, so the GitHub path updates via
`view_issue` / `edit_issue`; `--commit` is silently ignored there. `/issue-start`
Step 4 calls this command.

`kaji issue context` resolves context in this priority order: frontmatter
`branch_prefix` -> `type:*` label mapping -> `chore` fallback. It works for both
`provider.type='local'` and `'github'`. `/issue-start` uses it to derive the
worktree and branch names.

`dev-local.yaml` is the local-provider version of `dev.yaml`. It removes the
GitHub-only PR-related steps (`i-pr`, `review-poll`, PR review, `pr-fix`, and
`pr-verify`) and also removes the pre-start steps (`review-ready`, `fix-ready`,
and `start`). It starts at `design`, assuming `/issue-create` and `/issue-start`
were run manually in advance, including worktree creation. After `final-check`,
it ends at `issue-close`; no PR is created, and `/issue-close` runs
`git merge --no-ff` plus frontmatter close. Use the analogous `docs-local.yaml`
for docs-only issues.

## 5. ID grammar

| Form | Meaning |
|------|---------|
| `local-pc1-3` | Third issue for machine_id `pc1` (full form) |
| `pc1-3` | Short form. Accepted only when provider=local |
| `3` | When provider=local, completed to `local-<self>-3` |
| `gh:153` | Read-only reference from the GitHub cache. Populate with `kaji sync from-github` first (see section 10) |

## 6. `/issue-close` behavior (local)

The six steps follow `draft/design/local-mode/design.md` section
"local mode `/issue-close` procedure":

1. Preflight check (uncommitted changes / branch / base).
2. Update the base branch (`git fetch` + `merge --ff-only`).
3. Run the merge (`git merge --no-ff --no-edit`).
4. Update issue frontmatter + commit (`kaji issue close [issue_id] --reason completed`).
5. Cleanup (`git worktree remove` -> `git branch -d`).
6. Push (if a remote exists, `git push [git_remote] [default_branch]`).

The issue is definitively closed after Step 4. Failures in Step 5 or 6 only warn.
The default `--reason` is `completed`, matching GitHub Issue API convention.

### Example overriding `git_remote`

The remote name used by `git push` / `git fetch` inside skills can be overridden
with `provider.local.git_remote` (default: `"origin"`). For example, if `origin`
points to GitHub but you want the kaji workflow to push through an external
mirror named `backup`:

```toml
# .kaji/config.local.toml (gitignored)
[provider.local]
machine_id = "pc1"
git_remote = "backup"
```

This assumes `git remote get-url backup` resolves (register it beforehand with
`git remote add backup ...`). The `[git_remote]` placeholder in skill prompts
resolves to this value, and `/issue-close` Steps 2 and 6 use that remote.
See the [Configuration Reference](../reference/configuration.md#providerlocal)
for the default and type specification of `provider.local.git_remote`.

### `kaji issue {edit,comment} --commit` flag (local)

Under LocalProvider, passing `--commit` to `kaji issue edit` or
`kaji issue comment` performs the issue-file update and `git stage + commit`
**atomically in the same process**. The provider persistence boundary is
`LocalProvider.commit_issue_change()` in `kaji_harness/providers/local.py`
(active only when `provider.type='local'`).

Key behavior:

- The commit target is limited to the path modified by the CLI (`issue.md` or a
  new comment markdown file). It stages only that path with `git add -- <path>`,
  then uses `git commit --only -- <path>` to build a temporary index containing
  only that path for `HEAD`. Pre-existing staged files are not included in this
  commit and remain protected in the user's index (per `man git-commit` section
  `--only`).
- Commit messages are `chore(local): edit for <issue_ref>` or
  `chore(local): comment for <issue_ref>`.
- For a no-op edit through `kaji issue edit --commit`, the command checks staged
  diff with `git diff --cached --quiet` and skips the commit when empty, avoiding
  exit 1 from `nothing to commit`.
- Skills attach `--commit` to every invocation, establishing the clean base
  worktree prerequisite for `/issue-close` section 6 Step 2.
- Under `provider.type='github'`, `--commit` is **silently stripped**. The flag
  is recognized but does nothing, preserving passthrough-path idempotency.

### Verdict markers on `kaji issue comment` (local)

When `kaji issue comment` receives `--verdict-step <step> --verdict-status <STATUS>`,
the CLI deterministically prepends an HTML marker on **line 1** of the comment
body before persisting it to
`.kaji/issues/<id>/comments/<timestamp>-<machine>.md`:
`<!-- kaji-verdict: step=<step> status=<STATUS> -->`. This mechanism keeps the
cross-skill contract (BACK re-entry detection in `issue-design`) in the CLI
layer (ADR 008 decision 3).

- **Both flags are required together**: one without the other exits 2. Calls
  without both flags leave the body unchanged.
- **Vocabulary validation (fail-loud)**: `--verdict-step` must match
  `^[a-z][a-z0-9_-]*$`, and `--verdict-status` must be `PASS`, `RETRY`, `ABORT`,
  `BACK`, or `BACK_<UPPER>` (`BACK_[A-Z0-9_]+`). Invalid values exit 2.
- Can be combined with `--commit` (atomic commit behavior is unchanged; the
  marker is line 1 of the committed file).
- GitHub and local providers behave identically (marker format, placement, and
  vocabulary validation).
- Implementation: `kaji_harness/providers/markers.py`
  `build_kaji_verdict_marker` (the canonical contract).

### Main worktree redirection

Under `provider.type='local'`, file writes and `--commit` behavior for
`kaji issue {create,edit,comment,close}` are pinned to the worktree that has
`provider.local.default_branch` checked out, i.e. the main worktree,
**regardless of cwd**. Even if you run
`kaji issue comment local-pc1-3 --commit` from a feature worktree (`fix/N`,
for example), the comment file and commit land in the main worktree /
`default_branch`.

Implementation: `kaji_harness/providers/_worktree.py` `resolve_main_worktree()`
parses `git worktree list --porcelain` and selects the worktree whose branch is
`refs/heads/<default_branch>` as `LocalProvider.repo_root` once inside
`kaji_harness/providers/__init__.py` `get_provider()`.

Troubleshooting:

| Symptom | Cause / action |
|---------|----------------|
| `LocalProviderError: no worktree found for branch 'main'` | No worktree has `default_branch` checked out. Run `git worktree add ../main main`, or align `provider.local.default_branch` with an existing branch |
| `LocalProviderError: git CLI not found on PATH ...` | `git` is not on PATH. Install `git` and add it to PATH, or switch away from `provider.type = "local"` |
| `LocalProviderError: 'git -C ... worktree list' failed (exit ...)` | The directory configured with `provider.type='local'` is not a git repository. Run `git init` in the target directory (or start from an initialized worktree), or switch providers |
| `warning: multiple worktrees checking out 'main'` (stderr) | Warning for defensive input. The first matching worktree is used, but normal git operations should not create this state |

For the GitHub provider, `repo_root` still starts from cwd (the `gh` CLI is not
cwd-dependent, so there is no impact).

## 7. Files / layout

```
.kaji/
├── config.toml          (tracked, repo default)
├── config.local.toml    (gitignored, overlay)
├── counters/<machine>.txt   (gitignored)
├── issues/local-<machine>-<n>-<slug>/
│   ├── issue.md         (frontmatter + body)
│   └── comments/<timestamp>-<machine>.md   (compact ISO 8601, e.g. 20260521T123456Z-pc1.md)
└── cache/gh-<n>.json       (read-only GitHub Issue cache, populated by `kaji sync from-github`)
```

## 8. `kaji pr` behavior

Under `provider.type='local'`, `kaji pr ...` exits 2 with a **bare-provider
error**. Local mode has no PR concept, so every subcommand behaves the same way:
`kaji pr create`, `list`, `review-comments`, and so on. This guard prevents
accidental PR creation.

Alternatives:

| Old path (GitHub mode) | Local-mode alternative |
|------------------------|------------------------|
| Code review (`/pr-fix`, `/pr-verify`) | `/issue-review-code` / `/issue-fix-code` / `/issue-verify-code` (cycle on the issue without a PR concept) |
| Merge & close (`/i-pr` -> review -> `/issue-close`) | Go directly to `/issue-close` (`git merge --no-ff <feat-branch>` + `kaji issue close --reason completed`) |
| PR list | `git branch --list 'feat/local-*'` |

The `pr-fix`, `pr-verify`, and `i-pr` skills also check `provider_type` in Step
0 and ABORT under local (bare) provider, even when invoked manually
(`/pr-fix <issue_id>`). Manual invocation resolves it as:

```bash
PROVIDER_TYPE="${provider_type:-$(kaji config provider-type 2>/dev/null || true)}"
```

To return to GitHub mode, set `[provider] type = "github"` in
`.kaji/config.local.toml` or delete the overlay so the tracked
`.kaji/config.toml` becomes active.

## 9. Known limitations

- Native Windows is not supported at this time. Use WSL on Windows.

## 10. `kaji sync from-github` (populate GitHub cache)

When referencing GitHub Issues with `gh:N` from `provider.type='local'`, first
populate the cache with `kaji sync from-github`. The cache is
`.kaji/cache/gh-<n>.json`.

> If you operate with `provider.type='github'` and GitHub is kaji's primary
> forge, see the [GitHub Mode CLI Guide](github-mode.md) for setup and
> authentication. This section covers only the cache-population path used to
> reference GitHub Issues read-only from `local` mode.

```bash
# Initial sync (when [provider.github].repo is written in config)
$ kaji sync from-github

# Specify repo by CLI argument
$ kaji sync from-github --repo apokamo/kaji

# Read from cache
$ kaji issue view gh:42

# Check sync status (output excerpt; actual output also includes last_sync / elapsed)
$ kaji sync status
forge        github
repo         apokamo/kaji
cached       47 (gh-*.json under .kaji/cache/)
```

**Scope and prerequisites**:

- Sync covers **all open issues in the GitHub repo**. GitHub REST `/issues`
  endpoint also returns PRs, so entries with a `pull_request` key are excluded.
- Additional flags such as `--include-closed`, `--state`, and `--since` are not
  implemented in this release. Passing them fails fast with `exit 2` (they are
  not silently ignored).
- Issues that exist in the local cache but are not included in the GitHub fetch
  result remain in the cache with `kaji_local.is_stale=true`.

## 11. Emergency fallback operation

This repository's normal mode is the GitHub provider. Local mode is a temporary
fallback for GitHub outages or loss of connectivity. See the
[Local Mode Emergency Fallback Runbook](../operations/local-mode-runbook.md) for
switching to fallback, multi-PC operation, and deciding when to return to GitHub.
