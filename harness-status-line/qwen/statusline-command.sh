#!/bin/bash
# Qwen Code status line — the closest match to ~/.claude/statusline-command.sh.
# Order: model | worktree | context | mode: work× 🐌spd× time t/s [session] | session use | git branch | cwd
# The load mode comes from the shared library (~/.local/share/harness-status-line/hsl-lib.sh): the endpoint has no
# limits, so instead of the limit burn speed it shows spd = how fast the models answer vs their usual speed.
#
# Qwen feeds the same shape of JSON on stdin (session_id, model.display_name,
# context_window, workspace.current_dir, git.branch, worktree.name, metrics) but
# not every Claude field exists here. Each place where a segment had to be
# rebuilt from another source is marked "QWEN:".
#
# Runs under node's child_process.exec with a 5s timeout and a 10KB buffer,
# so the transcript is read through tail, never whole (sessions reach 26MB).
input=$(cat)
jq_in() { printf '%s' "$input" | jq -r "$1" 2>/dev/null; }
# Transcript tail to scan for the last-5-minutes rate. Records carry the full
# response text, so ~4MB covers a few hundred responses and parses in ~80ms —
# far inside the 5s exec timeout, and never a full read of a 26MB session.
TAIL_BYTES=4000000

segments=()
printf -v now '%(%s)T' -1
HSL_OK=""
[ -f "$HOME/.local/share/harness-status-line/hsl-lib.sh" ] && . "$HOME/.local/share/harness-status-line/hsl-lib.sh" && HSL_OK=1
[ -n "$HSL_OK" ] && hsl_kick daily      # collector every 5 min + the daily profile (shared with the other harnesses)

# --- model + reasoning effort ---------------------------------------------
model=$(jq_in '.model.display_name // empty')
# QWEN: the payload carries no effort level (Claude has .effort.level). /effort
# writes model.reasoningEffort to settings, and project settings win over user.
effort=""
cwd=$(jq_in '.workspace.current_dir // .cwd // empty')
for cfg in "$cwd/.qwen/settings.json" "$HOME/.qwen/settings.json"; do
    [ -f "$cfg" ] || continue
    v=$(jq -r '.model.reasoningEffort // empty' "$cfg" 2>/dev/null)
    if [ -n "$v" ]; then effort="$v"; break; fi
done
if [ -n "$model" ]; then
    if [ -n "$effort" ]; then
        segments+=("$(printf '\033[36m%s %s\033[0m' "$model" "$effort")")
    else
        segments+=("$(printf '\033[36m%s\033[0m' "$model")")
    fi
fi

# --- worktree, when present (no agent name in Qwen's payload) --------------
wt=$(jq_in '.worktree.name // empty')
[ -n "$wt" ] && segments+=("$(printf '\033[96mwt:%s\033[0m' "$wt")")

# --- context window usage (tokens, no fractions) --------------------------
used_pct=$(jq_in '.context_window.used_percentage // empty')
window_size=$(jq_in '.context_window.context_window_size // empty')
# QWEN: current_usage is a plain integer (last prompt's tokens), not Claude's
# usage object. Accept either so the script survives both hosts.
used_tokens=$(jq_in '(.context_window.current_usage // empty)
    | if type == "object"
      then ((.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0))
      else . end')
if { [ -z "$used_tokens" ] || [ "$used_tokens" = "0" ]; } && [ -n "$used_pct" ] && [ -n "$window_size" ]; then
    used_tokens=$(awk -v p="$used_pct" -v s="$window_size" 'BEGIN{printf "%.0f", p*s/100}')
fi
fmt_k() {
    awk -v n="$1" 'BEGIN{
        if (n >= 1000000) { s = sprintf("%.1f", n/1000000); sub(/\.0$/, "", s); printf "%sM", s }
        else if (n >= 1000) printf "%.0fK", n/1000
        else printf "%d", n
    }'
}
if [ -n "$used_pct" ]; then
    pct=$(awk -v p="$used_pct" 'BEGIN{printf "%.0f", p}')
    if [ -n "$used_tokens" ] && [ -n "$window_size" ]; then
        ctx="ctx $(fmt_k "$used_tokens")/$(fmt_k "$window_size")"
    else
        ctx="ctx ${pct}%"
    fi
    segments+=("$(printf '\033[35m%s\033[0m' "$ctx")")
    ctx_added=1
fi

# --- the session transcript, located by session_id ------------------------
# QWEN: the payload has no transcript_path (Claude does). sessionId is the file
# name, so a glob over project dirs finds it without rebuilding the slug.
sid=$(jq_in '.session_id // empty')
transcript=""
if [ -n "$sid" ]; then
    for cand in "$HOME"/.qwen/projects/*/chats/"$sid".jsonl; do
        [ -f "$cand" ] && { transcript="$cand"; break; }
    done
fi

# --- prompt-cache share (last response) -----------------------------------
# QWEN: Claude shows time left on the Anthropic 5m/1h cache TTL. There is no
# such TTL on an OpenAI-compatible endpoint — what the gateway reports back is
# a hit ratio, so that is what this slot carries.
if [ -n "$transcript" ]; then
    cache_pair=$(tail -c "$TAIL_BYTES" "$transcript" 2>/dev/null | jq -Rsr '
        split("\n")
        | map(fromjson? | select(.subtype == "ui_telemetry") | .systemPayload.uiEvent
              | select(."event.name" == "qwen-code.api_response")
              | {cached: (.cached_content_token_count // 0), inp: (.input_token_count // 0)})
        | map(select(.inp > 0))
        | if length == 0 then empty
          else [ (map(.cached) | add), (map(.inp) | add) ] | @tsv end' 2>/dev/null)
    if [ -n "$cache_pair" ]; then
        c_cached=${cache_pair%%$'\t'*}
        c_inp=${cache_pair##*$'\t'}
        c_pct=$(awk -v a="$c_cached" -v b="$c_inp" 'BEGIN{if (b>0) printf "%.0f", a*100/b}')
        if [ -n "$c_pct" ] && [ "$c_pct" -gt 0 ]; then
            if [ "$c_pct" -ge 50 ]; then c_col='\033[32m'; else c_col='\033[33m'; fi
            cache_seg="$(printf "${c_col}⛁%s%%\033[0m" "$c_pct")"
        fi
    fi
fi
if [ -n "$cache_seg" ]; then
    if [ -n "$ctx_added" ]; then segments[${#segments[@]}-1]+=" $cache_seg"; else segments+=("$cache_seg"); fi
fi

# --- output speed, tokens/sec: last 5 min [whole session] -----------------
# QWEN: better than the Claude version on both halves. The transcript logs
# generation_ms per response (Claude has to infer a duration from entry
# timestamps), and the payload sums latency per model in metrics.models[].
if [ -n "$transcript" ]; then
    tps_pair=$(tail -c "$TAIL_BYTES" "$transcript" 2>/dev/null | jq -Rsr '
        def ts: ((.[0:19] + "Z") | fromdateiso8601) + ((.[20:23] | tonumber) / 1000);
        def rate: if length == 0 then null else ((map(.out) | add) * 1000 / (map(.ms) | add) | round) end;
        split("\n")
        | map(fromjson? | select(.subtype == "ui_telemetry") | .systemPayload.uiEvent
              | select(."event.name" == "qwen-code.api_response")
              | {end: (."event.timestamp" // ""), out: .output_token_count, ms: .generation_ms})
        | map(select(.out != null and .ms != null and .out >= 50 and .ms >= 1000 and .end != ""))
        | map(.endt = (.end | ts))
        | (map(select(.endt >= now - 300)) | rate) as $w
        | (rate) as $s
        | if $w == null and $s == null then empty else [$w, $s] | @tsv end' 2>/dev/null)
    tps_w=${tps_pair%%$'\t'*}
    tps_s=${tps_pair##*$'\t'}
    [ "$tps_w" = "null" ] && tps_w=""
    [ "$tps_s" = "null" ] && tps_s=""
else
    tps_w=""; tps_s=""
fi
# Prefer the host's own totals for the session figure: the tail is a window, not
# the whole session.
tps_live=$(jq_in '[.metrics.models // {} | to_entries[] | {o: .value.tokens.completion, l: .value.api.total_latency_ms}]
    | map(select(.o != null and .l != null and .o > 0 and .l > 0))
    | if length == 0 then empty else ((map(.o) | add) * 1000 / (map(.l) | add) | round) end')
[ -n "$tps_live" ] && tps_s="$tps_live"
spd_txt=""
if [ -n "$tps_w" ] || [ -n "$tps_s" ]; then
    spd_txt="$(printf '\033[33m%s\033[0m \033[2m[%s]\033[0m' "${tps_w:-–}" "${tps_s:-–}")"
fi
# --- mode block (no limits, so no burn): work× 🐌spd× t/s [session]
#     speed icon: 🐌 answers slower than usual / ▸ normal / 🐇 faster (all harnesses on the same endpoint)
mode_ok=""
if [ -n "$HSL_OK" ]; then
    hsl_conn qwen "${model// /_}"
    if [ -n "$REPLY" ] && hsl_seg "$REPLY" "$spd_txt"; then
        seg=$REPLY; [ -n "$HSL_USE" ] && seg="$seg $(printf '\033[2m·\033[0m') $HSL_USE"
        segments+=("$seg"); mode_ok=1
    fi
fi
[ -z "$mode_ok" ] && [ -n "$spd_txt" ] && segments+=("$(printf '\033[33m⚡\033[0m')$spd_txt $(printf '\033[33mt/s\033[0m')")

# --- session use: tokens burned + lines changed ---------------------------
# QWEN: Claude's 5h/weekly rate-limit slots have no analogue here — a gateway
# key has no subscription window. These two payload fields are what Qwen does
# count for the session. Comment this block out if the line gets too busy.
tok_in=$(jq_in '.context_window.total_input_tokens // empty')
tok_out=$(jq_in '.context_window.total_output_tokens // empty')
if [ -n "$tok_in" ] && [ "$tok_out" ]; then
    seg=$(printf '\033[2mΣ %s→%s\033[0m' "$(fmt_k "$tok_in")" "$(fmt_k "$tok_out")")
    add=$(jq_in '.metrics.files.total_lines_added // 0')
    del=$(jq_in '.metrics.files.total_lines_removed // 0')
    if [ "${add:-0}" != "0" ] || [ "${del:-0}" != "0" ]; then
        seg="$seg $(printf '\033[32m+%s\033[0m\033[31m−%s\033[0m' "$add" "$del")"
    fi
    segments+=("$seg")
fi

# --- git branch, with [default] marker ------------------------------------
if [ -n "$cwd" ] && git -C "$cwd" --no-optional-locks rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    branch=$(jq_in '.git.branch // empty')
    [ -z "$branch" ] && branch=$(git -C "$cwd" --no-optional-locks branch --show-current 2>/dev/null)
    if [ -n "$branch" ]; then
        default_branch=$(git -C "$cwd" --no-optional-locks symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')
        if [ -z "$default_branch" ]; then
            for candidate in main master; do
                if git -C "$cwd" --no-optional-locks show-ref --verify --quiet "refs/heads/$candidate"; then
                    default_branch="$candidate"
                    break
                fi
            done
        fi
        if [ -n "$default_branch" ] && [ "$branch" = "$default_branch" ]; then
            segments+=("$(printf '\033[32m%s\033[0m \033[2m[default]\033[0m' "$branch")")
        else
            segments+=("$(printf '\033[33m%s\033[0m' "$branch")")
        fi
    fi
elif [ -n "$cwd" ]; then
    segments+=("$(printf '\033[2mno git\033[0m')")
fi

# --- cwd, abbreviated with ~, max 25 chars --------------------------------
if [ -n "$cwd" ]; then
    case "$cwd" in
        "$HOME") disp="~" ;;
        "$HOME"/*) disp="~${cwd#"$HOME"}" ;;
        *) disp="$cwd" ;;
    esac
    max=20      # boundary window: a directory touched by the last 20 chars is kept whole
    hard=25     # absolute cap on the displayed length
    if [ "${#disp}" -gt "$max" ]; then
        rest="$disp"; suffix=""
        while [ -n "$rest" ] && [ "${#suffix}" -lt "$max" ]; do
            comp="${rest##*/}"
            if [ "$rest" = "$comp" ]; then rest=""; else rest="${rest%/*}"; fi
            if [ -z "$suffix" ]; then suffix="$comp"; else suffix="$comp/$suffix"; fi
        done
        if [ -n "$rest" ]; then
            disp="…/$suffix"
        elif [ "${disp:0:1}" = "/" ]; then
            disp="/$suffix"
        else
            disp="$suffix"
        fi
    fi
    if [ "${#disp}" -gt "$hard" ]; then
        disp="…${disp: -$((hard - 1))}"
    fi
    segments+=("$(printf '\033[34m%s\033[0m' "$disp")")
fi

# --- join and print (Qwen keeps the first 2 non-empty lines) ---------------
out=""
for seg in "${segments[@]}"; do
    if [ -z "$out" ]; then
        out="$seg"
    else
        out="$out $(printf '\033[2m·\033[0m') $seg"
    fi
done
printf '%s' "$out"
