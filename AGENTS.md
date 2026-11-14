# AGENTS.md — harness-status-line

The repository stores **only the status lines** of AI harnesses (Claude Code, Qwen Code, Codex, MiniMax Code; OpenCode, pi and the Claude VS Code extension are read as data sources) and the script `harness_status_line.py`, which extracts them from live configs (`extract`) and deploys them back (`deploy`). Details and human-oriented presentation — `README.md`; **requirements, terminology (`work`, `burn`, matrices) and decisions taken — `docs/requirements.md` (read first)**, connections without limits (`spd`, load modes, all-sessions activity) — `docs/connections.md`; architecture, measurements and open questions — `docs/`.

## Rules
- **No secrets.** Configs contain `apiKey` and the like (`~/.minimax/config.yaml`, `~/.qwen/settings.json`). Only status-line fragments go into the repository; `harness_status_line.py` refuses to write content that looks like keys. Do not copy whole configs.
- **Where data lives.** Nothing of ours in a harness's dir (`~/.claude` etc.): durable data is in `~/.local/state/harness-status-line/`, one folder per connection (provider = endpoint host; the Claude account is `api.anthropic.com/`), see `docs/architecture.md` → "Where the data lives". `*.lock` = a stop file (collection stopped), flock mutexes are `*.mutex`.
- **The source of truth is the live files** (`~/.claude/statusline-command.sh`, `~/.qwen/statusline-command.sh`, `~/.minimax/statusline-command.sh`, `~/.local/share/harness-status-line/{statusline-analyze.py,hsl-lib.sh}`). Edit them, then `python3 harness_status_line.py extract`, then `status` (should say "in sync"). The user commits; do not run `git init`/commit unless asked.
- **Do not touch or append to session transcripts and harness logs** (`~/.claude/projects/**.jsonl`, `~/.qwen/usage`, the mcode/OpenCode sqlite — opened `mode=ro` only, `~/.pi`, the imported copies in `~/.local/state/harness-status-line/imports/`). Test on copies in the scratchpad or with `HOME=<fake home>` whose log dirs are symlinks to the real ones (outputs then land in the fake home). Tests must not write to the real caches (`/tmp/claude-sl-$UID`, `/tmp/hsl-$UID`, `~/.local/state/harness-status-line`, incl. its per-connection folders); clean up after tests.
- The status line must be fast (a single `jq`, no extra forks, cache in tmpfs). Heavy work only in the background `statusline-analyze.py` (`nice 19`, once a day; `--collect` every ≤5 min, ≈0.1 s).
- Modes are shown **approximately**: the goal is not to plan heavy work for peak hours. Do not complicate things for the sake of accuracy.
- **Credentials — one exception.** Do not read `~/.claude/.credentials.json`, except `poll_usage()` in `statusline-analyze.py`, which the user approved (2026-11-13) to read `claudeAiOauth.accessToken`/`expiresAt` in memory for the limit poll: never print, log, store or refresh the token; no other code reads it.
- The speed `– [session]` when idle >5 min stays as is (the user's decision).

## Commands
```bash
python3 harness_status_line.py status|extract|deploy [--dry-run] [--only shared|claude|qwen|codex|codex-personal|mcode]
A=~/.local/share/harness-status-line/statusline-analyze.py
bash -n ~/.claude/statusline-command.sh && python3 -I $A && python3 -I $A --collect   # check
```
Analyzer: `--collect` — the one entry for cron and the status lines (collector + usage poll; never polls while a session's limits are fresh or another process polls; after 3 HTTP errors in a row other than 401/403/429 (those are retried: 3 tries with a growing wait, then hourly) or 3 collector crashes it writes a stop file with the reason (`api.anthropic.com/usage-poll-stopped.lock` / `collect-stopped.lock` in `~/.local/state/harness-status-line/`) and sends no more requests until the cause is understood); `--health` — its state and the reasons; `--resume` — remove the stop files. `--burn` — read-only report, does the burn rate (limit %/$) differ by mode / UTC hour / weekday / day (idx 1.00 = average, bootstrap CI over days; then a structured token model with no prices (raw tokens by model × type incl. cache write 5m/1h, model scales × shared type ratios, grid scored by leave-one-day-out, identifiability profile) and the burn per weighted token over time (mode / UTC block / day / each 5h window); from the two raw axes, writes nothing). `--wk-x100 N --reset EPOCH` — a test override (does not save calibration, but does write the profile — rerun without it after the test).

## Map
| File | Role |
|---|---|
| `shared/statusline-analyze.py` | the one analyzer → `~/.local/share/harness-status-line/`: Claude cost profile, calibration, mode coefficients and boundaries; per-connection load/speed matrices; `--collect` = activity of all sessions, current `work`/`spd` |
| `shared/hsl-lib.sh` | bash helpers sourced by all status lines (`hsl_kick`, `hsl_conn`, `hsl_seg`) |
| `shared/hsl-import.sh` | `hsl-import.sh <host>`: rsync another machine's transcripts (same account) into `imports/<host>/`, rerun the analyzer |
| `claude/statusline-command.sh` | the status line itself, reads JSON from stdin |
| `claude/settings.fragment.json` | `statusLine` (`refreshInterval` 60) |
| `qwen/`, `mcode/` | status line scripts + config fragments (`ui.statusLine`; `tui.statusLine` + `tui.customStatusLine`) |
| `codex/codex-personal/` | Codex built-in status line items (main `codex` — empty for now) |

## Documentation map
- `docs/requirements.md` — goal, terms, calculation model, display, where things are in the code, decided/cancelled.
- `docs/connections.md` — limited/metered/free connections, sources of all harnesses, `spd`, load modes, all-sessions activity.
- `docs/architecture.md` — data flow, segments, caches, profile format.
- `docs/measurements.md` — what and how the analyzer measures.
- `docs/roadmap.md` — open questions.

## What remains
See `docs/roadmap.md`. Raw axes for any re-analysis: `~/.local/state/harness-status-line/api.anthropic.com/history` (limits) and `~/.local/state/harness-status-line/api.anthropic.com/tokens` (tokens), both kept forever; other machines: transcript copies in `~/.local/state/harness-status-line/imports/<host>/`, unimported use is flagged as `outside_use` (profile JSON). Main point: there is little real data for fitting modes so far (≥14 days of history from 2026-11-09 are needed); check the status in `~/.local/state/harness-status-line/api.anthropic.com/profile.json` → `burn_factors`.

## Calculation model (short)
two matrices over the 168 hours of the week, once a day (load; burn speed) → **your pace** = actual `wk%` ÷ my usual use per active hour (profile line 7) × burn speed over the current and two previous hours, active only (the `act` journal) → the mode block: `burn` group (icon, burn speed, time to next mode) · `work` (pace) · speed group (load icon, `spd×`, t/s), there is no combined number → the `wk` **forecast** = the remaining profile × pace. More in `README.md`, `docs/architecture.md`.
