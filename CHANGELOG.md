# Changelog

All notable changes to kaji are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.0] - 2026-05-17

This release makes a **multi-provider architecture** the backbone of
kaji. Issue / PR operations now route through a `Provider` abstraction
with GitHub, GitLab, and local-filesystem backends, and a `[provider]`
section is now mandatory in `.kaji/config.toml`. It also adds a GitLab
provider (`provider.type='gitlab'`) and a `review-cycle` workflow.

### BREAKING CHANGE

- `[provider]` section is now **required** in `.kaji/config.toml`.
  `kaji issue` / `kaji pr` / `kaji run` exit 2 with a setup message if it
  is missing. Previously, missing `[provider]` triggered a one-time WARN
  and fell back to GitHub passthrough.
- `.kaji/config.toml` itself is now required to invoke `kaji issue` /
  `kaji pr`. The legacy passthrough that forwarded these commands to
  `gh` outside of a kaji repository has been removed.
- `provider.local.machine_id` is now validated at config load time
  (must match `[a-z0-9]{1,16}`). Hand-edited `config.local.toml` with
  invalid values (`PC1`, `pc-1`, 17+ characters, etc.) now fails fast
  with `ConfigLoadError` instead of crashing later in `kaji issue` /
  `kaji run`.
- `kaji pr ...` (including `pr create` / `pr list` / `pr review-comments`
  / `pr reviews` / `pr reply-to-comment`) now exits 2 with a `forge-only`
  error message when run under `provider.type='local'`. Previously, the
  call was passed through to `gh pr` even in local mode, risking
  accidental PR creation against the GitHub remote.
- `kaji run` now validates that `workflow.requires_provider` matches
  `config.provider.type` before dispatching the runner. Mismatches exit 2
  with a switching guide (e.g. running `feature-development.yaml` under
  `provider.type='local'` exits 2 instead of stopping mid-workflow at the
  `i-pr` step).
- `prompt.build_prompt(...)` requires `issue_context: IssueContext` (no
  longer Optional). All callers must pass the resolved `IssueContext`
  from `WorkflowRunner._resolve_run_issue_context()`. The internal
  `if issue_context is not None:` fallback paths have been removed.

### Added

#### Provider abstraction & local mode

- `IssueProvider` Protocol + `IssueContext` providing 9 context variables
  (`issue_id`, `issue_ref`, `issue_input`, `branch_prefix`,
  `branch_name`, `worktree_dir`, `design_path`, `provider_type`,
  `default_branch`; `step_id` continues to come from the step
  definition).
- `LocalProvider` for GitHub-independent issue management
  (`.kaji/issues/<id>-<slug>/issue.md`, file-based CRUD, POSIX flock for
  the ID counter, atomic frontmatter writes via `os.replace`).
- `kaji local init` CLI (overlay-only: writes `.kaji/config.local.toml`,
  never the tracked `.kaji/config.toml`; hostname-based machine_id
  candidate; `.gitignore` integration).
- `kaji config provider-type` — read-only subcommand that prints the
  resolved provider type (`github` / `local` / `gitlab`) on stdout.
- `Workflow.requires_provider` field (`"github"` / `"local"` /
  `"gitlab"` / `"any"`, default `"any"`). Declares which provider type a
  workflow expects; builtin `.kaji/wf/*.yaml` declare it explicitly.
- `feature-development-local.yaml` workflow (final step is `issue-close`
  instead of `i-pr`; no PR concept under local mode) and
  `docs-maintenance-local.yaml` (lets `type:docs` issues run under
  `provider.type='local'` without hitting the bare-provider PR guard).
- `kaji_harness/providers/_mappings.py` `LABEL_TO_PREFIX` table —
  canonical source of `type:* label → branch_prefix` mapping.
- ID normalization across `local-<machine>-<n>` / `<machine>-<n>` /
  numeric / `gh:N` / `gl:N` forms.
- Step 0 provider-check guard in the `pr-fix` / `pr-verify` / `i-pr`
  skills — forge-only skills ABORT under `provider.type='local'` with
  guidance toward the bare-mode alternatives.
- `docs/operations/local-mode-runbook.md` — operations runbook covering
  single-PC / multi-PC setup, the daily Issue lifecycle, code
  synchronisation strategy, forge migration judgement criteria, and
  troubleshooting.

#### GitLab provider

- `GitLabProvider` — `provider.type='gitlab'` backed by the `glab` CLI
  (mutating ops) and `glab api` (reads). 8-method `IssueProvider`
  implementation with `GitLabProviderConfig` and config-overlay support.
- `kaji issue` / `kaji pr` GitLab passthrough with a `gl:N` issue-id
  form. Skill-facing args stay GitHub-shaped (`--body`, `edit`,
  `comment`, `--base`, `--head`); the dispatcher rewrites them to `glab`
  equivalents. Unsupported subcommands are rejected with exit 2 instead
  of being silently passed through.
- `GitLabProvider.resolve_pr_context()` + `PRContext` dataclass —
  resolves the MR for the current branch and injects `pr_id` / `pr_ref`
  into skill prompts.
- `kaji sync from-gitlab` / `kaji sync status` — fetch GitLab issues into
  a local read cache with an all-or-nothing 3-phase contract
  (fetch → stale check → atomic write) and paginated retrieval.

#### Workflows & skills

- `review-cycle.yaml` / `review-close.yaml` workflows and the
  `/review-cycle` skill — drive the `review → pr-fix ⇄ pr-verify` loop
  (and optionally `issue-close`) with a single command.

### Changed

- `kaji issue` / `kaji pr` dispatch now routes through the
  `get_provider()` factory; `--repo` is auto-injected when
  `[provider.github] repo` is configured.
- `cmd_run` validates the provider configuration before constructing the
  runner; `[provider]` misconfiguration is reported as exit 2 and no
  longer surfaces as an `IssueContextResolutionError` at exit 3.
- `LocalProvider.close_issue(reason=None)` now writes
  `close_reason: "completed"` (was an empty string), aligning with the
  GitHub Issue API default.
- Repositioned local-mode from "BCP for GitHub outage" to "primary SoT
  during validation period"; GitHub recovery is no longer a precondition
  for the project.

### Fixed

- `kaji run` step が CLI セッションの terminal event（Claude/Gemini
  `type:"result"` / Codex `turn.completed` / `turn.failed`）受信後も
  stdout EOF を待ち続けて `default_timeout` まで blocking する不具合を
  修正。`CLIEventAdapter` に `is_terminal_event` / `is_terminal_failure`
  を追加し、stream loop が terminal event 観測時に break して
  `terminate -> wait(5) -> kill` で後始末する。timer は最終ガードとして
  温存し、`terminal_seen` 観測時は `timer.cancel()` 先行で grace wait
  中の race を構造的に排除。Claude/Gemini の failure terminal は
  `is_terminal_failure` で `CLIExecutionError` に伝搬する。
- `kaji issue create/edit/comment` now accept `--body-file` (and `-` for
  stdin) under the GitLab provider; the flag is expanded to `--body`
  before reaching `glab`, restoring contract parity with the GitHub and
  local providers.

### Removed

- WARN-then-fallback path for a missing `[provider]` section (now
  fail-fast — see BREAKING CHANGE above).
- Legacy `kaji issue` / `kaji pr` passthrough outside kaji repositories
  (now fail-fast — see BREAKING CHANGE above).

### Internal

- Migrated the skill suite from kamo2: `_shared/` rewrite, `docs/dev`
  workflow-doc renames, lifecycle / readiness / PR-gate skills, and the
  `i-pr` / `i-dev-final-check` / `i-doc-final-check` skills.
- Local-mode Phase 1/2 scaffolding: `kaji issue` / `kaji pr` wrappers,
  str-typed issue ids, and `kaji pr review-comments` / `reviews` /
  `reply-to-comment`.
- Hardening: `resolve_main_worktree()` fail-fast, `LocalProvider`
  `repo_root` pinned to the main worktree, and `large_local` subprocess
  E2E fixtures (pytest markers `large_local` / `large_forge`, target
  `make test-large-local`).
- `CodexAdapter` `command_execution` / `file_change` / `web_search`
  rendering was merged and then reverted within this release window — no
  net change in 0.10.0.

### Migration

For existing GitHub-based usage, add to `.kaji/config.toml`:

    [provider]
    type = "github"

    [provider.github]
    repo = "<owner>/<repo>"

For local-first usage, run `kaji local init` (creates
`.kaji/config.local.toml` overlay). See `docs/cli-guides/local-mode.md`.

For **custom workflow YAMLs** that include forge-only skills (`i-pr` /
`pr-fix` / `pr-verify` / direct `kaji pr` invocations), add to opt into
the new fail-fast guard:

    requires_provider: github

The default value `any` keeps existing custom workflows running, but the
guard will not catch provider mismatches until the field is set. See
`docs/dev/workflow-authoring.md` for details.

The exit-code contract is now:

- Configuration / provider setup problems → exit 2
  (`EXIT_INVALID_INPUT` / `EXIT_CONFIG_NOT_FOUND`)
- Issue resolution problems (missing local issue dir, agent CLI not
  found, runtime exceptions) → exit 3 (`EXIT_RUNTIME_ERROR`)
