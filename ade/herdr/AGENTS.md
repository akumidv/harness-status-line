# AGENTS.md — ade/herdr

Herdr customizations (mods + layout) for the GUI app whose home is `~/.config/herdr-app`.
What each mod is and how to deploy: [README.md](README.md). This file is how to **design and change** them.
Repo-wide rules (no secrets, owner commits, repo is the source of truth): [../../AGENTS.md](../../AGENTS.md).
How the owner uses it day to day (profiles, per-space file preview, dock): [README.md](README.md) "How to use" — update it when that behaviour changes.

## Herdr's own AGENTS.md — the API source of truth

Herdr writes its own guide into the app home on every start: **`~/.config/herdr-app/AGENTS.md`** (≈1300 lines; edits there are overwritten,
and if `AGENTS.local.md` exists there, read it too). It is the reference for everything below; read the part you need, do not copy it here:

| Need | Section in `~/.config/herdr-app/AGENTS.md` |
|---|---|
| What can / cannot be done, what mods are written in (gpui-kit, no DOM) | "What you can and can't do", "What mods are written in" |
| Checking, error files, the escape hatch | "Changing things safely", "Check before you finish" |
| Spaces / tabs / terminals model, statuses | "The model: spaces, tabs, terminals" |
| Layout JSON: blocks, stacks, sizes, look, pane frames, floats, cloning | "Layouts: `layouts/<name>.json`" (+ "The look", "Pane frames", "Floating blocks") |
| App commands (`space.*`, `tab.*`, `pane.*`, `layout.*`, `terminal.*`, `mod.clone` …) | "Commands" |
| A mod: files, `mod.json`, link handlers, UI elements, the `herdr` module (`spaces()`, `panes()`, `shared`, `readTextFile`, `watchFolder` …) | "Mods: `mods/<name>/`" → "A mod", "UI", "The `herdr` module" |
| `hc` (herdr-core) | "herdr-core (`hc`)" |

The type files next to it are the truth for names and signatures: `herdr.d.ts` (data and commands), `gpui-kit.d.ts` (elements).
Both are rewritten on every start for the installed version — re-check a name there before relying on it.

## Areas — read the area's doc before designing anything in it

| Area | Layout blocks (in `layouts/my-main.json`) | Mods | Doc |
|---|---|---|---|
| Left panel (sidebar) | `sidebar` → `brand`, `spaces`, `agents`, `sidebar-footer` | `space-tree` (clone of `spaces`), `agent-list` (clone of `agents`: other profiles' agents last), `sidebar-chrome` (clone of `workspace-chrome`: brand row and footer, profile tint), `machine-add` (to be cloned) | [docs/left-panel.md](docs/left-panel.md) |
| Central window | `work` → `tabs`, `main`, `status` | `tab-bar` (clone of `tabs`), `status-bar` (clone of `workspace-status`), shipped `pane` | [docs/central-window.md](docs/central-window.md) |
| Right panel | `panel` → `panel-tabs`, `panel-body` (`files`, `changes`), `panel-shell` (dock) | `panel-tabs`, `file-panel`, `git-panel`, `panel-shell` | [docs/right-panel.md](docs/right-panel.md) |
| Transfer (all areas) | — | `herdr_transfer.py` (export / import of mods, layout, settings, groups, spaces) | [docs/transfer.md](docs/transfer.md) |
| Profiles (windows, left panel) | filter in `space-tree`, key commands; launchers outside the repo | `space-tree` (proposed: show only the spaces of this window's profile) | [docs/profiles.md](docs/profiles.md) |
| Voice input (all areas) | indicator block in the status row | `voice` (proposed: hotkey, local speech-to-text daemon, text into the focused terminal) | [docs/voice.md](docs/voice.md) |

An area doc holds: what is there now, the data it reads, the design (status: *proposed* / *accepted* / *built*),
open decisions, gotchas specific to the area. Cross-area facts (a status vocabulary, a shared key) live in
one doc and the others link to it — currently the agent-state model in [docs/left-panel.md](docs/left-panel.md#agent-state-model-shared-with-the-central-window).

## How to design a change

1. **Read** the area doc, then the shipped mod it replaces (`~/.config/herdr-app/mods/<name>/main.js`) and the relevant sections of
   Herdr's own `AGENTS.md` (table above) with `herdr.d.ts` / `gpui-kit.d.ts`. Verify names against them, they drift between Herdr versions.
2. **Design in the area doc first** (status *proposed*): data sources, state shape, shared keys, phases, what could break.
   The owner accepts consequential choices (state shape, shared keys, what is replaced) before code; record the answer in the doc.
3. **Implement here** (never in `~/.config/herdr-app`): a shipped mod is never edited — it is replaced on every start —
   it is cloned (`mod.clone`, `layout.clone`) and the clone is kept in `mods/<name>/`. Then
   `python3 herdr_sync.py deploy`, `herdr-app check mods/<name>`, `herdr_sync.py status` must say in sync.
4. **Verify on the running app**: `herdr-app shot <png>`, `herdr-app do <mod>.<command>` (the mod must be shown).
   Do not steal the owner's focus while testing — they type in the agent pane.
5. **Update the doc** (status *built*, what differs from the design) and README's table if a mod was added. Commits are the owner's.

## Conventions

- **Shared state** (`shared` / `setShared`) is how mods talk. Keys in use: `space.colors` (spaces), `panel-shell.dock`,
  `panel-shell.addrs` (right panel), `file-panel.open` (right panel, from terminal links and git-panel's Files button), `git-panel.open` (file-panel's Changes button; request channels: the asker writes, the owner reads, newest `at` wins), `agents.sort`, `sidebar.width`, `space.groups` (space-tree).
  `profile.tint` (space-tree only: the chrome colour with the profile colour mixed in; read by `tab-bar`, `agent-list`, `sidebar-chrome`),
  `space.owned`, `space.foreign` (space-tree only, when profiles are on: the ids this window owns, the active space when it is another profile's; read by `agent-list`, `panel-shell`, `status-bar`),
  `agent.since`, `agent.state` (written by space-tree only, from its poll; `tab-bar` and `status-bar` only read `agent.state`; see the left-panel doc).
  A key has one writer; readers only read. Name keys `<mod>.<thing>`.
- **Per-viewer prefs** in `localStorage` (`git-panel.prefs`, `machines.folded`); never state another mod needs.
- **Files outside the app** (state written by the status line or hooks) go to `~/.local/state/ai_env/<component>/`,
  read with `readTextFile` (`on: machine` for a remote one). Raw runtime data never enters the repo.
- **Layout file**: never `layout.set` (it deletes `layouts/.state/<name>.json`: the panel shown, sizes, the stack's child).
  Run-time edits go through the layout file with `writeTextFile` (see panel-shell); `herdr_sync.py` keeps those run-time parts.
- **Colors** from `theme()` (`t.working`, `t.blocked`, `t.done`, `t.accent`, `t.dim`, …) — not hex, so themes work.
  Space avatars use the fixed `MARK` palette (same in `spaces` and `picker`).
- **Icons**: SVG with `currentColor` in the mod's folder or `mods/workspace-chrome/` (shipped: spinner, plus, check, server, monitor, chevrons, …);
  draw with `svg(path).size(n).text_color(c)`; animated with `.spin(ms)`.

## Gotchas (all areas)

- An invalid file is not applied: Herdr keeps the last good version and writes the reason to `<name>.error` (`layout.error`, `keys.error`, …) — look there after every edit.
- `herdr-app check` runs `init()` for real: guard side effects (starting helpers, writing files) with `if (checking()) return;` (`checking` from `"herdr"`).
  `exec`, `hc` and `writeTextFile` still run during a check.
- Escape hatch if the window breaks: `ctrl-alt-shift-escape` sets the shown layout and `keys.json` aside (`*.reset-<time>`) and returns to the default `main` layout.
- `.line_height(n)` with a bare number is a multiplier.
- `text_color(undefined)` breaks the render ("expected a string, got nil").
- QuickJS interrupts long synchronous work ("error in a promise continuation: interrupted"): keep per-line loops linear, no quadratic regexes.
- `console.log` does not reach the logs: show debug text in the UI, or a temporary mod command that writes a file.
- `herdr-app do <mod>.<cmd>` works only while the mod is shown.
- Herdr rewrites the layout file (key order, drops `hidden:false`); a terminal has one size wherever it is shown.
- A space's `folder` can be home (= unset); remote ids carry a `machine/` prefix.
- `panes()` / `spaces()` copy everything on each call: read once per render, pass down.
- `layout.select` only after the child exists in `layout()` (else toast "no stack … with a child").
