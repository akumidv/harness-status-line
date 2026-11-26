# Connections: limited vs. unlimited (load) models, all harnesses

Decided with the user on 2026-11-10. It extends `requirements.md` (terms `work`/`burn`, the limited Claude model) to
every harness and every endpoint.

## 1. Connection and billing
A **connection** is the API endpoint a harness talks to, keyed by host (`api.anthropic.com`, the host from the
harness config, `opencode.ai`). Several harnesses can share one connection (now Qwen Code, mcode, OpenCode and pi go
through one endpoint), and its load is a property of the endpoint, not of the harness: it is measured over all of them together.

| billing | what it is | mode block shows | usage segment |
|---|---|---|---|
| `limited` | subscription with 5h / weekly limits | burn group, `work`, speed group | `5h %`, `wk %` + forecast |
| `metered` | pay per token, price > 0 | `work`, speed group | `day $1.23` |
| `free` | no limits, no price (Qwen Code, mcode, free tier) | `work`, speed group | `day Σ3.2M` tokens |

How the billing is decided (first match wins):
1. Override in `~/.local/state/harness-status-line/config.json` (the old `~/.config/harness-status-line/connections.json` is still read when it is missing): `{"<host>": {"billing": "free|metered|limited"}}`
   (no keys or secrets there, only the host and the billing type).
2. The status line: Claude Code passes `rate_limits` → `limited`. No `rate_limits` (API key, Bedrock, another
   `ANTHROPIC_BASE_URL`) → the load branch of the connection.
3. The analyzer: `api.anthropic.com` with a fresh `wk%` history → `limited`; any reported cost > 0 (mcode
   `cost_usd`, OpenCode `cost`, pi `usage.cost.total`, the Claude price table) → `metered`; else `free`.
   A zero price is not trusted alone: an explicit price wins, and a future price in the harness config switches the
   connection to `metered` without code changes.

### Several Claude accounts (decided 2026-11-25)
A Claude Code profile started with `CLAUDE_CONFIG_DIR=~/.claude-<name>` (e.g. `claude-personal`) may be a **different
Anthropic account** with its own limits, so it is its own connection: `api.anthropic.com_claude-<name>`, with its own
folder in `~/.local/state/harness-status-line/` (limit axis `history`, `tokens`, `profile`, `daily`, `wkreset`, ... as
for `api.anthropic.com/`, which stays the default account `~/.claude`; nothing is migrated). Rules:
- **Discovery is automatic**: `~/.claude` and every `~/.claude-*` that has a `projects/` folder (no list to maintain).
- **The status line picks the account from `transcript_path`** (`~/.claude-<name>/projects/...`, else `CLAUDE_CONFIG_DIR`)
  and only reads and writes that account's files and tmpfs caches; two accounts never adopt each other's limits (`lim`).
- **The analyzer** runs every account in turn (`statusline-analyze.py`, or one: `--account <key>`, which is what a status
  line passes); the collector (`--collect`) scans all of them (state: `collect-state.json` for the default account,
  `collect-state.<key>.json` for the others). `imports/` (other machines' transcripts) belong to the default account only.
- A new account starts with an empty history: `~` (preliminary) values for the first ~14 days, like the default one did.

## 2. Sources (the collector reads all of them, read-only)
| harness | where | tokens | speed (duration) |
|---|---|---|---|
| Claude Code (terminal **and VS Code extension**) | `~/.claude/projects/**/*.jsonl`, `~/.claude-<name>/projects/**/*.jsonl` | usage | response end − previous entry |
| Qwen Code | `~/.qwen/usage/token-usage-YYYY-MM.jsonl` | in/out | `apiDurationMs` |
| mcode | `~/.minimax/v2/sqlite/runtime-state.sqlite` → `local_runtime_token_usage` | in/out/cache | — |
| OpenCode | `~/.local/share/opencode/opencode.db` → `message` | tokens, cost | `time.completed − time.created` |
| pi | `~/.pi/agent/sessions/**/*.jsonl` | usage, cost | entry time − message time |

The host comes from the harness config (`baseUrl` / `baseURL` lines only — Qwen `settings.json`, mcode
`config.yaml`, OpenCode `opencode.jsonc`, pi `models.json`); known providers without a URL map to their public host.

## 3. Activity of all sessions (`work`)
`work` needs to know which hours were active. Before, only the Claude status line wrote that log, so the VS Code
extension (no status line there) was invisible. Now the **collector** (`statusline-analyze.py --collect`, started in
the background by any status line at most every 5 min, ≈0.1 s) writes `act-<host>` from the logs of **all** sessions of
all harnesses: the epoch of the first response in every clock hour, last 26 h.
- Claude (`limited`): `calc_rel` merges its own `act` with `act-api.anthropic.com`. The `wk%` step is measured from
  the last history sample, which may be older than the 3-hour window when no status line ran (work only in the
  extension) — the expected usage is therefore summed over every active second **from that sample** to now.
- `free`/`metered`: the collector computes `work` itself: units (tokens or USD) over the current + 2 previous active
  hours ÷ my usual units **per active hour** (14 days, idle hours excluded; `<host>/conn` line 1, field 6) for those
  seconds; pseudo-observation K = 0.1 × that (6 usual minutes). Decided 2026-11-12, replaces the load matrix with the
  `REL_FLOOR` (an hour-of-week matrix averages idle weeks in, so ordinary work read as 2–17×).

- **Frozen limits** (decided 2026-11-10): Claude Code takes `rate_limits` from the headers of the session's own
  responses, so an idle session keeps showing the `5h%`/`wk%` of its last response while other sessions work (seen:
  `wk` 22% for 3 h of parallel work, then 26%, `work` .2×). When the session's last response is older than 10 min the
  status line first takes the limits of another session (decided 2026-11-11): every fresh session publishes its
  `5h%`/`wk%` to `/tmp/claude-sl-$UID/lim` (`epoch five_x100 r5e wk_x100 rwe`, the max per window); a stale one adopts
  them if they are < 10 min old, newer than its own last response and of the same weekly window (= the same account),
  and is then fresh again (no `≥`, `work` from `wk%`). Only without such a neighbour (work only in the extension) it
  marks the limits `≥22%`, writes nothing to the history and takes `work` from the collector (tokens of all sessions
  vs the load matrix).
- **History hygiene** (2026-11-11): two sessions with different `5h%` made the history flip every minute. Now a
  sample is not written for an older window or a lower `%` within the same window (weekly and 5h alike; `hist-last`
  keeps `5h_x100 r5e`); a 5h window whose reset has passed is unknown (`-` in the history, not shown, not a change).

The status line is not needed where the work is done: the extension's sessions count, the status line of any
terminal shows the result.

## 4. Speed and load (`spd`, speed icon)
- Per (connection, model): the **median tokens/s** over 42 days (responses ≥ 50 output tokens and ≥ 1 s).
- **Speed matrix**: for every response the ratio to its model's median (clipped 0.2–5), pooled over models,
  averaged per hour of the week with 4 pseudo-responses at 1. Models on different servers have different speeds; the
  ratio to their own median makes them comparable, so the matrix is the averaged load of the endpoint and works for any
  set of models or paid endpoints.
- **Load modes** (only for unlimited connections): `🐌` busy (index < 0.92), `▸` normal, `🐇` light (> 1.08);
  time to the next mode as for Claude. Status `fit` (≥ 300 responses, ≥ 14 days) / `prelim` (≥ 30) / `-` (all normal).
- **`spd`** (decided 2026-11-17, replaces the 2026-11-10 "live ratio only"): the coefficient and the icon are **this hour of
  the week's** own values from the daily profile: the speed index (line 3) and its load mode (line 4); an hour with fewer
  than 5 responses has `.` in line 4 and shows a dim dot instead of the icon, no number. The live t/s of the session stays
  next to it (`125 [126]`). The live ratio (t/s of this session over the last 5 min ÷ the median of its model; else the
  mean over the responses of the last 15 min on the connection, collector) is only kept for `work` colouring (orange > 1.25
  while it is < 0.95) and as the fallback when there is no hour matrix; without both nothing is compared (`⚡`). Colours
  inverted vs `burn`: orange < 0.95 (slower; a warning, red is only for peak `burn` and the forecast), green > 1.05. Measured 2026-11-17: the speed index by hour is flat (sd 0.05) and
  does not correlate with the burn factor of the hour (r = −0.06; mean speed eco 99.8, normal 100.5, peak 98.0).
- **Speed group** (every connection, Claude too): load icon `🐌` (spd < 0.92) / `▸` / `🐇` (> 1.08) + `spd×` +
  tokens/s [session] of this session; it replaces the old `⚡` (kept only when there is no load data at all). Speed is
  never mixed into `burn`: burn = limit % per token cost, speed = server load.

## 5. Display
Order (decided 2026-11-10): **burn group** (only with limits) → **`work`** → **speed group**. The time to the next mode
follows the burn group (Claude with limits only).
```
Claude, limited:   🔥~.9× 2h45m .8× 🐌.8× 52 [48] · 5h 16%↑ 3h36m · wk 13%↑ 6d12h [→81%]
Claude, no limits: .8× ▸1.1× 115 [99] · day Σ5.8M
Qwen Code:         1.5× 🐌.9× – [115] · day Σ528K · Σ 900K→12K · main
mcode:             1.5× 🐌.9× · day Σ528K                (custom-command item, ≤ 60 columns, no speed in its stdin)
```
`work` colours as for Claude (cyan < 0.75, orange > 1.5, or > 1.25 while the endpoint is busy, `spd` < 0.95).

## 6. Files
| File | What |
|---|---|
| `~/.local/share/harness-status-line/statusline-analyze.py` | the one analyzer (daily + `--collect`) |
| `~/.local/share/harness-status-line/hsl-lib.sh` | bash helpers sourced by every status line (`hsl_kick`, `hsl_conn`, `hsl_seg`, `hsl_rt`) |
| `~/.local/state/harness-status-line/<host>/conn` | daily profile: 1 `updated billing coverage unit samples`; 2 load matrix (tokens or USD×10⁴); 3 speed matrix ×100; 4 load modes (168 chars: 0 light, 1 normal, 2 busy, `.` no data); 5 `model=tps`; 6 `fit/prelim/-` |
| `~/.local/state/harness-status-line/connections.json` | summary for inspection |
| `~/.local/state/harness-status-line/collect-state.json` | collector state (Claude transcript offsets, last 26 h) |
| `/tmp/hsl-$UID/rt-<host>` | `epoch work spd n today unit billing last_response` |
| `/tmp/hsl-$UID/act-<host>`, `conn-map` | activity hours; `harness model host` |
| `~/.minimax/statusline-command.sh` | mcode command (`tui.customStatusLine`, item `custom-command` in `tui.statusLine`) |

mcode notes (0.7.6, read from its bundle): `tui.statusLine` is a list of built-in items — setting it replaces the
defaults, so the fragment lists the defaults + `custom-command`. The command is run **without a shell** and `~` is
not expanded, hence `bash -c 'exec bash "$HOME/..."'`. stdin: `{protocol, event, session_id, workspace_dir, model,
session_title, tui_version}`; inline output is cut to 60 columns; `intervalSeconds` ≥ 10.
