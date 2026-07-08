# Codex CLI Session Guide

## Overview

This guide summarizes the Codex CLI behavior that matters when running or
integrating kaji workflows: starting sessions, resuming work, collecting
machine-readable output, and choosing safe automation flags.

Verified environment:

| Item | Value |
|------|-------|
| CLI | OpenAI Codex CLI `0.142.5` |
| Verification date | 2026-07-08 |
| Verification method | `codex --version`, `codex --help`, selected subcommand `--help` output, and the current OpenAI Codex manual |

The installed command line is the source of truth for local behavior. Re-check
`codex --help` after upgrading Codex CLI.

Official documentation:

- <https://developers.openai.com/codex/cli/reference/>
- <https://developers.openai.com/codex/cli/features/>
- <https://developers.openai.com/codex/noninteractive/>
- <https://developers.openai.com/codex/models/>

## 1. Command Shape

Codex starts an interactive terminal UI when no subcommand is provided:

```bash
codex [OPTIONS] [PROMPT]
```

Use `codex exec` for non-interactive automation:

```bash
codex exec [OPTIONS] [PROMPT]
```

The installed CLI exposes these session-oriented commands:

| Command | Alias | Purpose |
|---------|-------|---------|
| `codex exec` | `codex e` | Run Codex non-interactively. |
| `codex exec resume` | | Resume a previous non-interactive session by ID or with `--last`. |
| `codex resume` | | Resume a previous interactive session by ID, picker, or `--last`. |
| `codex fork` | | Fork a previous interactive session into a new thread. |
| `codex review` | | Run a non-interactive code review. |
| `codex exec review` | | Run code review through the `exec` command path. |
| `codex apply` | `codex a` | Apply the latest diff produced by a Codex agent to the local tree. |
| `codex cloud` | | Browse and manage Codex Cloud tasks. |
| `codex mcp` | | Manage MCP servers. |
| `codex plugin` | | Manage plugins and plugin marketplaces. |
| `codex features` | | Inspect and persist feature flags. |
| `codex doctor` | | Diagnose local installation, config, auth, and runtime health. |
| `codex sandbox` | | Run commands inside a Codex-provided sandbox. |
| `codex archive` / `codex unarchive` | | Hide or restore saved interactive sessions. |
| `codex delete` | | Permanently delete a saved interactive session. |

## 2. Core Options

Common runtime options:

| Option | Short | Notes |
|--------|-------|-------|
| `--model <model>` | `-m` | Select the model for the session or run. |
| `--cd <dir>` | `-C` | Set the working directory for the agent. |
| `--sandbox <mode>` | `-s` | One of `read-only`, `workspace-write`, or `danger-full-access`. |
| `--ask-for-approval <policy>` | `-a` | One of `untrusted`, `on-request`, or `never`; `on-failure` is deprecated. |
| `--add-dir <dir>` | | Add another writable root alongside the main workspace. |
| `--config <key=value>` | `-c` | Override config values for one invocation. Values are parsed as TOML when possible. |
| `--enable <feature>` / `--disable <feature>` | | Override feature flags for one invocation. |
| `--strict-config` | | Fail when config contains fields not recognized by this CLI version. |
| `--image <file>` | `-i` | Attach one or more images to the prompt. |
| `--profile <name>` | `-p` | Layer `$CODEX_HOME/<name>.config.toml` on top of the base user config. |
| `--oss` | | Use the open-source provider. |
| `--local-provider <provider>` | | Choose `lmstudio` or `ollama` when using local models. |
| `--search` | | Enable live web search for the run. |
| `--no-alt-screen` | | Run the interactive TUI inline instead of using the alternate screen. |
| `--dangerously-bypass-approvals-and-sandbox` | | Disable approvals and sandboxing. Use only inside an external sandbox. |
| `--dangerously-bypass-hook-trust` | | Run enabled hooks without persisted hook trust for this invocation. |

`codex exec` adds automation-friendly flags:

| Option | Notes |
|--------|-------|
| `--json` | Emit a JSON Lines event stream to stdout. |
| `--output-last-message <file>` / `-o <file>` | Write the final agent message to a file. |
| `--output-schema <file>` | Require the final response to match a JSON Schema. |
| `--ephemeral` | Do not persist session files to disk. |
| `--skip-git-repo-check` | Allow execution outside a Git repository. |
| `--ignore-user-config` | Do not load `$CODEX_HOME/config.toml`; auth still uses `CODEX_HOME`. |
| `--ignore-rules` | Do not load user or project execpolicy `.rules` files. |
| `--color <always|never|auto>` | Control ANSI color output. |

In `0.142.5`, `codex exec resume --help` also lists `--json`,
`--output-last-message`, and `--output-schema`, so new and resumed
non-interactive runs can use the same structured-output path.

## 3. Sessions

### 3.1 Start a Non-Interactive Session

```bash
codex exec \
  --model gpt-5.5 \
  --sandbox workspace-write \
  --ask-for-approval never \
  "Summarize the current repository"
```

Use `--ask-for-approval never` for scripted or otherwise unattended
non-interactive runs. Reserve `on-request` for supervised local sessions where a
human can answer approval prompts.

If no prompt argument is provided, or if the prompt is `-`, `codex exec` reads
instructions from stdin. If stdin is piped and a prompt argument is also
provided, Codex treats the prompt as the instruction and appends stdin as
additional context.

### 3.2 Resume a Non-Interactive Session

```bash
codex exec resume --last "Continue from the previous findings"
codex exec resume "$SESSION_ID" "Run the next verification step"
```

Use explicit session IDs for parallel agent workflows. `--last` is convenient
for a single local thread, but it can pick the wrong run when several Codex
sessions are active or have recently completed.

`codex exec resume --all` disables the current-working-directory filter when
listing sessions.

### 3.3 Resume or Fork an Interactive Session

```bash
codex resume
codex resume --last
codex resume "$SESSION_ID" "Pick up the refactor"

codex fork --last "Explore an alternative implementation"
codex fork "$SESSION_ID"
```

`codex resume` reopens the saved transcript. `codex fork` preserves the
original transcript and starts a new thread from it.

### 3.4 Archive, Delete, and Restore Sessions

```bash
codex archive "$SESSION_ID"
codex unarchive "$SESSION_ID"
codex delete "$SESSION_ID"
```

Archive sessions when you only want to hide them from active lists. Delete
sessions only when the transcript should be removed.

## 4. Non-Interactive Output

Without `--json`, `codex exec` streams progress to stderr and prints only the
final agent message to stdout. This makes shell pipelines simple:

```bash
codex exec "Generate release notes for the last 10 commits" > release-notes.md
```

Use `--json` when a harness needs structured progress:

```bash
codex exec --json "Summarize the repository" | jq
```

The documented JSONL event stream includes:

| Event | Meaning |
|-------|---------|
| `thread.started` | Session started; includes `thread_id`. |
| `turn.started` | A model turn started. |
| `item.started` / `item.completed` | A work item such as command execution, MCP call, file change, web search, reasoning, plan update, or agent message. |
| `turn.completed` | A turn completed; includes usage fields. |
| `turn.failed` | A turn failed. |
| `error` | An error event. |

Extract a session ID from JSONL output:

```bash
SESSION_ID=$(
  codex exec --json "Start the investigation" |
    jq -r 'select(.type == "thread.started") | .thread_id'
)
```

Write the final message while still receiving the normal stream:

```bash
codex exec \
  --json \
  --output-last-message result.md \
  "Inspect this change and summarize the result"
```

Require a structured final response:

```bash
codex exec \
  --output-schema ./schema.json \
  --output-last-message ./result.json \
  "Extract the requested fields"
```

## 5. Common Patterns

### 5.1 Single-Agent Continuation

```bash
codex exec \
  --sandbox workspace-write \
  --ask-for-approval never \
  "Start the documentation audit"

codex exec resume --last "Apply the next documentation update"
```

### 5.2 Parallel Agents

```bash
SESSION_REVIEW=$(
  codex exec --json -C /path/to/project \
    --sandbox workspace-write \
    "Review the current diff" |
    jq -r 'select(.type == "thread.started") | .thread_id'
)

SESSION_DOCS=$(
  codex exec --json -C /path/to/project \
    --sandbox workspace-write \
    --search \
    "Check whether the docs are current" |
    jq -r 'select(.type == "thread.started") | .thread_id'
)

codex exec resume "$SESSION_REVIEW" "Report the top risks"
codex exec resume "$SESSION_DOCS" "Update only the stale documentation"
```

### 5.3 Prompt Plus Stdin

```bash
git diff --stat |
  codex exec "Summarize the scope of this change for a pull request"
```

### 5.4 Ephemeral Automation

```bash
codex exec \
  --ephemeral \
  --sandbox read-only \
  "Triage this repository and suggest the next three checks"
```

Use `--ephemeral` when the session should not be resumed later.

## 6. Permissions and Sandbox

Use the least access needed for the task:

| Mode | When to use |
|------|-------------|
| `--sandbox read-only` | Inspection, review, and planning. |
| `--sandbox workspace-write --ask-for-approval never` | Scripted non-interactive local coding or docs work where failures should return to the agent instead of prompting. |
| `--sandbox workspace-write --ask-for-approval on-request` | Supervised interactive or local runs where a human is available to answer approval prompts. |
| `--sandbox danger-full-access --ask-for-approval never` | Only inside an externally isolated container, VM, or CI runner. |

`workspace-write` can be extended with `--add-dir` when a workflow genuinely
needs another writable root:

```bash
codex exec \
  --sandbox workspace-write \
  --add-dir ../shared-docs \
  "Update cross-repository references"
```

Avoid using the bypass flags as a convenience shortcut on a normal workstation.
They remove the guardrails that make agentic shell execution reviewable.

Inside the interactive TUI, use `/permissions` to adjust permission behavior
without restarting the session.

## 7. Web Search and Models

Codex has a first-party web search tool. Cached search may be available by
default depending on configuration. Use `--search` when the task needs live
results:

```bash
codex exec --search "Check the current upstream release notes"
```

Models change over time. The current OpenAI Codex manual recommends `gpt-5.5`
for most Codex work and `gpt-5.4-mini` for faster, lower-cost lighter tasks.
Use `codex --model <model>`, `codex exec --model <model>`, or `/model` inside
the TUI to select a model, and re-check the official model documentation after
upgrading Codex.

```bash
codex --model gpt-5.5
codex exec --model gpt-5.5 "Review this branch"
```

## 8. MCP, Plugins, and Feature Flags

Manage MCP servers with `codex mcp`:

```bash
codex mcp list
codex mcp add context7 -- npx -y @upstash/context7-mcp
codex mcp get context7
codex mcp remove context7
```

MCP configuration lives in Codex config files such as
`~/.codex/config.toml`, and trusted projects can also use project-scoped
`.codex/config.toml`.

Manage plugins with `codex plugin`:

```bash
codex plugin list
codex plugin add <plugin-name>
codex plugin remove <plugin-name>
codex plugin marketplace list
```

Inspect feature flags with `codex features`:

```bash
codex features list
codex features enable <feature-name>
codex features disable <feature-name>
```

Feature, plugin, and MCP availability can differ by installed Codex version,
account, workspace policy, and local config.

## 9. Codex Cloud

`codex cloud` is experimental in the installed `0.142.5` CLI.

```bash
codex cloud
codex cloud list --limit 10
codex cloud list --json --env "$ENV_ID"
codex cloud exec --env "$ENV_ID" --attempts 3 "Investigate this bug"
codex cloud status "$TASK_ID"
codex cloud diff "$TASK_ID"
codex cloud apply "$TASK_ID"
```

`codex cloud exec` requires `--env <ENV_ID>`. In `0.142.5`, it also accepts
`--branch <BRANCH>` to choose the Git branch for the cloud task.

Use `codex apply <TASK_ID>` or `codex cloud apply <TASK_ID>` only after
reviewing the task and confirming that applying the diff to the current local
tree is safe.

## 10. Troubleshooting

### 10.1 `--last` Resumes the Wrong Session

Use explicit session IDs in scripts and parallel workflows. Reserve `--last`
for a single local thread where recent session ordering is obvious.

### 10.2 Git Repository Check Fails

Codex requires a Git repository for normal local work. If an automation target
is intentionally outside Git, use:

```bash
codex exec --skip-git-repo-check "Inspect this directory"
```

Only use this when the surrounding environment is controlled.

### 10.3 Config Drift After Upgrade

Run:

```bash
codex doctor
codex --strict-config --help
```

Then remove or update config keys that the new CLI version no longer accepts.

### 10.4 Automation Auth

For one-off automation with an API key, pass the key only to the Codex process:

```bash
CODEX_API_KEY="$CODEX_API_KEY" codex exec --json "Triage this change"
```

Do not expose API keys to unrelated setup commands, tests, dependency hooks, or
repository-controlled scripts in the same job environment.

### 10.5 Older Flag Examples

Older guides used flags such as `--experimental-json` and broad shortcuts such
as `--full-auto`. For current examples, use `--json` and explicit sandbox /
approval flags.

## 11. References and Verification

| Information | Source | Verification |
|-------------|--------|--------------|
| Installed version | `codex --version` | Local command returned `codex-cli 0.142.5`. |
| Top-level commands and global flags | `codex --help` | Local command on 2026-07-08. |
| Non-interactive options | `codex exec --help` | Local command on 2026-07-08. |
| Resume options | `codex exec resume --help`, `codex resume --help`, `codex fork --help` | Local command on 2026-07-08. |
| Cloud options | `codex cloud --help`, `codex cloud exec --help`, `codex cloud list --help` | Local command on 2026-07-08. |
| MCP, plugin, and feature commands | `codex mcp --help`, `codex plugin --help`, `codex features --help` | Local command on 2026-07-08. |
| JSONL event model, sandbox semantics, model guidance, slash commands | Current OpenAI Codex manual | Fetched on 2026-07-08. |

## Change History

| Date | Change |
|------|--------|
| 2025-11-27 | Initial Japanese guide for Codex CLI v0.63.0. |
| 2025-12-02 | Added notes about earlier `--json` resume limitations. |
| 2026-03-09 | Updated for v0.112.0 era commands and features. |
| 2026-05-23 | Updated for v0.124.0 and replaced `--experimental-json` with `--json`. |
| 2026-07-08 | Rewritten as the English canonical guide for Codex CLI `0.142.5`; stale model, command, and session notes were trimmed or updated. |
