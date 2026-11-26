# Profiles: several Herdr windows, each showing its own spaces and groups

Rules and design procedure: [../AGENTS.md](../AGENTS.md). Status: **built 2026-11-26, not yet verified in a profiled window**: `herdr_profile.py` (+ `tests/herdr_profile_test.py`), `space-tree` filter, claim and key commands (`mods/space-tree/profile.js`, `tests/profile-filter.test.mjs`) are deployed; the agents list sorts other profiles' agents last (`mods/agent-list`), the status count is still general; the as-built notes are at the end (proposed 2026-11-26; owner's wish: groups belong to a profile, a profile opens in its own window
with its own theme, ideally switchable inside Herdr, otherwise by scripts; same layout and mods everywhere, only the spaces and groups shown differ).
Cross-area: the left panel (`space-tree`), keys, the dock, the launcher scripts, the window icon. Target platforms (owner, 2026-11-26): WSL, Linux and macOS, "desirably" the last two; verified so far on WSL only.

## What is known (spike 2026-11-26, two windows run side by side)

| Thing | Fact | How it shapes the design |
|---|---|---|
| A window | One `herdr-app` per app home (`$HERDR_APP_HOME`, default `~/.config/herdr-app`); the socket is in the home, so two homes run at once. `herdr-app status/ls/do/shot` pick the window by `HERDR_APP_HOME` | a profile = an app home + a launcher |
| What is per window | `settings.json` (theme, fonts), `keys.json`, `layouts/`, `mods/`, `shared.json` (groups, colours, dock, agent state), `view.json` (which space and tab is active) | theme and look per profile come free; groups are already per profile |
| What is shared | **Spaces, tabs and terminals live in herdr-core**: a new window lists every space (a fresh home showed all 5, ungrouped) | "a profile owns spaces" must be built: the sidebar has to filter |
| Active space | Per window: the two windows reported different active spaces at the same time | a window can sit in a space the other window does not show |
| A fresh home | Starts with the shipped layout `main`, shipped mods and default keys; our mods and `my-main` arrive only through `herdr_sync.py deploy`; `shared.json` `{"layout": "my-main"}` selects the layout at start | the launcher seeds the home (done, see Launchers) |
| Dock collision | The dock mod of the second window tried to create `ps-<space>-<n>` terminals that the first window's dock already owns ("pick another name") and left one stray terminal | a space must never be shown by two windows (single owner), or the dock names must carry the profile |
| Window icon / grouping | `herdr-app` reads **`HERDR_APP_ID`** (verified: the Wayland app id in the WSLg log became `herdr-app-probe`) and `set_class_name` for X11. With one id all profile windows share one taskbar group. WSLg looks the icon up by app id in `/usr/share/applications` only (log: `appId:herdr-app … entry (nil)`); `~/.local/share/applications` is not seen, so every window shows the Linux icon | the tool sets a distinct `HERDR_APP_ID` per profile (built); a root-owned `.desktop` + PNG per id is still needed for icons (the owner copies them in with root rights); badges on the Windows taskbar are not reachable from WSL directly |
| Cursor | The pointer vanished twice until the mouse moved: once ~3 min after the second window started (log: `cursor role is added after creation`), once when the second window was closed (no log line). Both fit WSLg not re-sending the pointer when focus moves between windows | not ours; mouse move brings it back; expect it with several windows |

## Launchers (built, outside the repo for now)

Profiles (owner, 2026-11-26): the **main** profile (the current window and home `~/.config/herdr-app`, all existing spaces and groups) and further profiles (new homes `~/.config/herdr-app-<profile>`,
start empty; the owner recreates spaces and groups there by hand). Their names, colours and labels are the owner's own and live in the local profile table, not in the repo ([Profile table](#profile-table)).
These bash scripts in `~/workspace` are the first, WSL-only version (superseded by `herdr_profile.py`, below): `herdr-profile.sh <profile>` holds the logic and `herdr-launch-<profile>.sh` are one-line wrappers;
all go through the owner's `launch.sh herdr` (scale, proxy, Wayland). `herdr-launch.sh` (no profile) stays as it was.

- Every start writes `<home>/profile.name` and `<home>/profile.color` (the mods read them).
- A non-main profile on every start runs `herdr_sync.py deploy` for its home (mods and layout from the repo); on the first start it writes `shared.json` `{"layout": "my-main"}`, copies `keys.json` and `settings.json`
  from the main home and sets the theme (the profile's `theme` from the table, none = unchanged; `HERDR_PROFILE_THEME` overrides). `.seeded` marks it done.
- `HERDR_PROFILE` selects the launcher log (`/tmp/herdr-launch-<profile>.log`, one line in `apps.d/herdr.conf`).
- Not verified: the non-main homes were only dry-run (seeded, nothing started); `personal` from the spike was started and closed and its home removed.

## Design: a window shows only the spaces of its profile

```
home/profile.name            "<profile name>"                              written by the launcher; absent = profiles off (today's behaviour)
home/profile.color           "#rrggbb"                                     written by the launcher next to profile.name; read by the mods for the window tint
~/.local/state/ai_env/herdr/profiles.json
                             { "default": "<main profile>", "spaces": { <spaceId>: <profile> } }
space-tree                   mine = spaces().filter(owner(id) === thisProfile)   used for every list, group, count and drop
```

- **Ownership** `owner(id) = profiles.spaces[id] ?? profiles.default`. Existing spaces therefore stay in the default profile with no migration.
- **Claiming.** A window of a non-default profile claims a space that appeared **after** its mod started and is active **in this window**
  (that is how a space created here shows up); the claim writes `profiles.spaces[id]`. A space created in the default window needs no claim. Until the claim lands (under a second)
  the other window may show the new space for an instant. The ids present when the mod starts are never claimed (they belong to the default profile or to whoever claimed them earlier).
- **No move command** (owner, 2026-11-26): a space stays with the profile that owns it; to put work into another profile the owner recreates the space and its group there (a handful). Existing spaces stay with the default (main) profile.
- **Writer rule.** `profiles.json` is a file, not shared state (shared state is per home). Writers: `space-tree` of any window (claims only), read-modify-write of one id; reader: all; the launcher tool creates the file once if it is missing and never rewrites it. It holds ids only, no names or paths.
- **Groups** stay per home (`space.groups`), so each profile has its own groups with no change. Group pruning looks only at `mine`.
- **Keys.** (`herdr_profile.py launch` writes the rebinding into `keys.json` section `layout:my-main`, only over default-valued bindings, with a backup.) `alt-1..9` and `ctrl-shift-pagedown/up` (`space.select:N`, `space.next`, `space.prev`) count **all** spaces. In a profiled window they must count `mine`, so `space-tree` gets commands
  `select-1..9`, `next`, `prev` and each home's `keys.json` rebinds to them (a profile without `profile.name` keeps the old keys). Needs a check that a mod command can sit in `keys.json` `global` (it can, per Herdr's guide, if the mod is shown).
- **Foreign active space.** Another mod (`git-panel` "Open space", the agents list, a terminal link) can still `space.select` a space of another profile. The window then shows a space missing from its sidebar. First version: the sidebar adds
  a one-line "other profile" row for the active foreign space so the owner is never lost; it is not added to `mine`.
- **Dock.** No change while one space has one owner (no two docks for a space). The name collision above came from showing every space in both windows.
- **Agents list:** the clone `mods/agent-list` (of the shipped `agents`) sorts the agents of other profiles' spaces **last** (never hidden): in the priority view a final section "Other profiles", in the by-space view their spaces come last, rows dimmed, `alt-up/down` (`setAgentOrder`) follow the same order. It reads `shared("space.owned")`, published by `space-tree` (the ids this window owns; null when profiles are off). The status bar (`mods/status-bar`, clone of the shipped `workspace-status`) counts only this window's agents, by the same `shared("space.owned")` rule ([central-window.md](central-window.md#status-bar-accepted-2026-11-26)).
- **Opening a profile from inside Herdr** (phase 2): a `space-tree` command that starts the profile's launcher with `setsid` (a mod `exec` is capped at 10 min, the launched window outlives it) and a menu entry per profile.
  The launcher path would be read from `profiles.json` (`"launchers": {"work-b": "~/workspace/herdr-launch-work-b.sh"}`), not hard-coded.

## Profile table

Status: **built** (the launcher, icons and the window tint).

Profile names, colours, icon labels and themes are the owner's own data and are **not in this repo**. They live in a local JSON file:
`$HERDR_PROFILES_CONFIG` if set, else `~/.config/ai_env/herdr-profiles.json` (resolved once in `Config.from_env()` and passed down). `profiles.example.json` next to `herdr_profile.py` is the neutral template
(`main`, `work-a`, `work-b`); without the file every command stops with an error naming the path to copy it to.

```json
{"main": "main",
 "profiles": [
   {"name": "main",   "label": "MN", "color": "#3b6fd4"},
   {"name": "work-a", "label": "WA", "color": "#2f9e6b", "theme": "moss", "home": "herdr-app-work-a", "app_id": "herdr-app-work-a"}]}
```

| Key | Meaning | Default |
|---|---|---|
| `main` | name of the main profile (owner of every existing space, `profiles.json` `default`) | the first entry |
| `profiles[].name` | unique profile name (free text; the key in `profiles.json`) | required |
| `profiles[].color` | `#rrggbb`; the icon tile and the window tint | required |
| `profiles[].stop_prompt` | the checkpoint prompt the agents list's "Prepare to stop" button sends (any text, may span lines); written to `<home>/profile.stop_prompt`, removed when unset | optional (the mod has a default) |
| `profiles[].label` | icon text | first two letters of the name, upper-case |
| `profiles[].theme` | Herdr theme set on the first start | none (the main settings' theme stays) |
| `profiles[].home` | folder under `~/.config` | `herdr-app-<name>`; the main profile **always** `herdr-app` |
| `profiles[].app_id` | window application id (taskbar grouping, `.desktop` name) | the home folder name; the main profile always `herdr-app` |

Validation (a `ProfileError`, exit status 1, the message names the value): at least one profile, unique `name` / `home` / `app_id`, `color` is `#rrggbb`, `main` names an entry, `home` / `app_id` have no path separators.

- **`<home>/profile.color`.** On every launch the tool writes one line, the profile's `#rrggbb`, next to `profile.name`. The mods read it (the same way they read `profile.name`).
- **Window tint (built).** `space-tree` reads `<home>/profile.color`, mixes 24 % of it into the theme's `chrome` colour (`mods/space-tree/tint.js`, test `node tests/tint.test.mjs`, so it follows a theme change and light or dark) and publishes the result as shared `profile.tint` (one writer; a change only). `tab-bar` (the title row and its inactive tabs), `space-tree`, `agent-list` and `sidebar-chrome` (a clone of the shipped `workspace-chrome`, the brand row and the footer) paint with `shared("profile.tint") ?? t.chrome`, so the whole left panel and the title row carry the profile's colour in a dark shade; a window without `profile.color` looks as before. The layout's own `sidebar` background stays `chrome` (it is only visible in gaps). Nothing in the launcher depends on it.
- **One palette inside, the profile only on the edges (owner, 2026-11-26).** The owner's profiles set no `theme`: every window uses the main home's theme, so the inner panels (terminals, panes, right panel, status bar) look the same in all profiles, and a profile differs only by the tint of the left panel and the title row. `profiles[].theme` stays supported for whoever wants a whole-window theme; an existing home keeps the theme its `settings.json` already has (the launcher sets it on the first start only), so dropping it means removing `theme` from that home's `settings.json`. The look itself is the repo's own theme `themes/ai-env.json` (deployed by `herdr_sync.py` like layouts), set as the root `theme` of `layouts/my-main.json`: `graphite` everywhere except `surface`, which is atom-one-dark's grey `#21252b`; `git-panel` and `file-panel` paint their card with `surface` and the dock column's frame is `surface`, so the right panel is grey and the terminals dark (owner 2026-11-26). The dock's terminal stays dark: a terminal always uses the theme's `background`. Herdr ignores `theme` and `colors` on nested layout nodes (tried 2026-11-26, live and after a layout rebuild), so a per-area look has to come from colors the mods choose. A window open while the theme file first lands keeps its old theme until `herdr-app do app.theme '{"name":"ai-env"}'` or a restart; a theme picked in Herdr's own menu overrides the layout's.
- **Icons.** `icons/make_icons.py` reads the same table and draws one tile per profile and size (`<name>-<size>.png`) into `~/.local/state/ai_env/herdr/icons/` (`HERDR_PROFILE_STATE_DIR` / the same state dir as the launch logs); nothing is written into the repo.
  `herdr_profile.py desktop` runs it on demand when an icon is missing or older than the table (needs Pillow, only for that step).

## Platforms (WSL, Linux, macOS)

| Part | WSL (verified) | Linux / macOS (not verified) |
|---|---|---|
| App home | `~/.config/herdr-app`, `$HERDR_APP_HOME` | Herdr's guide and `herdr.d.ts` give the same path on every platform (`appHome()`); macOS to be checked once |
| Mods, filter, keys, `profiles.json` | JS in the mods; the file under `~/.local/state/ai_env/herdr/` (same convention as backups) | identical: nothing platform-specific; `window().platform` is not needed |
| Launcher | the owner's local `launch.sh herdr` (WSLg scale, proxy, Wayland, `setsid`) is injected through `$HERDR_LAUNCHER` by the local scripts | **no `launch.sh`**: start `herdr-app` detached (`start_new_session=True`, which also works on macOS where `setsid` does not exist); binary from `PATH` or `$HERDR_APP_BIN` |
| Window icon / taskbar | WSLg: root-owned `.desktop` + PNG, one per profile (phase 4) | Linux: a per-profile `.desktop` in `~/.local/share/applications` works without root (own app id); macOS: a tiny `.app` bundle per profile with its own icon (Dock icon; badge unverified) |
| Pointer loss | seen | WSLg only |

So the launcher is **one portable Python tool in this repo**, `herdr_profile.py` (stdlib, same style as `herdr_sync.py` / `herdr_transfer.py`): `launch <profile> [-- options]`, `list`, `status`.
It does the seeding (calls `herdr_sync.py deploy` for the home, writes `profile.name`, the keys merge, first-start copies), then starts the app: `$HERDR_LAUNCHER` if set, else `$HERDR_APP_BIN`, else `herdr-app` from `PATH`. It has **no platform-specific code**; a wrapper (WSLg scale, proxy, Wayland) is injected by the caller through `$HERDR_LAUNCHER`.
The profile table (names, homes, themes, colours, labels) is a local file read by the tool ([Profile table](#profile-table)). The owner's local scripts in `~/workspace` (`herdr-profile.sh` and one `herdr-launch-<profile>.sh` per profile) are the **WSL wrapping**: they set `HERDR_LAUNCHER` to the existing `launch.sh herdr` and call the tool. They stay local, outside the repo (owner's decision); on Linux or macOS the tool is called directly.

## Phases

1. **Done on WSL:** bash launchers, seeded homes (layout, mods, keys, settings, theme). **Next (1b):** the same as the portable `herdr_profile.py` (see Platforms), tests with a fake home and a fake `herdr-app`.
2. **Filter** (this design): `profile.name` written by the launcher tool, `profiles.json`, `space-tree` filter + claim + key commands + foreign-active row; tests in `tests/` (pure functions: owner, claim, filter, pruning); `herdr_sync.py` and `herdr_transfer.py` take a profile (`--home` / `HERDR_APP_HOME`), the transfer bundle records `profile.name`.
3. **Inside Herdr:** open-profile command and menu, agents list filtered.
4. **Icons (built, not yet installed or verified):** `herdr_profile.py desktop` stages a `.desktop` and a PNG tile (`icons/`) per application id; installing under `/usr/share` needs root rights (the owner runs it); taskbar badges stay unverified (a Windows-side helper would be needed).

## Decisions

Taken (owner, 2026-11-26): several profiles (three at first); the default profile (owner of every existing space) is the **main** one and its home is the current one; no move command (spaces and groups are recreated by hand);
ownership in one file shared by all homes (`profiles.json`); the portable launcher `herdr_profile.py` lives in this repo, the WSL wrapping stays in the owner's local scripts in `~/workspace`; themes are placeholders; names, colours and labels stay out of the repo, in a local profile table (owner, 2026-11-26).

Open:
1. ~~Agents list~~ — decided 2026-11-26: not hidden, sorted last (`agent-list`); the owner accepts the "N agents" counter staying general.

## Gotchas (this area)

- Spaces, tabs and terminals are core-wide; only the view (`view.json`, `shared.json`) is per home. Never assume a space is private to a window.
- Closing a window closes nothing in the core; terminals and agents keep running and appear in the other window if it shows that space.
- `herdr_sync.py` needs the home to exist (`mkdir` first) and only targets `$HERDR_APP_HOME`; deploying "to all profiles" means running it per home.
- A fresh home may create an initial space on first start (a stray dock terminal appeared once for a space that no longer existed); the claim rule gives such a space to the window that created it.
- A launcher must `mkdir -p` the home and seed `shared.json` before the first start; after the first start Herdr owns the file.

## As built / deviations

- `space-tree` names the filtered list `view.visible` (`this.view = {visible, ids, activeId}`, computed once per render); pruning of `space.groups` / `space.colors`, the git logic, `importGroups` and the `space.move` index still use **all** spaces on purpose
  (a late or failed `profiles.json` read must never wipe groups; `space.move` needs Herdr's real index). `rows()` indexes into the visible list, so a rail number and `select-N` agree.
- Before the first read of `profile.name` / `profiles.json` finishes (`loaded` flag) every space is shown, so a profiled window may flash foreign spaces for a moment at start; an unprofiled window never changes.
- The claim runs on the 250 ms new-space tick from the cached file, re-reads the file fresh, writes with `writeTextFile(path, text, expectedPrevious)`, retries once and waits 5 s after a failure; it never overwrites an invalid existing file (`validProfilesText`).
- `startIds` = the ids at mod start (taken lazily from the first non-empty `spaces()`), so a space that already exists is never claimed, including an initial space a fresh home may create before the mod starts.
- The foreign-active row is read-only (`other profile` + name); the rail shows one cell with a tooltip.
- Window identity: `herdr_profile.py` passes `HERDR_APP_ID=<profile app id>` (`herdr-app` for the main profile, `herdr-app-<profile>` for the others) and `space-tree` sets the OS title `Herdr · <profile>` once it has read `profile.name`. A window started before the change keeps the old id until restarted.
- `herdr_profile.py`: platform-neutral (`$HERDR_LAUNCHER`, `$HERDR_APP_BIN`, `herdr-app` on `PATH`); the main home is always `~/.config/herdr-app`; options for the app go after `--`
  (`herdr-launch-<profile>.sh -- --scale 2`); `launch` on a home whose window already runs only seeds, writes `profile.name` and the keys, and does not start a second window.
- **Taking effect in a running window:** `space-tree` reads `profile.name` at start, so a window that was already open (the main one) needs its mod reloaded (save a mod file) or a restart after the first `launch <main profile>`.
- `launch` merges 11 bindings (`alt-1..9`, `ctrl-shift-pagedown/up`) into `layout:my-main` of the home's `keys.json` (backup first); they point at the `space-tree` commands, which are deployed.
- Not verified: the real window start through the tool, a profiled window (claim, poll, `select-N`, foreign row), Linux and macOS.
