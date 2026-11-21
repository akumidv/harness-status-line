# Analyses log (reviews of the burn data)

Frozen reviews of "what makes the limit burn faster or slower", each with the question, the result, the verdict, what could change it, and the exact files to rerun. Purpose: come back to a question, or **not** come back to it (the verdict says why), or compare with a later run on more data.

- Scripts here are read-only: they import `~/.local/share/harness-status-line/statusline-analyze.py` and read `~/.local/state/harness-status-line/api.anthropic.com/{history,tokens,profile,conn,profile.json}`. They write nothing. Run with `python3 -I docs/analyses/<name>.py`.
- **The repository holds only this summary and the scripts. Raw outputs and raw data stay out of git:** the dated outputs `<name>-<date>.txt` live in `~/.local/state/harness-status-line/analyses/results/` (per-hour / per-day tables, $ sums), and the raw axes (`history`, `tokens`, `imports/`) are in `~/.local/state/harness-status-line/`; `.gitignore` blocks those names and `docs/analyses/results/` in case they are copied here. Numbers below are a digest of those outputs; any review can be rerun and diffed against its dated snapshot.
- Rerun everything: `R=~/.local/state/harness-status-line/analyses/results; mkdir -p $R; D=$(date +%F); for f in burn-split schedule-tz burn-by-hour hour-lambda speed-vs-burn fable-vs-burn; do python3 -I docs/analyses/$f.py > $R/$f-$D.txt; done; python3 -I docs/analyses/ctx-vs-burn.py 2 > $R/ctx-vs-burn-$D.txt; python3 -I ~/.local/share/harness-status-line/statusline-analyze.py --burn > $R/burn-report-$D.txt`. Refresh the token axis first (`python3 -I ~/.local/share/harness-status-line/statusline-analyze.py`; it appends tokens once a day, so the last hours of `history` are otherwise unmatched by tokens and look like a burn spike).

## Data behind the snapshots of 2026-11-17
- History (limit axis) `history`: 749 rows, 2026-11-09 → 2026-11-17 14:51 UTC (last row: wk 22 %). Token axis `tokens`: refreshed at 14:40 UTC the same day (this machine only; ≈76 000 requests since 2026-10-28). The first pass at 12:55 UTC had stale tokens, which made today look like a burn spike (0.156 %/$); after the refresh it is 0.10 %/$ (see §7).
- **9 days of limit history, ONE weekend (Sat 14 + Sun 15 Nov), one weekly window.** The limit has whole-% resolution (1 % ≈ $10–12), so anything on < $30–40 per bin is noise. The mode fit is preliminary (`~`): `burn_factors.status` = "preliminary (701 wk pairs)"; the gate is ≥14 days, ≥150 pairs, ≥$100 → about 2026-11-23. Recheck dates are below.
- Known contamination: a parallel session on another machine on 2026-11-12 ~05–07 UTC (flagged and excluded, `outside_use`); other unflagged use there is possible and would show up as excess 5h burn at 04–09 UTC.

## Reading guide: the index
`idx` = limit points per $ (or per token type) relative to the average, 1.00 = average; > 1 = dearer. "$" in the tables = cost in the bin, it is the weight of the evidence.

## 1. Does the burn differ by mode / weekday / hour? — `burn-split.py`, `--burn`
- Result (9 days, the **published** schedule used as modes: peak 13–19 UTC weekdays, weekends eco): weekly limit idx by mode eco 1.01 / normal 1.17 / **peak 0.87** (CI 0.78–1.01); the 5h limit eco ≈1.2 / normal ≈1.0 / **peak 0.82** (CI 0.72–0.93). Same picture per output tokens, per cache read, per cache write: peak is the cheapest in every unit. Mix per mode is stable (read/out ≈ 121–132).
- Per day the weekly limit burns at ≈0.078–0.092 %/$ (Mon 9 → Mon 16), no day outlier except Thu 12 (outside use) and the incomplete Tue 17 (0.156 %/$ on $71: watch it, see open points).
- **Verdict:** peak is *not* dearer; there is no evidence of the published peak penalty for Claude Code on this account. The apparent eco / early-hours excess is outside use or small bins. Matches the public reports that the peak reduction for Claude Code was removed (see §6).
- Files: outputs `burn-split-2026-11-17.txt`, `burn-report-2026-11-17.txt` (in `~/.local/state/harness-status-line/analyses/results/`) (also `--burn` part 2: the structured token model, see below).

## 2. Hours of the day and the weekday/weekend split — `burn-by-hour.py`, `schedule-tz.py`, `hour-lambda.py`
- Weekdays (wk idx, hours UTC): cheapest 13–14 (0.69–0.87), 17–18 (0.59–0.64), 12 (0.83); dear 04–06 (1.29–1.57) and 11 (1.35), 15 (1.64, $75), 19 (1.81, $45). The 5h limit says the same, stronger: 04–06 UTC ×2.1–2.8 (on $19–44 per hour) → very likely unflagged other-machine use, not a schedule.
- Weekend (Sat 14 + Sun 15): whole-day idx **0.95 / 0.92** (cheaper than average). The "peak" hours of the fitted weekend (06–10, 13–15, 21–24 UTC) stand on $5–21 per hour bin (07–09: $8/$19/$14; 14: $6; 23: $5); the steady weekend hours 17–22 UTC ($27–95) are 0.76–1.02, i.e. ordinary. **A weekend "peak" is an artefact of one weekend and tiny sums** — user noticed it on 2026-11-17 and asked.
- Timezone test (`schedule-tz.py`): the published window (05–11 local) shifted over all UTC offsets: no zone makes a window dearer inside than outside except UTC+0…+6 with 04–12 UTC (idx 1.4–2.8, on $19–260, outside-use-like). The published PT window (13–19 UTC) is the *cheapest* (idx 0.79). A user-local CET window (04–10 UTC) gives 1.63, but it is the contaminated block. No zone reading rescues the published schedule.
- Ridge strength (`hour-lambda.py`): λ 1.0 over-smooths (modes vanish), 0.3 (chosen: `HOUR_LAMBDA`) keeps weekday structure, 0.1 and 0.03 add only noise (weekday modes flip hour by hour). Weekend modes are the same noise at every λ.
- **Verdict:** weekday structure (cheap afternoon 12–19 UTC, dear early 04–06) is plausible but 9 days and probable outside use; weekend structure is not supported. Decided 2026-11-17: hours without enough data are neutral `normal` with a dot (not the published schedule).
- Fix options **not yet taken**: require ≥2 weekends or a higher `HOUR_MIN_USD` for weekend bins (e.g. $30–40) before assigning peak/eco; shrink weekend bins toward normal. Decide after ≥14 days.
- Outputs: `burn-by-hour`, `schedule-tz`, `hour-lambda` (2026-11-17).

## 3. Burn speed (limit) vs generation speed (`spd`) — `speed-vs-burn.py`
- 109 hours of the week with both a burn factor and a speed index: Pearson **−0.11**, Spearman −0.05 (first pass −0.06 / −0.02). By burn mode the mean speed index is 100.5 / 100.4 / 97.9 for eco / normal / peak (speed sd 4.7 %, burn sd 0.15): the two do not move together.
- **Verdict:** do not use the speed index to predict the limit burn or to forecast. The forecast uses the hourly burn factor only (decided 2026-11-17).
- Recheck: after ≥14 days, or if a model/endpoint change makes speed vary a lot. Output: `speed-vs-burn-2026-11-17.txt`.

## 4. Does the context size of a request change the burn? — `ctx-vs-burn.py`
Triggered by the web findings ("caps at peak hours and ballooning 1M contexts"). The token axis has `msgs` per 5-min bucket, so the average context per request = (input + cache read + cache write) / msgs (a proxy; no per-request context is stored).
- Context distribution (USD-weighted): 50–100K 28 %, 100–150K 41 %, 150–200K 20 %, 200–300K 7 %, 300–500K 2 %, >500K ≈0. Average 122K (opus), 136K (sonnet), 92K (haiku). No 1M contexts in the history.
- 62 segments of the weekly limit (each ≥2 pts, no outside-use window, 159 pts, 96 h), non-negative regression, leave-one-day-out rmse (pts; quantisation floor ≈0.4):

  | model | LOO | coefficients (pts per $) |
  |---|---|---|
  | one price for all $ (baseline) | 0.47 | 0.081 |
  | $ split by context class (<100K / ≥100K) | 0.46 | 0.099 / 0.075 |
  | $ split <100 / 100–150 / 150–200 / 200K+ | 0.48 | 0.101 / 0.072 / 0.077 / 0.078 |
  | + per request term | 0.48 | 0.039 per 1000 requests (≈0) |
  | $ × (ctx/100K)^k, k = −0.5 … 1.5 | 0.48 … 1.15 | worse as k grows (k = 1: 0.80) |
  | + msgs × ctx² (attention-like) | 0.47 | 0.000 |
  | $ by type (in / out / read / w5 / w1) | 0.45 | 0 / 0.159 / 0.037 / 0.051 / 0.097 |

- Segments by average context: 96–113K 0.099 pts/$, 113–137K 0.080–0.083, 137–241K 0.081; corr(log ctx, pts/$) −0.32. By day corr −0.42 (without the last, partial day), by hour of the week corr with the hourly burn factor −0.19 (97 hours; ctx terciles 1.04 / 1.01 / 1.01).
- **Verdict:** no surcharge for large context beyond what the $ weights already contain (large context = many cache-read/write tokens, priced). Super-linear growth with context is rejected by the data; if anything small contexts are slightly dearer per $ (an effect of the output/write mix, not significant). Do not add context to the model.
- Limits of the test: proxy context; only ≈9 % of $ above 200K (a ×2 surcharge on that part would show, a +20 % would not); nothing near 1M; whole-% resolution.
- Side hint (not a result): per type the fit weights output ≈2× and cache-write-1h ≈1.2× but cache read ≈0.45× and write-5m ≈0.6× the price table, with LOO only 0.45 vs 0.47, and types correlate at 0.999 — see §5.
- Recheck: when ≥14 days exist AND any 1M-context sessions appear in the history (grep `ctx` buckets ≥500K), or after the token mix varies independently. Output: `ctx-vs-burn-2026-11-17.txt`.

## 5. What the limit counts per token type (no price list) — `--burn` part 2
- Structured token model (type ratios on a grid, LOO by day) on one weekly window: **not identified**. The best grouping on 2026-11-17 puts the whole weight on Haiku and cache-write-1h (scale of Opus+Sonnet = 0), rmse 18.9, LOO 25.0 vs "every token alike" 26.4: all 12-weight and structured models are about equal and the level regression is noisy (rmse ~19 points; 2 windows, 9 days; the 2026-11-12 reading of the earlier 5–6 day window gave a different best, LOO 1.5). The curves out / read / write correlate at 0.999.
- **Verdict:** the type weights are not known yet; the price-table $ stays as the cost unit (it explains the per-day burn at ≈0.08–0.09 %/$ well enough). The interval regression of §4 suggests output and write-1h are heavier than their price, cache reads lighter: to be tested when the token mix varies independently and after ≥14 days (two or more windows).
- Output: `burn-report-2026-11-17.txt` (whole `--burn` report).

## 6. Outside sources (web, 2026-11-17; secondary, some dates unverified)
- No source says weekends burn the quota faster; the 2026-03 promo doubled the 5h limits off-peak and on weekends (ended 27/28 March; not counted against the weekly limit). The published peak was 5–11 AM PT on weekdays (= 13–19 UTC in winter), applied to the 5h window, the weekly limit "unchanged".
- Reportedly on 2026-05-06 Anthropic doubled the 5h limits and removed the peak-hour reduction for Claude Code on Pro and Max (Verdent, The Decoder). Consistent with §1.
- Anthropic's staff attributed fast drain to peak-hour caps and ballooning contexts (1M); a November report of faster drain since mid-October, and Anthropic admitting that some issues used more limit than intended plus a one-time reset (Startup Fortune). Explains weekly-level jumps in cost, not an hourly schedule.
- Links: [TechRadar](https://www.techradar.com/ai-platforms-assistants/claude/claude-is-limiting-usage-more-aggressively-during-peak-hours-heres-what-changed), [The Decoder](https://the-decoder.com/anthropic-says-claude-codes-usage-drain-comes-down-to-peak-hour-caps-and-ballooning-contexts/), [Techsy](https://techsy.io/en/blog/claude-2x-usage-limits-explained), [Explainx](https://explainx.ai/blog/claude-usage-limits-2026-timeline-explained), [Startup Fortune](https://startupfortune.com/claude-admits-recent-issues-used-more-limits-than-intended-and-offers-users-a-reset-but-that-is-not-enough), [Verdent](https://www.verdent.ai/guides/claude-code-limits-doubled-may-2026), [Portkey / dev.to](https://dev.to/portkey/everything-we-know-about-claude-code-limits-3984), [Anthropic](https://www.anthropic.com/news/higher-limits-spacex). Reddit searches returned nothing. Verify against the official limits page before relying on any date.

## 7. Fable (own limit system) — `fable-vs-burn.py`
User ran Fable (`fable-5-2`) on 2026-11-11, 11-14 and 11-17: 171+ requests (≈$13 at the analyzer's price, 1–4 % of the days' cost; average context 155K). `PRICES` has no Fable row, so Fable is priced with `DEFAULT_PRICE` (the Sonnet 5 row): an assumption.
- 12 weekly-limit segments contain Fable: 28 pts on $291 = **0.096 pts/$** against **0.083** for the 50 segments without it (+16 %). Regression with a separate price for Fable: other models 0.080 pts/$, Fable 0.37 pts per assumed $ (≈4.6× the other price; LOO 0.44 vs 0.47 for one price). Per token type Fable output ≈1.1 pts/Mtok, read ≈0.10 (other models: out 0.5, read 0.012): same direction, no precision.
- Single segments: 11-14 07:08–10:50 UTC (Fable 6–16 % of the $) burned 0.109–0.181 pts/$ against 0.081 expected; the others 0.07–0.10 (11-17: 0.078, 0.097, 0.086, 0.138).
- **Verdict:** consistent with Fable weighing more than its assumed (Sonnet-like) price — i.e. an unknown true price or its own multiplier — but the amount of Fable is too small to pin it down (≈3–4 extra points over 12 segments, whole-% resolution). Not enough to change the model. To do it properly: add a Fable row to `PRICES` once the price is known, collect the Fable limit counter (no source yet, see `docs/roadmap.md`), and rerun this script when Fable use is ≥$50.
- A Fable-only reset lowers the weekly % partially (see memory / `docs/requirements.md` §3); if one happens, segments across it are invalid (the script drops resets but a partial drop is read as a reset).

## Overall verdict (2026-11-17) and when to look again
1. Peak/eco by the published schedule: **not supported** for Claude Code on this account. Weekday afternoon (UTC 12–19) is the cheapest, which is the opposite of the published peak.
2. Weekend peak: noise from one weekend. Context size: no effect. Generation speed: no link.
3. The status-line modes come from the data (`derive_hourly`, preliminary until ~2026-11-23); no-data hours are neutral normal. If the modes still look flat or noisy at ≥14 days and ≥2 weekends (~2026-11-30), drop the mode icons and keep only `burn×`.
4. Fable: a few $ of use so far (1–4 % of a day, 3 days); it burns ≈15 % more per assumed $ than the other models, an estimate on 12 segments and ≈$13 (see §7). Its own limit counter is not collected.

| When | What to rerun / decide |
|---|---|
| ~2026-11-23 (≥14 days) | `--burn`, `burn-by-hour`, `schedule-tz`, `ctx-vs-burn`; the fit leaves `~`; decide on `HOUR_MIN_USD`/weekend rule |
| ~2026-11-30 (≥2 weekends) | weekend hours in `burn-by-hour` and the weekend modes; flat modes → drop mode icons |
| any 1M-context session | `ctx-vs-burn` with a class ≥ 500K |
| Fable ≥ $50 or a Fable price / counter source | `fable-vs-burn` (§7), a `PRICES` row |
| second window / token mix changes | `--burn` part 2 type weights (§5) |
