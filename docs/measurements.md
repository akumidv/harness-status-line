# What is monitored and how

## Inputs
| What | From | Why |
|---|---|---|
| `rate_limits.five_hour / seven_day` (% and reset time) | status line stdin | limit usage; history (**limit axis**) is written to `~/.local/state/harness-status-line/api.anthropic.com/history` on every whole-percent change + a pulse every 30 min, with both reset times, kept forever |
| tokens by type (input/output/cache read/write) and model | transcripts `~/.claude/projects/**/*.jsonl` (read-only, incremental) | token cost by model prices, hourly slots; **token axis** `~/.local/state/harness-status-line/api.anthropic.com/tokens` — raw tokens per 5 min × model × effort × speed (fast mode), thinking tokens separately; no prices, kept forever |
| mode by schedule (UTC) | computed | icon, time to change |

## What `statusline-analyze.py` computes (once a day, background)
1. **Cost profile**: tokens × prices (Opus 5.5 4/20/0.20, Sonnet 5.x 2/10/0.20, Haiku 5.5 0.10/0.50 per 1M; cache write 1.25×/2× — an assumption) into 168 hourly slots of the week (matrix 1 — load; matrix 2 — burn speed by hour, both once a day); calibration "% of limit per $" against the current `wk%` (latest: 0.0757 %/$; a typical week ≈ $1025 ≈ 77 % of the limit).
2. **Pace**: actual weekly-limit usage ÷ my usual use per active hour (profile line 7, × burn speed) over the current and two previous hours, active only (computed by the status line; the analyzer gives the base).
3. **Forecast `[..]`**: remaining profile hours × pace → otherwise the 7-day pace → otherwise the week's pace (`~`).
4. **Mode burn coefficients** (preliminary when data is scarce: shrunk regression, eco ≤ 1, normal = 1, peak ≥ 1, limits 0.6–1.6) and **mode boundaries**: fitted on history (the last 42 days), see below.
5. **Summary**: tokens and cost by model, mode, hour of day (`profile.json`); the daily journal `measures.jsonl`.

## Mode fitting
- Pairs "percent rose by Δ / cost by slots" for 5h and wk (each in its own units).
- Grid: `flat` or `schedule3` (3 modes); ridge regression, cross-validation by days, permutation null.
- Boundaries: search ±2 h around 13/19/8; accept if the CV error is lower by >7 %.
- Minimum data: ≥150 intervals, ≥14 days, ≥$100. Otherwise `insufficient data`.
- Accuracy checked on synthetic data; the mode-change indicator (`regime_change`) is unreliable — treat it as a number only.

## Where to look
`~/.local/state/harness-status-line/api.anthropic.com/profile.json` → `burn_factors` (`status`, `grid`, `schedule_utc`, `mode_factors`, `cv_error_vs_flat`, `factors_by_hour_utc`, `pairs/days/cost_usd_in_wk_pairs`).

`python3 -I ~/.local/share/harness-status-line/statusline-analyze.py --burn` — a read-only look at the raw axes without the fit: the %/$ index per mode, 3-hour UTC block, weekday and day for both limits (each interval between samples of one weekly window ≤ 90 min is spread over its hours by cost; outside-use hours are skipped; bootstrap CI over whole days). The limit has whole-% resolution (1% of the week ≈ $10), so small groups are noise. First reading (4 days, 2026-11-09..12): the weekly limit burns at a constant ≈0.093 %/$ across days (index 0.91–1.05, any model mix); the apparent mode/hour effects (peak 0.81, eco 1.67) are within the noise, and the 5h limit's early-hours excess (03–09 UTC, ×2–4) looks like unflagged use on another machine. Re-read after ≥14 days.

`--burn` part 2 ("token model"): no price list — the API price applies to API requests, the subscription limit has its own token weights. Raw tokens (Mtok) per model family × type (output / cache read / cache write 5m incl. fresh input / cache write 1h; fresh input is ≈0 in this data) enter a structured model: limit = Σ_g a_g · Σ_m Σ_k r_k · X[m,k], with a_g the scale of a model group and r_k the type ratios to cache read (shared by the models, assumed stable), searched on a grid and scored by leave-one-day-out (LOO) error. The limit level at every history sample is regressed on the cumulative tokens since the window start (free intercept per weekly / 5h window, weights ≥ 0): no selection on the size of the rise, no gap limit (per-interval regressions are biased: an interval ends when the limit moves). The report prints references (free 12 weights, every token alike), the best 5, the best per model grouping, a **profile** (best LOO with one ratio fixed: a flat row = not identified) and the weights in limit points per Mtok; then the burn per weighted unit U over time: mode, UTC 4-hour block, day (jackknife ±2SE by day) and each 5h window.

First reading (5–6 days, one weekly window): splitting cache write into 5m and 1h was the big step (LOO 2.6 → 1.5 pts; the free 12-weight fit overfits: LOO 3.0; every token alike 2.1). The best structure is "Opus and Sonnet share one scale, Haiku ≈ 0" with cache write 1h as the driver (≈2.5 points per Mtok, ≥100× cache read; ≈60 of the ≈95 explained points, almost all Opus) and cache reads nearly free (≈0.0085/Mtok). Treat it as a hint: the ratio is flat from 100× to 1000×, only ≤30× is clearly worse; output and cache write 5m are **not identified** (flat rows); cumulative out / read / write / write-1h curves correlate at 0.999, so the data fix one weighted sum, not the separate ratios; "Haiku not counted" vs "counted alike" differ by LOO 1.54 vs 1.57 (not distinguishable). Separate scales for Opus and Sonnet are worse in LOO (2.0 vs 1.54). Burn per U in time: by day 1.09 / 0.77 / 1.16 / 1.05 / 0.83 (no trend), by mode eco ×1.6 (±0.4) / normal 0.75 (±0.5) / peak 0.93 (±0.4), by block 04–08 UTC ×1.4 (±0.7) — an eco excess that appears with this unit but not with the earlier ones, so it depends on the weights; 5h windows: ×2.06 for the 05 UTC window of 11-12 but ×0.92 for 04 UTC on 11-13 (mixed). A within-window rmse of ≈1.2 points (weekly) is burn not explained by this machine's tokens. Re-read after ≥14 days and after importing the other machine's transcripts; the type ratios become identifiable only when the token mix (output / write / read) varies independently over time.
