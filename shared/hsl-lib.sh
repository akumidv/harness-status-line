# harness-status-line: shared bash helpers for the status lines of every harness (sourced, not executed).
# Needs: $now (epoch). No forks on the hot path: only `read` of small files; python runs in the background.
#
# Files (written by statusline-analyze.py):
#   $HSL_STATE/conn-<host>   daily profile of a connection (endpoint host):
#       1 "updated billing coverage_days unit speed_samples"  billing = limited|metered|free, unit = tok|usd
#       2 load matrix L: my usage per hour of the week (tokens, or USD x10000)      168 numbers
#       3 speed matrix S: answer speed vs the models' own median, x100 (100 = usual)  168 numbers
#       4 load modes: 168 digits, 0 light / 1 normal / 2 busy (from S)
#       5 median tokens/s per model "model=tps ..."     6 fit|prelim|- (speed matrix status)
#   $HSL_RT/rt-<host>        collector, every ~5 min: "epoch work_x100 spd_x100 n today unit billing last_response"
#       work = my pace over the current + 2 previous active hours vs my usual for those hours (all sessions);
#       spd = answer speed over the last 15 min vs the models' medians, averaged over models ("-" = no answers)
#   $HSL_RT/act-<host>       epoch of the first response in each clock hour, all sessions of all harnesses
#   $HSL_RT/conn-map         "harness model host" lines (and "harness - host" = its latest connection)
E=$'\033'; R="${E}[0m"
HSL_DIR="$HOME/.local/share/harness-status-line"
HSL_STATE="$HOME/.local/state/harness-status-line"
HSL_AN="$HSL_DIR/statusline-analyze.py"
HSL_RT="/tmp/hsl-$UID"
[ -d "$HSL_RT" ] || mkdir -m 700 "$HSL_RT" 2>/dev/null
if [ ! -O "$HSL_RT" ] || [ -L "$HSL_RT" ]; then HSL_RT="$HSL_STATE/rt"; mkdir -p "$HSL_RT" 2>/dev/null; fi
HSL_COLLECT_TTL=${HSL_COLLECT_TTL:-300}

# hsl_kick [daily]: start the collector in the background every HSL_COLLECT_TTL s; with "daily" also the daily analysis
hsl_kick() {
    [ -f "$HSL_AN" ] || return
    local cl=0 d sd=""
    [ -f "$HSL_RT/collect-last" ] && read -r cl < "$HSL_RT/collect-last"
    if [ $(( now - ${cl:-0} )) -ge "$HSL_COLLECT_TTL" ]; then
        printf '%s\n' "$now" > "$HSL_RT/collect-last"
        ( nice -n 19 python3 -I "$HSL_AN" --collect >/dev/null 2>&1 & )
    fi
    if [ "$1" = daily ]; then
        d=$(( now / 86400 ))
        [ -f "$HSL_STATE/daily" ] && read -r sd < "$HSL_STATE/daily"
        if [ "$sd" != "$d" ]; then
            mkdir -p "$HSL_STATE" && printf '%s\n' "$d" > "$HSL_STATE/daily"
            ( nice -n 19 python3 -I "$HSL_AN" >/dev/null 2>&1 & )
        fi
    fi
}

# hsl_conn HARNESS MODEL -> REPLY = connection host ("" until the collector has seen this harness)
hsl_conn() {
    REPLY=""
    local h m k fb=""
    [ -f "$HSL_RT/conn-map" ] || return
    while read -r h m k; do
        [ "$h" = "$1" ] || continue
        [ "$m" = "$2" ] && { REPLY=$k; return; }
        [ "$m" = - ] && fb=$k
    done < "$HSL_RT/conn-map"
    REPLY=$fb
}

hsl_fx() {   # x100 -> ".4" / "1" / "1.4"
    local v=$(( ($1 + 5) / 10 ))
    if [ $(( v % 10 )) -eq 0 ]; then REPLY=$(( v / 10 ))
    elif [ "$v" -lt 10 ]; then REPLY=".$v"
    else REPLY="$(( v / 10 )).$(( v % 10 ))"; fi
}
hsl_fs() {   # seconds -> 3h05m / 45m / 2d4h
    local s=$1; [ "$s" -lt 0 ] && s=0
    if [ "$s" -ge 86400 ]; then printf -v REPLY '%dd%dh' $(( s / 86400 )) $(( s % 86400 / 3600 ))
    elif [ "$s" -ge 3600 ]; then printf -v REPLY '%dh%02dm' $(( s / 3600 )) $(( s % 3600 / 60 ))
    else printf -v REPLY '%dm' $(( s / 60 )); fi
}
hsl_fk() {   # 1234567 -> 1.2M
    local n=${1%%.*} t
    if [ "$n" -ge 1000000000 ]; then t=$(( (n + 50000000) / 100000000 )); printf -v REPLY '%d.%dG' $(( t / 10 )) $(( t % 10 ))
    elif [ "$n" -ge 1000000 ]; then t=$(( (n + 50000) / 100000 )); printf -v REPLY '%d.%dM' $(( t / 10 )) $(( t % 10 ))
    elif [ "$n" -ge 1000 ]; then printf -v REPLY '%dK' $(( (n + 500) / 1000 ))
    else REPLY=$n; fi
}

# hsl_rt HOST: HSL_REL / HSL_SPD / HSL_TODAY / HSL_UNIT / HSL_BILL from the collector ("" when missing or stale > 15 min)
hsl_rt() {
    HSL_REL=""; HSL_SPD=""; HSL_TODAY=""; HSL_UNIT=""; HSL_BILL=""
    local f="$HSL_RT/rt-$1" t rel spd n today unit bill last
    [ -f "$f" ] || return 1
    read -r t rel spd n today unit bill last < "$f"
    [ $(( now - ${t:-0} )) -le 900 ] || return 1
    [ "$rel" != - ] && HSL_REL=$rel
    [ "$spd" != - ] && HSL_SPD=$spd
    HSL_TODAY=$today; HSL_UNIT=$unit; HSL_BILL=$bill
}

# hsl_spd_col X100 -> REPLY = colour of a speed ratio (inverted vs burn: slow = red, fast = green)
hsl_spd_col() {
    if [ "$1" -lt 95 ]; then REPLY="${E}[31m"; elif [ "$1" -gt 105 ]; then REPLY="${E}[32m"; else REPLY="${E}[2m"; fi
}

# Mode block order everywhere: [burn icon burn× time-to-next-burn-mode] work× [speed icon spd× t/s [session]]
HSL_ICON=('🐇' "${E}[33m▸$R" '🐌')                       # speed: 0 light (faster) / 1 normal / 2 busy (slower)

# hsl_spd HOST SPEED_TEXT [MODEL TPS] -> REPLY = speed group, e.g. "🐌.8× 52 [48]"
#   icon + spd× = how fast the answers are NOW vs the model's usual (its median t/s over 42 days):
#   1) TPS of this session over the last 5 min ÷ the median of MODEL (live, every refresh);
#   2) else the collector: all sessions on the endpoint over the last 15 min;
#   3) else nothing to compare: "⚡SPEED_TEXT". The hour-of-week matrix is not shown (decided 2026-11-10). HSL_V = spd x100.
hsl_spd() {
    REPLY=""; HSL_V=""
    local P="$HSL_STATE/conn-$1" l5="" v="" cur bc kv
    case "$4" in ''|*[!0-9]*) ;; *)
        [ -n "$3" ] && [ -f "$P" ] && { read -r _; read -r _; read -r _; read -r _; read -r l5; } < "$P"
        for kv in $l5; do
            [ "${kv%%=*}" = "$3" ] || continue
            kv=${kv#*=}; kv=${kv%%.*}
            [ "${kv:-0}" -gt 0 ] 2>/dev/null && v=$(( $4 * 100 / kv ))
            break
        done ;;
    esac
    hsl_rt "$1"
    [ -z "$v" ] && v=$HSL_SPD
    case "$v" in ''|*[!0-9]*) v="" ;; esac
    if [ -z "$v" ]; then [ -n "$2" ] && REPLY="${E}[33m⚡$R$2"; return; fi
    HSL_V=$v
    if [ "$v" -lt 92 ]; then cur=2; elif [ "$v" -gt 108 ]; then cur=0; else cur=1; fi
    hsl_spd_col "$v"; bc=$REPLY; hsl_fx "$v"
    REPLY="${HSL_ICON[$cur]}$bc$REPLY×$R${2:+ $2}"
}

# hsl_seg HOST SPEED_TEXT [MODEL TPS] -> REPLY = mode block of a connection WITHOUT limits (no burn):
#   work× 🐌spd× t/s [session]      e.g.  .3× 🐌.9× 52 [102]
#   work = my pace over the current + 2 previous active hours vs my usual (all sessions); cyan <.75, red >1.5
#   (>1.25 while the endpoint is slow)
#   HSL_USE = today's usage on this connection, all harnesses: "day Σ3.2M" (tokens) or "day $1.23" (metered)
hsl_seg() {
    REPLY=""; HSL_USE=""
    local sp seg="" pc red
    [ -f "$HSL_STATE/conn-$1" ] || return 1
    hsl_spd "$1" "$2" "$3" "$4"; sp=$REPLY
    if [ -n "$HSL_REL" ]; then
        hsl_fx "$HSL_REL"; red=150; [ -n "$HSL_V" ] && [ "$HSL_V" -lt 95 ] && red=125
        if [ "$HSL_REL" -lt 75 ]; then pc="${E}[96m"; elif [ "$HSL_REL" -gt "$red" ]; then pc="${E}[31m"; else pc=""; fi
        seg="$pc$REPLY×$R"
    fi
    [ -n "$sp" ] && seg="${seg:+$seg }$sp"
    if [ -n "$HSL_TODAY" ]; then
        if [ "$HSL_UNIT" = usd ]; then HSL_USE="${E}[2mday \$$HSL_TODAY$R"; else hsl_fk "$HSL_TODAY"; HSL_USE="${E}[2mday Σ$REPLY$R"; fi
    fi
    REPLY=$seg
    [ -n "$seg" ]
}
