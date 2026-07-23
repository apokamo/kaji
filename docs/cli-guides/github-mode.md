# GitHub Mode CLI Guide

Language: English | [日本語](github-mode.ja.md)

Setup / operation / prerequisite guide for running `kaji` with
`provider.type = "github"`. It collects GitHub-mode-specific prerequisites,
configuration, naming rules, and troubleshooting in one file.

## When to use it

- You operate a repository on GitHub (`github.com/<owner>/<name>`) as kaji's primary forge.
- You are returning from emergency local-mode fallback ([Local Mode CLI Guide](local-mode.md)) to normal GitHub operation.
- You want to import GitHub Issues into the local cache with `kaji sync from-github`.

> **Auto-close keyword warning**: GitHub interprets `Closes #<N>`, `Fixes #<N>`,
> `Resolves #<N>`, and similar phrases as auto-close keywords, via **two distinct
> paths**: a keyword in a **PR description** links the PR to the issue and closes it
> on merge; a keyword in a **commit message** closes the issue once that commit
> reaches the default branch (official docs:
> [Closing issues using keywords](https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue#linking-a-pull-request-to-an-issue-using-a-keyword)).
>
> This repository (apokamo/kaji) disables the repository setting
> **Auto-close issues with merged linked pull requests**
> (Settings → General → Features → Issues). That setting suppresses **only the
> linked-PR path**; it is not documented to cover the commit-message path. On that
> premise, `/i-pr` emits exactly one live `Closes <issue_ref>` line in the PR
> description (the only automatable way to link a PR to an issue), while closing
> keywords remain forbidden in commit bodies (including merge commit messages) and
> elsewhere in the PR description. Issues are closed explicitly via `/issue-close`.
>
> If you run kaji against your own repository **without** disabling that setting,
> keep the avoidance convention for PR descriptions as well. See
> [docs/dev/shared_skill_rules.md section auto close keyword avoidance](../dev/shared_skill_rules.md)
> for the full convention and grep procedure.

## 1. Prerequisites

### 1.1 Required tools

| Tool | Role | Notes |
|------|------|-------|
| `gh` | GitHub CLI (launched behind `kaji pr`, `kaji issue`, and `kaji sync from-github`) | Must be on PATH |
| `git` | Normal operation | SSH push to `git@github.com` is assumed |

When `gh` is not installed, `kaji sync from-github` and `kaji issue` /
`kaji pr` under `provider.type='github'` exit with an error starting with
`'gh' CLI not found in PATH. ...` (the following guidance differs by entry
point; for example, the passthrough path says
`Install GitHub CLI to use 'kaji issue' / 'kaji pr'.`).

### 1.2 Authentication

Authenticate interactively with `gh auth login`:

```bash
gh auth login
gh auth status            # -> "Logged in to github.com as <user>"
```

For CI / unattended scripts, pass a PAT through the `GH_TOKEN` environment
variable. The PAT must have the **`repo`** scope, including issue / PR read and
write access.

### 1.3 `.kaji/config.toml`

Minimal `provider.type = "github"` configuration:

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
type = "github"

[provider.github]
repo = "<owner>/<name>"             # Example: "apokamo/kaji"
default_branch = "main"             # default "main"
git_remote = "origin"               # Optional. default "origin"
```

`agent_runner = "interactive_terminal"` is a runner backend that launches normal
`claude` / `codex` sessions inside tmux panes. See the
[Interactive Terminal Runner guide](./interactive-terminal-runner.md) for setup,
CLI options, and manual verification.

The exhaustive specification for each key's type / default / validation is the
[Configuration Reference](../reference/configuration.md). GitHub mode essentials:

- `[provider.github].repo` must be in **`owner/name`** form. Do not add an
  `https://` prefix or `.git` suffix. The value is passed to
  `gh --repo <owner>/<name>` / `gh api repos/<owner>/<name>/...`.
- For `worktree_prefix`, `agent_runner`, and `git_remote` defaults and effective
  behavior, see the section / key specification in the
  [Configuration Reference](../reference/configuration.md).

### 1.4 Coupling with `.github/labels.yml`

kaji treats `.github/labels.yml` at the GitHub project root as the source of
truth for labels (see [GitHub label operation](../dev/labels.md) for add/remove
procedures). `kaji_harness/providers/_mappings.py` maps `type:*` labels to
branch prefixes. When `.github/labels.yml` changes, the GitHub Actions
`labels-sync` workflow (`.github/workflows/labels-sync.yml`) synchronizes labels
on GitHub.

## 2. `kaji issue` / `kaji pr` behavior

Under `provider.type = "github"`, `kaji issue` / `kaji pr` operate with the skill
compatibility contract.

- `kaji pr create`, `view`, `list`, `comment`, `review`, `merge`,
  `review-comments`, `reviews`, `reply-to-comment`, and `review-poll` work with
  the same invocation style on GitHub.
- `kaji pr merge` silently strips `--squash` / `--rebase` on the kaji side and
  always invokes `gh pr merge --merge`, enforcing the `--no-ff`-only merge rule.
  `--admin` (administrator bypass) and `--match-head-commit <SHA>` (HEAD pinning)
  are **not** method flags, so they are preserved and forwarded to `gh pr merge`
  alongside the forced `--merge`.
- `kaji pr admin-merge-check <branch>` is a **read-only** self-PR admin-merge
  eligibility gate used by `/issue-close` when a normal merge is blocked by a base
  branch policy (e.g. unresolved review threads under
  `required_review_thread_resolution=true`). It never writes to GitHub. It ALLOWs
  (exit 0, printing the resolved 40-hex HEAD SHA on stdout) only when **all** of
  the following hold, and otherwise DENYs (non-zero exit, empty stdout, `DENY:
  <reason>` on stderr):
  1. **self-PR** — PR author equals the authenticated user.
  2. **fresh APPROVED marker** — the latest `kaji-review` decision marker
     (`<!-- kaji-review: state=APPROVED -->`), read from **all** issue comments via
     `gh api --paginate --slurp`, was posted after the current HEAD commit time
     (`committedDate`). A newer `CHANGES_REQUESTED`, a stale APPROVED, or no marker
     all DENY.
  3. **policy-block eligibility** — `mergeStateStatus == BLOCKED` **and**
     `mergeable == MERGEABLE` **and** no failing/pending `statusCheckRollup` entry.
     This excludes transient/auth/conflict/non-policy failures from elevated merge.
  4. **admin bypass** — `permissions.admin == true` for the authenticated user.

  Any `gh` error, JSON parse failure, ambiguous PR resolution (0 or >1 open PRs for
  the head), or invalid `headRefOid` fails closed to DENY. `/issue-close` branches
  on the exit code and passes the printed SHA to
  `kaji pr merge <branch> --admin --match-head-commit "$HEAD_SHA"`, which GitHub
  rejects if HEAD moved since the check. It never falls back to `--auto` and never
  resolves review threads.
- `kaji pr review <pr> --approve` / `--request-changes` detects self-PRs
  (PR author == authenticated user). For self-PRs, it posts an issue comment
  marker (`<!-- kaji-review: state=APPROVED -->` /
  `<!-- kaji-review: state=CHANGES_REQUESTED -->`) through the Issue Comments
  API and returns rc=0, because GitHub rejects author APPROVE / REQUEST_CHANGES
  events with 422 (`Can not approve your own pull request` /
  `Can not request changes on your own pull request`). For non-self PRs, it
  delegates to normal `gh pr review --approve` / `--request-changes`. `--comment`
  or no flag does not route to `_github_pr_review`; it continues to passthrough
  to `gh pr review`.
  - **Body-required contract for `--request-changes` (same for self and non-self)**:
    GitHub REST API `event=REQUEST_CHANGES` requires a body parameter, so kaji
    fails fast with `EXIT_INVALID_INPUT` (rc=2) before subprocess invocation when
    `--body` / `--body-file` is missing or blank-only. `--approve` keeps the
    existing behavior and allows an empty body because GitHub treats body as optional.
  - **Observation path asymmetry for marker comments**: marker comments posted by
    the self-PR fallback are written through the Issue Comments API
    (`/repos/<repo>/issues/<N>/comments`). They are visible through
    `kaji pr view <pr> --comments`, but not through `kaji pr reviews <pr>`
    (`/pulls/<N>/reviews`). The downstream `pr-fix` skill primarily reads via
    `kaji pr view --comments`, so this is fine on the observation path.
- `kaji issue comment <id> --verdict-step <step> --verdict-status <STATUS>` adds
  a verdict marker to judgment comments (see section 2.1).

### 2.2 Sequential Issue series

Use a tracked `.kaji/series/<id>.yaml` when several Issues must run in an explicit order. Validate
and preview before starting:

```bash
kaji validate-series .kaji/series/<id>.yaml
kaji run-series .kaji/series/<id>.yaml --dry-run
kaji run-series .kaji/series/<id>.yaml
```

Each member runs through the existing `kaji run` command. The next member starts only when the child
exits zero and `gh issue view` reports `closed` with state reason `completed`. Resume an interrupted
or stopped series with `--resume`; a changed definition fingerprint or a live orphan child is
rejected. Runtime state and the advisory lock live under `<artifacts_dir>/_series/<id>/`.

`validate-series` and `--dry-run` fully preflight every member workflow in the current plan: YAML
schema, workflow references, and skill metadata. A normal run repeats the same preflight at startup
instead of trusting an earlier dry-run. If any member is invalid, no member process, series state, or
lock is created. Dry-run reads and validates workflows but does not access provider APIs or mutate
Issues, artifacts, state, locks, or member executions.

`/series-create <issues...> --id <id>` generates the YAML, runs validation and dry-run, and stops.
Use `--workflow <issue>=<path>` for a non-standard workflow variant. The skill reads Issue metadata
but does not edit Issues or start the series.

### 2.1 Verdict markers on `kaji issue comment`

When `kaji issue comment` receives `--verdict-step <step> --verdict-status <STATUS>`,
the CLI deterministically prepends an HTML comment marker on **line 1** of the
comment body before posting it:
`<!-- kaji-verdict: step=<step> status=<STATUS> -->` (invisible in GitHub UI).
This keeps the cross-skill contract (BACK re-entry detection in `issue-design`)
in the CLI layer rather than prose in SKILL.md (ADR 008 decision 3).

- **Both flags are required together**: one without the other exits 2 (stderr
  error). Calls without both flags leave the body unchanged and use normal `gh`
  passthrough.
- **Vocabulary validation (fail-loud)**: `--verdict-step` must match
  `^[a-z][a-z0-9_-]*$`, and `--verdict-status` must be `PASS`, `RETRY`, `ABORT`,
  `BACK`, or `BACK_<UPPER>` (`BACK_[A-Z0-9_]+`). Invalid values exit 2 without
  launching `gh`.
- GitHub and local providers behave identically. `--commit` is silently ignored
  on GitHub.
- Example: `kaji issue comment 261 --verdict-step review-code --verdict-status BACK --body-file - <<'EOF' ... EOF`

## 3. Using `kaji sync from-github`

This path populates a cache so GitHub Issues can be referenced read-only as
`gh:N` from `provider.type = "local"`. It is unnecessary under
`provider.type = "github"`, which calls the API directly.

```bash
# Initial sync (when [provider.github].repo is written in config)
kaji sync from-github

# Specify repo by CLI argument
kaji sync from-github --repo <owner>/<name>

# Read from cache
kaji issue view gh:42

# Check sync status
kaji sync status
```

Cache layout is `.kaji/cache/gh-<n>.json`. The schema wraps the raw issue:

```json
{
  "schema_version": 1,
  "forge": "github",
  "fetched_at": "2026-05-21T12:34:56Z",
  "kaji_local": {
    "is_stale": false,
    "last_seen_at": "2026-05-21T12:34:56Z",
    "staled_at": null
  },
  "issue": {
    "number": 42,
    "title": "...",
    "body": "...",
    "state": "open",
    "labels": [{"name": "type:feature"}]
  }
}
```

The `issue` field is the **raw JSON from GitHub REST API
`GET /repos/{owner}/{repo}/issues`** (snake_case). GitHub REST also returns PRs
from the `/issues` endpoint, so entries with a `pull_request` key are excluded
during sync.

### 3.1 Local manual connectivity check

GitHub-side E2E targets are not added in this release. Manually verify real
GitHub API connectivity with this procedure:

```bash
# auth check
gh auth status

# List issues
gh api -X GET repos/<owner>/<name>/issues -F state=open -F per_page=100 -F page=1 | jq '.[].number'

# Through kaji
kaji sync from-github --repo <owner>/<name>
kaji issue view gh:<N>
kaji sync status            # forge=github / repo=<owner>/<name> / cached=<N>
```

## 4. Troubleshooting

### 4.1 `'gh' CLI not found in PATH`

You ran `kaji issue`, `kaji pr`, or `kaji sync from-github` under
`provider.type='github'`, but `gh` is not installed. Install it with your OS
package manager.

### 4.2 `gh auth status` says `not logged in`

Run `gh auth login`, or export `GH_TOKEN`. The env path is recommended in CI.

### 4.3 `'kaji sync from-github' requires a GitHub repo`

Add `repo = "owner/name"` to `.kaji/config.toml` `[provider.github]`, or pass
`--repo owner/name` as a CLI argument.

### 4.4 `multiple open pull requests found for head branch ...`

`GitHubProvider.resolve_pr_context` assumes one open PR per branch. If
`gh pr list --head <branch> --state open` returns multiple PRs, close the
unneeded PRs or operate on the target one explicitly with `kaji pr`.

### 4.5 Commit / PR description `Fix #<N>` auto-closes an unrelated GitHub issue

GitHub closing keywords (`Closes`, `Fix(es|ed)`, `Resolves`, etc. + `#<N>`) close the
referenced issue automatically: from a **PR description** on merge (suppressed when
the repository setting **Auto-close issues with merged linked pull requests** is
disabled), or from a **commit message** once the commit reaches the default branch
(a separate path that the setting is not documented to cover)
([official docs](https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue#linking-a-pull-request-to-an-issue-using-a-keyword)).
apokamo/kaji disables that setting, and the only live closing keyword kaji emits is
the single `Closes <issue_ref>` line generated by `/i-pr`. Any other match — in a
commit body, or elsewhere in the PR description — is a hazard: rewrite it as a
placeholder. See the grep procedure and placeholder convention in
[docs/dev/shared_skill_rules.md section auto close keyword avoidance](../dev/shared_skill_rules.md).

If an issue was closed unintentionally, reopen it with
`gh issue reopen <N> --repo <owner>/<repo>` and check whether the repository
setting is still disabled.

## 5. References

- Local mode: [docs/cli-guides/local-mode.md](local-mode.md)
- Design: `draft/design/issue-34-github-pr-context-auto-injection-kaji-sy.md`
