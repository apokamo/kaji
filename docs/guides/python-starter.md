# Python Starter Guide (kaji-starter-python)

Language: English | [日本語](python-starter.ja.md)

How to start a Python project that runs kaji's issue-driven development
workflow (design → implement → review → PR) from day one, using the
[kaji-starter-python](https://github.com/apokamo/kaji-starter-python)
template repository.

The starter ships:

- A Python project skeleton (`src/` layout / `uv` / `ruff` / `mypy` / `pytest` / `Makefile`)
- kaji preinstalled as a dev dependency (run it with `uv run kaji`)
- Five workflow YAMLs under `.kaji/wf/` (3 for the GitHub provider, 2 for the
  local provider), all in a claude single-agent configuration by default
- 23 generalized skills under `.claude/skills/` (non-Claude agents reference
  the same files via `.agents/skills/` symlinks)

This guide lives in the kaji repository — not in the starter — so that
repositories created from the template do not carry a meta-document about
"how to use the starter".

## 1. Create a repository from the starter

Prerequisites: [uv](https://docs.astral.sh/uv/), [gh](https://cli.github.com/),
and at least one agent CLI (the default configuration uses
[Claude Code](https://claude.com/claude-code); see
[§ 2.4](#24-agent-clis-and-models) for codex / gemini).

1. On GitHub, open
   [kaji-starter-python](https://github.com/apokamo/kaji-starter-python),
   click **Use this template** → **Create a new repository**, then clone it.
2. Edit the setup values:
   - `.kaji/config.toml`: set `[provider.github]` `repo = "<owner>/<repo>"`
     to your repository (required)
   - `AGENTS.md`: fill in the `<project-name>` placeholder
   - `LICENSE`: the starter is 0BSD (no attribution obligation), so replace
     it with your project's license if you like
   - (optional) rename the package — see [§ 4.3](#43-renaming-the-package)
3. Install and run the quality gate:

   ```bash
   uv sync
   source .venv/bin/activate && make check   # passes right after creation
   ```

4. Commit the initial setup and land it on `main` **before running any
   workflow**:

   ```bash
   git add -A && git commit -m "chore: initial setup"
   ```

   Commit after `uv sync` so a regenerated `uv.lock` is included. If the
   setup changes are left uncommitted, the workflow's skills self-repair them
   inside the feature worktree and they leak into your first feature PR.

5. Authenticate with GitHub and create the workflow labels (first time only):

   ```bash
   gh auth status
   scripts/setup_labels.sh
   ```

   GitHub labels (`type:*`) are **not** copied by "Use this template", and
   the workflow's issue handling depends on them.

6. Run your first workflow:

   ```bash
   uv run kaji issue create --title "..." --body-file issue.md --label type:feature
   uv run kaji run .kaji/wf/dev.yaml <issue-id>
   ```

## 2. Setup details

### 2.1 Supported environments

Linux / macOS / WSL2. Native Windows is not supported — the interactive
terminal runner is tmux-based — so Windows users should work inside WSL2.

### 2.2 Choosing a provider

| Provider | When to use | Workflows |
|----------|-------------|-----------|
| `github` (default) | Standard path. Issues, PRs, and reviews on GitHub | `dev.yaml` / `dev-thorough.yaml` / `docs.yaml` |
| `local` | Trying kaji without GitHub, working before auth is set up, or as a fallback | `dev-local.yaml` / `docs-local.yaml` |

The starter's tracked `.kaji/config.toml` is GitHub-first. To try the local
provider, create a machine-local overlay (gitignored) with:

```bash
uv run kaji local init
```

The local provider stores issues under `.kaji/issues/` (tracked) and has no
PR concept: `dev-local.yaml` starts at the `design` step and assumes
issue-create / issue-start were done manually (worktree creation included).
The manual steps are documented in the starter's
[`docs/dev/kaji-workflow.md`](https://github.com/apokamo/kaji-starter-python/blob/main/docs/dev/kaji-workflow.md)
(§ local provider issue-create / issue-start). Do not expect the GitHub
experience (PR review cycle, review-ready gate driven by GitHub issues) from
the local provider.

Keep the provider and the workflow consistent — running `dev-local.yaml`
under the github provider (or vice versa) is rejected fail-fast by kaji.

### 2.3 GitHub authentication and labels

- `gh auth status` must pass before running GitHub workflows (kaji delegates
  issue / PR operations to the `gh` CLI)
- `scripts/setup_labels.sh` creates the `type:*` labels
  (`type:feature` / `type:bug` / `type:refactor` / `type:docs` / `type:test` /
  `type:chore` / `type:perf` / `type:security`). It is idempotent
  (`gh label create --force`) and only needed for the GitHub provider

### 2.4 Agent CLIs and models

All five workflow YAMLs default to a **claude single-agent configuration**.
If you use a different agent CLI, convert all YAMLs at once:

```bash
uv run python scripts/set_agent.py codex    # or: gemini / claude
```

The CLI → model mapping is keyed by step tier: light steps (`start` / `pr` /
`close`) get a lighter model, everything else is heavy. The canonical mapping
lives in `scripts/set_agent.py`; the table below is transcribed from it.

| CLI | Model (heavy steps) | Model (light steps) | Valid `effort` values | Fallback for invalid `effort` |
|-----|---------------------|---------------------|-----------------------|-------------------------------|
| `claude` | `opus` | `sonnet` | low / medium / high / xhigh / max | — |
| `codex` | `gpt-5.5` | `gpt-5.5` | none / minimal / low / medium / high / xhigh | max → xhigh |
| `gemini` | `gemini-3-pro` | `gemini-3-flash` | low / medium / high / xhigh | max → xhigh, none · minimal → low |

`effort` values are workflow-specific tuning (e.g. `dev-thorough.yaml` raises
them) and are preserved by the conversion unless invalid for the target CLI.
The script is idempotent and atomic: re-running with the same CLI produces no
diff, and on any error no file is written.

The selected CLI must be installed and authenticated on the machine that runs
`kaji run`.

### 2.5 Interactive terminal runner (tmux)

The default runner is `headless` and needs no tmux. If you switch
`.kaji/config.toml` to `execution.agent_runner = "interactive_terminal"`
(agent sessions rendered in visible panes), you must run kaji **inside a tmux
session** with **tmux 3.1 or newer**. See the
[Interactive Terminal Runner guide](../cli-guides/interactive-terminal-runner.md).

## 3. Development flow

### 3.1 One lap of the workflow

```bash
# 1. File an issue (a type:* label routes it; body quality is gated by review-ready)
uv run kaji issue create --title "feat: ..." --body-file issue.md --label type:feature

# 2. Run the workflow
uv run kaji run .kaji/wf/dev.yaml <issue-id>
```

`dev.yaml` then advances the issue through: readiness review of the issue
body (`review-ready`) → worktree creation (`start`) → design + design review
cycle → TDD implementation + code review cycle → final check → PR creation →
PR review by the `review` skill (with `pr-fix` / `pr-verify` convergence) →
merge and worktree cleanup (`close`). Each step posts its work report and
verdict to the issue, and run artifacts are written under
`.kaji/artifacts/<issue>/runs/<timestamp>/` (gitignored).

Resume and single-step execution:

```bash
uv run kaji run .kaji/wf/dev.yaml <issue-id> --from <step>   # resume from a step
uv run kaji run .kaji/wf/dev.yaml <issue-id> --step <step>   # run one step only
```

### 3.2 Choosing among the five workflows

| Workflow | Provider | Use for |
|----------|----------|---------|
| `dev.yaml` | github | Standard development: issue → design → implement → review → PR → close |
| `dev-thorough.yaml` | github | Same transition graph as `dev.yaml` with higher effort on design / implementation |
| `docs.yaml` | github | Docs-only changes: doc-update → doc-review → PR → close |
| `dev-local.yaml` | local | Development without GitHub (no PR concept) |
| `docs-local.yaml` | local | Docs-only without GitHub (no PR concept) |

## 4. Customization

### 4.1 Switching the agent CLI

Use `scripts/set_agent.py` as described in
[§ 2.4](#24-agent-clis-and-models). After converting, validate:

```bash
uv run kaji validate .kaji/wf/*.yaml
```

### 4.2 Mixed-agent configurations

If you have multiple agent CLI subscriptions, you can assign different CLIs
per step by editing the YAMLs directly — the main motivation is review
diversity: having a different model review what another implemented. The kaji
repository itself runs this way (claude implements, codex reviews in its
[dev.yaml](../../.kaji/wf/dev.yaml)). Edit the `agent:` / `model:` /
`effort:` fields of the review steps (`review-code`, `verify-code`, `review`,
`pr-verify`, …), then re-validate. The workflow YAML syntax is documented in
[workflow-authoring.md](../dev/workflow-authoring.md).

Note that `set_agent.py` converts **all** steps to one CLI; running it after
a manual mixed setup overwrites your per-step choices.

### 4.3 Renaming the package

The default package is `src/starter_app/`, and `make check` passes without
renaming. To rename:

1. Change `name` in `pyproject.toml`
2. `git mv src/starter_app src/<your_package>`
3. Update the import in `tests/test_smoke.py`
4. `uv sync` (regenerates `uv.lock`), then `make check`

Do this as part of the initial setup commit ([§ 1](#1-create-a-repository-from-the-starter) step 4).

### 4.4 Growing the docs and conventions

The starter deliberately starts with a minimal docs set. When you grow it:

- Keep `AGENTS.md` as a thin entry point (index + non-negotiable rules);
  push details into `docs/` and register them in the `docs/README.md` index
- `docs/reference/python-standards.md` and `configuration.md` are the
  canonical conventions the skills load before writing code — extend them
  there rather than in skill files

### 4.5 Adjusting skills

`.claude/skills/` is the canonical location; `.agents/skills/` contains
per-skill relative symlinks for non-Claude agents. When you add or adjust a
skill, keep both in sync (one symlink per skill, `_shared` included). Skills
call the starter's `make check` family as their quality gate — if you change
Makefile target names, update the skills that reference them.

Skills are bundled copies, not synced from the kaji repository: kaji upgrades
do not change your skills, and your edits are yours to keep.

### 4.6 Extending the quality gates

`make check` runs `ruff check` / `ruff format --check` / `mypy` / `pytest`.
Extend it in the `Makefile` (e.g. add coverage or security scanners), and
record which change types require which gates in
`docs/dev/change-types-and-gates.md`. `make verify-docs` (doc link checker,
`AGENTS.md` included) is available from the start as an optional gate.

## 5. Optional: Codex auto-review (`review-poll`)

By default, the GitHub workflows review PRs with the bundled `review` skill —
no external bot involved. If your repository has the **Codex GitHub
integration** (`chatgpt-codex-connector[bot]`) installed, you can switch to
kaji's `review-poll` flow, which waits for Codex's auto-review on the PR and
converts its outcome into a workflow verdict.

1. Install the Codex GitHub connector on your repository (via ChatGPT's Codex
   settings → GitHub integration) and confirm that opening a PR gets an
   automatic review from `chatgpt-codex-connector[bot]`
2. In `dev.yaml` (and `dev-thorough.yaml` / `docs.yaml` if desired), change
   the `pr` step's `PASS` target from `review` to `review-poll`, and add the
   step:

   ```yaml
     - id: review-poll
       exec: [uv, run, kaji, pr, review-poll]
       on:
         PASS: close
         RETRY: pr-fix
         BACK_FALLBACK: review
         ABORT: end
   ```

   Keep the existing `review` step: `BACK_FALLBACK` falls back to it when the
   bot does not respond, so the workflow still converges without the bot.
   The kaji repository's own
   [dev.yaml](../../.kaji/wf/dev.yaml) uses this exact construction as a
   reference.
3. Validate: `uv run kaji validate .kaji/wf/dev.yaml`

## 6. Bundled documentation

| File | Role |
|------|------|
| `AGENTS.md` | Canonical agent instructions: entry point, index, and non-negotiable rules only |
| `CLAUDE.md` | `@AGENTS.md` import plus Claude Code-specific notes (skills list, memory settings) |
| `README.md` / `README.ja.md` | Human-facing overview + quickstart (English primary, Japanese counterpart) |
| `docs/README.md` | Documentation index |
| `docs/dev/change-types-and-gates.md` | Required quality gates per change type |
| `docs/dev/testing-convention.md` | Small / Medium / Large test conventions and when to add permanent tests |
| `docs/dev/git-workflow.md` | Branch / commit / merge rules (`--no-ff`, no direct commits to main) |
| `docs/dev/kaji-workflow.md` | The five workflows, skill lifecycle, and local-provider manual steps |
| `docs/dev/shared_skill_rules.md` | Responsibility boundaries between skills and verdict conventions |
| `docs/dev/documentation_update_criteria.md` | Framework for deciding whether a change needs docs updates |
| `docs/reference/configuration.md` (+ `.en.md`) | `.kaji/config.toml` and `.env` responsibilities, including where run logs go (Japanese primary + English — the starter's current layout, see the note below) |
| `docs/reference/python-standards.md` (+ `.en.md`) | Python coding standards the skills load before writing code (Japanese primary + English — the starter's current layout, see the note below) |
| `LICENSE` | 0BSD — no attribution obligation, replace freely. Intentionally different from kaji itself (Apache-2.0): the starter's contents become *your* repository |
| `scripts/` | `set_agent.py` (agent conversion) / `setup_labels.sh` (labels) / `check_doc_links.py` (doc link checker) |
| `.claude/skills/` + `.agents/skills/` | 23 generalized skills (canonical + per-skill symlinks) |

Language policy (provisional; describes the **starter template as it ships
today**, not kaji's convention): public-facing docs are English-primary with
Japanese counterparts (`README.ja.md`, this guide's `.ja`), internal
references are Japanese-primary with `.en.md` translations, and internal
process docs (`docs/dev/`, `AGENTS.md`, `CLAUDE.md`, skills) are
Japanese-only for now. Note that kaji itself has since switched to "base
name = English canonical + optional `.ja.md`, no `.en.md`" for user-facing
docs (see the [translation policy in kaji's docs index](../README.md));
re-aligning the starter template is tracked in the starter repository, not
here.

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Issue / PR operations fail with auth errors | `gh` is not authenticated | `gh auth login`, confirm with `gh auth status` |
| Issue creation fails with a label error | `type:*` labels do not exist — GitHub labels are not copied by "Use this template" | Run `scripts/setup_labels.sh` once |
| Workflow fails to resolve the repository | `.kaji/config.toml` still has the `repo = "<owner>/<repo>"` placeholder | Set your repository, commit, and land it on `main` |
| Setup changes (config / rename / `uv.lock`) show up in your first feature PR | Initial setup was left uncommitted when the workflow ran, and skills self-repaired it inside the worktree | Rename → `uv sync` → `make check` → commit to `main` **before** the first run ([§ 1](#1-create-a-repository-from-the-starter) step 4) |
| kaji exits immediately with a provider error | Provider / workflow mismatch (github provider with `dev-local.yaml`, or local with `dev.yaml`) | Match them; kaji rejects the mismatch fail-fast by design |
| A step dies at agent launch, or the model is rejected | The agent CLI named in the YAML is not installed, or the model / effort does not match your environment | Install the CLI or convert with `uv run python scripts/set_agent.py <cli>` |
| `review-poll` keeps polling and never progresses | You switched to the review-poll configuration but `chatgpt-codex-connector[bot]` is not installed on the repo | Install the Codex GitHub connector, or revert to the default `review` step (see § 5) |
| Interactive terminal runner fails to start panes | Running outside a tmux session, or tmux is older than 3.1 | Start a tmux session first, upgrade tmux, or keep the default `headless` runner |
| `dev-local.yaml` aborts around `design` with a missing worktree | Local workflows start at `design` and assume manual issue-create / issue-start | Follow the manual steps in the starter's `docs/dev/kaji-workflow.md`, then rerun |
| `issue-close` aborts, warning about untracked files | A stray log file sits in the repository root (e.g. a redirected `kaji run ... > run.log`) and trips the close safety guard | No redirect is needed — full logs are already in `.kaji/artifacts/<issue>/runs/<ts>/`. If you must keep stdout, write it outside the repo (or a gitignored `tmp/`) |
| Local provider feels "incomplete" | Expecting GitHub PRs / review-poll / the review-ready gate from the local provider | That is by design: the local provider has no PR concept. Use GitHub for the full experience |

## Related documents

- Starter repository: <https://github.com/apokamo/kaji-starter-python>
- kaji configuration reference: [configuration.md](../reference/configuration.md)
  ([Japanese](../reference/configuration.ja.md))
- Workflow YAML authoring: [workflow-authoring.md](../dev/workflow-authoring.md)
- Local mode CLI guide: [local-mode.md](../cli-guides/local-mode.md)
- Interactive terminal runner: [interactive-terminal-runner.md](../cli-guides/interactive-terminal-runner.md)
