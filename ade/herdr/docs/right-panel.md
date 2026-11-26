# Right panel

Rules and design procedure: [../AGENTS.md](../AGENTS.md). Status: **built**; only the improvement ideas below are open.
What each mod does for the user: [../README.md](../README.md). This doc: structure, state and constraints for changing it.

## Now

Layout column `panel` (weight 2, min 320, max 2400, gap 8) = `panel-tabs` (34 px) + `panel-body` (stack, weight 3) + `panel-shell` (column, weight 2, the dock).

| Block | Mod | State it owns |
|---|---|---|
| `panel-tabs` | `panel-tabs` | selects the stack child `changes` / `files` (`layout.select`); Changes comes first and is the default |
| `panel-body` → `files` | `file-panel` (+ `link.js`) | tree root, open file, history (back/forward) **per space** (localStorage `file-panel.spaces`: `{spaceId: {file, history, ahead, at}}`, paths only, newest 60 spaces); localStorage prefs; command `file-panel.open` |
| `panel-body` → `changes` | `git-panel` | prefs in localStorage `git-panel.prefs` (size, wrap, mode, tab, tree, allRefs); commands `git-panel.changes|history|branches|tree|list|graph-all|graph-head` |
| `panel-shell` → `panel-shell-bar` + `panel-shell-term` | `panel-shell` | shared `panel-shell.dock` = `{<space>: {tabs: [{id,name,tree}], active, seq}}`, tree = `{addr}` or `{dir: "row"|"column", children}`; shared `panel-shell.addrs` |

- The dock's terminals are core terminals (`hc run --keep --name ps-<space16>-<n>`), cwd = the git toplevel (or cwd) of the space's focused / first pane away from home, as Files and Changes show it; `space.folder` is only the fallback (it is the folder the space was created in and goes stale: the name follows the pane, the folder does not). An existing dock terminal keeps its folder (`cd` button).
  The mod rewrites the stack `panel-shell-term` **in the layout file** (`writeTextFile`); never `layout.set`. `herdr_sync.py` compares that stack as a placeholder and keeps the live value.
- `panel-shell.addrs` makes `git-panel` / `file-panel` ignore focus on dock terminals in their `location()` (otherwise Changes shows "Not a git repository" for the dock's cwd).
- `file-panel/link.js` handles terminal Ctrl-clicks (rule by extension in `mod.json`, layout root `link_handlers: ["file-panel"]`) and resolves relative paths against the terminal's cwd, the repo root, the space folder and their direct subfolders. A folder path (second rule in `mod.json`: no extension; `test -d` decides) is sent as `file-panel.open {dir: true}`: the tree expands to it, or it becomes the tree root when it is outside the current root (the tree is un-hidden if needed); the open file stays. Not verified with a real click yet. The header's `Open` button (hidden for remote files) opens the previewed file with the system default app (macOS `open`; a Linux guest on Windows hands the file to the host's explorer when `wslpath` and `explorer.exe` exist; `xdg-open` elsewhere), next to `↗` (VS Code).
- The commit message is a multi-line field (`TextareaState`, auto-grow 1–3 rows, then it scrolls): Enter adds a line,
  Ctrl+Enter (Cmd+Enter on macOS) or the Commit button commits; the whole text is passed as one `-m` message.
- `git-panel` runs git through `exec(...)` (`on` = the space's machine); long diffs go through a linear-time wrapper (a quadratic regex hit QuickJS's interrupt once).

Data: `panes()` focus / cwd / branch for `location()`, `spaces()` for the space folder, `watchFolder` for refreshes.

## Ideas not done (from the 2026-11-25 handoff)

- Rename dock tabs.
- Move a tab / terminal between the central area and the dock: `hc label <t> herdr.space=<id> herdr.tab=<id>` adopts a terminal into a central tab, `terminal.park` takes one out.
- Not verified live: real mouse Ctrl-click on a terminal path (the handler was tested by a direct call), Changes not blanking when a dock terminal is focused in a git project.

## Gotchas (this area)

- Long lists go in a virtual `list` (History: up to 300 rows, each drawing graph paths). Built in one render they overran the script's time budget: "This view could not be re-rendered: interrupted" (fixed 2026-11-26).
- A virtual list's rows may not register handlers (`on_click`, `on_mouse_down`): the list takes them, `on_item_click(key)` / `on_item_secondary_click(key, event)`; the error shows only in the launch log, the row just stays blank.
- Changes always follow the focused pane; with the dock focused they must keep the last real location (see `panel-shell.addrs`).
- A terminal has one size wherever it is shown, so moving one between the dock and the central area resizes it.
- Herdr rewrites the layout file, so anything that edits it must tolerate reordered keys and dropped `hidden:false`.

## Jumps between Changes and Files (built)

- **Changes → Files:** the **Files** button in the diff header (not for deleted files) writes shared `file-panel.open {path, on, at}` (the same request a terminal Ctrl-click makes; `path` = repo root + the file) and selects the stack child `files`: the file opens as it is now and the tree reveals it.
- **Files → Changes:** the **Changes** button in the preview header writes shared `git-panel.open {path (absolute), on, at}` and selects `changes`. `git-panel` reads it on a 200 ms timer (a shared read only), switches to its Changes tab, refreshes and selects the file (unstaged before staged before conflict). A file outside the repository, or one without changes, leaves the selection alone and shows a one-line note.
- Both keys are request channels: the asker writes, the owning mod reads, the newest `at` wins, and a request older than the mod's start is not replayed. Not verified with a real click yet.

## Push and the private-terms bypass in Changes (built 2026-11-26)

The owner's global git hooks (`~/.config/git/allasso-hooks`: `pre-commit`, `commit-msg`, `pre-push`) block a commit or
push that contains a private term; `ALLOW_PRIVATE_TERMS=1` lets one through. The panel had Fetch and Commit, no Push.

- **Push** in the header, next to Fetch: `Push ↑N` while the branch is ahead; `Publish` when it has no upstream
  (`git push -u <remote> HEAD`, remote `origin` if present, else the only one; several and no `origin` → a note).
  Hidden when there is nothing to push or HEAD is detached. Timeout 2 min.
- **Allow private terms** checkbox under the commit message. Checked → the next Commit and Push run as
  `env ALLOW_PRIVATE_TERMS=1 git …` (`exec` has no env option). One-shot: it clears after a successful commit or push,
  and is not remembered. While it is checked, Push asks for a second click. After a hook block (the note contains
  `allasso guard`) the checkbox is highlighted.

## Copying from Changes and Files (built 2026-11-26)

Nothing in either panel could be copied: names, hashes and paths are plain labels.

- **Context menu on right-click**, drawn by the mod itself as a popup (`openOverlay({mod: <self>, kind: "popup",
  props: {menu: …}})`, the pattern of `agent-list`'s sort menu), items grouped under a dim group title; a click copies
  with `cx.write_to_clipboard` and closes the popup; Esc closes it.
- **Files** (`file-panel`), on a file or folder row — group **Path**: *Copy full path*, *Copy path from project root*
  (relative to the git repository root that holds it, else to the Files root).
- **Changes** (`git-panel`):
  - a file row (Changes, Staged, Merge changes, a commit's files, Compare) — group **Path**, the same two items, plus
    *Copy name*;
  - the branch name in the header and a branch row in Branches — *Copy branch name*;
  - a commit (the header's last commit, a History row, the opened commit) — *Copy hash*, *Copy short hash*,
    *Copy subject*.
- **Selectable text** where it reads as text, not as a control: the opened commit's hash, subject and message.
- The menu helper is a small pure module copied verbatim into both mods (mods cannot import each other; `file-panel`
  is the source of the copy, a test keeps the copies identical).
- As built: the popup opens in the click's own call at `"pointer"` (as the tab bar's menu); Files opens it at once with
  the Files root and the popup copy asks `git rev-parse --show-toplevel` itself. Fetch and Push report success in a dim
  line ("Fetched at …", "Pushed at …"); Push is shown on any branch (`Push ↑N` / `Push` / `Publish`).
