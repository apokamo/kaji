---
name: herdr-kaji-launch
description: Open a sibling Herdr pane and launch kaji there as a real interactive command. Use when a Codex or Claude Code agent running inside Herdr is asked to start, show, or hand off a kaji workflow in another pane; never use Claude Code print mode (`-p`).
---

# Herdr Kaji Launch

Launch kaji in a response-identified sibling pane while preserving the caller's cwd and focus.

## Input

Use the kaji command requested by the user. For `kaji run`, require a workflow path and Issue ID.
Unless the user explicitly supplied equivalent options, add:

```text
--agent-runner interactive-terminal --interactive-terminal-backend herdr
```

## Procedure

1. Check `HERDR_ENV=1` and a non-empty `HERDR_PANE_ID`. If either check fails, stop and tell the
   user to start this agent inside Herdr. Do not inspect or control the UI-focused session from
   outside Herdr.
2. If the release-matched Herdr skill is not already in context, run `herdr --skill` and read its
   complete output before any pane operation. Follow its guardrails if they are stricter than this
   skill.
3. Resolve the intended cwd. Default to the caller's current working directory; use a different cwd
   only when the user supplied it or the target worktree was resolved explicitly.
4. Split from the explicit caller pane with `--no-focus` and parse the new pane ID from the JSON
   response. Never predict an ID and never omit the target.

   ```bash
   herdr pane split "$HERDR_PANE_ID" \
     --direction right \
     --ratio 0.5 \
     --cwd "$PWD" \
     --no-focus
   ```

5. Build one shell-quoted interactive kaji command from the user's arguments and run it in the
   response-derived pane ID.

   ```bash
   herdr pane run "<response-pane-id>" \
     "kaji run <workflow> <issue> --agent-runner interactive-terminal --interactive-terminal-backend herdr"
   ```

6. Report the pane ID, cwd, and exact kaji argv to the user. Leave the pane open for interaction.
   Close it only if the user asks, and only after re-reading that exact pane ID.

## Invariants

- Launch kaji as a real interactive command. Do not invoke `claude -p`, Claude Code print mode, or
  any headless substitute for this path.
- Do not use `agent start` for kaji; kaji is the command running in the pane, not the Herdr-recognized
  coding-agent occupant.
- Do not use terminal output matching as workflow completion authority. The nested interactive
  runner advances on its own `verdict.yaml` artifacts.
- Do not install Herdr integrations or plugins as part of launching kaji.
- Do not close, prune, or reuse any pane whose ID did not come from this split response.
