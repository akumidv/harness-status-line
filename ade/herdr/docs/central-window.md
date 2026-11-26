# Central window

Rules and design procedure: [../AGENTS.md](../AGENTS.md). Status: **built** — *Now*, the status glyphs and the quick rename by the icon (2026-11-25); **built** — the status bar (2026-11-26).

## Now

Layout column `work` = `tabs` (42 px, mod `tab-bar`, `window_drag`) + `work-body` (`main` + the right panel, see [right-panel.md](right-panel.md)) + `status` (28 px, mod `status-bar`).
`main` is the block showing the active tab (`content.tab: "active"`) with the pane chrome: header mod `pane` (30 px, always), 10 px radius, 8 px gap,
focus ring in `accent`. Weight: main 3 / panel 2.

- `tab-bar` (`mods/tab-bar/main.js`) — a clone of the shipped `tabs` (0.1.18): the Files / Changes toggles are gone (the right panel is fixed) and `+`
  sits right after the last tab. Per tab: program icon of its lead pane (first agent pane, else first pane), name (follows the agent's live title when
  the tab is unnamed, `clean()` strips status glyphs), `Z` when zoomed, the agents' **status** marks, close button on the active tab.
  Double-click renames (Herdr's own prompt), a click on the icon renames in place, drag moves (`tab.move`), right-click → shipped `menu` popup,
  wheel / swipe switches tabs, window buttons on Windows/macOS.
- `status-bar` (`mods/status-bar/main.js`, pure logic in `status.js`, test `node tests/status-bar.test.mjs`) — a clone of the shipped `workspace-status` (0.1.18):
  machine, git of the active space, this profile's agents by status, the focused Claude agent's limits, palette hint; design in [Status bar](#status-bar-accepted-2026-11-26).
  The profile filter reads `shared("space.owned")` (space-tree) with a verbatim copy of `isForeign` from `agent-list/foreign.js`.
- The pane header (`pane`) and the terminal rendering are shipped mods, not cloned.

Data: the active space's `TabInfo[]` (`status`, `named`, `zoomed`, `working_since`, `changed_at`), `panes()` for the lead pane's `program`, `title`, `terminal`, `agents()` + `shared("agent.state")` for the status marks.

## Status in tabs and quick rename (built 2026-11-25)

### The same agent status as in the sidebar

The tab shows the glyphs of the agent-state model in [left-panel.md](left-panel.md#agent-state-model-shared-with-the-central-window):
interaction glyph (working spinner / asks `?` / waits = yellow hourglass) and a separate red cache mark (a clock from 40 min since the agent stopped working, a filled dot from 60 min).
A tab's state = `worstOf` over its agents (the `agents()` entries with `program.agent` in that tab): cache chip > asks > waits > working, the glyph first, then the chip.
`tab-bar` only **reads** `shared("agent.state")` (an agent not stamped yet shows by Herdr's own status); it writes nothing, reads no files and runs no `exec`.

As built:
- **Two copies of one block.** Mod folders cannot import each other, so the glyph block (`stateGlyph`, `cacheChip`, `stateMarks`, `stateColor`) and `worstOf` / `URGENCY`
  are copied into `tab-bar/main.js` under a banner naming the source (`space-tree/main.js`, `space-tree/agent-state.js`). Change them in `space-tree` first and copy;
  `stateMarks` and `stateColor` are copied but unused in the tab bar. The tab composes its own `tabMarks` (below).
- **Idle (seen) agents show nothing in a tab** — decided: no ring, tabs stay quiet. An idle agent whose cache chip is due shows the chip alone.
- **Minutes advance** through a 30 s `cx.notify()` timer in `tab-bar` (skipped while `checking()`); the state itself still comes from the sidebar's 250 ms poll.
- The `on_size_change` → `scroll_to_item(active)` hook is kept, so a widening tab does not push the active one out of view.

### Quick rename by the icon

The owner names tabs with a **marker + a short description**. A single click on the program icon starts an inline rename of **that** tab (the active one or not):
a retained `InputState` + `Input` replaces the name (150 px, accent border), prefilled with the name shown — a named tab's own name, an unnamed tab's clean label —
with all of it selected. Enter or losing focus saves through `tab.rename {id, name}`, Escape cancels. The same pattern as the group rename in `space-tree`
(focus asked again on the next render, `renaming` cleared first so the blur it causes is a no-op).

As built:
- The icon is a small `cursor_pointer` handle with a hover background and the tooltip "Rename tab". Its press stops before the tab: no drag starts, and the release
  does not `tab.select` (selecting could move focus to the terminal and so blur the field at once), so the clicked tab is **not** activated. The field's own press behaves the same.
- An empty or unchanged name writes nothing (unchanged would turn a live-titled tab into a named one); to return to Herdr's automatic name use the shipped double-click prompt and clear it.
- The rename is dropped if its tab or space disappears while it is open.
- `clean()` is applied only to an unnamed tab's live agent title, never to `tab.name` of a named tab, so a leading marker (`🔴`, `●`, `[ ! ]`) is kept — checked with node.
- Deviation: double-click on the name still opens Herdr's own rename prompt (`tab.rename` without a name); only the icon click is inline.
- Not built, not decided: the marker palette (a row of the owner's markers, `shared("tab-bar.markers")`); the owner types the marker.

## Status bar (built 2026-11-26)

Goal: the bottom bar shows what no other place in the window shows and turns its labels into actions; account-wide
limits move here from the Claude Code status line when Claude runs inside Herdr.

Mod `status-bar` (clone of the shipped `workspace-status`), in `my-main.json` block `status`. Left to right:

1. **Machine** — as now.
2. **Git of the active space** — `⎇ branch ↑a ↓b ● n`. Own `exec git status --porcelain=v2 --branch` in the space's
   folder (`on: space.machine`), on space change and every 10 s while the window is visible (`cx.timer`, never from
   `render`). Click on the branch → right panel + `git-panel.branches`; on `● n` → `git-panel.changes`
   (`layout.show {id:"panel"}`, `layout.select {stack:"panel-body", child:"changes"}`, then the command).
3. **Agents of this profile by status** — `● 2 working · ● 1 needs you · ● 1 done · ◷ 1`; colours from the theme.
   Click on "needs you" / "done" / `◷` → `agent.focus` on the next such agent (cycles). `◷` counts agents whose
   `shared("agent.state")` entry has `cache` `warn`/`expired` (written by `space-tree`). Agents are filtered by the
   window's profile with the same rule as `space-tree`.
4. **Limits of the focused agent's harness** — shown only for a harness that has them:
   - Claude Code (`program.key === "claude"` or `app === "claude-code"`): `◆ 1.0× → 4h53m · work 1.2× · 🐇1.1× ·
     5h 31% 1h23m · wk 47% 6d19h [→81%]` — the account-wide part of today's Claude status line, same glyphs and colours,
     of that agent's own account.
   - Focus not on an agent → the last focused agent of this window; none yet → the block is hidden.
   - Other harnesses → hidden for now (Codex shows its own limits natively; Qwen / MiniMax have no limit data).
5. **Palette hint** — as now.

### Data contract with harness-status-line (accepted 2026-11-26)

The Claude status-line script stays the only place that computes and formats limits, burn mode, work, speed and
forecast; the bar only lays out what it gets. Format owner: harness-status-line
([docs/architecture.md](../../../harness-status-line/docs/architecture.md)). Inside Herdr (below) the script writes,
atomically (temp + `mv`), on every fresh computation of the line:

- `/tmp/claude-sl-$UID/terminals/<HC_TERMINAL_ID>.json` — `{"v":1, "t":<epoch>, "account":"<label>", "dir":"<abs
  account cache dir>"}`; `account` is `default` or the suffix after `~/.claude-` (`personal`), `dir` is the account's
  tmpfs cache dir (`/tmp/claude-sl-$UID` for the default account, `…/<ACCT>` otherwise).
- `<dir>/summary.json` — `{"v":1, "t":<epoch>, "account":"<label>", "segments":[{"id", "text", "tone", "tip"}…]}`;
  `id` ∈ `burn` (mode icon + `burn×`), `next` (time to the next mode), `work`, `spd`, `five`, `wk`, `forecast`, in
  display order, each present only when the line would show it; `text` without ANSI; `tone` ∈ `none` / `dim` / `good`
  (green) / `cool` (cyan) / `warn` (orange) / `bad` (red) / `mode-eco` / `mode-normal` / `mode-peak` — the same
  thresholds as the line, decided by the script; `tip` an optional one-line explanation.

The bar maps the focused agent's address → `terminals/<address>.json` → `summary.json`, so every agent shows the
limits of its own account (`claude` vs `claude-personal`). It reads only local agents for now (a remote space's
agent → block hidden). Older than 10 min → dimmed with the age in the tooltip; older than 1 h → hidden.

### Claude status line inside Herdr (accepted 2026-11-26)

`statusline-command.sh` treats itself as inside Herdr when `HC_TERMINAL_SOCKET` and `HC_TERMINAL_ID` are set (set by
herdr-core in every terminal it runs; observed, not in Herdr's guide) and `HSL_FULL` is not `1`. Then it writes the
two files above and prints only the per-session part: model/effort, `agent:`/`wt:`, `ctx` + `◷` cache TTL,
`t/s [session]`, git, cwd. The account-wide part (burn mode, next mode, work, speed, 5h, wk + forecast) moves to the
bar. Outside Herdr, or with `HSL_FULL=1`, it prints everything as before and writes nothing new. The fallback block
without `rate_limits` stays as is. One script, nothing differs at deploy time.

As built (deviations and additions):
- Agent counts: "needs you" uses `t.done`, "done" the yellow hourglass (`t.working`, as in the sidebar; changed 2026-11-26), `◷` uses `t.blocked` — red stays for the cache, as in the sidebar
  (one constant, `AGENT_COLOR` in `mods/status-bar/status.js`). With nothing to count the block says `N idle` / `no agents`.
- Profile filter: reads `shared("space.owned")` written by `space-tree` + a verbatim copy of `isForeign` from
  `mods/agent-list/foreign.js` (a test keeps the copy identical) instead of re-reading the profile file.
- An account's `summary.json` is written by whichever of its sessions rendered last; a value marked `≥` (stale, from
  that session's last response) can therefore replace a fresher one until another session renders.
- Tests: `ade/herdr/tests/status-bar.test.mjs`; `harness-status-line/tests/statusline_herdr_test.py`.

Not decided: Qwen / MiniMax provider load (`work× spd× day Σ` from the collector's `rt-<host>`) in block 4; remote agents.

## Gotchas (this area)

- A mod's `init` is `init(props, cx)`; `init(cx)` gets the props as `cx` and fails only in the live window — `herdr-app check` passes because `checking()` returns before `cx` is used. Check `herdr-app status` → `errors` after a deploy.
- A tab that changes width (a new status mark) can push the active tab out of view: keep the `on_size_change` → `scroll_to_item(active)` hook for tabs up to the active one.
- Do not read files or run `exec` from `render()` of the tab bar; it re-renders on every pane change.
- A new tab/pane that must not take the owner's focus while testing: use `space.new {focus:false}` / existing tabs, not `tab.select`.
