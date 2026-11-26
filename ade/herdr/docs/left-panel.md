# Left panel (sidebar)

Rules and design procedure: [../AGENTS.md](../AGENTS.md). Status of this doc: **built** = Herdr's shipped behaviour
(and, below, what is deployed), **accepted** = designed and accepted by the owner 2026-11-25 (decisions below).
Phase 1 and the sidebar half of phase 2 are **built** (`mods/space-tree`, see "Phase 1 as built" and "Phase 2 (sidebar) as built");
the tab-bar half of phase 2 is **built** too (see [central-window.md](central-window.md)); phase 3 was **dropped** by the owner (2026-11-25).

## Now

Layout block `sidebar` (268 px, rail below 160 px) = `brand` (`workspace-chrome`) + `spaces` + `agents` + `sidebar-footer`
(`workspace-chrome` with `footer: true`). `spaces` is replaced in `my-main` by the clone `space-tree` (phase 1); the others are
**shipped** mods, replaced on every Herdr start, none cloned yet. The next bullet describes the shipped `spaces`.

- `spaces` (`~/.config/herdr-app/mods/spaces/main.js`, 573 lines): a row per space — colored avatar (`space.colors`), name,
  git tail (repo · branch ↑ahead ↓behind, only where unusual), worktrees nested under their main checkout
  (`ordered()` really moves them with `space.move`). **Already grouped by machine** once a non-local machine exists:
  a header per machine ("This computer" first; connected dot, fold, `+` new space there, reconnect / login guidance).
  Git is read ref-only on purpose (no worktree scan), refreshed by folder watches, agent turn ends and a 60 s fallback.
  The row shows no agent state ("Agent state lives in the agents list").
- `agents`: priority list (Needs you / Done / Working / Idle) or grouped by space; sub-agents as a tree; durations since the last change.
- `workspace-chrome`: brand and footer chrome; owns the shared icon set `mods/workspace-chrome/*.svg`.
- `machine-add`: the dialog for adding machines.

Data (see `herdr.d.ts`): `spaces()` → `SpaceInfo {id, name, folder, machine|null, branch, active, status, tabs[]}`;
`TabInfo {status, working_since, changed_at, …}`; `panes()` → `PaneInfo {terminal, cwd, status, program{agent,icon}, …}`;
`agents()`; `machines()`; `hc machine ls --json`. Status vocabulary from Herdr: `working | blocked | done | idle`
(`blocked` = the agent asks / needs a confirmation, `done` = finished while you were not looking, `idle` = seen);
aggregated blocked > done > working > idle.

## Design (accepted) — machine → group → space

Goal (owner, 2026-11-25): the hierarchy *machine → group → space*, the local computer being a machine like the others; per space,
next to the branch, the **changed-files count** and **dots for the running agents (tabs)** with their activity as icons and colors;
warn in **red when the prompt cache is about to expire**, and show in **blue** when a session asks a question and in **yellow** when it waits for a prompt;
the same status in the central window's tabs.

```
▾ 🖥 This computer                      ◷1 ?1 ●2        machine: worst status inside
   ▾ client-x                            ?1 ●1           group
      [A] client-x-data feat/x ↑2  ±7  ● ◷ ○ ○         space: branch, ±files, one mark per agent
      └─ wt-fix                         ±0  ●           worktree, nested (visual only)
   ▾ tools
      [H] ai_env                        ±12 ?
▸ 🖧 devbox (connected)                 ●3
```

- **Clone** `spaces` as a new mod (working name `space-tree`) and point the layout's `spaces` block at it. Keep the machine header code
  (reconnect, login, folding) as it is.
- **Groups are a view concept and manual.** `shared("space.groups")` = `{<machine|"local">: [{id, name, folded, spaces: [spaceId…]}]}`; ids of
  closed spaces are pruned (as `space.colors` is). A space in no group goes to an implicit "Ungrouped" at the top. Groups never call `space.move`.
  Order inside a group = the global space order filtered. **The shipped physical reordering of worktrees (`ordered()` → `space.move`) is dropped**:
  worktree nesting under its main checkout stays but is render-only.
- **Managing groups**: `+` on a machine header creates one, drag a space onto a group header, double-click renames, **drag a group header onto another
  group's header of the same machine to put it right before that one** (onto the machine header: to the end). The shipped
  `menu` mod's space menu cannot be extended, so a "Move to group" entry would need an own small overlay — dropped, dragging a space onto a group is enough.
- **Rail** (narrow sidebar): machine and group marks only, tinted by their worst status.
- **`±N` changed files** (dropped: the shipped row already shows the changed-files count, e.g. 9 on akmon; the design below is kept only as history): `git status --porcelain` count, per visible repo, throttled and triggered by the same events as the git tail
  (agent turn end, folder watch); never a periodic scan of every space. The shipped code avoids worktree scans deliberately.
- **Agent marks**: every pane with `program.agent` in any tab of the space, drawn with the same glyph as in the tab bar, at most 6, then `+n`.

### Agent state model (shared with the central window)

One glyph block (`stateMarks` in `mods/space-tree/main.js`, copied verbatim into `tab-bar`) used by the sidebar and by `tab-bar`, so the glyphs are identical;
the state itself is computed by `mods/space-tree/agent-state.js`. All of it comes from Herdr's own status plus one timer — no status line, no hooks,
no files outside Herdr.

| State | Source | Glyph | Color |
|---|---|---|---|
| working | `status=working` | spinner (exists) | `t.working` |
| asks (question / confirmation) | `blocked` | `?` in a ring, pulsing | blue, strong (`t.done`) |
| waits for prompt | `done` | hourglass (`mods/space-tree/hourglass.svg`): the best time to answer | yellow (`t.working`) |
| idle, seen | `idle` | hourglass, the same as `done` (not in tabs) | yellow (`t.working`) |
| sleeping (fresh context) | `sleeping`: quiet after `/compact`, `/clear` or a new session (below) | empty ring | light blue (`t.accent`) |
| cache chip | not working for ≥ 40 min | an SVG clock alone (the agents list below shows the minutes) | red (`t.blocked`) |
| cache expired | not working for ≥ 60 min | filled dot in the sleeping ring's footprint (no clock, no time) | red (`t.blocked`) |

Colours come from `theme()`: Herdr's own `t.blocked` is a red and `t.done` the only strong blue (`t.accent` is a grey-blue), so the *asks* state
deliberately does not use `t.blocked`; red is kept for the cache chip.

- **Cache timer (simplified, owner's decision):** the time since the agent's status last changed *away from* `working`
  (the last reply / the moment it started to wait). Back to `working` → the timer is cleared and the chip hidden. No thresholds beyond the
  two constants above (`CACHE_WARN_MS = 40 min`, `CACHE_EXPIRED_MS = 60 min`, one place in the code), no TTL detection, no transcripts. It applies to **every** agent
  with `program.agent`, not only Claude. The 40 / 60 min assume the 1 h cache; a 5 min cache has lapsed long before the chip shows — accepted.
- `done` ↔ `idle` (looking at a finished agent) does **not** restart the timer (same rule as the shipped `agents` mod's `track`).
- `/compact` is not special: it runs as `working`, and the timer restarts when it stops.
- **One indicator per agent (owner's choice 2026-11-26; before, glyph and chip showed side by side and read as two agents):**
  asks (`?`) or working (spinner) first, else the cache chip, else waits (yellow hourglass, `done` or `idle`) or sleeps (light blue ring, `t.accent`); a tab shows nothing for idle agents with the cache fine.
  The same single mark is used for a tab, a group and a machine header (their aggregate from `worstOf`); the rail shows a dot only when something speaks.
- Aggregation up (space → group → machine): cache chip (red) > asks > waits > working > idle > sleeping. A space whose agents
  all sleep is dimmed: grey name, faded avatar (grey number on the rail); one awake agent lights it up again (added 2026-11-26).
- **Prepare to stop (built; owner accepted 2026-11-26: prompt in the profile config, `working` only, no interrupt).** The agents list's header has an archive button.
  A click asks for confirmation ("N working agents will get the checkpoint prompt, then /compact"); *Start* sends the profile's prompt (`profile.stop_prompt`, from `herdr-profiles.json`
  `stop_prompt`, else a built-in default) with `hc send <terminal> <text>` to every agent that is `working`, is Claude Code (`program.key === "claude"`), is on this computer and in a
  space this window owns. Not `blocked` ones (the text would answer their question), not idle / done ones, not other harnesses. Nothing is interrupted: Claude Code queues the text
  until the turn ends. Every 2 s `advance()` (`mods/agent-list/stop.js`) moves each agent on: *saving* (resting 10 s = the checkpoint turn is over; work or a question restarts the
  timer; 20 min limit) -> `hc send <terminal> /compact` -> *compacting* -> *ready* (the compact marker file below, else work followed by 10 s of rest; 5 min limit -> failed).
  The strip under the header shows `n/m ready` and each agent's phase; *Cancel* only stops the following steps (what was sent stays sent). The state lives in the mod (this window only),
  nothing is written to shared state. Not verified with a real run yet.
- **After `/compact`, `/clear` or a new session an agent is quiet (built; `/clear` and new sessions added 2026-11-26).** A compacted or fresh context is small, so a cold
  prompt cache costs next to nothing and "waiting for you" is no news (a reused terminal keeps its old `since`, so without this a new session showed the red chip at once).
  A `PostCompact` hook and a `SessionStart` hook with matcher `startup|clear` of Claude Code write the epoch seconds to `~/.local/state/ai_env/herdr/compacted/<HC_TERMINAL_ID>` (the terminal id the hook inherits; an agent's `address` is that bare id
  for terminals on this computer). `space-tree` reads the folder every 30 s (one `exec`, never from render) and `publishState` (`agent-state.js`, `quietAfterCompact`) publishes such an
  agent as `sleeping` (a light blue empty ring (`t.accent`), no cache chip; it was `idle` before 2026-11-26) while it has been resting since at most 60 s after the marker (or before it). `resume` is left out on purpose: a resumed
  context is large and its cache is cold, so the chip is real news there. Any later work changes the stamp and ends the quiet. A question
  (`blocked`) and `working` are never hidden. Remote machines' agents (`machine/…` addresses) are not covered. The hook commands are in README "Compact marker".

### Data flow

Everything lives in the sidebar mod; nothing is written outside Herdr.

1. Every 250 ms tick of the mod's poll (`checkNew`, also with the rail collapsed; not from `render`) the sidebar mod reads `agents()` and keeps `shared("agent.since")` = `{<terminal address>: {s, at}}`:
   `s` = the status with `done` folded into `idle`, `at` = ms when it last changed. A new status → new `at`; unchanged → kept. Saved by Herdr in
   `shared.json`, so it survives a mod reload and a Herdr restart; an agent first seen already waiting starts counting from that sight
   (under-estimates after a restart — accepted). Entries of terminals that are gone and old are pruned.
2. It publishes `setShared("agent.state", {<address>: {status, since, cache: null|"warn"|"expired"}})`; `tab-bar` and the sidebar rows only read it.
   The sidebar mod is always shown, so the timer runs even with the rail collapsed (same pattern as `panel-shell.addrs`).
3. Remote machines: addresses carry the `machine/` prefix and `agents()` includes them — no extra work.

### Phases

1. **Built** — `space-tree`: machine → group → space, groups in `shared`, drag onto a group, worktree nesting render-only.
2. `agent.since` / `agent.state`, state glyphs, marks in the space row (**built**, sidebar half); glyphs and cache chip in `tab-bar` (**built**, see [central-window.md](central-window.md)).
3. **Dropped** (owner, 2026-11-25): `±N` (Herdr's row already shows the changed-files count), the "Move to group" overlay (drag is enough), a sound for the cache chip (not wanted).

### Phase 1 as built (`mods/space-tree`)

A clone of the shipped `spaces` (icons still come from the shipped `mods/workspace-chrome/`), referenced by the `spaces` block of `my-main`.

- **Tree**: a header per machine, **always** (also "This computer" alone), with the shipped header features (connected dot, fold, `+` new space,
  reconnect, login guidance, rail tooltips) and a new folder-plus control = new group. Under it Ungrouped (first, header only when the machine
  has groups), then the groups; a space is indented under a group header. Worktree rows nest under their main checkout **inside one
  group** (`nest()`, render-only); `ordered()`/`orderingDue`/`this.ordering` and every physical `space.move` for nesting are gone.
- **`space.groups`**: shape as designed; single writer (`editGroups`); pruned in `render()` like `space.colors`. Group id = `g<time base36>`.
- **Group header**: click folds (stored in the group), double-click renames inline (a retained `InputState` + `Input` in the header; Enter
  or losing focus saves, Escape cancels), `✕` deletes (spaces become ungrouped; shown only while the pointer is over the header, hidden it keeps
  its width so the row does not shift), the new-group control creates "Group" / "Group 2" … and
  starts the rename at once.
- **Drag**: onto a group header or the Ungrouped header → moves the space into it; onto a row → reorders with `space.move` as before, and if that
  row is in another group the space joins that group first. Restricted to the same machine. While a grouped space is dragged, an empty
  Ungrouped header is shown as a drop place. Dragging a **group header** reorders the groups (see phase 2 below).
- **Rail**: machine mark, one mark per group (chevron + initial, tooltip with the name, click folds), the compact space cells; a folded group
  hides its spaces; no Ungrouped mark.

Deviations from the design, and why:
- **Prune only settled machines**: ids of a remote machine's spaces are dropped from `space.groups` only while that machine's layout is
  `ready` (local always), and nothing is pruned while `spaces()` is empty. A machine that is away or loading lists no spaces, and pruning
  then would wipe the owner's manual groups.
- **Delete is a `✕` control**, not a right-click (a right-click is conventionally a menu; a "Move to group" menu was dropped).
- **Drop onto a row of another group** also regroups (design only named the header); it follows from "order inside a group = the global order".
- Not verified on the running app (the owner works in it): drag onto a header, inline rename (focus handoff of the text field), fold, delete.
  Static review, `herdr-app check` and a screenshot of the idle sidebar only.

### Phase 2 (sidebar) as built (`mods/space-tree`)

- **Group sort by drag**: press a group header and release over another group's header of the same machine → the dragged group goes right before
  it in the machine's array in `space.groups` (`moveGroup`, written through `editGroups`; a move that changes nothing writes nothing). Drop on the
  **machine header** = end of the machine's groups. Dropping on Ungrouped, on itself, on a row or on another machine does nothing. Drop place: a 2 px accent
  line on top of the target header (as a space drag draws it on a row), a thick accent border on the machine header, the dragged header at half opacity.
  A group drag never calls `space.move`; a drag that moved over another header does not fold on release; the ✕ and the rename field do not start a drag.
  No group drag in the rail.
- **`agent-state.js`** (pure, no Herdr imports; test: `ade/herdr/tests/agent-state.test.mjs`, run with `node`): `CACHE_WARN_MS` / `CACHE_EXPIRED_MS` (the only
  place of the 45 / 60 min), `trackSince` (the `agent.since` rule above, prunes gone terminals older than 30 days and never while `agents()` is empty),
  `publishState` (`agent.state`), `worstOf` (aggregation).
- **Writers**: `syncAgents()` in `space-tree/main.js` runs in `init`, then on the existing 250 ms `checkNew` tick; it writes `agent.since` and `agent.state`
  only when the text changed (so in practice on a status change, and when an agent crosses 45 or 60 min). A separate 30 s timer only redraws so the chip's
  minutes advance. `render` reads, never writes (except the pruning it already did). Only this mod writes both keys; `tab-bar` only reads `agent.state`.
- **Marks** (`stateGlyph`, `cacheChip`, `stateMarks`, `stateColor` — one block with a "copy verbatim into tab-bar" banner): in a space row, after the git tail, one mark per
  agent (`agents()` entries with `program.agent` in that space, any tab), at most 6 then `+n`; the row height is unchanged. Asks pulse via `.loop("opacity")`.
  Machine, group and Ungrouped headers show the aggregate of their spaces: the most urgent interaction (asks > waits > working) **and**, beside it, the cache chip of the
  agent that has been silent longest — a superset of "cache chip > asks > …", so an ask is never hidden behind a chip. A header stays quiet while all it holds is idle
  with no chip. Rail: machine, group and space marks are tinted by the aggregate (cache red > blue (asks) > yellow (working, waits) > dim).
- **Not built, dropped**: `±N` changed files, "Move to group" overlay, cache sound (phase 3).

Deviations from the design, and why:
- **250 ms poll instead of "every render and a 30 s timer"**: render must not write (see Gotchas), and the poll is the mod's existing hook for watching Herdr's
  state; the thresholds are checked on every tick, so the chip appears within a quarter of a second of the 40th minute.
- **The aggregate keeps the interaction glyph beside the chip** (see above) instead of letting the chip replace it.
- **Idle aggregates are not drawn** on machine / group headers (a mark on every header is noise); a space row always shows its agents, idle ones as yellow hourglasses.
- **Not verified on the running app** (the owner works in it): the group drag itself (press, hover, drop, no fold after a drag), and a real `blocked` / `done` / 47 min
  agent. The glyphs, chips and aggregates were seen in a screenshot of a temporary build that fed fake states to the render (reverted); the state rules by the node test.

### Decisions (owner, 2026-11-25)

1. Groups: manual only — accepted.
2. Cache thresholds: dropped; only the time since the last status update, chip at 45 min — accepted (replaces the % of TTL idea); moved to 40 min by the owner on 2026-11-26.
3. Physical worktree reordering of `ordered()` dropped in favour of visual nesting — accepted.
4. Transcripts in Herdr terminals: no action; the earlier inheritance of `CLAUDE_CODE_CHILD_SESSION` was a one-off (Herdr was started once from inside a Claude session). Not needed for this design either.
5. The status line is not touched.

## Gotchas (this area)

- A space row selects on mouse-up over the same row. Nothing may change the layout above the rows between press and release (any redraw can land in between: the 250 ms `checkNew`, an agent state write): the Ungrouped drop header appears only once a real drag starts (`dragging`), otherwise a first click on a space was lost (fixed 2026-11-26).
- The shipped `spaces` ran `space.move` writes from `ordered()` on every change (a sync conflict can lose a write, hence its ≥ 1 s retry); `space-tree` has none of that. Do not add writes in `render` except pruning `space.groups` / `space.colors`; `agent.since` / `agent.state` are written from `syncAgents` (poll), never from `render`.
- A machine away: git calls reject; keep the last shown value and mark the place `unsure`, retry later (shipped behaviour — keep it).
- At most 6 git places are looked at at once (`AT_ONCE`): a few hundred spaces exhaust a macOS app's open files and a machine core's 16 execs.
- Collapsed rail is decided by the sidebar's width (< 160 px), not by a flag.
