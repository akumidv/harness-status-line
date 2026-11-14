# Requirements, terminology and decisions (Claude Code status line)

Connections without limits (Qwen, mcode, OpenCode, pi, Claude via API key), the `spd` speed and the activity of all sessions (VS Code extension) — `connections.md`.

This document gathers everything agreed with the user, so work can continue in a new session from the documentation, `AGENTS.md` and the code alone. If the code diverges from this file, work out which is stale and fix it.

## 1. Goal
Show **approximately** when limits burn fast or slowly, so that **heavy work is not planned for the peak**, and forecast whether the weekly limit lasts until the reset. Accuracy is nominal ("without fanaticism"); do not complicate things for it. The status line must be fast (bash + one `jq`, cache in tmpfs); anything heavy only in the background analyzer once a day.

## 2. Terminology (do not mix up)
| Term | What it is | Source |
|---|---|---|
| **mode** | `peak 🔥` / `normal ◆` / `eco ❄` by UTC schedule (peak — weekdays 13–19, eco — weekends and weekdays before 8; boundaries adapt, profile line 5) | status line |
| **grid** | 1 hour, 168 cells of the week; index `(epoch/3600) % 168` (epoch 0 = Thursday) | both |
| **matrix 1 — load (L)** | your average weekly-limit usage in this hour of the week, thousandths of a % at normal burn speed | analyzer, once a day |
| **matrix 2 — burn speed (S)** | limit burn coefficient in this hour (×100), 100 = normal | analyzer, once a day |
| **`work`** — work pace | your intensity relative to **your own average while working** — per active hour, idle hours excluded (>1 — more active than usual; decided 2026-11-12) | status line, `calc_rel`; base = profile line 7 |
| **outside use** | limit use this machine's (and imported) transcripts do not explain — another computer, claude.ai. An hour is flagged when its 5h step > 2× the median 5h%-per-$ × its cost + 3 points (after 24 hours of data); flagged hours are left out of the calibration and the mode fit, but stay in `work` and the forecast (decided 2026-11-12) | analyzer `outside_use` |
| **limit axis / token axis** | two separate raw journals, joined by epoch for any analysis: `history` (wk%, 5h% and both resets) and `tokens` (raw tokens per 5 min × model × reasoning effort × speed, no prices); both kept forever | status line / analyzer |
| **`burn`** — burn speed | how many times faster/slower the limit burns in the current mode relative to normal (normal = 1) | profile line 3 |
| `~` next to `burn` | the coefficient is **preliminary** (little data) | profile line 6 = `prelim` |
| **forecast** `[→N%]` / time | what % of the weekly limit will be used by the reset / when it runs out | `calc_bo` |
| "mode price", "combined number" | **cancelled**: there is no combined `work×burn` number, the components are shown separately | — |

Why separately: the user steers their work — more active when burn is slow, calmer when it is fast.

## 3. Calculation model
1. **Two matrices** (L and S) are cached in the profile and recomputed once a day. Expected usage of hour `i`: `L[i] × S[i] / 100`.
2. **Pace `work`** (`calc_rel`, ×100): actual `wk%` usage over the window ÷ what **I usually use per active hour** (profile line 7) × burn speed `S` over the active seconds of the same window.
   - The base (decided 2026-11-12) is the mean over the **active** hours of the last 14 days (an hour is active from its first reply to its end — the same rule as below), not matrix 1: matrix 1 averages the idle weeks of an hour too, so ordinary work read as `work` 2–2.5. Without line 7 the status line falls back to matrix 1. The collector (other connections, frozen Claude limits) uses the same base in its units (`<host>/conn` line 1, field 6).
   - The window is the **current hour and the two previous ones**, nothing older, and **not before the weekly reset**.
   - Only **active hours** count (the hour had an assistant reply; journal `$CACHE_DIR/act` + `/tmp/hsl-$UID/act-api.anthropic.com` from the collector — all sessions, the VS Code extension too; the first reply time in each hour). Empty hours are skipped so they are not averaged with zero: just started — the current hour's pace, second hour began — two hours, and so on.
   - Proportional **by seconds**, not whole hours: an active hour counts from `max(first activity, hour start)` to the end of the hour (the current one — to "now"). Otherwise the first 10 minutes would give activity 6 times smaller than an hour.
   - Actual Δ% = `wk%` now − `wk0` (the last history reading at the window start or earlier, with the same reset, not older than 24 h). Expected is summed over the active seconds **from that reading** to now (the reading can be older than the window when no status line ran, e.g. work only in the extension).
   - The limit arrives in **whole percents** → a short window is noisy. Damping: `rel = ((Δ×10 + K)×100) / (exp + K)`, `K = 1000` (pseudo-observation of 1 % at pace 1). Threshold `got ≥ 120` s, otherwise the week since the reset.
   - `work` does not depend on the mode.
   - **Manual reset of the limits** (decided 2026-11-14). In Claude a manual reset does **not** move the weekly reset time (in Codex it does — that is simply a new weekly window, nothing special; Codex limits are not collected anyway). It may be full or **partial** (e.g. only Fable, which has its **own limit system** in Claude — not collected yet, see roadmap: its limit goes to 0 and its use is taken off the weekly limit, so `wk%` drops by that share, not to 0). Detection: a `wk%` drop ≥ 3 points (`RESET_DROP` = 300 ×100; the history has 1-point backward steps = noise) between consecutive samples **within the same weekly reset time**. The status line accepts such a sample from a fresh response (a smaller drop is still treated as a stale value and not written), writes the row and notes `weekly_reset_epoch response_epoch wk_x100_after` in `~/.local/state/harness-status-line/api.anthropic.com/wkreset`. From then on: `wstart` = that moment and `wbase` = the `wk%` right after the reset (0 after a full one) — `ew`, the "not before the weekly reset" bound, the baseline `wk0` (samples before the moment are another baseline), the week-to-date expected (actual = `wk% − wbase`), the weekly burn `mw` and the time-to-exhaustion fallback all count from there. A session whose last response is older than the reset is stale (it still carries the old %) and adopts the shared fresh limits. The daily weekly pace (`w_pace`) counts a drop with the same reset time as "nothing used". The analyzer calibrates on `wk% + credit` (`wk_credit_of`: the sum of such drops in this weekly window, from the history) over the cost of the whole week, so the scale `% per $` is unchanged by a reset; fits already skip pairs across a drop (`w2 < w1`). The moment is known only to the first response seen after the reset.
3. **Forecast** (`calc_bo`): the sum over the **remaining hours until the reset** of `L × S × work`. Only the remainder from the current hour is recomputed, not the whole week. **The current mode is not used in the forecast** — each hour has its own speed. The result is compared with the remaining limit in normalized units (remaining % ÷ average speed 1). `work` is clamped to 30–400 % in the calculation.
4. **Preliminary `burn`** (little data): ridge regression shrunk toward 1, at ≥8 intervals; limits 0.6–1.6; by meaning eco ≤ 1, normal = 1, peak ≥ 1 (prelim only — otherwise noise draws a "cheap peak"). Measured (fit) values are **not** clamped. A full fit requires ≥150 intervals, ≥14 days, ≥$100 (history since 2026-11-09). If there is no time dependence — `flat`, coefficients 1.
5. Mode boundaries are searched ±2 h on the last 42 days, accepted if the CV error improves by >7 % (`SHIFT_MARGIN = 0.93`).
6. Without a profile (the first day) `work` is the old "uniform" pace, `burn` is not shown.

## 4. Display
```
🔥~.9× 2h45m .8× 🐌.8× 52 [48] · 5h 16%↑ 3h36m · wk 13%↑ 6d12h [→81%]
◆1× 4h53m 1.2× ▸1.1× 143 [140]          (normal limit mode, normal answer speed)
```
- Order (decided 2026-11-10): **burn group** = mode icon + `burn` + time to the next mode (the next mode's color, dim); then **`work`**; then the **speed group** = load icon (`🐌` slower than usual / `▸` normal / `🐇` faster, `api.anthropic.com` over 15 min, all sessions) + `spd×` (red slower, green faster, `~` from the matrix) + `speed [session]` t/s. Zeros without the integer part (`.8×`); `~` — preliminary. Without limits the burn group is absent and the speed group carries the time to the next load mode. Without any load data the old `⚡speed [session]`.
- Icons (decided 2026-11-10): limit modes `❄` eco / green `◆` normal / `🔥` peak; answer speed `🐇` faster / yellow `▸` normal / `🐌` slower. The two normal icons differ on purpose, so the burn and speed groups are not confused.
- `burn`: green `<0.95`, red `>1.05`, otherwise dim.
- `work`: cyan `<0.75`; red `>1.5` (and while burn is fast, `burn` >1.05, — `>1.25`); otherwise the terminal's normal color (like the limit percents).
- There is **no** coefficient in the `wk` segment: only `[→N%]` (lasts until the reset) or the time to exhaustion (red if before the reset).
- The speed `– [session]` when idle >5 min stays as is (the user's decision).

## 5. Implementation (where things are in the code)
| What | Where |
|---|---|
| activity journal `act` | the transcript parsing block in `statusline-command.sh` (epoch of the first reply in the hour; the old "hours" format <10 000 000 is read too) |
| `load_matrix`, `calc_rel`, `calc_bo` | `statusline-command.sh` |
| `burn` cache | line 1 — epoch; line 2 — `bk b_mult rel bo proj pre mfe mfn mfp`; current part (`b_mult rel bo proj`) recomputed on key `r5e:5h%:wk%` change or every 5 min; history part (`pre mfe mfn mfp`) re-read only when the profile is newer; `b_mult` is the obsolete "uniform" pace, only without a profile |
| profile (7 lines) | `statusline-analyze.py` → `~/.local/state/harness-status-line/api.anthropic.com/profile`; format — `architecture.md`; line 7 = base of `work` (thousandths of % per active hour) |
| outside use, imports | `outside_use` (history rows with the 5h reset × message cost by end time); imports: `IMPORTS/<host>/**/*.jsonl` scanned with `scan(..., host)` — records get an 14th field `host`; connection profiles use local messages only |
| raw axes | `append_tokens` (analyzer, daily: completed 5-min buckets after the file's last one, so reruns do not duplicate) → `tokens`; history line written by the status line (`epoch wk_x100 5h_x100 wk_reset 5h_reset`) |
| factor/boundary/calibration calculation | `analyze_factors`, calibration by weighted cost `wb`, then the matrices; `STATE_VERSION = 7` (version change = rescan of transcripts, `frozen` and `cal` are kept; 7 added effort, speed, thinking tokens; 6 added cache write 1h; 5 added request start/end per message for speed); `SLOT, SLOTS = 3600, 168`; `PRELIM_PAIRS, PRELIM_LAMBDA = 8, 8.0` |

Speed: ≈13 ms per run with recompute, ≈8 ms on a `burn` cache hit, ≈2 ms on a line cache hit — no Python is needed in the status line.

## 6. Known limitations
- The activity journal sees **only this machine** (all harnesses and the VS Code extension on it, via the collector, up to 5 min late); work from other devices does not enter "active hours", although the limit is shared.
- Whole percents → the first minutes of the window are noisy (damped by `K`).
- While data is scarce, `burn` is preliminary and almost everywhere ≈1 — this is normal, not an error ("≈1 = normal mode").
- Price assumptions (cache 1.25×/2×, Haiku 5.5 read 0.1×) — to be checked against the table.

## 7. What was decided not to do
- A combined `work×burn` number; a coefficient in the `wk` segment; grids finer than an hour; automatic boundary shifts beyond ±2 h; invented peak coefficients (until there is data we show 1).

## 8. Verification and working rules
```bash
bash -n ~/.claude/statusline-command.sh && python3 -I ~/.local/share/harness-status-line/statusline-analyze.py
python3 harness_status_line.py extract && python3 harness_status_line.py status
```
- Edit the live files (`~/.claude/...`), then `extract`; `git init`/commit only at the user's request.
- Tests write to the real tmpfs cache: afterwards delete `burn`, `out-tst`, `mode`, `act` (keep and restore the real `act`). Do not touch transcripts.
- Analyzer `--wk-x100 N --reset EPOCH` is a test override (it writes the profile, then rerun without it).
