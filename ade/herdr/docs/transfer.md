# Transfer: export / import of the Herdr setup

Rules and design procedure: [../AGENTS.md](../AGENTS.md). Status: **built 2026-11-26** (accepted the same day; owner: "утилита экспорта и импорта … заодно и конфигурацию и моды"; the
three open choices below were taken as recommended). Cross-area: it covers the mods, the layout, the settings and the workspace (groups, spaces, tabs, panes).

## What can and cannot move

| Thing | Moves? | How |
|---|---|---|
| Our mods and `layouts/my-main.json` | yes | taken from the **repo** (source of truth); on a machine with the repo `git clone` + `herdr_sync.py deploy` does the same |
| `settings.json` | yes, an allowlist of keys | merged on import, other keys untouched, backup first |
| `keys.json` | yes | merged per binding |
| Layout clicks (sizes, the stack's shown child) | yes | `layout.resize {id, weight\|px}`, `layout.select {stack, child}` after `layout.switch`; the `.state` file is never written |
| Groups, space colours | yes | by **folder + name**, not by id; written by a mod command (no command sets shared state from outside) |
| Spaces, tabs, panes | structure only | `space.new`, `tab.new`, `pane.split`; every pane starts a fresh shell in its folder |
| Running programs, agent sessions | **no** | a process cannot move; the report lists the agent that stood in each pane, `--agents=launch` starts it fresh (no history). `--resume` is out of scope (needs the session id and transcript) |
| Not carried | | window position, `view.json`, `agent.*`, `panel-shell.*` (terminal addresses), `data/`, `installed.json` (hashes of shipped mods), split geometry (the API does not give it: panes are restored as a row of right splits) |

## The tool

`ade/herdr/herdr_transfer.py` (same conventions as `herdr_sync.py`: `HERDR_APP_HOME`, backups in `~/.local/state/ai_env/backups/herdr/<time>/`, idempotent).

```
python3 herdr_transfer.py export [FILE] [--no-workspace] [--allow-drift]
python3 herdr_transfer.py import FILE [--apply] [--only env|workspace] [--agents report|launch]
```

- **export** reads the repo (mods, layout), the app home (settings, keys, `layouts/.state/my-main.json` → sizes and the stack child only) and the open window
  (`herdr-app ls` / `panes()` data for the workspace, `shared.json` read-only for `space.groups` and `space.colors`).
  It refuses when `herdr_sync.py status` is not in sync, unless `--allow-drift`. Default file:
  `~/.local/state/ai_env/herdr/exports/herdr-<date>.tar.gz` (folder 0700, file 0600). It holds project names and paths, so it **never goes into the repo**.
- **import** is a dry run unless `--apply`: it prints every action. Order: preflight (the app answers `herdr-app status`; version differences warned) →
  env (deploy mods and layout through `herdr_sync.py --repo <unpacked dir>`; merge settings and keys with a backup) → `layout.switch my-main` and the clicks →
  workspace (spaces first, then groups and colours). A space that already exists (same machine, folder, name) is skipped. It never closes or renames anything and never takes focus (`focus: false`).
- A folder that does not exist on the new machine: the space is not created (it would silently start in home); it is skipped and reported ("clone the repo, then import again").
- Groups and colours: `herdr-app do space-tree.import-groups` — a new command in `space-tree`; the tool writes the mapping (`{machine, group, folded, spaces: [space id]}` and `{space id: colour}`
  with the **new** ids) to `~/.local/state/ai_env/herdr/import-groups.json` and the command reads it with `readTextFile`, applies it through `editGroups` (the single writer) and deletes the file.

## Bundle shape (`manifest.json` first, then)

```
manifest.json                  {version: 1, created, herdr_version, parts: [...]}
env/mods/<name>/…              our mods only (no gpui-kit.d.ts)
env/layouts/my-main.json
env/settings.json              allowlisted keys (worktrees, zoom, sounds, font_*, terminal_*, minimum_contrast, appearance)
env/keys.json
env/layout-state.json          {nodes: {id: {size, active}}}
workspace.json                 {layout, machines: [{machine|null, groups: [{name, folded, spaces: [key]}], ungrouped: [key]}],
                                spaces: {key: {name, named, folder, machine, color, tabs: [{name, named, panes: [{cwd, name, program}]}]}}}
```

`key` = `<machine|local>:<folder with ~ for home>:<name>`. Folders are `~`-relative so the bundle does not carry the user name.

## Decisions taken

1. The bundle is private, outside the repo.
2. Agents: shell + a line in the report; `--agents=launch` starts the program fresh.
3. Split geometry is flattened.

## Open / not decided

- Resume of agent sessions (a SessionStart hook writing the session id per terminal, see the 2026-11-25 checkpoint) — a separate component, not part of this tool.
- Remote machines: their spaces are exported with `machine`, but import only recreates them when the same machine name is connected; otherwise reported.

## As built / deviations

Built in [../herdr_transfer.py](../herdr_transfer.py) (+ `--repo DIR` in `herdr_sync.py`, command `space-tree.import-groups`), tests in `../tests/herdr_transfer_test.py`.

- **Data sources.** `herdr-app ls` gives no folder per pane, no `named` flag and no agent command, so: pane `cwd` comes from `hc ls --json` (this computer only), a space is `named` unless its name equals the
  basename of its folder, of its first pane's folder, or `~` (a wrong guess only keeps the name fixed instead of following the folder), a tab is `named` unless its name is its position, the agent
  `program` is `{app, run}` with `run` = the foreground command name from `hc get` (no arguments; an interpreter such as `node` gives `null`). Mod panes are not listed by `ls`, so they are not carried.
- **Where a space starts.** `space.new` starts in the first pane's folder when it exists here (else the space's folder), so an unnamed space keeps its name; on the next import a space matches by machine, name and
  *either* folder. Extra tabs start in their first pane's folder (`tab.new {folder}`); `pane.split` has no folder argument, so the other panes start where Herdr starts a split.
- **Groups and colours** follow new spaces only (existing spaces are never regrouped) and are applied only when at least one space was created, so a second import does not bring back a deleted group. A group is
  matched by name on its machine and created when missing. Groups of a machine that is not connected are skipped. The mod owns `space.colors` already (its render prunes and assigns); the command sets the
  imported colours through the same key. File format: `{groups: [{machine, name, folded, spaces: [id]}], colors: {id: colour}}`; the file is deleted with `exec rm -f` (the API has no delete) and is consumed even when invalid.
- **Settings allowlist** is exactly: `worktrees` (its folder with `~`), `zoom`, `sounds`, `minimum_contrast`, `appearance`, `font_*`, `terminal_*`. `font`, `ui_font`, `theme`, `colors`, `core` and the rest stay on the machine.
- **Dry run** still asks the app read-only questions (`status`, `ls`, `hc ls`) and runs `herdr_sync.py deploy --dry-run`; it runs no `do`. Failed steps do not stop the import; they are listed at the end (exit 1).
- **Not verified on the live app** (no spaces were created while building): the answers of `space.new` / `tab.new` / `pane.split` (taken from Herdr's guide; the pane is looked up through `ls` when `pane` is missing),
  whether `pane.split {pane}` leaves focus alone in a tab that is not shown, and the mod applying a real payload (only an empty payload was run: file consumed, shared state unchanged).
