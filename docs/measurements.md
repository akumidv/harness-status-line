# What is monitored and how

## Inputs
| What | From | Why |
|---|---|---|
| `rate_limits.five_hour / seven_day` (% and reset time) | status line stdin | limit usage; history is written to `~/.claude/.statusline-history` on every whole-percent change + a pulse every 30 min, kept 90 days |
| tokens by type (input/output/cache read/write) and model | transcripts `~/.claude/projects/**/*.jsonl` (read-only, incremental) | token cost by model prices, hourly slots |
| mode by schedule (UTC) | computed | icon, time to change |

## What `statusline-analyze.py` computes (once a day, background)
1. **Cost profile**: tokens × prices (Opus 5.5 4/20/0.20, Sonnet 5.x 2/10/0.20, Haiku 5.5 0.10/0.50 per 1M; cache write 1.25×/2× — an assumption) into 168 hourly slots of the week (matrix 1 — load; matrix 2 — burn speed by hour, both once a day); calibration "% of limit per $" against the current `wk%` (latest: 0.0757 %/$; a typical week ≈ $1025 ≈ 77 % of the limit).
2. **Pace**: actual weekly-limit usage ÷ what the profile expects over the current and two previous hours, active only (computed by the status line, not the analyzer).
3. **Forecast `[..]`**: remaining profile hours × pace → otherwise the 7-day pace → otherwise the week's pace (`~`).
4. **Mode burn coefficients** (preliminary when data is scarce: shrunk regression, eco ≤ 1, normal = 1, peak ≥ 1, limits 0.6–1.6) and **mode boundaries**: fitted on history (the last 42 days), see below.
5. **Summary**: tokens and cost by model, mode, hour of day (`.statusline-profile.json`); the daily journal `.statusline-measures.jsonl`.

## Mode fitting
- Pairs "percent rose by Δ / cost by slots" for 5h and wk (each in its own units).
- Grid: `flat` or `schedule3` (3 modes); ridge regression, cross-validation by days, permutation null.
- Boundaries: search ±2 h around 13/19/8; accept if the CV error is lower by >7 %.
- Minimum data: ≥150 intervals, ≥14 days, ≥$100. Otherwise `insufficient data`.
- Accuracy checked on synthetic data; the mode-change indicator (`regime_change`) is unreliable — treat it as a number only.

## Where to look
`~/.claude/.statusline-profile.json` → `burn_factors` (`status`, `grid`, `schedule_utc`, `mode_factors`, `cv_error_vs_flat`, `factors_by_hour_utc`, `pairs/days/cost_usd_in_wk_pairs`).
