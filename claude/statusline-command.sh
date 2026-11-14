#!/bin/bash
# Claude Code status line.
# Order: model | agent/worktree | context+cache | mode: burn time work speed | 5h | wk [burnout] | git | cwd
#   mode block: 🔥burn× time-to-next-burn-mode  work×  🐌spd× t/s [session]   (speed icon = endpoint load)
# Without rate_limits (API key, another endpoint) there is no burn: work× 🐌spd× t/s [session]
# from the shared library (~/.local/share/harness-status-line/hsl-lib.sh), plus today's usage instead of 5h/wk.
#
# Cheap by design:
#  * runtime cache lives in tmpfs (/tmp/claude-sl-UID, RAM); durable data in ~/.local/state/harness-status-line/api.anthropic.com (one folder per connection)
#  * the finished line is cached OUT_TTL seconds per session -> update bursts cost ~1 ms
#  * the transcript is read incrementally (only bytes added since last time)
#  * one jq on stdin, everything else is bash arithmetic
IFS= read -r -d '' input   # builtin, no cat process
printf -v now '%(%s)T' -1
E=$'\033'; R="${E}[0m"   # ANSI escape / reset, so segments need no printf subshells
OUT_TTL=${OUT_TTL:-15}

# --- runtime cache dir (tmpfs); fall back to ~/.local/state/harness-status-line/cache if /tmp is unusable ---
RT="/tmp/claude-sl-$UID"
[ -d "$RT" ] || mkdir -m 700 "$RT" 2>/dev/null
if [ ! -O "$RT" ] || [ -L "$RT" ]; then RT="$HOME/.local/state/harness-status-line/cache"; mkdir -p "$RT"; fi
CACHE_DIR="$RT"

# --- whole-line cache per session ------------------------------------------
sid="x"
[[ $input =~ \"session_id\":\"([^\"]+)\" ]] && sid=${BASH_REMATCH[1]//[^A-Za-z0-9_-]/}
OUT_FILE="$RT/out-$sid"
if [ -f "$OUT_FILE" ]; then
    { read -r ot; read -r oline; } < "$OUT_FILE"
    if [ $(( now - ${ot:-0} )) -lt "$OUT_TTL" ] && [ -n "$oline" ]; then printf '%s' "$oline"; exit 0; fi
fi

# --- shared library (all harnesses): collector of all sessions, connection profiles ---
HSL_OK=""
[ -f "$HOME/.local/share/harness-status-line/hsl-lib.sh" ] && . "$HOME/.local/share/harness-status-line/hsl-lib.sh" && HSL_OK=1
[ -n "$HSL_OK" ] && hsl_kick          # collector every 5 min: activity of ALL Claude sessions (VS Code extension too)
ANALYZER="$HOME/.local/share/harness-status-line/statusline-analyze.py"
# durable data of the Claude account (limits, profile, ...): the folder of its connection (provider) api.anthropic.com
HSL_ANTH="$HOME/.local/state/harness-status-line/api.anthropic.com"
[ -d "$HSL_ANTH" ] || mkdir -p "$HSL_ANTH"

# --- schedule constants (UTC) ------------------------------------------------
# peak = weekdays PEAK_START..PEAK_END; eco = weekends + weekdays before ECO_END; else normal
PEAK_LAST_DOW=5; PEAK_START=13; PEAK_END=19; ECO_END=8
HISTORY="$HSL_ANTH/history"   # LIMIT axis: "epoch wk%x100 5h%x100 weekReset 5hReset", on every whole-% change + 30 min heartbeat, kept forever (durable; token axis = .statusline-tokens)
PROFILE="$HSL_ANTH/profile"       # line 2: my load matrix (168 hours of the week, 0.001% of the weekly limit); line 4: burn-speed matrix (168, x100); line 6: fit|prelim; line 7: my use per ACTIVE hour, 0.001% (statusline-analyze.py)
WKRESET="$HSL_ANTH/wkreset"     # "weekly_reset_epoch epoch wk_x100": the limits were reset by hand inside that weekly window (wk% dropped >= RESET_DROP), possibly only partly (e.g. one model); wk_x100 = the value right after it
RESET_DROP=300                                   # x100: a wk% drop this big within one weekly window = manual reset (history has 1-point backward steps = noise, ignored)
DAILY="$HSL_ANTH/daily"       # line1: day stamp; line2: weekly burn pace over 7 days, %/h x100 or "-"

# --- once per UTC day: first run turns the history into a weekly burn pace -----
days=$(( now / 86400 ))
daily_day=""; w_pace="-"
[ -f "$DAILY" ] && { read -r daily_day _; read -r w_pace; } < "$DAILY"
if [ "$daily_day" != "$days" ]; then
    find "$CACHE_DIR" -type f -mtime +1 -delete 2>/dev/null
    w_pace="-"
    if [ -f "$HISTORY" ]; then
        # pace = weekly % consumed over the last 7 days / hours covered (a drop with a new weekly reset time = new week: count the new value; the same reset time = limits reset by hand: nothing used)
        w_pace=$(awk -v now="$now" '
            $1 >= now-604800 { if (n++) { cons += ($2 >= w0) ? $2-w0 : ($4 == r0 ? 0 : $2) } else t1 = $1; w0 = $2; r0 = $4 }
            END { span = (now - t1)/3600; if (n > 1 && span >= 24) printf "%d", cons/span + 0.5; else printf "-" }' "$HISTORY")
        [ -z "$w_pace" ] && w_pace="-"
    fi
    printf '%s %(%F)T\n%s\n' "$days" "$now" "$w_pace" > "$DAILY"
fi

# cache_fresh CACHEFILE TRANSCRIPT MIN_AGE: valid if newer than the transcript,
# or younger than MIN_AGE seconds (line 1 of a cache file = epoch it was written)
cache_fresh() {
    [ -f "$1" ] || return 1
    [ "$1" -nt "$2" ] && return 0
    local t; read -r t < "$1"
    [ $(( now - ${t:-0} )) -lt "$3" ]
}

# --- single jq pass over the input ----------------------------------------
IFS=$'\t' read -r model model_id effort agent wt used_pct window_size used_tokens transcript cwd \
    five_pct five_x100 five_reset seven_pct seven_x100 seven_reset < <(
    printf '%s' "$input" | jq -r '
    def r: if . == null then "-" else round end;
    def r100: if . == null then "-" else (. * 100 | floor) end;
    [ (.model.display_name // "-"), (.model.id // "-"), (.effort.level // "-"), (.agent.name // "-"), (.worktree.name // "-"),
      (.context_window.used_percentage | r), (.context_window.context_window_size // "-"),
      ((.context_window.current_usage // null) | if . then ((.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0)) else 0 end),
      (.transcript_path // "-"), (.workspace.current_dir // .cwd // "-"),
      (.rate_limits.five_hour.used_percentage | r), (.rate_limits.five_hour.used_percentage | r100), (.rate_limits.five_hour.resets_at // "-"),
      (.rate_limits.seven_day.used_percentage | r), (.rate_limits.seven_day.used_percentage | r100), (.rate_limits.seven_day.resets_at // "-")
    ] | map(tostring) | @tsv' 2>/dev/null)
for v in model model_id effort agent wt used_pct window_size transcript cwd five_pct five_x100 five_reset seven_pct seven_x100 seven_reset; do
    [ "${!v}" = "-" ] && printf -v "$v" ''
done

segments=(); spd_txt=""

# --- model + effort -------------------------------------------------------
if [ -n "$model" ]; then
    if [ -n "$effort" ]; then
        segments+=("${E}[36m$model $effort$R")
    else
        segments+=("${E}[36m$model$R")
    fi
fi
[ -n "$agent" ] && segments+=("${E}[96magent:$agent$R")
[ -n "$wt" ] && segments+=("${E}[96mwt:$wt$R")

# --- helpers (results in $REPLY, no subshells) ------------------------------
fmt_k() {
    local n=$1 t
    if [ "$n" -ge 1000000 ]; then
        t=$(( (n + 50000) / 100000 ))
        if [ $(( t % 10 )) -eq 0 ]; then printf -v REPLY '%dM' $(( t / 10 )); else printf -v REPLY '%d.%dM' $(( t / 10 )) $(( t % 10 )); fi
    elif [ "$n" -ge 1000 ]; then printf -v REPLY '%dK' $(( (n + 500) / 1000 ))
    else printf -v REPLY '%d' "$n"; fi
}
fmt_secs() {   # 3h20m / 45m / 2d4h
    local s=$1
    [ "$s" -lt 0 ] && s=0
    if [ "$s" -ge 86400 ]; then printf -v REPLY '%dd%dh' $(( s / 86400 )) $(( s % 86400 / 3600 ))
    elif [ "$s" -ge 3600 ]; then printf -v REPLY '%dh%02dm' $(( s / 3600 )) $(( s % 3600 / 60 ))
    else printf -v REPLY '%dm' $(( s / 60 )); fi
}
to_epoch() {
    case "$1" in
        ''|*[!0-9]*) REPLY=$(date -d "$1" +%s 2>/dev/null) ;;
        *) REPLY=$1 ;;
    esac
}
fmt_until() {  # compact time until epoch/ISO $1; hours rounded up beyond a day
    to_epoch "$1"; local ts=$REPLY left hrs
    [ -z "$ts" ] && { REPLY=""; return; }
    left=$(( ts - now )); [ "$left" -lt 0 ] && left=0
    if [ "$left" -ge 86400 ]; then
        hrs=$(( (left + 3599) / 3600 )); printf -v REPLY '%dd%dh' $(( hrs / 24 )) $(( hrs % 24 ))
    elif [ "$left" -ge 3600 ]; then printf -v REPLY '%dh%02dm' $(( left / 3600 )) $(( left % 3600 / 60 ))
    else printf -v REPLY '%dm' $(( left / 60 )); fi
}

# --- context window usage -------------------------------------------------
cache_seg=""; ctx_added=""
if [ -n "$used_pct" ]; then
    if { [ -z "$used_tokens" ] || [ "$used_tokens" = "0" ]; } && [ -n "$window_size" ]; then
        used_tokens=$(( used_pct * window_size / 100 ))
    fi
    if [ -n "$used_tokens" ] && [ -n "$window_size" ]; then
        fmt_k "$used_tokens"; a=$REPLY; fmt_k "$window_size"; ctx="ctx $a/$REPLY"
    else
        ctx="ctx ${used_pct}%"
    fi
    segments+=("${E}[35m$ctx$R")
    ctx_added=1
fi

# --- transcript stats, INCREMENTAL: prompt-cache time left + output speed -----
# State file: line1 = when updated, line2 = byte offset already processed, line3 = summary
# "w5min session lastResponseEnd lastAssistantTs cacheTTL", line4 = running state (JSON).
# Only bytes added since line2 are parsed; refreshed at most every 20s.
tr_summary=""
if [ -n "$transcript" ] && [ -f "$transcript" ]; then
    tf="$CACHE_DIR/tr-${transcript##*/}"
    if cache_fresh "$tf" "$transcript" 20; then
        { read -r _; read -r _; read -r tr_summary; } < "$tf"
    else
        off=0; st_json=null; tr_summary=""
        [ -f "$tf" ] && { read -r _; read -r off; read -r tr_summary; read -r st_json; } < "$tf"
        size=$(stat -c %s "$transcript" 2>/dev/null || echo 0)
        if [ "${off:-0}" -gt "$size" ]; then off=0; st_json=null; fi           # file replaced/truncated
        if [ "${off:-0}" -eq 0 ] && [ "$size" -gt 8388608 ]; then off=$(( size - 8388608 )); fi   # bound first scan
        if [ "$size" -gt "${off:-0}" ]; then
            out=$(tail -c +$(( off + 1 )) "$transcript" | head -c $(( size - off )) | jq -nRr --argjson st "${st_json:-null}" '
                def ts: (.[0:19] + "Z" | fromdateiso8601) + (((.[20:23] | tonumber?) // 0) / 1000);
                def fin: if .open != null and .open.out >= 50 and (.open.end - .open.start) >= 1
                         then .tot_out += .open.out | .tot_dur += (.open.end - .open.start)
                              | .recent += [{e: .open.end, o: .open.out, d: (.open.end - .open.start)}] | .recent |= .[-60:]
                         else . end;
                ($st // {tot_out: 0, tot_dur: 0, open: null, prev: null, recent: [], h1: false, last: null}) as $s0
                | reduce (inputs | fromjson? | select((.type == "user" or .type == "assistant") and (.isSidechain | not))) as $x ($s0;
                    ($x.timestamp | ts) as $t
                    | if $x.type == "assistant" and $x.message.usage.output_tokens != null then
                        $x.message.id as $id
                        | (($x.message.usage.cache_creation.ephemeral_1h_input_tokens // 0) > 0) as $h
                        | .h1 = (.h1 or $h) | .last = $t
                        | if .open != null and .open.id == $id
                          then .open.end = $t | .open.out = ([.open.out, $x.message.usage.output_tokens] | max)
                          else fin | .open = {id: $id, start: (.prev // $t), end: $t, out: $x.message.usage.output_tokens} end
                        | .prev = $t
                      else .prev = $t end)
                | . as $s
                | ($s.open | if . != null and .out >= 50 and (.end - .start) >= 1 then {e: .end, o: .out, d: (.end - .start)} else null end) as $po
                | ($s.recent + (if $po then [$po] else [] end)) as $rec
                | ($s.tot_out + ($po.o // 0)) as $to | ($s.tot_dur + ($po.d // 0)) as $td
                | ($rec | map(select(.e >= now - 300))) as $win
                | (if $td > 0 then ($to / $td | round) else "-" end) as $sess
                | (if ($win | length) > 0 then (($win | map(.o) | add) / ($win | map(.d) | add) | round) else "-" end) as $w
                | ([$w, $sess, (($rec | last | .e) // 0 | floor), (($s.last // 0) | floor), (if $s.h1 then 3600 else 300 end), (if ($rec | length) > 0 then ($rec | last | (.o / .d) | round) else "-" end)] | map(tostring) | join(" ")),
                  ($s | tojson)' 2>/dev/null)
            if [ -n "$out" ]; then
                { read -r tr_summary; read -r st_json; } <<< "$out"
                printf '%s\n%s\n%s\n%s\n' "$now" "$size" "$tr_summary" "$st_json" > "$tf"
            fi
        else
            printf '%s\n%s\n%s\n%s\n' "$now" "${off:-0}" "$tr_summary" "${st_json:-null}" > "$tf"
        fi
    fi
fi
if [ -n "$tr_summary" ]; then
    read -r tps_w tps_s tps_end c_epoch c_ttl _ <<< "$tr_summary"
    # activity log: per clock hour, the epoch of the first assistant response of this machine in it; the pace window counts from there
    if [ "${c_epoch:-0}" -gt 0 ]; then
        ah=$(( c_epoch / 3600 )); af="$CACHE_DIR/act"; seen=""
        [ -f "$af" ] && while read -r x; do [ "$x" -ge 10000000 ] && x=$(( x / 3600 )); [ "$x" = "$ah" ] && seen=1; done < "$af"
        if [ -z "$seen" ]; then
            printf '%s\n' "$c_epoch" >> "$af"                          # epoch of the first response seen in this clock hour
            [ "$(wc -l < "$af")" -gt 220 ] && { tail -n 170 "$af" > "$af.t" && mv "$af.t" "$af"; }   # ~a week of hours
        fi
    fi
    # prompt-cache time left (TTL 1h if the session uses 1h cache writes, else 5m)
    if [ "${c_epoch:-0}" -gt 0 ]; then
        c_left=$(( c_ttl - (now - c_epoch) ))
        if [ "$c_left" -le 0 ]; then
            cache_seg="${E}[31m◷ ✗$R"
        else
            if [ "$c_left" -ge 60 ]; then c_str="$((c_left / 60))m"; else c_str="${c_left}s"; fi
            if [ $((c_left * 4)) -lt "$c_ttl" ]; then c_col="${E}[33m"; else c_col="${E}[32m"; fi
            cache_seg="${c_col}◷ $c_str$R"
        fi
    fi
    if [ -n "$cache_seg" ]; then
        if [ -n "$ctx_added" ]; then segments[${#segments[@]}-1]+=" $cache_seg"; else segments+=("$cache_seg"); fi
    fi
    # output speed, tokens/sec: last 5 min [whole session] - shown at the end of the mode block, after the speed
    # icon of the endpoint load (hsl_spd: 🐌 slower than usual / ▸ normal / 🐇 faster, all sessions over 15 min).
    if [ "$tps_s" != "-" ]; then
        { [ "$tps_w" = "-" ] || [ $(( now - ${tps_end:-0} )) -gt 300 ]; } && tps_w="–"
        spd_txt="${E}[33m$tps_w$R ${E}[2m[$tps_s]$R"
    fi
fi

# --- load mode (UTC schedule) + burn coefficient, right after the speed -----
# Shows: mode icon, burn coefficient, then time until the NEXT mode in that mode's dimmed colour.
mode_at() {   # $1 epoch -> MODE: 0 eco, 1 normal, 2 peak
    local d=$(( $1 / 86400 )) h=$(( $1 % 86400 / 3600 )) dow
    dow=$(( (d + 3) % 7 + 1 ))
    if [ "$dow" -le "$PEAK_LAST_DOW" ] && [ "$h" -ge "$PEAK_START" ] && [ "$h" -lt "$PEAK_END" ]; then MODE=2
    elif [ "$dow" -gt "$PEAK_LAST_DOW" ] || [ "$h" -lt "$ECO_END" ]; then MODE=0
    else MODE=1; fi
}
M_ICON=("${E}[96m❄$R" "${E}[32m◆$R" '🔥')
M_COL=("${E}[96m" "${E}[32m" "${E}[31m")
M_DIM=("${E}[2;96m" "${E}[2;32m" "${E}[2;31m")
# The mode only changes at schedule boundaries, so it is computed once and cached together with
# the epoch of the next change; it is recomputed only when that moment passes.
mf="$CACHE_DIR/mode"; mn=0; cur_mode=""; next_mode=""
[ -f "$mf" ] && read -r mn cur_mode next_mode < "$mf"
# a fresher profile may carry shifted mode boundaries (line 5: peak start, peak end, eco end, UTC hours)
if [ -z "$next_mode" ] || [ "$now" -ge "${mn:-0}" ] || [ "$PROFILE" -nt "$mf" ]; then
    if [ -f "$PROFILE" ]; then
        { read -r _; read -r _; read -r _; read -r _; read -r b1 b2 b3; } < "$PROFILE"
        case "$b1$b2$b3" in ''|*[!0-9]*) ;; *) PEAK_START=$b1; PEAK_END=$b2; ECO_END=$b3 ;; esac
    fi
    mode_at "$now"; cur_mode=$MODE
    t=$(( (now / 3600 + 1) * 3600 ))
    for (( i = 0; i < 200; i++ )); do
        mode_at "$t"; [ "$MODE" -ne "$cur_mode" ] && break
        t=$(( t + 3600 ))
    done
    next_mode=$MODE; mn=$t
    printf '%s %s %s\n' "$mn" "$cur_mode" "$next_mode" > "$mf"
fi
until_next=$(( mn - now ))

# reset times (cheap, also used by the weekly forecast below)
# wstart = start of the weekly accounting window: the reset - 7d, or a later manual reset of the limits (WKRESET);
# ew = seconds of it elapsed. Everything "since the weekly reset" below counts from wstart.
# wbase = the wk% right after that reset (0 after a full one, >0 after a partial one): the use of the week counts above it.
r5e=""; rwe=""; ew=0; wstart=0; wbase=0; wkr_rw=0; wkr_t=0; wkr_w=0
[ -f "$WKRESET" ] && read -r wkr_rw wkr_t wkr_w < "$WKRESET"
case "$wkr_rw$wkr_t${wkr_w:-0}" in ''|*[!0-9]*) wkr_rw=0; wkr_t=0; wkr_w=0 ;; esac
set_wstart() {
    ew=0; wbase=0; wstart=$(( rwe - 604800 ))
    [ "$wkr_rw" = "$rwe" ] && [ "$wkr_t" -gt "$wstart" ] && { wstart=$wkr_t; wbase=${wkr_w:-0}; }
    [ "$rwe" -gt "$now" ] && ew=$(( now - wstart )); [ "$ew" -lt 0 ] && ew=0
}
if [ -n "$five_reset" ]; then to_epoch "$five_reset"; r5e=$REPLY; fi
if [ -n "$seven_reset" ]; then
    to_epoch "$seven_reset"; rwe=$REPLY
    [ -n "$rwe" ] && set_wstart
fi

# history sample: whenever the whole-% of the 5h or weekly limit changes (pins every step to the minute -> best data
# for the burn-factor analysis), plus a heartbeat every 30 min
# rate_limits come from the headers of THIS session's own responses: while it is idle (the other sessions, e.g. in the
# VS Code extension, keep working) they stay frozen at its last response. lim_stale = older than 10 min: not written to
# the history (a frozen wk% would read as "no use") and work falls back to the collector (tokens of all sessions).
lim_stale=""; [ "${c_epoch:-0}" -gt 0 ] && [ $(( now - c_epoch )) -gt 600 ] && lim_stale=1
# a response from before a manual reset of the limits carries the old (higher) %: stale, it adopts the shared fresh values instead
[ "$wkr_rw" = "${rwe:-0}" ] && [ "${c_epoch:-0}" -gt 0 ] && [ "$c_epoch" -lt "$wkr_t" ] && lim_stale=1
# an expired 5h window (its reset already passed): the % belongs to the previous window -> unknown
if [ -n "$r5e" ] && [ "$r5e" -le "$now" ]; then five_pct=""; five_x100=""; five_reset=""; r5e=""; fi
# Fresh limits shared between sessions (same account = same weekly reset): "epoch five_x100 r5e wk_x100 rwe".
# A fresh session publishes its limits (the max per window, if another fresh session reported more); a stale one
# adopts them when they are younger than 10 min and newer than its own last response -> it is fresh again.
LIM="$CACHE_DIR/lim"
if [ -n "$seven_x100" ] && [ -n "$rwe" ]; then
    lt=0; l5=""; l5r=""; lw=""; lwr=""; [ -f "$LIM" ] && read -r lt l5 l5r lw lwr < "$LIM"
    case "$lt$lw$lwr" in ''|*[!0-9]*) lt=0 ;; esac; case "$l5$l5r" in ''|*[!0-9]*) l5=""; l5r="" ;; esac
    # own session idle: the usage poll (statusline-analyze.py --collect -> lim-poll, same format) fills in when it is
    # newer than the shared one; a fresh own session never looks at it, and without poll data nothing changes
    if [ -n "$lim_stale" ] && [ -n "$HSL_OK" ] && [ -f "$HSL_RT/lim-poll" ]; then
        read -r pt p5 p5r pw_ pwr < "$HSL_RT/lim-poll"
        case "$pt$pw_$pwr" in ''|*[!0-9]*) ;; *)
            if [ "$pt" -gt "${lt:-0}" ]; then lt=$pt; l5=$p5; l5r=$p5r; lw=$pw_; lwr=$pwr; case "$l5$l5r" in ''|*[!0-9]*) l5=""; l5r="" ;; esac; fi ;;
        esac
    fi
    lfresh=""; [ $(( now - ${lt:-0} )) -le 600 ] && [ -n "$lw" ] && lfresh=1
    if [ -z "$lim_stale" ]; then
        p5=${five_x100:--}; p5r=${r5e:--}; pw=$seven_x100
        if [ -n "$lfresh" ] && [ "$lwr" = "$rwe" ]; then
            [ "$lw" -gt "$pw" ] && pw=$lw
            [ -n "$l5" ] && [ "$p5" != - ] && [ "$l5r" = "$p5r" ] && [ "$l5" -gt "$p5" ] && p5=$l5
            [ -n "$l5" ] && [ "$p5r" = - ] && [ "$l5r" -gt "$now" ] && { p5=$l5; p5r=$l5r; }
        fi
        printf '%s %s %s %s %s\n' "$now" "$p5" "$p5r" "$pw" "$rwe" > "$LIM"
    elif [ -n "$lfresh" ] && [ "$lt" -gt "${c_epoch:-0}" ] && { [ "$lwr" = "$rwe" ] || { [ "$rwe" -le "$now" ] && [ "$lwr" -gt "$now" ]; }; }; then
        seven_x100=$lw; seven_pct=$(( (lw + 50) / 100 )); rwe=$lwr; seven_reset=$lwr
        set_wstart
        if [ -n "$l5" ] && [ "$l5r" -gt "$now" ]; then
            five_x100=$l5; five_pct=$(( (l5 + 50) / 100 )); r5e=$l5r; five_reset=$l5r
        fi
        lim_stale=""
    fi
fi
if [ -n "$seven_x100" ] && [ -z "$lim_stale" ]; then
    # an idle session keeps passing the rate_limits of its last response: within one window the % never drops, so an
    # older window or a lower value than the last sample is stale and is not written (it made the history flip
    # between sessions); hist-last: "epoch 5h% wk% rwe wk_x100 5h_x100 r5e"
    hl=0; hf=""; hw=""; hr=""; hx=0; h5=""; h5r=""
    [ -f "$CACHE_DIR/hist-last" ] && read -r hl hf hw hr hx h5 h5r < "$CACHE_DIR/hist-last"
    case "$hr$hx" in *[!0-9]*) hr=0; hx=0 ;; esac; case "$h5$h5r" in ''|*[!0-9]*) h5=""; h5r="" ;; esac
    hok=1; wk_reset=""
    if [ "${rwe:-0}" -lt "${hr:-0}" ]; then hok=""
    elif [ "${rwe:-0}" = "$hr" ] && [ "$seven_x100" -lt "${hx:-0}" ]; then
        # a fresh response far below the last sample in the same weekly window: the limits were reset by hand
        if [ $(( hx - seven_x100 )) -ge "$RESET_DROP" ]; then wk_reset=1; else hok=""; fi
    fi
    if [ -n "$five_x100" ] && [ -n "$r5e" ] && [ -n "$h5r" ] && [ -z "$wk_reset" ]; then
        if [ "$r5e" -lt "$h5r" ] || { [ "$r5e" = "$h5r" ] && [ "$five_x100" -lt "$h5" ]; }; then hok=""; fi
    fi
    if [ -n "$hok" ]; then
        # an unknown 5h (expired window) is not a change; hist-last keeps the last known 5h for the checks above
        if [ $(( now - ${hl:-0} )) -ge 1800 ] || [ "$seven_pct" != "$hw" ] || { [ -n "$five_pct" ] && [ "$five_pct" != "$hf" ]; }; then
            printf '%s %s %s %s %s\n' "$now" "$seven_x100" "${five_x100:--}" "${rwe:-0}" "${r5e:--}" >> "$HISTORY"
            if [ -n "$wk_reset" ]; then      # the week now counts from the response that showed it (older responses = stale)
                wkr_rw=$rwe; wkr_t=$now; wkr_w=$seven_x100; [ "${c_epoch:-0}" -gt 0 ] && wkr_t=$c_epoch
                printf '%s %s %s\n' "$wkr_rw" "$wkr_t" "$wkr_w" > "$WKRESET"
                set_wstart
            fi
            [ -n "$five_x100" ] && [ -n "$r5e" ] && { hf=$five_pct; h5=$five_x100; h5r=$r5e; }
            printf '%s %s %s %s %s %s %s\n' "$now" "${hf:--}" "$seven_pct" "${rwe:-0}" "$seven_x100" "${h5:--}" "${h5r:--}" > "$CACHE_DIR/hist-last"
        fi
    fi
fi
# token-cost profile (python, background, low priority): once a day, or hourly while it is missing
run_an=""
[ "$daily_day" != "$days" ] && run_an=1
if [ ! -f "$PROFILE" ]; then
    al=0; [ -f "$CACHE_DIR/analyze-last" ] && read -r al < "$CACHE_DIR/analyze-last"
    [ $(( now - ${al:-0} )) -ge 3600 ] && run_an=1
fi
if [ -n "$run_an" ] && [ -f "$ANALYZER" ]; then
    printf '%s\n' "$now" > "$CACHE_DIR/analyze-last"
    ( nice -n 19 python3 -I "$ANALYZER" >/dev/null 2>&1 & )
fi
# burn coefficient = pace vs an even spend of the limit; the worse of 5h and weekly.
# Recomputed on any change of 5h%/wk% and at least every REL_TTL seconds; served from the cache in between.
# Burnout epoch of the weekly limit from the token-cost profile: walk the remaining half-hour slots,
# adding the expected use of each until the remaining % is gone. bo = epoch, 0 = lasts past the reset,
# "-" = no profile / no calibration yet.
# Two matrices from the profile file (written daily by the analyzer): line 2 = MY LOAD per hour of the week (0.001% of the
# weekly limit at the normal burn rate), line 4 = LIMIT BURN SPEED per hour of the week (x100, normal = 100).
# Expected use of hour i = load[i] * speed[i] / 100.
# MY PACE (rel, x100) = actual use of the weekly limit / expected, over the current clock hour and the two before it
# expected = MY USUAL USE PER ACTIVE HOUR (profile line 7: idle hours excluded, so 1 = "as I usually work") x burn speed S;
# matrix 1 (L, averaged over idle hours too) is the base only without line 7 and for the forecast.
# (nothing older, nothing before the weekly reset): only ACTIVE hours count (an assistant response in that hour, from the "act" log), so the first hour
# of work is judged on its own and an idle hour does not dilute it. Actual = wk% change since the window start
# (history). A pseudo-observation of 1% at pace 1 damps the 1%-step noise. No data: week-to-date fallback.
# Frozen rate_limits (lim_stale, this session idle > 10 min): the collector's work instead - tokens of ALL sessions.
load_matrix() {   # fills L[] and S[] (168 each) and AH (line 7, "" if absent); returns 1 if there is no usable profile
    local l2 l4 l7
    { read -r _; read -r l2; read -r _; read -r l4; read -r _; read -r _; read -r l7; } < "$PROFILE"
    AH=""; case "$l7" in ''|*[!0-9]*) ;; *) [ "$l7" -gt 0 ] && AH=$l7 ;; esac
    [ -n "$l2" ] && [ "${l2%% *}" != "-" ] || return 1
    read -ra L <<< "$l2"; [ "${#L[@]}" -eq 168 ] || return 1
    S=(); [ -n "$l4" ] && [ "${l4%% *}" != "-" ] && read -ra S <<< "$l4"
    [ "${#S[@]}" -eq 168 ] || { local i; S=(); for (( i = 0; i < 168; i++ )); do S[i]=100; done; }
    return 0
}
calc_rel() {
    [ -n "$seven_x100" ] && [ -n "$rwe" ] && [ "$rwe" -gt "$now" ] && [ -f "$PROFILE" ] || return
    local i h got dur exp t0 ts w f r wk0 ts0 K=1000 x wh af AH b
    local -A A
    local -a L S
    local st e hh
    if [ -n "$lim_stale" ] && [ -n "$HSL_OK" ]; then hsl_rt api.anthropic.com; rel=$HSL_REL; return; fi
    load_matrix || return
    h=$(( now / 3600 )); got=0; exp=0; t0=$now
    wh=$(( (wstart + 3599) / 3600 ))                                # first whole hour after the weekly reset (or the manual one): older activity belongs to the previous week
    # activity of this machine: the status line's own log + the collector's log of ALL sessions (VS Code extension, ...)
    for af in "$CACHE_DIR/act" ${HSL_OK:+"$HSL_RT/act-api.anthropic.com"}; do
        [ -f "$af" ] || continue
        while read -r x; do
            if [ "$x" -ge 10000000 ]; then hh=$(( x / 3600 )); else hh=$x; x=$(( x * 3600 )); fi
            [ -z "${A[$hh]}" ] || [ "$x" -lt "${A[$hh]}" ] && A[$hh]=$x
        done < "$af"
    done
    for (( i = 0; i <= 2; i++ )); do              # window: current hour and the two before it, active only
        hh=$(( h - i ))
        [ "$hh" -ge "$wh" ] && [ -n "${A[$hh]}" ] || continue
        st=${A[$hh]}; [ "$st" -lt $(( hh * 3600 )) ] && st=$(( hh * 3600 ))
        if [ "$i" -eq 0 ]; then e=$now; else e=$(( (hh + 1) * 3600 )); fi
        dur=$(( e - st )); [ "$dur" -le 0 ] && continue     # proportional: seconds of activity, not whole hours
        got=$(( got + dur )); t0=$st
    done
    if [ "$got" -ge 120 ] && [ -f "$HISTORY" ]; then
        wk0=""; ts0=""
        while read -r ts w f r _; do
            [ "${r:-0}" = "$rwe" ] && [ "$ts" -ge "$wstart" ] || continue     # samples from before a manual reset are another baseline
            [ "$ts" -le "$t0" ] && [ "$ts" -ge $(( t0 - 86400 )) ] && { wk0=$w; ts0=$ts; }   # wk% at the window start = last sample at or before it
        done < <(tail -n 200 "$HISTORY")
        if [ -n "$wk0" ] && [ "$seven_x100" -ge "$wk0" ]; then
            # the wk% step covers everything since that sample (it may be older than the window when no status line
            # ran, e.g. work only in the VS Code extension): expected = every ACTIVE second from the sample to now
            [ "$ts0" -lt $(( wh * 3600 )) ] && ts0=$(( wh * 3600 ))
            for (( hh = ts0 / 3600; hh <= h; hh++ )); do
                [ -n "${A[$hh]}" ] || continue
                st=${A[$hh]}; [ "$st" -lt $(( hh * 3600 )) ] && st=$(( hh * 3600 )); [ "$st" -lt "$ts0" ] && st=$ts0
                if [ "$hh" -eq "$h" ]; then e=$now; else e=$(( (hh + 1) * 3600 )); fi
                dur=$(( e - st )); [ "$dur" -le 0 ] && continue
                b=${AH:-${L[hh % 168]}}                       # my usual active-hour use (else the matrix)
                exp=$(( exp + b * S[hh % 168] / 100 * dur / 3600 ))
            done
            rel=$(( ((seven_x100 - wk0) * 10 + K) * 100 / (exp + K) )); return
        fi
    fi
    exp=0                                                             # week-to-date
    for (( i = wstart / 3600 + 1; i < h; i++ )); do exp=$(( exp + L[i % 168] * S[i % 168] / 100 )); done
    exp=$(( exp + L[h % 168] * S[h % 168] / 100 * (now % 3600) / 3600 ))
    [ "$exp" -ge 1000 ] && rel=$(( ((seven_x100 - wbase) * 10 + K) * 100 / (exp + K) ))
}
# Burnout epoch of the weekly limit: only the REMAINDER of the matrices is walked (from now to the reset), each hour
# = load * speed * my pace rel; adds the expected use until the remaining % is gone. bo = epoch, 0 = lasts past
# the reset (proj = projected % at the reset), "-" = no profile / no calibration yet.
calc_bo() {
    bo="-"; proj="-"
    [ -n "$seven_x100" ] && [ -n "$rwe" ] && [ "$rwe" -gt "$now" ] && [ -f "$PROFILE" ] || return
    local rem cum per t slot frac k=${rel:-100} AH
    local -a L S
    [ "$k" -lt 30 ] && k=30; [ "$k" -gt 400 ] && k=400
    load_matrix || return
    rem=$(( (10000 - seven_x100) * 10 ))
    [ "$rem" -le 0 ] && { bo=$now; return; }
    slot=$(( now / 3600 )); frac=$(( 3600 - now % 3600 ))
    cum=$(( L[slot % 168] * S[slot % 168] / 100 * k / 100 * frac / 3600 ))
    [ "$cum" -ge "$rem" ] && { bo=$(( now + frac )); return; }
    t=$(( (slot + 1) * 3600 ))
    while [ "$t" -lt "$rwe" ]; do
        i=$(( (t / 3600) % 168 ))
        per=$(( L[i] * S[i] / 100 * k / 100 ))
        if [ $(( cum + per )) -ge "$rem" ]; then
            bo=$(( t + (rem - cum) * 3600 / (per > 0 ? per : 1) )); return
        fi
        cum=$(( cum + per )); t=$(( t + 3600 ))
    done
    bo=0; proj=$(( (seven_x100 * 10 + cum) / 1000 ))
}
calc_burn() {
    b_mult=""
    if [ -n "$five_x100" ] && [ -n "$r5e" ] && [ "$r5e" -gt "$now" ]; then
        el=$(( 18000 - (r5e - now) ))
        [ "$el" -ge 600 ] && b_mult=$(( five_x100 * 180 / el ))
    fi
    if [ -n "$seven_x100" ] && [ "$ew" -ge 21600 ]; then
        mw=$(( (seven_x100 - wbase) * 6048 / ew ))
        { [ -z "$b_mult" ] || [ "$mw" -gt "$b_mult" ]; } && b_mult=$mw
    fi
}
# Two kinds of values in the burn cache:
#  - CURRENT (from the actual limits): work rel, b_mult, the wk forecast bo/proj. Recomputed on any change of 5h%/wk%
#    and at least every REL_TTL s (between % steps the pace falls with time; new active hours come from the collector).
#  - HISTORY (from the daily profile): burn factors per mode + fit/prelim. Re-read only when the profile is newer.
REL_TTL=300
bf="$CACHE_DIR/burn"; bt=0; bk=""; b_mult=""; rel=""; bo="-"; proj="-"; mfe="-"; mfn="-"; mfp="-"; pre="-"
[ -f "$bf" ] && { read -r bt; read -r bk b_mult rel bo proj pre mfe mfn mfp; } < "$bf"
bkey="${r5e:-x}:${five_x100}:${seven_x100}:${lim_stale:-0}"
if [ "$bk" != "$bkey" ] || [ $(( now - ${bt:-0} )) -ge "$REL_TTL" ] || { [ -f "$PROFILE" ] && [ "$PROFILE" -nt "$bf" ]; }; then
    b_mult=""; rel=""
    calc_burn; calc_rel; calc_bo
    if [ -f "$PROFILE" ] && { [ "$PROFILE" -nt "$bf" ] || [ "$mfn" = "-" ]; }; then
        { read -r _; read -r _; read -r mfe mfn mfp; read -r _; read -r _; read -r pre; } < "$PROFILE"   # mode factors x100 (eco norm peak) + fit/prelim (line 6)
    fi
    printf '%s\n%s %s %s %s %s %s %s %s %s\n' "$now" "$bkey" "${b_mult:--}" "${rel:--}" "$bo" "${proj:--}" "${pre:--}" "${mfe:--}" "${mfn:--}" "${mfp:--}" > "$bf"
fi
[ "$b_mult" = "-" ] && b_mult=""; [ "$rel" = "-" ] && rel=""
seg=${M_ICON[$cur_mode]}
fmt_x() {   # x100 -> ".4" / "1" / "1.4" (no leading zero below 1, no ".0")
    local v=$(( ($1 + 5) / 10 ))
    if [ $(( v % 10 )) -eq 0 ]; then REPLY=$(( v / 10 ))
    elif [ "$v" -lt 10 ]; then REPLY=".$v"
    else REPLY="$(( v / 10 )).$(( v % 10 ))"; fi
}
# burn = how fast the LIMIT burns in this mode (1 = normal; eco/peak measured from history by the analyzer, shown after
# the mode icon), work = my pace vs usual for this time
# Two separate numbers, no combined one (so you can steer: work more while the limit burns slowly, less while fast):
#   work = my pace vs my own average for the same hours (rel; >1 = busier than usual); cyan <.75; red >1.5, or >1.25
#          while the limit burns fast (burn >1.05)
#   burn = how fast the limit burns in the current mode vs normal (1 = normal; "~" = preliminary); green <.95, red >1.05, else dim
mf=""
if [ -n "$rel" ]; then
    case $cur_mode in 0) mf=$mfe ;; 1) mf=$mfn ;; *) mf=$mfp ;; esac
    case "$mf" in ''|-|*[!0-9]*) mf="" ;; esac
fi
# Order: burn icon + burn× + time to the next burn mode | work× (mine) | speed icon + spd× + tokens/s.
# Without burn (no limits): work× 🐌spd× t/s. spd× = this session's t/s (5 min) ÷ the model's median, else all sessions 15 min.
bseg=$seg
if [ -n "$mf" ]; then
    fmt_x "$mf"; tl=""; [ "$pre" = "prelim" ] && tl="~"
    if [ "$mf" -lt 95 ]; then bc="${E}[32m"; elif [ "$mf" -gt 105 ]; then bc="${E}[31m"; else bc="${E}[2m"; fi
    bseg="$bseg$bc$tl$REPLY×$R"
fi
fmt_secs "$until_next"
bseg="$bseg ${M_DIM[$next_mode]}$REPLY$R"
wseg=""
pv=${rel:-$b_mult}                                                       # no profile yet: old even-spend pace
if [ -n "$pv" ]; then
    fmt_x "$pv"; red=150; [ -n "$mf" ] && [ "$mf" -gt 105 ] && red=125
    if [ "$pv" -lt 75 ]; then pc="${E}[96m"; elif [ "$pv" -gt "$red" ]; then pc="${E}[31m"; else pc=""; fi
    wseg="$pc$REPLY×$R"
fi
seg=$bseg; [ -n "$wseg" ] && seg="$seg $wseg"
km=${model_id#claude-}; km=${km%%[[]*}; km=${km%-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]}   # opus-5-5
if [ -n "$HSL_OK" ]; then
    hsl_spd api.anthropic.com "$spd_txt" "$km" "$tps_w"; [ -n "$REPLY" ] && seg="$seg $REPLY"
elif [ -n "$spd_txt" ]; then seg="$seg ${E}[33m⚡$R$spd_txt"; fi
if [ -z "$five_pct$seven_pct" ] && [ -n "$HSL_OK" ]; then
    # no subscription limits in the payload: the load mode of the connection instead (no burn, no limit forecast)
    hsl_conn claude "$km"; hk=${REPLY:-api.anthropic.com}
    if hsl_seg "$hk" "$spd_txt" "$km" "$tps_w"; then seg=$REPLY; [ -n "$HSL_USE" ] && seg="$seg ${E}[2m·$R $HSL_USE"; fi
fi
segments+=("$seg")

# --- rate limits (5h session / 7d weekly) -----------------------------------
# lim_stale (this session idle > 10 min, other sessions may be working): the % are as of its last response -> "≥"
lge=""; [ -n "$lim_stale" ] && lge="≥"
if [ -n "$five_pct" ]; then
    seg="5h $lge${five_pct}%↑"
    fmt_until "$five_reset"; [ -n "$REPLY" ] && seg="$seg $REPLY"
    segments+=("$seg")
fi
if [ -n "$seven_pct" ]; then
    seg="wk $lge${seven_pct}%↑"
    fmt_until "$seven_reset"; [ -n "$REPLY" ] && seg="$seg $REPLY"
    # [time until the weekly limit burns out]. Best: simulate the remaining half-hour slots with the
    # token-cost profile; else remaining % / last-7-days pace; until 24h of stats, this week's pace (~).
    # Red when it burns out before the weekly reset, dim otherwise.
    if [ -n "$seven_x100" ] && [ -n "$rwe" ] && [ "$rwe" -gt "$now" ]; then
        w_rem=$(( 10000 - seven_x100 )); [ "$w_rem" -lt 0 ] && w_rem=0
        s=""; approx=""; proj_txt=""
        if [ -n "$bo" ] && [ "$bo" != "-" ]; then            # token-cost profile (best)
            if [ "$bo" -gt 0 ]; then s=$(( bo - now )); [ "$s" -lt 0 ] && s=0; else s=inf; fi
            [ "$s" = inf ] && [ "${proj:--}" != "-" ] && proj_txt="→${proj}%"
        elif [ "$w_pace" != "-" ]; then
            if [ "$w_pace" -gt 0 ]; then s=$(( w_rem * 3600 / w_pace )); else s=inf; fi
        elif [ "$ew" -ge 1800 ]; then      # no 24h of stats yet: this week's average so far, marked "~"
            approx="~"
            if [ "$seven_x100" -gt "$wbase" ]; then s=$(( w_rem * ew / (seven_x100 - wbase) )); else s=inf; fi
        fi
        if [ -n "$s" ]; then
            if [ "$s" = inf ] || [ "$s" -ge 2592000 ]; then br=${proj_txt:-∞}; approx=""; bcol="${E}[2m"
            else
                fmt_secs "$s"; br=$REPLY
                if [ "$s" -lt $(( rwe - now )) ]; then bcol="${E}[31m"; else bcol="${E}[2m"; fi
            fi
            seg="$seg ${bcol}[$approx$br]$R"
        fi
    fi
    segments+=("$seg")
fi

# --- git branch, with [default] marker (cached 15s per directory) ---------
git_seg=""
if [ -n "$cwd" ]; then
    gf="$CACHE_DIR/git-${cwd//[^A-Za-z0-9]/_}"
    gt=0
    [ -f "$gf" ] && { read -r gt; read -r git_seg; } < "$gf"
    if [ $(( now - ${gt:-0} )) -ge 15 ]; then
        git_seg=""
        if git -C "$cwd" --no-optional-locks rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            branch=$(git -C "$cwd" --no-optional-locks branch --show-current 2>/dev/null)
            if [ -n "$branch" ]; then
                default_branch=$(git -C "$cwd" --no-optional-locks symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)
                default_branch=${default_branch#origin/}
                if [ -z "$default_branch" ]; then
                    for candidate in main master; do
                        if git -C "$cwd" --no-optional-locks show-ref --verify --quiet "refs/heads/$candidate"; then
                            default_branch="$candidate"; break
                        fi
                    done
                fi
                if [ -n "$default_branch" ] && [ "$branch" = "$default_branch" ]; then
                    git_seg="${E}[32m$branch$R ${E}[2m[default]$R"
                else
                    git_seg="${E}[33m$branch$R"
                fi
            fi
        else
            git_seg="${E}[2mno git$R"
        fi
        printf '%s\n%s\n' "$now" "$git_seg" > "$gf"
    fi
    [ -n "$git_seg" ] && segments+=("$git_seg")
fi

# --- cwd, abbreviated with ~, max 25 chars -------------------------------
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
        if [ -n "$rest" ]; then disp="…/$suffix"
        elif [ "${disp:0:1}" = "/" ]; then disp="/$suffix"
        else disp="$suffix"; fi
    fi
    if [ "${#disp}" -gt "$hard" ]; then disp="…${disp: -$((hard - 1))}"; fi
    segments+=("${E}[34m$disp$R")
fi

# --- join, cache, print ----------------------------------------------------
out=""
for seg in "${segments[@]}"; do
    if [ -z "$out" ]; then out="$seg"; else out="$out ${E}[2m·$R $seg"; fi
done
printf '%s\n%s\n' "$now" "$out" > "$OUT_FILE.$$" && mv "$OUT_FILE.$$" "$OUT_FILE"
printf '%s' "$out"
