# Claude Code status line architecture
Requirements and terminology — `requirements.md`.

## Data flow
```
Claude Code --stdin JSON--> statusline-command.sh --> status line
                                 |  (once a day, background)
                                 v
                        statusline-analyze.py --> ~/.claude/.statusline-profile*
```
`rate_limits` in the JSON is per account (all sessions and devices), so limit usage is computed from the difference of percents, not from session tokens.

## Segments (left to right)
model · agent/worktree · context + `◷` prompt cache TTL · `⚡speed [session]` · mode `icon work× burn× time-to-next` (e.g. `🔥.8×~.9× 2h45m`) · `5h %` · `wk % time-to-reset [to exhaustion]` · git · cwd.

Mode: `🔥` peak (red), `●` normal (green), `❄` eco (cyan). `work` — your pace (current and two previous hours, active, ÷ your average for the same hours), `burn` — limit burn speed in the current mode (green <0.95, red >1.05, `~` — preliminary). Separate, with no combined value. The forecast uses the speed of each remaining hour, not of the current mode. The time is to the next mode, in the next mode's color, dim.

## Mode schedule (assumption, UTC)
Peak: weekdays 13–19; eco: weekends and weekdays before 08; otherwise normal. The boundaries are adjusted by the analyzer (see `measurements.md`); the status line reads them from line 5 of the profile.

## Resource saving
One `jq`; `printf -v now '%(%s)T'`; ANSI constants; cache in `/tmp/claude-sl-$UID` (tmpfs, chmod 700; fallback `~/.claude/.statusline-cache`): output per session 15 s, mode until the next transition (and reset when the profile is newer), burn 30 min, git 15 s, incremental transcript parsing (offset). Persistent data lives in `~/.claude`.

## Runtime files
`/tmp/claude-sl-$UID/{out-SID,tr-*,git-*,mode,burn,act,hist-last,analyze-last}` (`act` — hours with assistant replies, the pace window skips empty ones); `~/.claude/.statusline-{history,daily,profile,profile.json,measures.jsonl,analyze-state.json}`.
Profile: line 1 `updated scale coverage_days`; 2 — matrix 1: load by the 168 hours of the week (thousandths of a % of the limit at normal burn speed); 3 — `eco normal peak` ×100 or `-`; 4 — matrix 2: burn speed by the 168 hours ×100; 5 — boundaries `peak_start peak_end eco_end` (UTC hours); 6 — `fit`/`prelim`/`-`. Expected usage of an hour = load × speed / 100.

- The forecast `[→N%]`/`[time]` next to `wk`: `calc_bo` in `statusline-command.sh` replays the remaining hourly profile slots, multiplying them by the current pace `rel` (30–400 %); if the limit does not run out before the reset, the projected usage at reset is shown (`proj`, the 7th field of the `burn` cache).
- The `burn` cache (fields): `bk b_mult rel bo proj pre mfe mfn mfp`; `calc_rel` — pace over the current and two previous hours (active only), `calc_bo` — forecast over the remaining matrices until the reset, `work` = `rel`, `burn` = the coefficient of the current mode (from profile line 3).
