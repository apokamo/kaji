# Claude Code CLI Guide

## Overview

This guide summarizes the Claude Code CLI surface that is useful when running
or integrating kaji workflows.

Verified environment:

| Item | Value |
|------|-------|
| CLI | Claude Code `2.1.202` |
| Verification date | 2026-07-08 |
| Verification method | `claude --version`, `claude --help`, and selected subcommand `--help` output |

The command line is the source of truth for the local installation. Re-check
`claude --help` after upgrading Claude Code.

Official documentation:

- <https://docs.anthropic.com/claude-code>

## 1. Command Shape

Claude Code starts an interactive session by default:

```bash
claude [options] [prompt]
```

Use print mode for non-interactive output:

```bash
claude -p "Summarize this repository"
claude --print --output-format json "Return a short status report"
```

The installed CLI also exposes subcommands:

| Command | Purpose |
|---------|---------|
| `claude agents` | Manage background agents |
| `claude auth` | Manage authentication |
| `claude auto-mode` | Inspect auto mode classifier configuration |
| `claude doctor` | Check the auto-updater and environment health |
| `claude gateway` | Run the enterprise auth/telemetry gateway |
| `claude install` | Install the native build |
| `claude mcp` | Configure and manage MCP servers |
| `claude plugin` / `claude plugins` | Manage plugins |
| `claude project` | Manage Claude Code project state |
| `claude setup-token` | Set up a long-lived authentication token |
| `claude ultrareview` | Run a cloud-hosted multi-agent code review |
| `claude update` / `claude upgrade` | Check for updates and install them |

## 2. Core Options

| Option | Short | Notes |
|--------|-------|-------|
| `--print` | `-p` | Print a response and exit. Useful for scripts and pipes. |
| `--model <model>` | | Model alias or full model name. Help examples include `fable`, `opus`, `sonnet`, and full names such as `claude-fable-5`. |
| `--output-format <format>` | | Print-mode output: `text`, `json`, or `stream-json`. |
| `--input-format <format>` | | Print-mode input: `text` or `stream-json`. |
| `--session-id <uuid>` | | Start or use a specific UUID session. |
| `--continue` | `-c` | Continue the most recent conversation in the current directory. |
| `--resume [value]` | `-r` | Resume by session ID or open the picker with an optional search term. |
| `--fork-session` | | Resume or continue into a new session ID. |
| `--from-pr [value]` | | Resume a PR-linked session by number, URL, or picker search term. |
| `--name <name>` | `-n` | Set a display name for the session. |
| `--add-dir <directories...>` | | Allow access to additional directories. |
| `--system-prompt <prompt>` | | Replace the system prompt. |
| `--append-system-prompt <prompt>` | | Append to the default system prompt. |
| `--settings <file-or-json>` | | Load settings from a JSON file or JSON string. |
| `--setting-sources <sources>` | | Select setting sources: `user`, `project`, `local`. |
| `--debug [filter]` | `-d` | Enable debug output with an optional filter. |
| `--debug-file <path>` | | Write debug logs to a file. |
| `--verbose` | | Override the configured verbose mode. |
| `--version` | `-v` | Print the version. |
| `--help` | `-h` | Print help. |

System prompts can also be loaded from files:

```bash
claude --system-prompt-file ./system.txt "Task"
claude --append-system-prompt-file ./rules.txt "Task"
```

## 3. Print Mode and Structured Output

Print mode is the integration-friendly path:

```bash
claude -p "Explain the current branch"
```

Text output is the default. JSON output returns one final result object:

```bash
claude -p --output-format json "Return a one sentence summary"
```

Streaming output emits JSONL-style events:

```bash
claude -p --output-format stream-json --verbose "Analyze this change"
```

For realtime input pipelines, combine stream input and stream output:

```bash
producer | claude -p \
  --input-format stream-json \
  --output-format stream-json \
  "Process these events"
```

Useful print-mode options:

| Option | Notes |
|--------|-------|
| `--json-schema <schema>` | Validate structured output against a JSON Schema. |
| `--max-budget-usd <amount>` | Stop after the configured API spend budget. |
| `--fallback-model <model>` | Use fallback model(s) when the primary model is overloaded or unavailable. |
| `--no-session-persistence` | Do not save the session. Only works with `--print`. |
| `--include-partial-messages` | Include partial chunks with `--print --output-format stream-json`. |
| `--include-hook-events` | Include hook lifecycle events with `--output-format stream-json`. |
| `--prompt-suggestions [value]` | Emit predicted next prompts in print/SDK mode. |
| `--replay-user-messages` | Re-emit streamed user messages with stream input and stream output. |

## 4. Sessions

Claude Code sessions are resumable.

```bash
claude -p "Start a task"
claude -p -c "Continue the latest task"
claude -p -r "$SESSION_ID" "Continue this session"
claude -p -r "$SESSION_ID" --fork-session "Explore another path"
claude --from-pr 123 "Resume the PR-linked session"
```

Use `--session-id <uuid>` only with a valid UUID.

For temporary automation where the session should not be reused:

```bash
claude -p --no-session-persistence "One-off check"
```

## 5. Permissions and Tool Control

Tool availability and permission behavior are controlled separately.

| Option | Purpose |
|--------|---------|
| `--tools <tools...>` | Restrict the available built-in tools. Use `""` to disable all tools, `default` to use the default set, or names such as `Bash,Edit,Read`. |
| `--allowedTools` / `--allowed-tools <tools...>` | Allow tools or tool patterns without prompting. |
| `--disallowedTools` / `--disallowed-tools <tools...>` | Deny tools or tool patterns. |
| `--permission-mode <mode>` | Set the permission mode. Choices are `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, and `plan`. |
| `--dangerously-skip-permissions` | Alias for bypassing permission checks. Use only in isolated environments. |
| `--allow-dangerously-skip-permissions` | Make bypass mode available without enabling it by default. |
| `--permission-prompt-tool <tool>` | Delegate permission prompts to an MCP tool. |

Examples:

```bash
claude -p \
  --allowedTools "Read" "Grep" "Glob" \
  "Inspect the documentation"

claude -p \
  --tools "Read,Grep,Glob" \
  --permission-mode dontAsk \
  "Report on this repository without editing files"
```

Permission bypass options are dangerous outside a container, VM, or other
disposable sandbox.

## 6. Configuration, Safety, and Environment Modes

Claude Code can load project, local, and user settings:

```bash
claude --setting-sources user,project,local
claude --settings ./settings.json "Task"
claude --settings '{"permissions":{"allow":["Read"]}}' "Task"
```

Troubleshooting and reduced-context modes:

| Option | Purpose |
|--------|---------|
| `--safe-mode` | Start with customizations such as CLAUDE.md, skills, plugins, hooks, MCP servers, custom commands, themes, and keybindings disabled. |
| `--bare` | Minimal mode for explicit-context automation. Skips hooks, LSP, plugin sync, attribution, auto-memory, background prefetches, keychain reads, and CLAUDE.md auto-discovery. |
| `--exclude-dynamic-system-prompt-sections` | Move machine-specific default-prompt sections into the first user message for better prompt-cache reuse. |
| `--ax-screen-reader` | Render screen-reader friendly output. |

`--bare` requires explicit context through options such as
`--system-prompt`, `--append-system-prompt`, `--add-dir`, `--mcp-config`,
`--settings`, `--agents`, or `--plugin-dir`.

## 7. Agents and Background Work

Use `--agent` to choose the main agent for the current session:

```bash
claude --agent reviewer "Review this change"
```

Use `--agents` to define custom agents for a session:

```bash
claude --agents '{
  "reviewer": {
    "description": "Reviews code",
    "prompt": "You are a careful code reviewer."
  }
}' "Review the current branch"
```

Start a session as a background agent:

```bash
claude --background "Investigate flaky tests"
```

Manage background agents:

```bash
claude agents
claude agents --json
claude agents --json --all
```

Selected `claude agents` options:

| Option | Purpose |
|--------|---------|
| `--cwd <path>` | Show only background sessions started under a path. |
| `--json` | Print active sessions as JSON and exit. |
| `--all` | With `--json`, include completed sessions. |
| `--model <model>` | Default model for sessions dispatched from the agent view. |
| `--agent <agent>` | Default agent for dispatched sessions. |
| `--permission-mode <mode>` | Default permission mode for dispatched sessions. |
| `--add-dir <directory>` | Add an allowed directory for dispatched sessions. |

## 8. Worktrees and Terminal Layout

Claude Code can create a git worktree for a session:

```bash
claude -w feature-name "Work in an isolated worktree"
claude --worktree feature-name "Work in an isolated worktree"
```

Use `--tmux` with `--worktree` to create a tmux session:

```bash
claude --worktree feature-name --tmux "Start work"
claude --worktree feature-name --tmux=classic "Start work"
```

The old `--teammate-mode` option is not present in `claude --help` for
Claude Code `2.1.202`; use the currently documented `--tmux` option instead.

## 9. MCP Servers

MCP servers are managed through `claude mcp`.

```bash
claude mcp list
claude mcp get <name>
claude mcp remove <name>
```

Add servers from command lines, JSON, or Claude Desktop:

```bash
claude mcp add my-server -- my-command --some-flag arg1
claude mcp add --transport http sentry https://mcp.sentry.dev/mcp
claude mcp add-json my-server '{"type":"stdio","command":"my-command"}'
claude mcp add-from-claude-desktop
```

Authentication and project choices:

```bash
claude mcp login <name>
claude mcp logout <name>
claude mcp reset-project-choices
claude mcp serve
```

Session-level MCP controls:

| Option | Purpose |
|--------|---------|
| `--mcp-config <configs...>` | Load MCP server definitions from JSON files or strings. |
| `--strict-mcp-config` | Ignore MCP servers outside `--mcp-config`. |

## 10. Plugins

Plugins are managed through `claude plugin` or `claude plugins`.

```bash
claude plugin list
claude plugin install <plugin>
claude plugin enable <plugin>
claude plugin disable <plugin>
claude plugin update <plugin>
claude plugin uninstall <plugin>
```

Development and inspection commands:

```bash
claude plugin init my-plugin
claude plugin validate ./my-plugin
claude plugin details my-plugin
claude plugin eval ./my-plugin
claude plugin tag ./my-plugin
```

Session-level plugin options:

| Option | Purpose |
|--------|---------|
| `--plugin-dir <path>` | Load a plugin directory or zip for this session. Repeatable. |
| `--plugin-url <url>` | Fetch a plugin zip for this session. Repeatable. |

## 11. Authentication, Installation, and Health Checks

Authentication:

```bash
claude auth login
claude auth logout
claude auth status
```

Installation and updates:

```bash
claude install
claude install latest
claude install stable
claude install <version>
claude update
```

Health check:

```bash
claude doctor
```

`claude doctor` can spawn stdio servers from `.mcp.json`; run it only in trusted
directories.

## 12. kaji Integration Notes

For kaji, Claude Code is usually launched by the workflow runner rather than by
hand. The relevant concerns are:

- Use print or interactive terminal modes according to the kaji workflow runner.
- Keep working directory and worktree boundaries explicit.
- Prefer artifact-backed verdicts such as `verdict.yaml` for workflow
  completion signals.
- Avoid broad permission bypasses unless the runner environment is disposable.
- If MCP, plugins, or settings are needed for a workflow, make them explicit in
  repository configuration or the runner command.

When debugging an agent step manually, start with:

```bash
claude --version
claude --help
claude -p --output-format json "Return a JSON status summary"
```

## 13. Comparison with Codex CLI

| Capability | Claude Code | Codex CLI |
|------------|-------------|-----------|
| Non-interactive run | `claude -p` | `codex exec` |
| Resume latest session | `claude -c` | `codex exec resume --last` |
| Resume by ID | `claude -r <id>` | `codex exec resume <id>` |
| JSON result | `--output-format json` | `--json` |
| Streaming result | `--output-format stream-json` | JSONL event stream |
| Model selection | `--model` | `-m` |
| Extra working directories | `--add-dir` | `-C` for working directory selection |
| Tool control | `--tools`, `--allowedTools`, `--disallowedTools` | Sandbox and MCP configuration |
| Background agents | `claude agents`, `--background` | Not equivalent |
| Worktree creation | `--worktree` / `-w` | Not equivalent |

## 14. Troubleshooting

### Stream JSON Requires Verbose Output

If stream JSON output complains that verbose mode is required, add `--verbose`:

```bash
claude -p --output-format stream-json --verbose "Question"
```

### Session Resume Uses the Wrong Context

Use an explicit session ID or start a temporary non-persistent session:

```bash
claude -p -r "$SESSION_ID" "Continue"
claude -p --no-session-persistence "Run once"
```

### Configuration Appears Broken

Use safe mode or bare mode to separate Claude Code itself from project
customizations:

```bash
claude --safe-mode
claude --bare --add-dir . "Inspect this repository"
```

### Permission Prompts Block Automation

Constrain the available tools and choose a permission mode suitable for the
task:

```bash
claude -p \
  --tools "Read,Grep,Glob" \
  --permission-mode dontAsk \
  "Read-only documentation review"
```

## 15. Verification Log

| Area | Source | Verification |
|------|--------|--------------|
| Version | `claude --version` | Returned `2.1.202 (Claude Code)` on 2026-07-08. |
| Top-level options and commands | `claude --help` | Checked on 2026-07-08. |
| Authentication commands | `claude auth --help` | Checked on 2026-07-08. |
| Background agents | `claude agents --help` | Checked on 2026-07-08. |
| MCP commands | `claude mcp --help` | Checked on 2026-07-08. |
| Plugin commands | `claude plugin --help` | Checked on 2026-07-08. |
| Install command | `claude install --help` | Checked on 2026-07-08. |
| Doctor command | `claude doctor --help` | Checked on 2026-07-08. |
