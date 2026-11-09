# AGENTS.md — harness-status-line

The repository stores **only the status lines** of AI harnesses (Claude Code, Qwen Code, Codex, MiniMax Code) and the script `harness_status_line.py`, which extracts them from live configs (`extract`) and deploys them back (`deploy`). Details and human-oriented presentation — `README.md`; **requirements, terminology (`work`, `burn`, matrices) and decisions taken — `docs/requirements.md` (read first)**; architecture, measurements and open questions — `docs/`.

## Rules
- **No secrets.** Configs contain `apiKey` and the like (`~/.minimax/config.yaml`, `~/.qwen/settings.json`). Only status-line fragments go into the repository; `harness_status_line.py` refuses to write content that looks like keys. Do not copy whole configs.
- **The source of truth is the live files** (`~/.claude/statusline-command.sh`, `~/.claude/statusline-analyze.py`). Edit them, then `python3 harness_status_line.py extract`, then `status` (should say "in sync"). The user commits; do not run `git init`/commit unless asked.
- **Do not touch or append to session transcripts** (`~/.claude/projects/**.jsonl`). Test on copies in the scratchpad. Tests must not write to the real caches (`/tmp/claude-sl-$UID`, `~/.claude/.statusline-*`); clean up after tests.
- The status line must be fast (a single `jq`, no extra forks, cache in tmpfs). Heavy work only in the background `statusline-analyze.py` (`nice 19`, once a day).
- Modes are shown **approximately**: the goal is not to plan heavy work for peak hours. Do not complicate things for the sake of accuracy.
- The speed `⚡– [session]` when idle >5 min stays as is (the user's decision).

## Commands
```bash
python3 harness_status_line.py status|extract|deploy [--dry-run] [--only claude|qwen|codex|codex-personal|mcode]
bash -n ~/.claude/statusline-command.sh && python3 -I ~/.claude/statusline-analyze.py   # check
```
Analyzer: `--wk-x100 N --reset EPOCH` — a test override (does not save calibration, but does write the profile — rerun without it after the test).

## Map
| File | Role |
|---|---|
| `claude/statusline-command.sh` | the status line itself, reads JSON from stdin |
| `claude/statusline-analyze.py` | background: token cost profile (hour of week, 168 cells), calibration, mode coefficients (including preliminary) and boundaries |
| `claude/settings.fragment.json` | `statusLine` (`refreshInterval` 60) |
| `qwen/`, `codex/codex-personal/` | config fragments of other harnesses (mcode and main `codex` — empty for now) |

## Documentation map
- `docs/requirements.md` — goal, terms, calculation model, display, where things are in the code, decided/cancelled.
- `docs/architecture.md` — data flow, segments, caches, profile format.
- `docs/measurements.md` — what and how the analyzer measures.
- `docs/roadmap.md` — open questions.

## What remains
See `docs/roadmap.md`. Main point: there is little real data for fitting modes so far (≥14 days of history from 2026-11-09 are needed); check the status in `~/.claude/.statusline-profile.json` → `burn_factors`.

## Calculation model (short)
two matrices over the 168 hours of the week, once a day (load; burn speed) → **your pace** = actual `wk%` ÷ what the matrices expect over the current and two previous hours, active only (the `act` journal) → the mode block shows separately `work` (pace) and `burn` (the mode's burn speed, `~` — preliminary), there is no combined number → the `wk` **forecast** = the remaining profile × pace. More in `README.md`, `docs/architecture.md`.
