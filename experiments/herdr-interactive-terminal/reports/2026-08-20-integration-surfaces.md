# Herdr integration surface research

Issue: #396

## Scope

Evaluate the current Herdr 0.8.2 surfaces for:

1. kaji's core interactive terminal backend
2. a Codex or Claude Code agent opening a pane and launching interactive kaji
3. an optional human-facing kaji launcher plugin

## Primary sources

- installed `herdr --skill`
- installed `herdr api schema --json` (protocol 20)
- <https://herdr.dev/docs/agent-skill/>
- <https://herdr.dev/docs/agent-automation/>
- <https://herdr.dev/docs/integrations/>
- <https://herdr.dev/docs/socket-api/>
- <https://herdr.dev/docs/cli-reference/>
- <https://github.com/herdrdev/herdr/blob/master/docs/next/website/src/content/docs/plugins.mdx>

## Findings

### CLI wrappers are the default integration layer

Herdr recommends CLI wrappers for scripts and ordinary automation. Raw socket access is appropriate
for direct IPC or long-lived subscriptions. kaji needs short request/response operations, so the
core backend should invoke the installed `herdr` CLI with argv lists and parse JSON IDs.

### Caller context is the safety boundary

The release-matched skill requires `HERDR_ENV=1`; otherwise an agent must stop instead of controlling
the focused Herdr session from outside. Commands should use `--current`, an explicit pane ID, or a
unique agent name. Omitted targets are unsafe because they may resolve another client's focused pane.

### Pane and agent are different primitives

Pane operations manage topology and raw terminal commands. Agent operations validate a recognized
agent occupant and expose semantic state. `agent start` does not create topology and requires an
available shell pane.

kaji's existing wrapper owns model, effort, resume, policy, and initial prompt construction. The MVP
should compare:

- `pane split` + `pane run <existing wrapper>`
- `pane split` + `agent start` + `agent prompt`

The first option minimizes divergence; the second uses more semantic Herdr operations.

### Metadata tokens can mark kaji panes

Protocol 20 `PaneInfo` includes `tokens`, and `pane.report_metadata` can set source-scoped tokens
without becoming agent lifecycle authority. Proposed ownership fields are `kaji_origin`, `kaji_run`,
and `kaji_step`.

### Agent skill is the primary agent-to-kaji path

The bundled skill already teaches an in-Herdr agent to split a sibling pane, preserve cwd and focus,
parse the returned ID, and run an ordinary command. A small kaji-specific skill/guide can add the
kaji invocation contract and prohibit Claude Code `-p` / print mode.

### Plugin is optional human UX

Plugin v1 provides manifest actions, events, panes, and link handlers. It does not provide runtime
action registration or runtime argv pane declarations. Plugin code runs unsandboxed as the user.

A plugin fits a human keybinding/action such as "open kaji in this workspace". It should not be the
core runner backend or the required dynamic agent-to-kaji path.

### Official integrations are optional

No Herdr integrations were installed in the test environment. Claude and Codex integrations report
native session identity but do not own lifecycle state. Installing them modifies user agent config,
so integration-based tests are deferred until that mutation is explicitly in scope. The first
backend must retain kaji's existing session-ID fallbacks.

### Transcript parity is not available

The structured read result contains text plus `truncated`. Full-screen alternate-screen history may
be unavailable while an agent is working, blocked, or unknown, and lines lost from that screen may
not enter host scrollback. The backend must label the artifact as a rendered snapshot and record
truncation rather than claiming tmux raw transcript parity.

## Decisions carried into the design

- Explicit `tmux|herdr` backend; tmux remains default.
- Require `HERDR_ENV=1` and `HERDR_PANE_ID` for Herdr backend.
- Use release-matched CLI wrappers, not a custom socket transport.
- Keep `verdict.yaml` as the completion authority.
- Use metadata tokens for ownership and safe cleanup.
- Make Herdr integrations optional.
- Use Herdr skill + kaji-specific guidance for agent-originated launches.
- Evaluate a plugin only as an optional human-facing launcher.
