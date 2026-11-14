#!/usr/bin/env bash
# hsl-import.sh <host> [remote projects dir]
# Copies another machine's Claude Code transcripts (same account) for the analyzer, then reruns it:
#   rsync <host>:.claude/projects/ -> ~/.local/state/harness-status-line/imports/<name>/   (only *.jsonl, never deletes)
# <host> is anything ssh/rsync accepts: user@address (address = name or IP) or an alias from ~/.ssh/config; a non-default
# port: RSYNC_RSH="ssh -p 2222". One connection. The import name is the parameter itself (user@address -> user_address,
# an alias -> the alias; non-word characters -> "_", the same name the analyzer uses: ~/.local/state/harness-status-line/api.anthropic.com/tokens@<name>),
# so use the same form for a machine every time.
set -euo pipefail
host=${1:?usage: hsl-import.sh <user@address | ssh alias> [remote projects dir, default .claude/projects]}
src=${2:-.claude/projects}
name=${host//[^A-Za-z0-9_]/_}
dst="$HOME/.local/state/harness-status-line/imports/$name"
mkdir -p "$dst"
rsync -a --prune-empty-dirs --include='*/' --include='*.jsonl' --exclude='*' "$host:${src%/}/" "$dst/"
echo "imported: $host -> $dst ($(find "$dst" -name '*.jsonl' | wc -l) transcripts)"
A="$(dirname "$(readlink -f "$0")")/statusline-analyze.py"
nice -n 19 python3 -I "$A" > /dev/null
python3 -I - "$HOME/.local/state/harness-status-line/api.anthropic.com/profile.json" "$name" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
print('analyzer:', json.dumps((d.get('imports') or {}).get(sys.argv[2]) if isinstance(d.get('imports'), dict) else d.get('imports')))
print('calibration:', d.get('calibration'))
o = d.get('outside_use') or {}
print('outside use:', o.get('status'), o.get('flagged_hours_utc (excess 5h points)'))
EOF
