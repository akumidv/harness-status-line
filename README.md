# harness-status-line

Status lines for terminal AI harnesses — **Claude Code**, **Qwen Code**, **Codex**, **MiniMax Code (mcode)** — a shared analyzer that reads the logs of all of them (plus the Claude VS Code extension, OpenCode, pi), and a single Python script that **extracts** the status-line parts of their configs into this repository and **deploys** them back on any machine.

The repository does not store whole configs, only the status line: scripts and small fragments (`statusLine` in `settings.json`, `[tui] status_line*` in `config.toml`, the `tui.statusLine` block in `config.yaml`). Everything else in the configs (keys, providers, projects) stays where it is and never reaches the repository.

## Layout

```
harness_status_line.py          # extract / deploy / status (Python 3.11+, stdlib only)
shared/                         # -> ~/.local/share/harness-status-line
  statusline-analyze.py         # daily analysis of all harness logs + --collect (every <=5 min)
  hsl-lib.sh                    # bash helpers for every status line (load mode, spd, activity)
claude/
  statusline-command.sh         # the status line itself (bash + jq)
  settings.fragment.json        # {"statusLine": {...}} from ~/.claude/settings.json
AGENTS.md                       # agent instructions: what is here, how to continue
docs/                           # requirements, architecture, measurements, roadmap
qwen/
  statusline-command.sh
  settings.fragment.json        # {"ui": {"statusLine": {...}}} from ~/.qwen/settings.json
codex/
  codex-personal/               # per profile: ~/.codex, ~/.codex-personal
    config.fragment.toml        # [tui] status_line, status_line_use_colors
mcode/
  statusline-command.sh         # the custom-command item
  config.fragment.yaml          # tui.statusLine (items) + tui.customStatusLine (the command)
```

To continue the work (agents included) see [AGENTS.md](AGENTS.md) and [docs/](docs/), first of all [docs/requirements.md](docs/requirements.md) (requirements, the `work`/`burn` terms, the calculation model).

## Usage

```bash
python3 harness_status_line.py status                  # live configs vs repository
python3 harness_status_line.py extract [--dry-run]     # live configs -> repository
python3 harness_status_line.py deploy  [--dry-run]     # repository -> live configs
python3 harness_status_line.py deploy --only claude codex-personal
```

- `--only` accepts a harness (`claude`, `qwen`, `codex`, `mcode`) or a profile (`codex-personal`).
- `deploy` **merges** fragments into existing configs: foreign keys are untouched. A `*.bak-hsl-<time>` copy is made before any edit. `--dry-run` shows the diff and writes nothing. A repeated `deploy` changes nothing.
- The `settings.json` format is normalized on merge (2-space indent); the original is kept in the backup.
- **Secret protection:** `extract` refuses to put anything that looks like `api_key`/`token`/`secret`/`password` into the repository and takes only the blocks listed above from the configs.
- `HSL_HOME=/path` runs everything relative to another home directory (for a risk-free check).

Requirements: Python 3.11+. For the Claude/Qwen status line — `bash` ≥ 4.2, `jq`, `awk`; for the analyzer — `python3`.

## Claude Code status line

```
Opus 5.5 medium · ctx 270K/1M ◷ 59m · ◆1× 4h53m 1.2× ▸1.1× 143 [140] · 5h 31%↑ 1h23m · wk 5%↑ 6d19h [→81%] · main · ~/proj
```

| Segment | Meaning |
|---|---|
| `ctx 270K/1M` | tokens in context / window size |
| `◷ 59m` | how long the prompt cache still lives (TTL 5 min or 1 h, per session) |
| `◆1× 4h53m 1.2× ▸1.1× 143 [140]` | three groups. **burn**: limit mode icon (`❄` eco / `◆` normal / `🔥` peak) + limit burn speed in the mode (normal = 1, `~` preliminary) + time to the **next** mode (its color, dimmed) · **`work`**: your pace relative to your own average · **speed**: endpoint load icon (`🐌` answers slower than usual / yellow `▸` normal / `🐇` faster, all sessions over 15 min) + `spd×` (red slower, green faster, `~` expected from the matrix) + tokens/s over the last 5 min [whole session] (`–` — no replies in 5 min) |
| `5h 31%↑ 1h23m` | 5-hour limit and time to reset |
| `wk 5%↑ 6d19h [→81%]` | weekly limit, time to reset, **[forecast]**: `→81%` — usage at reset time; red time — when the limit runs out before the reset |

**Modes** (UTC, an assumed schedule, not official Anthropic data): `🔥` peak — weekdays 13–19; `❄` eco — weekends and weekdays before 08:00; `◆` normal — the rest. The boundaries are the `PEAK_*`/`ECO_END` constants at the top of the script.

**Two separate numbers after the mode icon** — `work` and `burn`; there is no combined value, so you can steer your work: **work more while the limit burns slowly (`burn` < 1, green), and calmer while it burns fast (`burn` > 1, red)**.

1. **`work` — your pace** relative to **your own average while working** (the mean weekly-limit use per **active** hour over 14 days, idle hours excluded — profile line 7): `>1` — you work harder than usual, `<1` — calmer. It is the actual weekly-limit usage over the **current hour and the two before it** (nothing older, and not before the weekly reset) ÷ that usual use × the burn speed of those hours. An "active hour" is an hour in which you had an assistant reply (an activity log on this machine); **inactive hours are skipped**. Just started — the pace of the current hour, the two previous ones are ignored; the second hour began — over two, the third is ignored; and so on up to three. **Computed proportionally, by seconds**, not whole hours: an hour in which work started at :50 counts as 10 minutes; the current hour counts as the minutes elapsed. The base `wk%` measurement is the last one at the window start or earlier. While activity is under 2 minutes, the week since reset is used. The limit arrives in whole percents, so a short window is noisy: a "pseudo-observation" of 1 % at pace 1 is added to the measurement to damp outliers (the cost: in the first minutes `work` is close to 1). `work` does not depend on the mode. Color: **below 0.75 — cyan** (noticeably calmer than usual); **red** — above 1.5, and while the limit burns fast (`burn` > 1.05) — above 1.25; in between — no color.
2. **`burn` — limit burn speed** in the current mode relative to normal (normal = 1). This is a **measurement**: the analyzer takes the history of `wk%`/`5h%` readings and the token cost from the transcripts. While data is scarce (<150 intervals, <14 days, <$100), a **preliminary** value is used: a strongly "shrunk toward 1" regression on what exists (≥8 intervals), within 0.6–1.6 and with a sanity constraint: eco not above 1, peak not below 1 (otherwise noise would draw a "cheap peak"); marked `~`. `burn` color: green below 0.95, red above 1.05, otherwise dim (for the preliminary value too). Today, for example, eco 0.97 / normal 1 / peak 1 — no visible differences yet. If no dependence on time of day is found, `burn` = 1.

**Forecast `wk [..]`** = the sum over **all remaining hours until the reset**: your average load of that hour (matrix 1) × `work` × the burn speed of **that same hour** (matrix 2). The current mode is not used in the forecast — each hour has its own speed. This is "how many tokens will burn", compared with how much limit is left (in normalized units: remaining % divided by average speed 1). Only the remainder of the matrices is recomputed, not the whole week.

Without a profile (the first day) `work` is the old pace relative to uniform limit spending (the worse of 5h and weekly), and `burn` is not shown.

#### How the analyzer chooses modes (once a day)

The purpose is to show **roughly** where the peak is, so heavy work is not planned for peak hours. High accuracy is not needed; what matters is (1) where the mode boundaries lie, (2) mode coefficients near 1 (eco < 1, peak > 1). It is most likely a dynamic quantity, so the analyzer **keeps adjusting to recent history** (the last 42 days), without fanaticism:

- **The grid is the hour of the week (168 cells)**, three modes (eco / normal / peak) versus "no time dependence" (`flat`). More is not needed: the goal is to understand where the peak roughly is. Half-hour and finer grids were removed.
- **Mode boundaries** (peak start, peak end, eco end; UTC hours, default 13 / 19 / 8) are searched within ±2 h of the default schedule. A shift is accepted only if it lowers the cross-validation error by more than 7 %; otherwise the default schedule stays. The status line reads the boundaries from line 5 of the profile.
- **Mode coefficients** — ridge regression "percent rose by Δ at token cost $C over the week's slots"; shrunk toward 1, amplitude understated (caution).
- **Choosing flat vs 3 modes** — cross-validation by days; 3 modes are accepted only if they beat `flat` by >5 % and themselves with shuffled slot labels (null).
- Data for the 5h and weekly limits are taken together. Requirements: ≥150 intervals, ≥14 days, ≥$100 cost; otherwise preliminary coefficients (see above) and default boundaries.
- Result: `.statusline-profile.json` (`burn_factors`: grid, `schedule_utc` — boundaries and whether shifted, CV errors, coefficients by hour) and profile lines 3–5.

**Honestly about accuracy** (synthetic data with a known answer, 40 days): no false shifts on flat and standard history; a true +2 h shift was found with error ≤1 h; a −1 h shift went unnoticed (the method is cautious and misses small shifts). Readings accumulate faster thanks to recording on every whole-percent change.

Pace and forecast are recomputed on every change of `5h%`/`wk%` and at least every 5 minutes (new active hours from the collector).

**`[time to exhaustion]`** is computed in three ways, the best available:
1. from the **token cost profile**: `statusline-analyze.py` reads transcripts once a day (read-only, incremental), weights tokens by model price and type (input/output/cache) into **hourly** slots of the week and calibrates "% of limit per dollar" against the current `wk%`. This gives **two matrices** (168 hours of the week), both recomputed daily: **1) your load** (how much % of the limit you usually spend in this hour at normal burn speed) and **2) limit burn speed** (the mode coefficient for the hour). The status line takes only the **remainder** of the matrices from the current hour to the reset and multiplies each hour by your pace: load × speed × pace; the sum is the forecast;
2. from the 7-day pace in the reading history;
3. from this week's average pace (marked `~`).

Red — if the limit runs out before the reset, dim — if you make it. If you make it, the **forecast usage at reset time** `[→97%]` is shown (the remaining profile × your current pace); `∞` — only when there is no forecast or it is far away. The profile is a typical week (nights and weekends with almost no usage), so "7 % in 7 hours" does not mean "will burn out": pace is measured against how much you usually spend by this time of the week.

**Pace window** — the last 3 active hours (details in the section on price above). Recomputed on every `wk%` change, at least every 5 minutes.

### Token and model measurements

`statusline-analyze.py` (once a day, ≈4 s), besides the profile, writes a 14-day summary to `~/.claude/.statusline-profile.json`: tokens by type (input / output / cache read / cache write), token and cost usage **by model** (cost share, output tokens per hour), **by mode** (eco / normal / peak, per wall-clock hour) and by UTC hour of day. Separately, once per completed day, it appends a line to `~/.claude/.statusline-measures.jsonl` (cost, tokens and per-model breakdown for the day + the last `wk%` reading) — this journal outlives the analysis window and accumulates history for future per-model calibration.

### How resources are saved

- one `jq` per input, the rest is bash arithmetic (no `cat`/`printf` subprocesses);
- the finished line is cached for 15 s per session (refresh bursts ≈ 1 ms);
- the transcript is read incrementally (offset + accumulated state), not whole;
- the runtime cache lives in **tmpfs** (`/tmp/claude-sl-$UID`, mode 700), durable data in `~/.claude`.

| File | Where | Purpose |
|---|---|---|
| `/tmp/claude-sl-$UID/*` | RAM | caches of the line, transcript, git, mode, coefficient — safe to delete |
| `~/.claude/.statusline-history` | disk | `wk%`/`5h%` reading (+ weekly and 5h reset times) on **every whole-percent change** + a pulse every 30 min; kept forever — the **limit axis**. A `wk%` lower than the last sample of the same week is not written: an idle session keeps passing the limits of its last response |
| `~/.claude/.statusline-daily` | disk | 7-day pace, recomputed once a day (UTC) |
| `~/.claude/.statusline-profile[.json]` | disk | line 2 — matrix 1 (load by 168 hours of the week, thousandths of a % of the limit); line 3 — coefficients `eco norm peak` (×100 or `-`); line 4 — matrix 2 (burn speed by 168 hours, ×100); line 5 — mode boundaries; line 6 — `fit`/`prelim`/`-` (how the coefficients were obtained); line 7 — base of `work`: thousandths of a % per active hour; `.json` — summary |
| `~/.claude/.statusline-tokens` | disk | the **token axis**: raw tokens per 5 min, model, reasoning effort and speed (`epoch model effort speed msgs input output cache_read cache_write_5m cache_write_1h thinking`; `thinking` is part of `output`), no prices; kept forever. A file with an older header is moved aside to `.statusline-tokens.<epoch>`. Join with the history by epoch |
| `~/.claude/.statusline-analyze-state.json` | disk | incremental analysis state |
| `~/.claude/.statusline-measures.jsonl` | disk | daily journal: tokens and cost by model |

Analyzer assumptions (in `.statusline-profile.json`): prices come from the Anthropic table, but cache-write prices (1.25× / 2× of input) and Haiku 5.5 cache-read (0.1×) are **assumptions**; only the ratios matter, the absolute scale is calibrated against the real `wk%`. Transcripts see only this machine while the limit covers the whole account, so the estimate is more accurate the more work is done here.

## Other harnesses

Connections **without limits** (Qwen Code, mcode, pay-per-token, Claude via API key) have no burn group: `work×` (your token pace vs usual), then the speed group `🐌`/`▸`/`🐇` + `spd×` (how fast the models answer now vs their own median, all harnesses on that endpoint; `~` — expected from the matrix) + time to the next load mode + tokens/s [session], and today's usage `day Σ3.2M` / `day $1.23`. Activity for `work` comes from the logs of **all** sessions, including the VS Code extension where there is no status line. Details — [docs/connections.md](docs/connections.md).

- **Qwen Code** — `ui.statusLine` (type `command`) and its own `statusline-command.sh`, adapted to the fields Qwen passes on stdin; 5 s execution limit, 2 lines. `1.5× 🐌.9× – [115] · day Σ528K · Σ 900K→12K · main`.
- **Codex** — built-in status line: `[tui] status_line = [...]` (item identifiers) and `status_line_use_colors`. The `~/.codex` and `~/.codex-personal` profiles are extracted separately; for profiles without these keys `extract` creates nothing.
- **mcode (MiniMax Code)** — `tui.statusLine` is a list of built-in items (setting it replaces the defaults, so the fragment lists them + `custom-command`); `tui.customStatusLine` runs `~/.minimax/statusline-command.sh` (no shell: `bash -c '…"$HOME"…'`). mcode passes no tokens on stdin, so the segment is built from the shared collector: `1.5× 🐌.9× · day Σ528K`. Format checked against the mcode 0.7.6 bundle.
