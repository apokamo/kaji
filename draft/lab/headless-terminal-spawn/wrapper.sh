#!/usr/bin/env bash
# Wrapper executed inside the spawned terminal window.
# Runs the agent (claude or codex) with a pre-filled prompt and records status.
#
# Usage: wrapper.sh <run_dir> <agent_kind>
#   agent_kind: claude | codex

set -u

RUN_DIR="${1:?run_dir required}"
AGENT="${2:?agent kind required}"

# claude shows a workspace-trust dialog the first time it sees a cwd in interactive
# mode (only -p mode skips it). Fresh run_dirs would block here, so cd to a
# known-trusted dir and pass run_dir via --add-dir / explicit paths in the prompt.
# This is overridable via KAJI_TRUSTED_CWD for environments where the default
# doesn't exist.
TRUSTED_CWD="${KAJI_TRUSTED_CWD:-$HOME/dev/kaji/main}"
if [[ ! -d "$TRUSTED_CWD" ]]; then
    TRUSTED_CWD="$HOME"
fi
cd "$TRUSTED_CWD" || exit 99

PROMPT_FILE="$RUN_DIR/prompt.txt"
STATUS_FILE="$RUN_DIR/status.json"
SENTINEL="$RUN_DIR/sentinel"

if [[ ! -f "$PROMPT_FILE" ]]; then
    echo "wrapper.sh: prompt.txt not found at $PROMPT_FILE" >&2
    echo "{\"exit_code\": 98, \"error\": \"prompt missing\"}" > "$STATUS_FILE"
    touch "$SENTINEL"
    exit 98
fi

PROMPT_CONTENT="$(cat "$PROMPT_FILE")"
START_TS="$(date -u +%FT%TZ)"

write_status() {
    local code="$1"
    local end_ts
    end_ts="$(date -u +%FT%TZ)"
    cat > "$STATUS_FILE" <<EOF
{
  "agent": "$AGENT",
  "exit_code": $code,
  "started_at": "$START_TS",
  "finished_at": "$end_ts"
}
EOF
}

# Ensure status / sentinel are written even on abnormal exit, but only if the
# agent itself didn't already create them (the agent is instructed to touch
# sentinel from within its turn).
on_exit() {
    local code=$?
    if [[ ! -f "$STATUS_FILE" ]]; then
        write_status "$code"
    fi
    if [[ ! -f "$SENTINEL" ]]; then
        touch "$SENTINEL"
    fi
}
trap on_exit EXIT

case "$AGENT" in
    claude)
        claude --dangerously-skip-permissions "$PROMPT_CONTENT"
        rc=$?
        ;;
    codex)
        codex --dangerously-bypass-approvals-and-sandbox "$PROMPT_CONTENT"
        rc=$?
        ;;
    *)
        echo "wrapper.sh: unknown agent kind: $AGENT" >&2
        rc=97
        ;;
esac

write_status "$rc"
# Leave the window open so the user can inspect the session manually.
echo
echo "--- agent exited with $rc; sentinel=$SENTINEL ---"
echo "Press Enter to close..."
read -r _ || true
exit "$rc"
