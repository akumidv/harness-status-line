# Herdr customizations

Mods and a layout for Herdr (the GUI app, home `~/.config/herdr-app`).
Developed here and deployed with `herdr_sync.py`.

| Path | What it is |
|---|---|
| `mods/git-panel/` | VS Code–style source control for the right panel: Changes (Push / Publish next to Fetch, an "Allow private terms" checkbox that runs the next commit / push with `ALLOW_PRIVATE_TERMS=1`, staged / unstaged / conflicts, folder tree or list, inline or side-by-side wrapped diff, own font size), History (commit graph with lanes, commit diffs), Branches & worktrees (the Worktrees section appears only when the repo has worktrees besides the main checkout; otherwise the current branch heads Local branches; status of each branch against the default branch: merged / rebased / squashed / content-merged / not merged, ahead/behind, Compare with three modes, expandable commit files, switch / merge / delete / remove worktree / clean up with a two-click confirm). Commands: `git-panel.changes`, `.history`, `.branches`, `.tree`, `.list`, `.graph-all`, `.graph-head`. The open diff's header has a **Files** button (the file as it is now, in the Files panel; hidden for deleted files); Files' header has a **Changes** button that opens the file's diff here (shared `git-panel.open`; a note says when the file has no changes or lies outside the repository). A right-click opens a copy menu: on a file row (Changes, Staged, Merge changes, a commit's or a comparison's files) *Copy full path* / *path from project root* / *name*; on the header's branch name or a branch row *Copy branch name*; on the header's last commit, a History row or the opened commit *Copy hash* / *short hash* / *subject*. The opened commit's hash and message are selectable text. |
| `mods/file-panel/` | Files panel with the preview under the tree: Markdown rendered (relative links open in the panel, back/forward; the open file and its back/forward are remembered per space) or as source, code read-only with line numbers and wrap, `Open` opens the file with the system default app (an .html in the browser; `open` on macOS, the host explorer from a Linux guest on Windows, `xdg-open` elsewhere; hidden for remote files), `↗` opens VS Code (`code -g file:line`). `link.js` takes Ctrl-clicks on file paths in terminals (rule by extension in `mod.json`) and opens them here; paths relative to another folder (an agent printing `guardrails/x.md` from the parent folder) are found in the terminal's cwd, the repo root, the space folder and their direct subfolders. A Ctrl-click on a folder path (no extension) expands that folder in the tree, or makes it the tree root when it is outside the current root; a path that is neither a folder nor a known file is left to Herdr. A right-click on a tree row (file or folder) opens a copy menu: *Copy full path*, *Copy path from project root* (from the git repository that holds it, else from the Files root). |
| `mods/panel-shell/` | The **dock** under the right panel (bar block `panel-shell-bar` + stack `panel-shell-term`): its own tabs of bash terminals, each tab split right/down like the central area; `+` new tab, `◫` / `⬓` split, `cd` (to the focused pane's folder), `✕` close terminal, `⌄` hide. One dock per space, kept in shared `panel-shell.dock`; terminals are core terminals (`hc run --keep`, named `ps-<space>-<n>`, started in the workspace folder: the repo root of the space's focused / first pane away from home, else the space's folder), so they outlive Herdr. Commands: `panel-shell.new-tab`, `.split-right`, `.split-down`, `.close-pane`, `.next-tab`; palette "Toggle panel terminal". Publishes its terminals as shared `panel-shell.addrs` so git-panel and file-panel ignore focus on them. |
| `mods/panel-tabs/` | The fixed Changes / Files switch above the right panel (selects the stack `panel-body`; Changes is first and the default). |
| `mods/tab-bar/` | Clone of Herdr's `tabs`: no Files / Changes toggles (the right panel is fixed), `+` right after the last tab; per tab the agents' state (glyph + red cache chip, read from `agent.state`), a click on the program icon renames the tab in place. |
| `mods/status-bar/` | Clone of Herdr's `workspace-status` (the bottom bar): the machine; the active space's git (`branch ↑a ↓b ● n` from its own `git status`, every 10 s; a click on the branch opens Branches & worktrees, on `● n` the Changes list, in the right panel); this profile's agents by status (`working`, `needs you`, `done`, `◷` = cache warn/expired from `agent.state`; a click focuses the next such agent, cycling); the limits of the focused (else last focused) local Claude agent's account, read from the Claude status line's `/tmp/claude-sl-<uid>/terminals/<terminal>.json` → `summary.json` (dimmed after 10 min, hidden after 1 h); the palette hint. Logic in `status.js`, test: `node tests/status-bar.test.mjs`. Design: [docs/central-window.md](docs/central-window.md#status-bar-accepted-2026-11-26). |
| `mods/space-tree/` | Clone of Herdr's `spaces` for the left panel: **machine → group → space** tree. "This computer" is always a machine header (connected dot, fold, new space, reconnect, login guidance); groups are manual, kept in shared `space.groups` and never move spaces in Herdr (new group from the machine header, click folds, double-click renames inline, `✕` deletes, drag a space onto a group or Ungrouped header; dragging between rows still reorders). Groups reorder by dragging a header onto another (before it) or onto the machine header (to the end). Worktree spaces nest under their main checkout inside a group, display only. **Agent marks**: one per agent pane in the space row (working spinner, pulsing blue `?` when it asks, yellow hourglass when it waits (done or idle), light blue ring when it sleeps (fresh context), a red clock alone from 40 min without a reply and a red dot from 60 min), the worst one on machine and group headers; the mod keeps shared `agent.since` and `agent.state` (logic in `agent-state.js`, test: `node tests/agent-state.test.mjs`). Rail: machine, group and space marks tinted by their worst state. Command `space-tree.import-groups` (used by `herdr_transfer.py import`) applies groups and colours from `~/.local/state/ai_env/herdr/import-groups.json` and deletes the file. Design and status: [docs/left-panel.md](docs/left-panel.md). |
| `mods/sidebar-chrome/` | Clone of the shipped `workspace-chrome` (the left panel's brand row and footer); the only change is the background: `shared("profile.tint")`, the chrome colour with the window's profile colour mixed in (written by `space-tree` from `<home>/profile.color`; `tab-bar`, `space-tree` and `agent-list` use it too, so the title row and the whole left panel carry the profile colour). Without `profile.color` it looks like the shipped one. Test of the mixing: `node tests/tint.test.mjs`. |
| `mods/agent-list/` | Clone of Herdr's `agents` (the left panel's agents list): the same views (priority / by space), but agents that belong to spaces of **another profile** sort last (priority view: a final "Other profiles" section; rows dimmed; `alt-up/down` follow the order). Reads `shared("space.owned")` from `space-tree`; with profiles off it behaves like the shipped mod. The header's archive button is **Prepare to stop**: after a confirmation it sends the profile's checkpoint prompt to every working Claude Code agent of this window (nothing is interrupted: a working agent queues it), then `/compact` once each rests, and lists who is ready (logic in `stop.js`, test: `node tests/stop.test.mjs`; needs the PostCompact hook below for a sure "ready"). |
| `layouts/my-main.json` | A clone of Herdr's `main`: main area weight 3 / panel weight 2 (the panel can grow to 2400 px and the terminal shrinks), `spaces` → `space-tree`, `tabs` → `tab-bar`, `status` → `status-bar`, the right panel always shown: `panel-tabs` + stack `panel-body` (`changes` → `git-panel`, `files` → `file-panel`) + the dock column `panel-shell`; `link_handlers: ["file-panel"]`. |
| `themes/ai-env.json` | The layout's theme (root `theme`): Herdr's `graphite` with `surface` = `#21252b`, which `git-panel` / `file-panel` paint their card with, so the right panel is grey and the terminals dark in every profile. |
| `herdr_sync.py` | `status` / `deploy` / `extract` between this folder and the app home (`--repo DIR`: take `mods/` and `layouts/` from another folder, e.g. an unpacked bundle). |
| `herdr_transfer.py` | `export` / `import` of the whole setup to another machine: our mods and layout, allowlisted `settings.json`, `keys.json`, the layout's clicks, and the workspace (groups, space colours, spaces, tabs, panes as shells). Private bundle, never in the repo. Design and deviations: [docs/transfer.md](docs/transfer.md); tests: `python3 tests/herdr_transfer_test.py`. `space-tree` has the command `space-tree.import-groups` for it. |
| `herdr_profile.py` | Opens a Herdr window for a profile (the first profile of the local table `~/.config/ai_env/herdr-profiles.json` = the main home, the others: own home `~/.config/herdr-app-<profile>`, theme, keys, groups; same mods and layout; template [profiles.example.json](profiles.example.json)). Portable (Linux, macOS, WSL), stdlib only. Design: [docs/profiles.md](docs/profiles.md); tests: `python3 tests/herdr_profile_test.py`. |

## Use

```bash
cd ai_env/ade/herdr
python3 herdr_sync.py status             # what differs from ~/.config/herdr-app
python3 herdr_sync.py deploy [--dry-run] # repo -> app home; backups in ~/.local/state/ai_env/backups/herdr/<time>/, then `herdr-app check` per changed mod
python3 herdr_sync.py extract            # only to bring back an edit made in the app home
```

```bash
python3 herdr_transfer.py export [FILE] [--no-workspace] [--allow-drift]   # default FILE: ~/.local/state/ai_env/herdr/exports/herdr-<date>.tar.gz (0600)
python3 herdr_transfer.py import FILE                                      # dry run: prints every action
python3 herdr_transfer.py import FILE --apply [--only env|workspace] [--agents report|launch]
```

`HERDR_APP_HOME=/path` points them at another app home. After the first deploy pick the layout
in Herdr (Settings → layout, or `herdr-app do layout.switch '{"name":"my-main"}'`).
Herdr reloads changed mods by itself.

## How to use (day to day)

**Profiles: several windows, each with its own spaces, groups and theme.** Design and as-built notes: [docs/profiles.md](docs/profiles.md).

- **Create the profile table first.** Profile names, colours, icon labels and themes are not in the repo; they live in a local file, `~/.config/ai_env/herdr-profiles.json`
  (or the file named by `$HERDR_PROFILES_CONFIG`). Copy [profiles.example.json](profiles.example.json) there and edit it (format below, at "herdr_profile.py"); without it every command stops with an error that says so.
- **Open a profile.** One window per profile; they run side by side. The first profile of the table (or the one named by `"main"`) is the main window, the home `~/.config/herdr-app`; the others get `~/.config/herdr-app-<profile>`.
  Linux / macOS: `python3 herdr_profile.py launch <profile>`. On WSL the owner's local scripts wrap it: `~/workspace/herdr-launch-<profile>.sh` (options for the app go after `--`).
  Launching a profile whose window is already open only refreshes its files and does not open a second window.
- **What is the same everywhere:** the mods and the layout (installed from this repo on every launch; after editing a mod run `python3 herdr_profile.py deploy`, plain `herdr_sync.py deploy` only reaches the main home), the keys and settings copied from the main home on a profile's first start, and **all terminals and agents** (they live in herdr-core, closing a window stops nothing).
- **What differs per profile:** the **spaces shown** and the **groups** (each window has its own groups), the theme (`settings.json` of that home; change it there, or with `HERDR_PROFILE_THEME` on the first start), the active space.
- **Spaces belong to a profile.** Every space that existed before profiles belongs to the main profile. A new window starts with an empty list: press `+` in the sidebar and the new space is that window's from then on (it is claimed in `~/.local/state/ai_env/herdr/profiles.json`).
  To move work to another profile, create the space and its group there. Until the first space is created a profile window may show another profile's active space in the main area and an "other profile" row above the list.
- **Keys.** In the `my-main` layout `alt-1..9` and `ctrl-shift-pagedown/up` count only the spaces of the window you press them in (`launch` rebinds them once, with a backup of `keys.json`).
- **Add a profile.** One more entry in the profile table (`herdr-profiles.json`) and, on WSL, one more wrapper script; no mod changes (profile names are free text in `profiles.json`).
- **Switch profiles off** for one window: delete `<home>/profile.name` (and `profile.color`) and reload the `space-tree` mod (save a file in it) or restart the window; the window then shows every space again.
- **Known limits.** The agents list shows other profiles' agents last (section "Other profiles", dimmed); the "N agents" counter still counts every profile's agents. Each profile window has its own application id (`HERDR_APP_ID`, set by `herdr_profile.py`; the main profile keeps `herdr-app`), so the taskbar does not group them (restart a window started before this change), and its title is `Herdr · <profile>`.
  Icons: `desktop` stages a coloured tile icon (drawn on demand by `icons/make_icons.py` from the profile table into `~/.local/state/ai_env/herdr/icons/`, not into the repo; needs Pillow) and a `.desktop` per application id in `~/.local/state/ai_env/herdr/desktop/share`; install it with `cp -r <dir>/share/. ~/.local/share/` (Linux) or, where the desktop reads icons only from `/usr/share` (WSLg), copy it there with root rights, then restart the windows. WSLg sometimes hides the pointer after focus moves between windows (move the mouse). Verified on WSL only.

**Files panel.** The open file and its back / forward history are remembered **per space**: switching spaces shows that space's last file (or an empty preview if it had none; `×` closes the preview for that space).
The tree follows the focused pane as before.

**Dock (bash tabs under the right panel).** One dock per space. A new tab or a new dock starts in the repository root of the space's focused pane (else its first pane away from home), the same folder Files and Changes show;
`space.folder` is only the fallback. A tab that already exists keeps its folder; the `cd` button moves it to the focused pane's folder.

**Voice input** is only designed ([docs/voice.md](docs/voice.md)); nothing of it is installed.

## herdr_profile.py

```bash
python3 herdr_profile.py list                                  # profiles, homes, whether a window answers
python3 herdr_profile.py status [PROFILE]
python3 herdr_profile.py launch PROFILE [--dry-run] [-- APP ARGS]   # seed the home, then start the window
python3 herdr_profile.py deploy [--dry-run]                      # herdr_sync.py deploy into every profile home that exists (each home has its own copy of the mods)
python3 herdr_profile.py desktop [--out DIR] [--exec 'PATH/herdr-launch-{profile}.sh']   # stage a .desktop and icons per profile (taskbar icon, name, Start-menu entry); prints the install commands
```

`launch` is idempotent and does, in order: (1) for a non-main profile creates the home, runs `herdr_sync.py deploy` into it and writes
`shared.json` `{"layout": "my-main"}` when missing; on the first start only (`<home>/.seeded`) copies `keys.json` and `settings.json` from the main home
and sets the profile's theme (from the table); (2) writes `<home>/profile.name` and `<home>/profile.color` (the profile's `#rrggbb`; the mods read both); (3) creates
`~/.local/state/ai_env/herdr/profiles.json` (`{"default": "<main profile>", "spaces": {}}`, 0600 in a 0700 folder) when missing and never changes it afterwards;
(4) merges the profile's keys into `<home>/keys.json` (section `layout:my-main`: `alt-1..9` -> `space-tree.select-N`, `ctrl-shift-pagedown/up` -> `space-tree.next/prev`,
only for bindings still at Herdr's default; backup in `~/.local/state/ai_env/backups/herdr/<time>/`; a missing `keys.json` is left alone); (5) starts the window
detached (log `~/.local/state/ai_env/herdr/launch-<profile>.log`) unless `herdr-app status` already answers for that home. `--dry-run` prints every step and the command and writes nothing.
The profile table is the local JSON file described below; the main profile's home is always `~/.config/herdr-app`.

**The profile table** (`~/.config/ai_env/herdr-profiles.json` or `$HERDR_PROFILES_CONFIG`; start from [profiles.example.json](profiles.example.json)):

```json
{"main": "main",
 "profiles": [
   {"name": "main",   "label": "MN", "color": "#3b6fd4"},
   {"name": "work-a", "label": "WA", "color": "#2f9e6b", "theme": "moss"}]}
```

`profiles` (at least one): `name` (unique) and `color` (`#rrggbb`: icon tile and window tint) are required; `label` is the icon text (default: first two letters of the name, upper-case);
`theme` (optional, set on the first start); `stop_prompt` (optional: what the agents list's **Prepare to stop** button sends to the working agents before `/compact`, see [docs/left-panel.md](docs/left-panel.md); the launcher writes it to `<home>/profile.stop_prompt`); `home` (folder under `~/.config`, default `herdr-app-<name>`) and `app_id` (default: the home folder name) are optional and must be unique.
`main` (optional, default: the first entry) names the main profile: its home and application id are always `herdr-app`, whatever `home` / `app_id` say. Invalid values stop the tool with a message that names them.

| Env | Meaning |
|---|---|
| `HERDR_LAUNCHER` | Start command (shell-split, app args appended) used instead of `herdr-app`. A platform wrapper, e.g. WSLg scaling, is injected through `HERDR_LAUNCHER` by the caller. |
| `HERDR_APP_BIN` | Path of `herdr-app` when it is not on `PATH` (also used for the `status` check). |
| `HERDR_PROFILE_THEME` | Overrides the profile's theme (first start). |
| `HERDR_PROFILES_CONFIG` | The profile table; default `~/.config/ai_env/herdr-profiles.json`. |
| `HERDR_PROFILE_STATE_DIR` | Replaces `~/.local/state/ai_env/herdr` (tests). |

The child gets `HERDR_APP_HOME=<home>` and `HERDR_PROFILE=<profile>`. Tests use a temp `HOME` and a fake `herdr-app`.

## Compact marker (quiet agents after `/compact`, `/clear` or a new session)

After `/compact`, `/clear` or a new session an agent shows as idle with no red cache chip and no blue "waiting" dot until it works again (a compacted or fresh
context is small, so a cold cache costs next to nothing; design: [docs/left-panel.md](docs/left-panel.md), "Agent state model"). It needs two hooks in Claude
Code's `settings.json` (`~/.claude`, and `~/.claude-personal` if you use it), `PostCompact` and `SessionStart` with matcher `startup|clear`, both running the same
command; the command is the whole contract, add them once per machine next to any other hooks of those events:

```json
{"hooks": {
  "PostCompact": [{"hooks": [{"type": "command", "timeout": 10, "command":
    "[ -z \"${HC_TERMINAL_ID-}\" ] || { d=\"${XDG_STATE_HOME:-$HOME/.local/state}/ai_env/herdr/compacted\"; mkdir -p \"$d\" && date +%s > \"$d/$HC_TERMINAL_ID\"; }"}]}],
  "SessionStart": [{"matcher": "startup|clear", "hooks": [{"type": "command", "timeout": 10, "command":
    "[ -z \"${HC_TERMINAL_ID-}\" ] || { d=\"${XDG_STATE_HOME:-$HOME/.local/state}/ai_env/herdr/compacted\"; mkdir -p \"$d\" && date +%s > \"$d/$HC_TERMINAL_ID\"; }"}]}]}}
```

It writes the time to `~/.local/state/ai_env/herdr/compacted/<terminal id>` (runtime data, never in the repo); `space-tree` reads the folder. Running sessions pick the hook up after a restart.

## Notes for development

Designing or changing a mod: start with [AGENTS.md](AGENTS.md) (procedure, conventions, gotchas) and the area docs —
[left panel](docs/left-panel.md), [central window](docs/central-window.md), [right panel](docs/right-panel.md). The API is described in
Herdr's own guide, `~/.config/herdr-app/AGENTS.md`. The short list below stays as the quick reminder.

- Herdr's shipped mods and layouts are replaced on every start: never edit them, clone them (`mod.clone`, `layout.clone`) and keep the clone here.
- Never rewrite the layout with `layout.set`: it deletes `layouts/.state/<name>.json` (which panel is shown, sizes, the stack's child). panel-shell edits the layout file instead, so the dock stack `panel-shell-term` changes at run time; `herdr_sync.py` compares it as a placeholder and keeps the live value on deploy.
- Check a mod after every change: `herdr-app check mods/<name>` (run in the app home). `console.log` doesn't reach the log; show debug text in the UI. `.line_height(n)` with a bare number is a multiplier. QuickJS interrupts long synchronous work, so keep per-line loops linear.
- Screens for checking: `herdr-app shot <png>`; mod commands: `herdr-app do <mod>.<command>` (the mod must be shown).
- The API reference is in the app home: `AGENTS.md`, `herdr.d.ts`, `gpui-kit.d.ts` (Herdr regenerates the latter in each mod folder; it is not kept here).
- The icons in `file-panel/` and parts of the code started as copies of Herdr's shipped `files` / `changes` mods (Apache-2.0).
