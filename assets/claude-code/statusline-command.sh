#!/bin/bash

input=$(cat)

cwd=$(echo "$input" | jq -r '.workspace.current_dir // ""')
project_dir=$(echo "$input" | jq -r '.workspace.project_dir // ""')
model_display=$(echo "$input" | jq -r '.model.display_name // "Claude"')
ctx_size=$(echo "$input" | jq -r '.context_window.context_window_size // 200000')
ctx_used_pct=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
total_in=$(echo "$input" | jq -r '.context_window.total_input_tokens // 0')
total_out=$(echo "$input" | jq -r '.context_window.total_output_tokens // 0')
r5_pct=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
r5_reset=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
r7_pct=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
r7_reset=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')
effort=$(echo "$input" | jq -r '.effort.level // empty')

# Muted palette (256-color). Soft and low-saturation.
C_PROJECT="38;5;108"   # sage green
C_PATH="38;5;66"       # muted steel blue
C_BRANCH="38;5;179"    # muted amber
C_MODEL="38;5;245"     # gray
C_LABEL="38;5;244"     # dim gray for labels (5h/7d)
C_TIME="38;5;109"      # dusty teal for reset times
RESET="\033[0m"

# Usage-level thresholds (muted)
color_for() {
    local p=$1
    if   [ "$p" -lt 50 ]; then echo "38;5;108"   # sage
    elif [ "$p" -lt 70 ]; then echo "38;5;179"   # amber
    elif [ "$p" -lt 85 ]; then echo "38;5;174"   # dusty rose
    else                       echo "38;5;167"   # muted red
    fi
}

project=""
[ -n "$project_dir" ] && project=$(basename "$project_dir")
[ -z "$project" ] && [ -n "$cwd" ] && project=$(basename "$cwd")

rel_path=""
if [ -n "$project_dir" ] && [ -n "$cwd" ]; then
    if [ "$cwd" = "$project_dir" ]; then
        rel_path="~"
    else
        rel_path="~/$(realpath --relative-to="$project_dir" "$cwd" 2>/dev/null || echo "?")"
    fi
fi

git_info=""
if [ -n "$cwd" ] && git -C "$cwd" rev-parse --git-dir >/dev/null 2>&1; then
    branch=$(git -C "$cwd" --no-optional-locks rev-parse --abbrev-ref HEAD 2>/dev/null)
    if [ -n "$branch" ]; then
        git_info="$branch"
        if ! git -C "$cwd" --no-optional-locks diff-index --quiet HEAD -- 2>/dev/null; then
            git_info="$git_info*"
        fi
    fi
fi

model_short=$(echo "$model_display" \
    | sed -E 's/\s*\(1M context\)//I; s/\s*\(200K context\)//I; s/^Claude //; s/ //g')
case "$ctx_size" in
    1000000) ctx_tag="1M" ;;
    200000)  ctx_tag="200K" ;;
    *)       ctx_tag=$(awk "BEGIN {printf \"%dK\", $ctx_size/1000}") ;;
esac
if [ -n "$effort" ]; then
    model_label="${model_short}(${effort},${ctx_tag})"
else
    model_label="${model_short}(${ctx_tag})"
fi

ctx_used_int=$(printf "%.0f" "$ctx_used_pct")
used_tokens=$((total_in + total_out))
if [ "$used_tokens" -ge 1000 ]; then
    used_display=$(awk "BEGIN {printf \"%.1fK\", $used_tokens/1000}")
else
    used_display="$used_tokens"
fi
ctx_color=$(color_for "$ctx_used_int")
ctx_seg=$(printf "\033[%sm%s/%d%%\033[0m" "$ctx_color" "$used_display" "$ctx_used_int")

fmt_limit() {
    local pct=$1 reset=$2 label=$3 tfmt=$4
    [ -z "$pct" ] && return
    local pct_int
    pct_int=$(printf "%.0f" "$pct")
    local col
    col=$(color_for "$pct_int")
    local when=""
    if [ -n "$reset" ]; then
        when=$(TZ=Asia/Tokyo date -d "@$reset" "+$tfmt" 2>/dev/null)
    fi
    if [ -n "$when" ]; then
        printf "  \033[%sm%s\033[0m \033[%sm%d%%\033[0m \033[%sm%s\033[0m" \
            "$C_LABEL" "$label" "$col" "$pct_int" "$C_TIME" "$when"
    else
        printf "  \033[%sm%s\033[0m \033[%sm%d%%\033[0m" \
            "$C_LABEL" "$label" "$col" "$pct_int"
    fi
}
r5_seg=$(fmt_limit "$r5_pct" "$r5_reset" "5h" "%H:%M")
r7_seg=$(fmt_limit "$r7_pct" "$r7_reset" "7d" "%m/%d %H:%M")

status=""
if [ -n "$project" ]; then
    status=$(printf "\033[%sm%s\033[0m" "$C_PROJECT" "$project")
fi
if [ -n "$rel_path" ] && [ "$rel_path" != "~" ]; then
    status="$status$(printf "\033[%sm:%s\033[0m" "$C_PATH" "$rel_path")"
fi
if [ -n "$git_info" ]; then
    status="$status  $(printf "\033[%sm%s\033[0m" "$C_BRANCH" "$git_info")"
fi
status="$status  $(printf "\033[%sm%s\033[0m" "$C_MODEL" "$model_label")"
status="$status  $ctx_seg"
[ -n "$r5_seg" ] && status="$status$r5_seg"
[ -n "$r7_seg" ] && status="$status$r7_seg"

echo -e "$status"
