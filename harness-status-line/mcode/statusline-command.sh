#!/bin/bash
# MiniMax Code (mcode) status line: the "custom-command" item of tui.statusLine, set in tui.customStatusLine.
# mcode passes one JSON line on stdin: {protocol, event, session_id, workspace_dir, model, session_title, tui_version}
# - no tokens, no speed, no limits. Everything below therefore comes from the shared collector, which reads the logs of
# all harnesses on the same connection (mcode's own token usage is in ~/.minimax/v2/sqlite).
# Output: one line, mcode shows at most ~60 columns:   1.2× 🐌.8× · day Σ3.2M
IFS= read -r -d '' input
printf -v now '%(%s)T' -1
[ -f "$HOME/.local/share/harness-status-line/hsl-lib.sh" ] || exit 0
. "$HOME/.local/share/harness-status-line/hsl-lib.sh"
hsl_kick daily
model=""
[[ $input =~ \"model\":\"([^\"]+)\" ]] && model=${BASH_REMATCH[1]}
model=${model##*/}                                  # custom_provider:<provider>/<model> -> <model>
hsl_conn mcode "${model// /_}"
[ -n "$REPLY" ] && hsl_seg "$REPLY" "" || exit 0
out=$REPLY
[ -n "$HSL_USE" ] && out="$out ${E}[2m·$R $HSL_USE"
printf '%s' "$out"
