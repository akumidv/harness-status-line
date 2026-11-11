# AGENTS.md — harness-status-line

The repository stores **only the status lines** of AI harnesses (Claude Code, Qwen Code, Codex, MiniMax Code; OpenCode, pi and the Claude VS Code extension are read as data sources) and the script `harness_status_line.py`, which extracts them from live configs (`extract`) and deploys them back (`deploy`). Details and human-oriented presentation — `README.md`; **requirements, terminology (`work`, `burn`, matrices) and decisions taken — `docs/requirements.md` (read first)**, connections without limits (`spd`, load modes, all-sessions activity) — `docs/connections.md`; architecture, measurements and open questions — `docs/`.

## Rules
- **No secrets.** Configs contain `apiKey` and the like (`~/.minimax/config.yaml`, `~/.qwen/settings.json`). Only status-line fragments go into the repository; `harness_status_line.py` refuses to write content that looks like keys. Do not copy whole configs.
- **The source of truth is the live files** (`~/.claude/statusline-command.sh`, `~/.qwen/statusline-command.sh`, `~/.minimax/statusline-command.sh`, `~/.local/share/harness-status-line/{statusline-analyze.py,hsl-lib.sh}`). Edit them, then `python3 harness_status_line.py extract`, then `status` (should say "in sync"). The user commits; do not run `git init`/commit unless asked.
- **Do not touch or append to session transcripts and harness logs** (`~/.claude/projects/**.jsonl`, `~/.qwen/usage`, the mcode/OpenCode sqlite — opened `mode=ro` only, `~/.pi`). Test on copies in the scratchpad or with `HOME=<fake home>` whose log dirs are symlinks to the real ones (outputs then land in the fake home). Tests must not write to the real caches (`/tmp/claude-sl-$UID`, `/tmp/hsl-$UID`, `~/.claude/.statusline-*`, `~/.local/state/harness-status-line`); clean up after tests.
- The status line must be fast (a single `jq`, no extra forks, cache in tmpfs). Heavy work only in the background `statusline-analyze.py` (`nice 19`, once a day; `--collect` every ≤5 min, ≈0.1 s).
- Modes are shown **approximately**: the goal is not to plan heavy work for peak hours. Do not complicate things for the sake of accuracy.
- The speed `– [session]` when idle >5 min stays as is (the user's decision).

## Commands
```bash
python3 harness_status_line.py status|extract|deploy [--dry-run] [--only shared|claude|qwen|codex|codex-personal|mcode]
A=~/.local/share/harness-status-line/statusline-analyze.py
bash -n ~/.claude/statusline-command.sh && python3 -I $A && python3 -I $A --collect   # check
```
Analyzer: `--wk-x100 N --reset EPOCH` — a test override (does not save calibration, but does write the profile — rerun without it after the test).

## Map
| File | Role |
|---|---|
| `shared/statusline-analyze.py` | the one analyzer → `~/.local/share/harness-status-line/`: Claude cost profile, calibration, mode coefficients and boundaries; per-connection load/speed matrices; `--collect` = activity of all sessions, current `work`/`spd` |
| `shared/hsl-lib.sh` | bash helpers sourced by all status lines (`hsl_kick`, `hsl_conn`, `hsl_seg`) |
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
See `docs/roadmap.md`. Raw axes for any re-analysis: `~/.claude/.statusline-history` (limits) and `~/.claude/.statusline-tokens` (tokens), both kept forever. Main point: there is little real data for fitting modes so far (≥14 days of history from 2026-11-09 are needed); check the status in `~/.claude/.statusline-profile.json` → `burn_factors`.

## Calculation model (short)
two matrices over the 168 hours of the week, once a day (load; burn speed) → **your pace** = actual `wk%` ÷ my usual use per active hour (profile line 7) × burn speed over the current and two previous hours, active only (the `act` journal) → the mode block: `burn` group (icon, burn speed, time to next mode) · `work` (pace) · speed group (load icon, `spd×`, t/s), there is no combined number → the `wk` **forecast** = the remaining profile × pace. More in `README.md`, `docs/architecture.md`.
