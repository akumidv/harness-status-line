# Claude Code status line architecture
Requirements and terminology — `requirements.md`.

## Data flow
```
Claude Code --stdin JSON--> statusline-command.sh --> status line
Qwen / mcode -------------> their statusline-command.sh (+ hsl-lib.sh)
                                 |  background: daily, and --collect every <=5 min
                                 v
   statusline-analyze.py  <-- logs of ALL sessions (Claude incl. VS Code extension, Qwen, mcode, OpenCode, pi)
        |--> ~/.claude/.statusline-profile*                  (limited: Claude cost profile, burn)
        |--> ~/.local/state/harness-status-line/conn-<host>  (every connection: load, speed matrix, modes)
        '--> /tmp/hsl-$UID/{rt,act}-<host>, conn-map         (now: work, spd, today; activity hours)
```
Connections without limits and the shared files — `connections.md`.
`rate_limits` in the JSON is per account (all sessions and devices), so limit usage is computed from the difference of percents, not from session tokens.

## Segments (left to right)
model · agent/worktree · context + `◷` prompt cache TTL · mode `icon burn× time-to-next work× load-icon spd× speed [session]` (e.g. `🔥~.9× 2h45m .8× 🐌.8× 52 [48]`) · `5h %` · `wk % time-to-reset [to exhaustion]` · git · cwd.

Mode: `🔥` peak (red), `◆` normal (green), `❄` eco (cyan). `work` — your pace (current and two previous hours, active, ÷ your usual use per active hour, profile line 7), `burn` — limit burn speed in the current mode (green <0.95, red >1.05, `~` — preliminary). Separate, with no combined value. The forecast uses the speed of each remaining hour, not of the current mode. The time is to the next mode, in the next mode's color, dim, right after `burn`. Speed group: `🐇` faster / `▸` normal / `🐌` slower than the models' usual speed (all sessions on the endpoint over 15 min), `spd×` (red slower, green faster, `~` from the matrix), tokens/s [session]. Without limits there is no burn group and the time to the next load mode follows `spd×` (`connections.md`).

## Mode schedule (assumption, UTC)
Peak: weekdays 13–19; eco: weekends and weekdays before 08; otherwise normal. The boundaries are adjusted by the analyzer (see `measurements.md`); the status line reads them from line 5 of the profile.

## Resource saving
One `jq`; `printf -v now '%(%s)T'`; ANSI constants; cache in `/tmp/claude-sl-$UID` (tmpfs, chmod 700; fallback `~/.claude/.statusline-cache`): output per session 15 s, mode until the next transition (and reset when the profile is newer), work/forecast on any change of 5h%/wk% or every 5 min, burn factors until the profile changes, git 15 s, incremental transcript parsing (offset). Persistent data lives in `~/.claude`.

## Runtime files
`/tmp/claude-sl-$UID/{out-SID,tr-*,git-*,mode,burn,act,hist-last,lim,analyze-last}` (`lim` — the newest fresh limits of any session, adopted by idle ones) (`act` — hours with assistant replies, the pace window skips empty ones); `~/.claude/.statusline-{history,tokens,daily,profile,profile.json,measures.jsonl,analyze-state.json}`.

**Raw axes** (kept forever, joined by epoch; everything else is derived from them and can be recomputed differently):
- limit axis `~/.claude/.statusline-history`: `epoch wk_x100 5h_x100 wk_reset 5h_reset` (5th column since 2026-11-12, `-` = unknown), on every whole-% change + 30-min pulse, only while a CLI status line runs with fresh limits;
- token axis `~/.claude/.statusline-tokens`: `epoch model effort speed msgs input output cache_read cache_write_5m cache_write_1h thinking` per 5-min bucket × model × effort (`low`..`max`, from the transcript field `effort`) × speed (`standard`/`fast`, `usage.speed`), by response end time; `thinking` = `usage.output_tokens_details.thinking_tokens`, a part of `output`, no prices; appended daily by the analyzer from the 15-day message state (backfilled from 2026-10-28 on the first run).
Profile: line 1 `updated scale coverage_days`; 2 — matrix 1: load by the 168 hours of the week (thousandths of a % of the limit at normal burn speed); 3 — `eco normal peak` ×100 or `-`; 4 — matrix 2: burn speed by the 168 hours ×100; 5 — boundaries `peak_start peak_end eco_end` (UTC hours); 6 — `fit`/`prelim`/`-`. Expected usage of an hour = load × speed / 100.

- The forecast `[→N%]`/`[time]` next to `wk`: `calc_bo` in `statusline-command.sh` replays the remaining hourly profile slots, multiplying them by the current pace `rel` (30–400 %); if the limit does not run out before the reset, the projected usage at reset is shown (`proj`, the 7th field of the `burn` cache).
- The `burn` cache (fields): `bk b_mult rel bo proj pre mfe mfn mfp`; `calc_rel` — pace over the current and two previous hours (active only), `calc_bo` — forecast over the remaining matrices until the reset, `work` = `rel`, `burn` = the coefficient of the current mode (from profile line 3).
