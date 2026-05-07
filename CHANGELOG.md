# Changelog

All notable changes to kaji are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- **Phase 4**: `kaji pr ...` (including `pr create` / `pr list` /
  `pr review-comments` / `pr reviews` / `pr reply-to-comment`) now
  exits 2 with a `forge-only` error message when run under
  `provider.type='local'`. Previously, the call was passed through to
  `gh pr` even in local mode, risking accidental PR creation against the
  GitHub remote.
- **Phase 4**: `kaji run` now validates that `workflow.requires_provider`
  matches `config.provider.type` before dispatching the runner.
  Mismatches exit 2 with a switching guide (e.g. running
  `feature-development.yaml` under `provider.type='local'` exits 2 instead
  of stopping mid-workflow at the `i-pr` step).
- **Phase 4**: `prompt.build_prompt(...)` requires `issue_context:
  IssueContext` (no longer Optional). All callers must pass the
  resolved `IssueContext` from
  `WorkflowRunner._resolve_run_issue_context()`. The internal `if
  issue_context is not None:` fallback paths have been removed.

### Added

- **Phase 4**: `kaji config provider-type` — read-only subcommand that
  prints the resolved provider type (`github` / `local`) on stdout. Skill
  manual-execution paths use this to reconcile `[provider_type]` when the
  context variable has not been injected by the harness.
- **Phase 4**: `Workflow.requires_provider` field
  (`"github"` / `"local"` / `"any"`, default `"any"`). Declares which
  provider type a workflow expects. Builtin workflows in `.kaji/wf/*.yaml`
  declare it explicitly: `feature-development.yaml`,
  `feature-development-light.yaml`, `implement-to-pr.yaml` →
  `github`; `feature-development-local.yaml` → `local`;
  `design-only.yaml` → `any`.
- **Phase 4**: Step 0 provider-check guard added to `pr-fix` /
  `pr-verify` / `i-pr` SKILL.md. Forge-only skills now ABORT under
  `provider.type='local'` with guidance toward the bare-mode alternatives
  (`/issue-review-code` / `/issue-fix-code` / `/issue-verify-code` /
  `/issue-close`).

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

## [Phase 3] — kaji local mode

### Added

- `LocalProvider` for GitHub-independent issue management
  (`.kaji/issues/<id>-<slug>/issue.md`, file-based CRUD, POSIX flock for
  ID counter, atomic frontmatter writes via `os.replace`).
- `IssueProvider` Protocol + `IssueContext` providing 9 context variables
  via `IssueContext` (`issue_id`, `issue_ref`, `issue_input`,
  `branch_prefix`, `branch_name`, `worktree_dir`, `design_path`,
  `provider_type`, `default_branch`; `step_id` continues to come from the
  step definition).
- `kaji local init` CLI (overlay-only: writes
  `.kaji/config.local.toml`, never the tracked `.kaji/config.toml`;
  hostname-based machine_id candidate; `.gitignore` integration).
- `feature-development-local.yaml` workflow (final step is
  `issue-close` instead of `i-pr`; no PR concept under local mode).
- `kaji_harness/providers/_mappings.py` `LABEL_TO_PREFIX` table —
  canonical source of `type:* label → branch_prefix` mapping;
  `.claude/skills/` markdown is now documentation only.
- ID normalization across `local-<machine>-<n>` / `<machine>-<n>` /
  numeric / `gh:N` forms (read-only `gh:N` for cached GitHub issues
  under local mode).
- Skill markdown placeholder unification: `[branch-name]` →
  `[branch_name]`, `[worktree-absolute-path]` → `[worktree_dir]`,
  `[design-path]` → `[design_path]`, `[issue-input]` → `[issue_input]`
  (21 skill files; legacy hyphen / angle-bracket forms grep-asserted to
  zero).
- `issue-close` skill provider branching: `provider=local` follows the
  6-step base-worktree merge flow defined in design.md L972-996.
- pytest markers `large_local` / `large_forge` and the
  `make test-large-local` target for subprocess E2E categorization
  (no external network).

### Changed

- `kaji issue` / `kaji pr` dispatcher routes through `get_provider()`
  factory; `--repo` is auto-injected when `[provider.github] repo` is
  configured.
- `LocalProvider.close_issue(reason=None)` now writes
  `close_reason: "completed"` (was empty string), aligning with
  design.md L985 and the GitHub Issue API default.
- `cmd_run` validates the provider configuration before constructing
  the runner; `[provider]` misconfiguration is reported as exit 2 and
  no longer surfaces as an `IssueContextResolutionError` at exit 3.

### Removed

- WARN-then-fallback path for missing `[provider]` section (now
  fail-fast — see BREAKING CHANGE above).
- Legacy `kaji issue` / `kaji pr` passthrough outside kaji repositories
  (now fail-fast — see BREAKING CHANGE above).
