# Roadmap / open questions
- Data for fitting modes has been accumulating since 2026-11-09; after ≥14 days check `burn_factors.status`. While data is scarce, coefficients are preliminary (`prelim`, marked `~`), the schedule is the default one.
- Check whether the boundaries shift on real data; on false shifts raise `SHIFT_MARGIN` / narrow `SHIFT` in `statusline-analyze.py`.
- Price assumptions (cache write 1.25×/2×, Haiku 5.5 cache read 0.1×) — check against the table.
- The status line for mcode (`/statusline` in MiniMax Code, the `tui.statusLine` block) and the main `codex` profile are not set up; `extract` will pick them up once they exist.
- Do not change the schedule automatically beyond ±2 h; there is no verdict on mode drift (the indicator is unreliable).
- Pace window: 3 active hours; the limit arrives in whole percents (noise; damped by a 1 % pseudo-observation, `K = 1000`). The activity journal `act` sees only this machine — work from other devices does not enter "active hours".
- Decide whether preliminary coefficients from ≥8 intervals are needed (currently `PRELIM_PAIRS`/`PRELIM_LAMBDA` in the analyzer) or ones are enough.
