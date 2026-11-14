# Claude Code status line architecture
Requirements and terminology — `requirements.md`.

## Data flow
```
Claude Code --stdin JSON--> statusline-command.sh --> status line
Qwen / mcode -------------> their statusline-command.sh (+ hsl-lib.sh)
                                 |  background: daily, and --collect every <=5 min
                                 v
   statusline-analyze.py  <-- logs of ALL sessions (Claude incl. VS Code extension, Qwen, mcode, OpenCode, pi)
        |--> ~/.local/state/harness-status-line/api.anthropic.com/profile*                  (limited: Claude cost profile, burn)
        |--> ~/.local/state/harness-status-line/<host>/conn  (every connection: load, speed matrix, modes)
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
One `jq`; `printf -v now '%(%s)T'`; ANSI constants; cache in `/tmp/claude-sl-$UID` (tmpfs, chmod 700; fallback `~/.local/state/harness-status-line/cache`): output per session 15 s, mode until the next transition (and reset when the profile is newer), work/forecast on any change of 5h%/wk% or every 5 min, burn factors until the profile changes, git 15 s, incremental transcript parsing (offset). Persistent data lives in `~/.claude`.

## Where the data lives (decided 2026-11-14)
Nothing of ours is kept in a harness's own dir (`~/.claude` holds only its transcripts and credentials). Durable data is in `~/.local/state/harness-status-line/`, **one folder per connection (provider = endpoint host)** — limits, speed and load belong to the endpoint/account, and several harnesses may share one (`llm.allasso.ch`: qwen, mcode, opencode, pi), so the split is by provider, not by harness (which harness used a connection is listed in `connections.json`, `harnesses`).
```
api.anthropic.com/     the Claude account (limited): history, tokens, tokens@<machine>, profile, profile.json, measures.jsonl,
                       daily, wkreset, analyze-state.json, conn, usage-poll, usage-poll-stopped.lock
<host>/conn            every other connection (llm.allasso.ch, opencode.ai, ...): daily profile (load, speed matrix, modes)
imports/<machine>/     transcript copies of other machines (hsl-import.sh)
collect-state.json, collect-fails, collect-stopped.lock, connections.json (summary of all), config.json (optional overrides), cache/ (fallback runtime cache)
```
`*.lock` = a **stop file**: while it exists the thing does not run (its text is the reason; `--resume` or deleting it clears it). The `flock` mutexes are `*.mutex` (`analyze.mutex`, `usage-poll.mutex`, `collect.mutex`) so the two are never confused. Code: `ANTH_DIR`/`HSL_STATE` in the analyzer, `HSL_ANTH` in the Claude status line, `$HSL_STATE/<host>/conn` in `hsl-lib.sh`.

## Runtime files
`/tmp/claude-sl-$UID/{out-SID,tr-*,git-*,mode,burn,act,hist-last,lim,analyze-last}` (`lim` — the newest fresh limits of any session, adopted by idle ones) (`act` — hours with assistant replies, the pace window skips empty ones); `~/.local/state/harness-status-line/api.anthropic.com/{history,tokens,daily,wkreset,profile,profile.json,measures.jsonl,analyze-state.json}` (`wkreset` — `rwe epoch wk_x100`: limits reset by hand (full or partial) within that weekly window; the status line counts the week from there above `wk_x100`; see requirements §3).

**Raw axes** (kept forever, joined by epoch; everything else is derived from them and can be recomputed differently):
- limit axis `~/.local/state/harness-status-line/api.anthropic.com/history`: `epoch wk_x100 5h_x100 wk_reset 5h_reset` (5th column since 2026-11-12, `-` = unknown), on every whole-% change + 30-min pulse; written by a CLI status line with fresh limits and by `poll_usage()` in `--collect` (account usage endpoint `api.anthropic.com/api/oauth/usage`, OAuth access token read in memory only, never refreshed; ≥4 min apart, backoff to 1 h on failure, state `~/.local/state/harness-status-line/api.anthropic.com/usage-poll`; reset epochs rounded to the minute like the CLI's; every poll also writes `/tmp/hsl-UID/lim-poll` (`epoch 5h% r5e wk% rwe`) — an idle status line adopts it when it is newer than its own session's limits and the shared `lim`; a row the poll appends is also entered into the status lines' `hist-last` (`/tmp/claude-sl-UID`), so no CLI session appends a stale lower value after it — all writers compare against the same "last row");
  - **One entry for cron and the status lines**: `statusline-analyze.py --collect` (the status line's `hsl_kick` every ≥5 min when a session is open; a crontab line keeps it going with no session). The poll **never asks twice**: skipped while a session's own limits are fresh (`/tmp/claude-sl-UID/lim` < 4 min old — those are the headers of its own API responses), while another process polls (`usage-poll.mutex`, the slot is claimed in `usage-poll` before the request) and within 4 min of the last poll — any number of sessions plus cron share one lock and one timer.
  - **Stops on errors, completely**: **3 HTTP errors in a row** other than 401/403/429, or unusable answers, with a growing wait between them (8, 16 min, ...) → the stop file `~/.local/state/harness-status-line/api.anthropic.com/usage-poll-stopped.lock` is written; its text is the reason (for an HTTP error also the server's own answer: truncated error body + `server` / `www-authenticate` / `retry-after`, long token-like strings masked — added 2026-11-14 after the first 403 left only `Forbidden`), time, rule and how to resume. While it exists neither cron nor any status line sends a usage request — only the sessions' own response limits keep being recorded — until the cause is understood and `--resume` is run (or the file is deleted). A refusal **401/403/429 never stops it** (decided 2026-11-14: the first 403 at 02:35 UTC passed by itself within hours — probably the OAuth token being rotated): it is retried 3 times with a growing wait (8, 16, 32 min), then once an hour. Network errors and an expired token only back off (nothing reached the server). A crash of the collector is counted in `collect-fails`; after 3 in a row `collect-stopped.lock` (also with the reason) stops the collector and the poll, and `hsl_kick` does not start it. `--health` shows both with the reasons.
- imports and outside use: copies of other machines' transcripts in `~/.local/state/harness-status-line/imports/<host>/` (`hsl-import.sh <host>`) are scanned like local ones (their tokens → `tokens@<host>`); hours whose 5h step is far above the median per local $ are flagged as outside use and excluded from calibration and the mode fit (`outside_use` in the profile JSON).
- token axis `~/.local/state/harness-status-line/api.anthropic.com/tokens`: as **JSONL** (decided 2026-11-14, so it can be read by hand; one object per line: `t` epoch + `ts` UTC of the bucket start, `model`, `effort`, `speed`, `msgs`, `input`, `output`, `cache_read`, `cache_write_5m`, `cache_write_1h`, `thinking`) per 5-min bucket; the previous whitespace format is read by `parse_tok_line` and converted losslessly on the next append, the original rotated to `tokens.<epoch>` (old files are rotated, never deleted) × model × effort (`low`..`max`, from the transcript field `effort`) × speed (`standard`/`fast`, `usage.speed`), by response end time; `thinking` = `usage.output_tokens_details.thinking_tokens`, a part of `output`, no prices; appended daily by the analyzer from the 15-day message state (backfilled from 2026-10-28 on the first run).
Profile: line 1 `updated scale coverage_days`; 2 — matrix 1: load by the 168 hours of the week (thousandths of a % of the limit at normal burn speed); 3 — `eco normal peak` ×100 or `-`; 4 — matrix 2: burn speed by the 168 hours ×100; 5 — boundaries `peak_start peak_end eco_end` (UTC hours); 6 — `fit`/`prelim`/`-`. Expected usage of an hour = load × speed / 100.

- The forecast `[→N%]`/`[time]` next to `wk`: `calc_bo` in `statusline-command.sh` replays the remaining hourly profile slots, multiplying them by the current pace `rel` (30–400 %); if the limit does not run out before the reset, the projected usage at reset is shown (`proj`, the 7th field of the `burn` cache).
- The `burn` cache (fields): `bk b_mult rel bo proj pre mfe mfn mfp`; `calc_rel` — pace over the current and two previous hours (active only), `calc_bo` — forecast over the remaining matrices until the reset, `work` = `rel`, `burn` = the coefficient of the current mode (from profile line 3).
