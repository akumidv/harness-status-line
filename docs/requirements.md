# Requirements, terminology and decisions (Claude Code status line)

This document gathers everything agreed with the user, so work can continue in a new session from the documentation, `AGENTS.md` and the code alone. If the code diverges from this file, work out which is stale and fix it.

## 1. Goal
Show **approximately** when limits burn fast or slowly, so that **heavy work is not planned for the peak**, and forecast whether the weekly limit lasts until the reset. Accuracy is nominal ("without fanaticism"); do not complicate things for it. The status line must be fast (bash + one `jq`, cache in tmpfs); anything heavy only in the background analyzer once a day.

## 2. Terminology (do not mix up)
| Term | What it is | Source |
|---|---|---|
| **mode** | `peak 🔥` / `normal ●` / `eco ❄` by UTC schedule (peak — weekdays 13–19, eco — weekends and weekdays before 8; boundaries adapt, profile line 5) | status line |
| **grid** | 1 hour, 168 cells of the week; index `(epoch/3600) % 168` (epoch 0 = Thursday) | both |
| **matrix 1 — load (L)** | your average weekly-limit usage in this hour of the week, thousandths of a % at normal burn speed | analyzer, once a day |
| **matrix 2 — burn speed (S)** | limit burn coefficient in this hour (×100), 100 = normal | analyzer, once a day |
| **`work`** — work pace | your intensity relative to **your own average for the same hours** (>1 — more active than usual) | status line, `calc_rel` |
| **`burn`** — burn speed | how many times faster/slower the limit burns in the current mode relative to normal (normal = 1) | profile line 3 |
| `~` next to `burn` | the coefficient is **preliminary** (little data) | profile line 6 = `prelim` |
| **forecast** `[→N%]` / time | what % of the weekly limit will be used by the reset / when it runs out | `calc_bo` |
| "mode price", "combined number" | **cancelled**: there is no combined `work×burn` number, the components are shown separately | — |

Why separately: the user steers their work — more active when burn is slow, calmer when it is fast.

## 3. Calculation model
1. **Two matrices** (L and S) are cached in the profile and recomputed once a day. Expected usage of hour `i`: `L[i] × S[i] / 100`.
2. **Pace `work`** (`calc_rel`, ×100): actual `wk%` usage over the window ÷ what the matrices expect over the same window.
   - The window is the **current hour and the two previous ones**, nothing older, and **not before the weekly reset**.
   - Only **active hours** count (the hour had an assistant reply; journal `$CACHE_DIR/act`, the first reply time in each hour). Empty hours are skipped so they are not averaged with zero: just started — the current hour's pace, second hour began — two hours, and so on.
   - Proportional **by seconds**, not whole hours: an active hour counts from `max(first activity, hour start)` to the end of the hour (the current one — to "now"). Otherwise the first 10 minutes would give activity 6 times smaller than an hour.
   - Actual Δ% = `wk%` now − `wk0` (the last history reading at the window start or earlier, with the same reset, not older than 24 h).
   - The limit arrives in **whole percents** → a short window is noisy. Damping: `rel = ((Δ×10 + K)×100) / (exp + K)`, `K = 1000` (pseudo-observation of 1 % at pace 1). Threshold `got ≥ 120` s, otherwise the week since the reset.
   - `work` does not depend on the mode.
3. **Forecast** (`calc_bo`): the sum over the **remaining hours until the reset** of `L × S × work`. Only the remainder from the current hour is recomputed, not the whole week. **The current mode is not used in the forecast** — each hour has its own speed. The result is compared with the remaining limit in normalized units (remaining % ÷ average speed 1). `work` is clamped to 30–400 % in the calculation.
4. **Preliminary `burn`** (little data): ridge regression shrunk toward 1, at ≥8 intervals; limits 0.6–1.6; by meaning eco ≤ 1, normal = 1, peak ≥ 1 (prelim only — otherwise noise draws a "cheap peak"). Measured (fit) values are **not** clamped. A full fit requires ≥150 intervals, ≥14 days, ≥$100 (history since 2026-11-09). If there is no time dependence — `flat`, coefficients 1.
5. Mode boundaries are searched ±2 h on the last 42 days, accepted if the CV error improves by >7 % (`SHIFT_MARGIN = 0.93`).
6. Without a profile (the first day) `work` is the old "uniform" pace, `burn` is not shown.

## 4. Display
```
🔥.8×~.9× 2h45m · 5h 16%↑ 3h36m · wk 13%↑ 6d12h [→81%]
```
- The first number is `work`, the second is `burn`; zeros without the integer part (`.8×`); `~` — preliminary `burn`; then the time to the next mode (the next mode's color, dim).
- `burn`: green `<0.95`, red `>1.05`, otherwise dim.
- `work`: cyan `<0.75`; red `>1.5` (and while burn is fast, `burn` >1.05, — `>1.25`); otherwise the terminal's normal color (like the limit percents).
- There is **no** coefficient in the `wk` segment: only `[→N%]` (lasts until the reset) or the time to exhaustion (red if before the reset).
- The speed `⚡– [session]` when idle >5 min stays as is (the user's decision).

## 5. Implementation (where things are in the code)
| What | Where |
|---|---|
| activity journal `act` | the transcript parsing block in `statusline-command.sh` (epoch of the first reply in the hour; the old "hours" format <10 000 000 is read too) |
| `load_matrix`, `calc_rel`, `calc_bo` | `statusline-command.sh` |
| `burn` cache | line 1 — epoch; line 2 — `bk b_mult rel bo proj pre mfe mfn mfp`; TTL 30 min or 5h reset; `b_mult` is the obsolete "uniform" pace, only without a profile |
| profile (6 lines) | `statusline-analyze.py` → `~/.claude/.statusline-profile`; format — `architecture.md` |
| factor/boundary/calibration calculation | `analyze_factors`, calibration by weighted cost `wb`, then the matrices; `STATE_VERSION = 4` (version change = rescan of transcripts); `SLOT, SLOTS = 3600, 168`; `PRELIM_PAIRS, PRELIM_LAMBDA = 8, 8.0` |

Speed: ≈13 ms per run with recompute, ≈8 ms on a `burn` cache hit, ≈2 ms on a line cache hit — no Python is needed in the status line.

## 6. Known limitations
- The activity journal sees **only this machine**; work from other devices does not enter "active hours", although the limit is shared.
- Whole percents → the first minutes of the window are noisy (damped by `K`).
- While data is scarce, `burn` is preliminary and almost everywhere ≈1 — this is normal, not an error ("≈1 = normal mode").
- Price assumptions (cache 1.25×/2×, Haiku 5.5 read 0.1×) — to be checked against the table.

## 7. What was decided not to do
- A combined `work×burn` number; a coefficient in the `wk` segment; grids finer than an hour; automatic boundary shifts beyond ±2 h; invented peak coefficients (until there is data we show 1).

## 8. Verification and working rules
```bash
bash -n ~/.claude/statusline-command.sh && python3 -I ~/.claude/statusline-analyze.py
python3 harness_status_line.py extract --only claude && python3 harness_status_line.py status
```
- Edit the live files (`~/.claude/...`), then `extract`; `git init`/commit only at the user's request.
- Tests write to the real tmpfs cache: afterwards delete `burn`, `out-tst`, `mode`, `act` (keep and restore the real `act`). Do not touch transcripts.
- Analyzer `--wk-x100 N --reset EPOCH` is a test override (it writes the profile, then rerun without it).
