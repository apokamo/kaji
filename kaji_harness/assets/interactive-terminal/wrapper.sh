#!/usr/bin/env bash
set -euo pipefail

agent="${1:?agent is required}"
prompt_path="${2:?prompt_path is required}"
verdict_path="${3:?verdict_path is required}"
workdir="${4:?workdir is required}"
resume_session_id="${5:-}"
launch_session_id="${6:-}"
model="${7:-}"
effort="${8:-}"

cd "$workdir"

unset NO_COLOR
export COLORTERM=truecolor

echo "kaji interactive terminal runner"
echo "agent: $agent"
echo "workdir: $workdir"
echo "prompt_path: $prompt_path"
echo "verdict_path: $verdict_path"
if [[ -n "$resume_session_id" ]]; then
  echo "resume_session_id: $resume_session_id"
fi
if [[ -n "$launch_session_id" ]]; then
  echo "launch_session_id: $launch_session_id"
fi
if [[ -n "$model" ]]; then
  echo "model: $model"
fi
if [[ -n "$effort" ]]; then
  echo "effort: $effort"
fi
echo
echo "The agent must read prompt_path and write a pure YAML verdict to verdict_path."
echo

initial_prompt="Read the full task prompt from: $prompt_path

Carry out the requested workflow step in this existing workspace: $workdir

When the step is complete, write only a pure YAML verdict file to this exact path:
$verdict_path

Do not wrap the YAML in Markdown. Use the valid status values described in the prompt."

# The runner records the pane transcript via `tmux pipe-pane`, so the wrapper
# launches the agent directly (no util-linux script(1) dependency / OS branch).
run_agent() {
  exec bash -c "$1"
}

case "$agent" in
  claude)
    claude_model_args=""
    if [[ -n "$model" ]]; then
      claude_model_args="$(printf ' --model %q' "$model")"
    fi
    claude_effort_args=""
    if [[ -n "$effort" ]]; then
      claude_effort_args="$(printf ' --effort %q' "$effort")"
    fi
    if [[ -n "$resume_session_id" ]]; then
      run_agent "$(printf 'exec claude --dangerously-skip-permissions%s%s --resume %q %q' "$claude_model_args" "$claude_effort_args" "$resume_session_id" "$initial_prompt")"
    fi
    if [[ -n "$launch_session_id" ]]; then
      run_agent "$(printf 'exec claude --dangerously-skip-permissions%s%s --session-id %q %q' "$claude_model_args" "$claude_effort_args" "$launch_session_id" "$initial_prompt")"
    fi
    run_agent "$(printf 'exec claude --dangerously-skip-permissions%s%s %q' "$claude_model_args" "$claude_effort_args" "$initial_prompt")"
    ;;
  codex)
    codex_model_args=""
    if [[ -n "$model" ]]; then
      codex_model_args="$(printf ' --model %q' "$model")"
    fi
    codex_effort_args=""
    if [[ -n "$effort" ]]; then
      codex_effort_args="$(printf ' --config %q' "model_reasoning_effort=\"$effort\"")"
    fi
    if [[ -n "$resume_session_id" ]]; then
      run_agent "$(printf 'exec codex resume --cd %q --dangerously-bypass-approvals-and-sandbox%s%s %q %q' "$workdir" "$codex_model_args" "$codex_effort_args" "$resume_session_id" "$initial_prompt")"
    fi
    run_agent "$(printf 'exec codex --cd %q --dangerously-bypass-approvals-and-sandbox%s%s %q' "$workdir" "$codex_model_args" "$codex_effort_args" "$initial_prompt")"
    ;;
  *)
    echo "unsupported agent: $agent" >&2
    exit 2
    ;;
esac
