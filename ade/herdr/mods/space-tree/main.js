// Space tree (clone of Herdr's `spaces`): machine -> group -> space. Stable colored
// avatars; worktrees nested under their checkout (display only: nothing is moved);
// groups are manual and live in shared `space.groups`, not in Herdr's space order.
// After a space's name, the repo its first terminal is in now, only where unusual:
// branch, ahead/behind, then one mark per agent (see stateMarks). Click to switch,
// right-click for its menu, drag a space to reorder or onto a group header to regroup,
// drag a group header onto another to reorder the groups.

import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, ScrollHandle, InputState, Input } from "gpui-base";
import { spaces, machines, panes, agents, run, openOverlay, exec, hc, theme, shared, setShared, watchFolder, window, layout, checking, nextCommand, home, appHome, readTextFile, writeTextFile, setWindowTitle } from "herdr";
import { trackSince, publishState, worstOf } from "./agent-state.js";
import { parseProfiles, validProfilesText, visibleSpaces, claimable, withClaims, pickByIndex, pickNext } from "./profile.js";
import { tintChrome } from "./tint.js";

function find(node, id) {
  if (node?.id === id) return node;
  for (const child of node?.children ?? []) {
    const found = find(child, id);
    if (found) return found;
  }
  return null;
}

// The sidebar's rail goes by its width, not a flag: dragging its edge narrows it too.
const narrow = () => (find(layout(), "sidebar")?.size?.px ?? Infinity) < 160;

// Where a space is now: its first terminal's live folder, else where it was made.
// `ps`: panes(), read once by the caller (each read copies every pane).
function place(s, ps) {
  const tabs = new Set((s.tabs ?? []).map((c) => c.id));
  return ps.find((p) => tabs.has(p.tab) && p.cwd)?.cwd || s.folder;
}
const rowHeight = ({ tree }) => (tree ? 28 : 30);
const RAIL_CELL = 32;
// A space's name and its git tail never shrink below this, so a short one ("api")
// stays readable instead of turning into an ellipsis next to a long branch.
const SIDE_MIN = 48;
const railCell = (el) => h_flex().flex_none().justify_center().child(el);
const where = (s, ps) => JSON.stringify([s.machine ?? null, place(s, ps)]);
const base = (path) => path.replace(/\/+$/, "").split("/").pop();
// A repo's name from its git folder: the folder holding .git, a bare repo's own
// name, or the folder holding a hidden bare repo (<project>/.bare).
const repoName = (common) => {
  if (common.endsWith("/.git")) return base(common.slice(0, -5));
  const own = base(common);
  return own === ".bare" ? base(common.slice(0, -own.length - 1)) : own.replace(/\.git$/, "");
};
// Changes made outside Herdr (an editor's save, a commit in another app): looked
// for this often, in repos the list shows, while the window is in front.
const FALLBACK = 60000;
// Places looked at together (two git runs each). All at once, a few hundred spaces
// run out of the app's open files (256 when started from the Dock on macOS) or
// past a machine core's 16 running execs, and those spaces wait for FALLBACK.
const AT_ONCE = 6;

// shared("space.groups") = { <machine name | "local">: [{ id, name, folded, spaces: [spaceId…] }] }.
// This mod is its only writer. A group is a view concept: it never moves a space in Herdr.
const GROUPS = "space.groups";
// Written by this mod only (agent-state.js computes them): when each agent's status last changed, and
// what the sidebar and tab-bar show from it.
const SINCE = "agent.since";
const STATE = "agent.state";
const LOCAL = "local";
// A PostCompact hook writes `<dir>/<terminal id>` = epoch seconds; an agent that rests after its compact shows
// quiet (idle, no cache chip) until it works again (agent-state.js, docs/left-panel.md). Read every COMPACT_POLL.
const COMPACTED_DIR = "/.local/state/ai_env/herdr/compacted";
const COMPACT_POLL = 30000;
const COMPACT_SCRIPT = 'cd "$1" 2>/dev/null || exit 0; for f in *; do [ -f "$f" ] && printf "%s %s\\n" "$f" "$(cat "$f")"; done; exit 0';
// `space-tree.import-groups` reads this file (written by ade/herdr/herdr_transfer.py import), applies it and deletes it:
// { groups: [{ machine: name | null, name, folded, spaces: [spaceId…] }], colors: { spaceId: colour } }.
const IMPORT_GROUPS = "/.local/state/ai_env/herdr/import-groups.json";
const groupKey = (machine) => machine ?? LOCAL;
// "" is the implicit Ungrouped place of a machine.
const slot = (machine, groupId) => JSON.stringify([groupKey(machine), groupId]);
// A group dragged onto a machine header goes to the end of that machine's groups.
const END = "*end";

// Profiles (docs/profiles.md). `<appHome>/profile.name` (one line, written by the launcher) names this window's
// profile; absent or empty = profiles off, every space is shown and nothing below runs. PROFILES_FILE is shared by
// all windows: { default, spaces: { <spaceId>: <profile> } }; this mod is the only one that writes it (claims).
const PROFILE_NAME = "/profile.name";
// `<appHome>/profile.color` (one hex line, written by the launcher): mixed into the chrome colour as shared "profile.tint".
const PROFILE_COLOR = "/profile.color";
const TINT = "profile.tint";
const PROFILES_FILE = "/.local/state/ai_env/herdr/profiles.json";
const PROFILES_POLL = 2000;
// After a failed claim wait this long before trying again (checkNew ticks every 250 ms).
const CLAIM_RETRY = 5000;

// The groups without the ids of spaces that are gone (as space.colors is pruned). A machine that
// is away or still loading has no spaces() to compare with: `settled(key)` says whether to prune it.
function pruneGroups(all, ids, settled) {
  const seen = new Set();
  const kept = {};
  for (const [key, list] of Object.entries(all ?? {})) {
    kept[key] = (Array.isArray(list) ? list : []).map((g) => ({
      ...g,
      spaces: (g.spaces ?? []).filter((id) => {
        if (seen.has(id) || (settled(key) && !ids.has(id))) return false;
        seen.add(id);
        return true;
      }),
    }));
  }
  return kept;
}

// Display order of one group's rows (already in the global space order): each worktree space right
// under its main checkout when both are in `rows`, else standalone. Nothing is moved in Herdr.
function nest(rows) {
  const mains = new Set(rows.filter((r) => r.g && !r.g.linked).map((r) => r.g.repo));
  const nested = (r) => r.g?.linked && mains.has(r.g.repo);
  const out = [];
  const placed = new Set(); // repos whose worktrees are listed (under the first of its spaces)
  for (const r of rows) {
    if (nested(r)) continue;
    out.push({ ...r, tree: null });
    if (!r.g || r.g.linked || placed.has(r.g.repo)) continue;
    placed.add(r.g.repo);
    const kids = rows.filter((k) => nested(k) && k.g.repo === r.g.repo);
    kids.forEach((k, n) => out.push({ ...k, tree: n === kids.length - 1 ? "└─" : "├─" }));
  }
  return out;
}

// Both runs, finished: a place isn't done while one of its runs still holds a slot.
async function both(a, b) {
  const done = await Promise.allSettled([a, b]);
  const failed = done.find((d) => d.status === "rejected");
  if (failed) throw failed.reason;
  return done.map((d) => d.value);
}

// What a stored color name looks like on an avatar (the same in every mod
// that draws space avatars): one strong color per hue, light and dark alike.
const MARK = { ansi6: "#22a99a", ansi3: "#e3814f", ansi4: "#5b8def", ansi5: "#8b7cf6", ansi2: "#3fae6a", ansi1: "#d9649a" };

// Keep the whole leading emoji in the avatar (same rule in spaces and picker).
// Newer pictographs such as 🖥 may be drawn as emoji without VS16. A trailing
// selector, modifier or joiner can signal that we matched only part of a glyph.
const EMOJI = /^(?:[#*0-9]\uFE0F?\u20E3|\p{Regional_Indicator}{2}|(?:\p{Emoji_Presentation}|(?=[\u{1F000}-\u{1FFFF}])\p{Extended_Pictographic}|\p{Extended_Pictographic}(?=\uFE0F)|\p{Emoji_Modifier_Base}(?=\p{Emoji_Modifier}))\uFE0F?[\p{Emoji_Modifier}\u{E0020}-\u{E007F}]*(?:\u200D\p{Extended_Pictographic}\uFE0F?\p{Emoji_Modifier}?)*)(?![\p{Grapheme_Extend}\p{Emoji_Modifier}\u200D])/u;
function spaceAvatar(name) {
  return EMOJI.exec(name)?.[0] ?? ([...name][0]?.toUpperCase() ?? "?");
}

// ---- Agent state marks. Copy this block verbatim into tab-bar (it needs div, svg, h_flex from the
// ---- same imports as here); the model is in docs/left-panel.md ("Agent state model").
// `st` = {status, since, cache} from shared("agent.state") (status = Herdr's: working | blocked |
// done | idle, or sleeping after /compact, /clear or a new session; cache = null | "warn" | "expired");
// `now` = Date.now() read once by the caller.
// Blue is t.done: the one blue Herdr's themes name (t.accent is a grey-blue), and it differs from t.working.
const MARK_SIZE = 14;
const MARK_BEAT = { from: 1, to: 0.4, period: 1400 };

// What an agent is doing with you: working = spinner, asks (blocked) = pulsing ? in a ring,
// answered and waits for you (done, and idle once seen) = a yellow hourglass (the best time to answer: ready
// and the cache warm), sleeping (a fresh context after /compact, /clear or a new session) = an empty light blue ring.
function stateGlyph(status, t) {
  if (status === "working") return svg("mods/workspace-chrome/spinner.svg").size(12).mx(1).flex_none().text_color(t.working).spin(900);
  if (status === "blocked") return div().size(MARK_SIZE).flex_none().rounded(MARK_SIZE / 2).border(1.5).border_color(t.done).flex().items_center().justify_center()
    .text_size(10).font_bold().text_color(t.done).loop("opacity", MARK_BEAT).child("?");
  if (status === "sleeping") return div().size(8).mx(3).flex_none().rounded(4).border(1.5).border_color(t.accent);
  return svg("mods/space-tree/hourglass.svg").size(11).mx(1).flex_none().text_color(t.working);
}

// The prompt cache mark, in red (t.blocked is Herdr's red): from 40 min a clock alone (the agents list
// below shows the minutes); from 60 min (expired) a filled dot in the sleeping ring's footprint. Null while the
// cache is fine. `now` is unused today; it stays so every mark function takes (st, t, now).
function cacheChip(st, t, now) {
  if (!st.cache) return null;
  if (st.cache === "expired") return div().size(8).mx(3).flex_none().rounded(4).bg(t.blocked);
  return svg("mods/workspace-chrome/clock.svg").size(11).mx(1).flex_none().text_color(t.blocked);
}

// One agent's mark, a single indicator (owner's choice 2026-11-26: a glyph and a chip side by side read as two
// agents): a question or work first, then the cache chip, then waits (done, idle) or sleeps.
function stateMarks(st, t, now) {
  if (st.cache && st.status !== "blocked" && st.status !== "working") return cacheChip(st, t, now);
  return stateGlyph(st.status, t);
}

// The colour an aggregate tints a rail mark with: cache red > blue (asks) > yellow (working, waits) > dim.
function stateColor(st, t) {
  if (st.cache) return t.blocked;
  return st.status === "blocked" ? t.done : st.status === "working" || st.status === "done" ? t.working : t.dim;
}
// ---- end of the agent state marks

// A machine or group header stays quiet while all it holds is idle and its cache is fine.
const speaks = (st) => Boolean(st) && ((st.status !== "idle" && st.status !== "sleeping") || Boolean(st.cache));
// A space whose agents all sleep (fresh context, nothing to do) is drawn dimmed.
const asleep = (states) => states.length > 0 && states.every((st) => st.status === "sleeping");
const MARKS_MAX = 6;

export default class Spaces extends View {
  init(_props, cx) {
    this.git = {}; // where() -> { name, branch, ahead, behind, repo, linked }, null: not a repo
    this.checked = {}; // where() -> when it was last looked at
    this.unsure = new Set(); // where()s git couldn't answer for (a machine away)
    this.pending = new Set(); // where()s to look at next
    this.busy = {}; // pane -> an agent working or a program in front of its shell
    this.ready = {}; // machine -> its layout was readable at the last look
    this.at = {}; // space -> its where() at the last look
    this.watching = new Map(); // local repos' pending folder watches
    // Spaces made while this runs wait (at most a moment) for their git, so a
    // worktree appears under its repo instead of jumping there.
    const started = spaces().map((s) => s.id);
    this.shown = new Set(started);
    this.born = {};
    this.pressed = null; // the space being dragged
    this.over = null; // the space row the drag is over
    this.dragging = false; // the pointer has left the pressed row: a real drag, so drop places may appear
    this.overGroup = null; // slot() of the group header (or Ungrouped) the drag is over
    this.hoverGroup = null; // slot() of the group header the pointer is over: only it shows its delete ✕
    // A group header being dragged: { machine: groupKey, id, over: group id | END | null }. `moved`: it was over
    // another header, so the click that may follow the release is not a fold.
    this.groupDrag = null;
    this.groupMoved = false;
    this.live = { bySpace: {}, now: Date.now() }; // this render's agent states per space id
    // Inline rename of a group: one retained text state, shown in the header of the group in `renaming`.
    this.renaming = null; // { machine: groupKey, id } while a group's name is edited
    this.nameInput = InputState.new({ placeholder: "Group name" });
    this.nameInput.on("submit", (_e, cx) => { this.finishRename(true); cx.notify(); });
    this.nameInput.on("blur", (_e, cx) => { this.finishRename(true); cx.notify(); });
    this.scroll = ScrollHandle.new(); // the list's position: the active space is brought into view through it
    this.activeAt = -1; // the active space's place among the list's children (headers and rows), -1 when not drawn
    this.machines = [];
    // Profile filter. `loaded`: profile.name and profiles.json were read once. Until then every space is shown
    // (an unprofiled window must not flash an empty sidebar); after it, a window with a profile name shows only
    // the spaces it owns (render computes `view`), and a window without one keeps showing all.
    this.profileName = null; // null = profiles off
    this.profiles = parseProfiles(""); // nobody owns anything until the file is read
    this.profileColor = null; // from profile.color; null = no tint
    this.profileText = undefined; // profiles.json as last read (null = absent); undefined = not read yet
    this.loaded = false;
    this.polling = false;
    this.claiming = false;
    this.claimedAt = 0;
    // Ids that existed when the mod started: never claimed (taken from the first non-empty spaces()).
    this.startIds = started.length ? new Set(started) : null;
    // This render's visible spaces (what every list, count and key command uses), their ids, the active space's id.
    this.view = { visible: [], ids: new Set(), activeId: null };
    try {
      this.reconnecting = JSON.parse(localStorage.getItem("machines.reconnecting") ?? "{}");
      if (!this.reconnecting || typeof this.reconnecting !== "object" || Array.isArray(this.reconnecting)) this.reconnecting = {};
      for (const state of Object.values(this.reconnecting)) state.busy = false;
    } catch { this.reconnecting = {}; }
    try { this.folded = new Set(JSON.parse(localStorage.getItem("machines.folded") ?? "[]")); } catch { this.folded = new Set(); }
    this.refreshMachines(cx);
    cx.timer.every(15000, cx => this.refreshMachines(cx));
    // Refresh only when the refs could have changed (see `due`).
    cx.timer.every(250, (cx) => this.checkNew(cx));
    // The sidebar is always shown, so agent states keep up with the rail collapsed too. `agents()` is
    // cheap and only a real change writes; the 30 s redraw keeps the chip's minutes current.
    this.syncAgents();
    cx.timer.every(30000, (cx) => cx.notify());
    // Only reads here, so a check may run them; the poll and the claim write nothing while checking.
    this.compacted = {}; // {<terminal id>: marker ms}, read by pollCompacted, used by syncAgents
    this.pollCompacted();
    cx.timer.every(COMPACT_POLL, () => this.pollCompacted());
    this.loadProfile(cx);
    cx.timer.every(PROFILES_POLL, (cx) => this.pollProfiles(cx));
    if (checking()) return;
    // Commands (keys.json, palette, `herdr-app do`): space-tree.import-groups, select-1 .. select-9, next, prev
    cx.spawn(async (cx) => {
      for (;;) {
        const c = await nextCommand();
        // A failed import must not end this loop; the file is deleted either way.
        if (c === "import-groups") await this.importGroups().catch((e) => { this.importError = String(e); });
        else this.selectSpace(c);
        cx.notify();
      }
    });
  }

  // Reads profile.name and, with a name, profiles.json, once. A failed read leaves profiles off (name) or
  // owning nothing (file); `loaded` is set either way so the sidebar does not stay in the show-all state.
  loadProfile(cx) {
    cx.spawn(async (cx) => {
      try {
        const text = await readTextFile(appHome() + PROFILE_NAME);
        this.profileName = text?.split(/\r?\n/)[0].trim() || null;
        // The OS title tells the profile windows apart in a window switcher or taskbar (the last call wins).
        if (this.profileName && !checking()) setWindowTitle(`Herdr · ${this.profileName}`);
        if (this.profileName) await this.readProfiles();
        this.profileColor = (await readTextFile(appHome() + PROFILE_COLOR).catch(() => null))?.split(/\r?\n/)[0].trim() || null;
      } catch {}
      this.loaded = true;
      cx.notify();
    });
  }

  // Reads profiles.json into the cache; true when it changed. A failed read throws and keeps the cache.
  async readProfiles() {
    const text = await readTextFile(home() + PROFILES_FILE);
    if (text === this.profileText) return false;
    this.profileText = text;
    this.profiles = parseProfiles(text ?? "");
    return true;
  }

  // Other windows claim spaces too, so the file is looked at every PROFILES_POLL (never from render).
  pollProfiles(cx) {
    if (checking() || !this.profileName || this.polling) return;
    this.polling = true;
    cx.spawn(async (cx) => {
      try { if (await this.readProfiles()) cx.notify(); } catch {}
      this.polling = false;
    });
  }

  // From checkNew: this window claims the space it just made. The decision uses the cache; the write
  // re-reads the file first (see writeClaims).
  claim(cx) {
    if (checking() || this.claiming || Date.now() - this.claimedAt < CLAIM_RETRY) return;
    this.claiming = true;
    cx.spawn(async (cx) => {
      let done = false;
      try { done = await this.writeClaims(); } catch {}
      this.claimedAt = done ? 0 : Date.now(); // a failure waits CLAIM_RETRY, a success does not delay the next claim
      this.claiming = false;
      cx.notify();
    });
  }

  // Read-modify-write of profiles.json, the claims added to what is there now. writeTextFile compares the
  // text it replaces: a window that wrote in between makes it reject, and the second round starts from the new
  // text. A file that exists but is not valid JSON is never overwritten (it would lose the default owner).
  // True when nothing is left to claim (written, or another window did it), false when the claim did not land.
  async writeClaims() {
    const path = home() + PROFILES_FILE;
    for (let round = 0; round < 2; round++) {
      const text = await readTextFile(path);
      if (text !== null && !validProfilesText(text)) return false;
      const current = parseProfiles(text ?? "");
      const ids = claimable(spaces(), current, this.profileName, this.startIds);
      if (!ids.length) {
        this.profileText = text;
        this.profiles = current;
        return true;
      }
      const next = `${JSON.stringify(withClaims(current, ids, this.profileName), null, 2)}\n`;
      try {
        if (text === null) await exec("mkdir", ["-p", path.slice(0, path.lastIndexOf("/"))]);
        await writeTextFile(path, next, text);
      } catch {
        continue;
      }
      this.profileText = next;
      this.profiles = parseProfiles(next);
      return true;
    }
    return false;
  }

  // Key and palette commands: select-1 .. select-9, next, prev over the spaces this window shows (as of the
  // last render, in the global space order). Nothing happens when there is no such space.
  selectSpace(command) {
    const { visible, activeId } = this.view;
    const n = /^select-([1-9])$/.exec(command);
    const id = n ? pickByIndex(visible, Number(n[1])) : command === "next" ? pickNext(visible, activeId, 1) : command === "prev" ? pickNext(visible, activeId, -1) : null;
    if (id) run("space.select", { id });
  }

  // Applies the groups and colours a transfer import left in IMPORT_GROUPS, then deletes the file (it is
  // consumed even when it cannot be read, so a bad file is not applied later). A group is matched by name
  // on its machine and created when missing; its spaces leave their other groups and join it. Spaces that do
  // not exist here are ignored. Groups go through editGroups, the only writer of `space.groups`.
  async importGroups() {
    const path = home() + IMPORT_GROUPS;
    const text = await readTextFile(path);
    if (text === null) return;
    try {
      const data = JSON.parse(text);
      const known = new Set(spaces().map((s) => s.id));
      const stamp = Date.now().toString(36);
      let made = 0;
      this.editGroups((all) => {
        for (const g of Array.isArray(data.groups) ? data.groups : []) {
          const list = (all[groupKey(g.machine ?? null)] ??= []);
          let target = list.find((k) => k.name === g.name);
          if (!target) {
            target = { id: `g${stamp}${made++}`, name: String(g.name), folded: Boolean(g.folded), spaces: [] };
            list.push(target);
          }
          for (const id of (Array.isArray(g.spaces) ? g.spaces : []).filter((k) => known.has(k))) {
            for (const other of list) other.spaces = other.spaces.filter((k) => k !== id);
            target.spaces.push(id);
          }
        }
      });
      const colors = { ...(shared("space.colors") ?? {}) };
      for (const [id, colour] of Object.entries(data.colors ?? {})) if (known.has(id) && typeof colour === "string") colors[id] = colour;
      setShared("space.colors", colors);
    } finally {
      await exec("rm", ["-f", path]);
    }
  }

  // Reads the compact markers (one `exec`, never from render). A failed read keeps the last answer; the next
  // syncAgents (250 ms) publishes the change.
  pollCompacted() {
    if (checking() || this.polledCompact) return;
    this.polledCompact = true;
    exec("sh", ["-c", COMPACT_SCRIPT, "sh", home() + COMPACTED_DIR], { timeout: 5000 })
      .then((r) => {
        const next = {};
        for (const line of String(r?.stdout ?? "").split("\n")) {
          const m = /^(\S+) (\d+)$/.exec(line.trim());
          if (m) next[m[1]] = Number(m[2]) * 1000;
        }
        this.compacted = next;
      })
      .catch(() => {})
      .finally(() => { this.polledCompact = false; });
  }

  // The only writer of shared `agent.since` and `agent.state`. Writes only what changed (both are saved
  // to shared.json and every mod redraws on a write); never called from render.
  syncAgents() {
    const now = Date.now();
    const list = agents();
    const { since, changed } = trackSince(shared(SINCE), list, now);
    if (changed) setShared(SINCE, since);
    const state = publishState(since, list, now, this.compacted);
    if (JSON.stringify(state) !== JSON.stringify(shared(STATE) ?? null)) setShared(STATE, state);
  }

  // shared("profile.tint") = the chrome colour with the profile colour mixed in (tint.js), or null (no profile
  // colour, or a theme colour that is not hex). Follows a theme change; space-tree is the only writer, it writes
  // on a change only, never from render.
  publishTint() {
    if (checking() || !this.loaded) return;
    const tint = this.profileColor ? tintChrome(theme().chrome, this.profileColor) : null;
    if ((shared(TINT) ?? null) !== tint) setShared(TINT, tint);
  }

  // shared("space.foreign") = the id of the active space when this window does not own it, else null. Mods that
  // must not act on another profile's space read it (panel-shell: a second dock would clash with the owner's
  // dock terminals). space-tree is the only writer; it writes on a change only, never from render.
  publishForeign(sp) {
    if (checking()) return;
    const profiled = this.loaded && Boolean(this.profileName);
    const foreign = profiled ? sp.find((s) => s.active && !visibleSpaces([s], this.profiles, this.profileName).length) : null;
    const id = foreign?.id ?? null;
    if ((shared("space.foreign") ?? null) !== id) setShared("space.foreign", id);
  }

  // shared("space.owned") = the ids of the spaces this window owns (the visible list), or null when profile mode
  // is off. Not written before the profile files are read: absent also means "nothing is foreign". The agent list
  // sorts the agents of other spaces last. space-tree is the only writer; compared as text, written on a change
  // only, never from render.
  publishOwned(sp) {
    if (checking() || !this.loaded) return;
    const owned = this.profileName ? visibleSpaces(sp, this.profiles, this.profileName).map((s) => s.id) : null;
    if (JSON.stringify(owned) !== JSON.stringify(shared("space.owned") ?? null)) setShared("space.owned", owned);
  }

  checkNew(cx) {
    const now = Date.now();
    const sp = spaces();
    for (const s of sp) if (!this.shown.has(s.id)) this.born[s.id] ??= now;
    this.syncAgents();
    this.publishTint();
    if (!this.startIds && sp.length) this.startIds = new Set(sp.map((s) => s.id));
    if (this.loaded && this.profileName && this.startIds && claimable(sp, this.profiles, this.profileName, this.startIds).length) this.claim(cx);
    this.publishForeign(sp);
    this.publishOwned(sp);
    const due = this.due(now, sp, panes());
    if (due.size) this.refreshGit(due, cx);
    if (sp.some((s) => !this.shown.has(s.id))) cx.notify();
  }

  // Places whose git could have changed: new ones (a new space, a cd), where a
  // command or an agent's turn just ended (a checkout, a commit, edits), those on
  // a machine that is back, and repos in view whose fallback is due.
  due(now, sp, ps) {
    // Moved there: what it knew of that place may be old.
    const at = {};
    for (const s of sp) at[s.id] = where(s, ps);
    const keys = new Set(Object.values(at).filter((k) => JSON.parse(k)[1]));
    for (const k of Object.keys(this.git)) if (!keys.has(k)) {
      delete this.git[k];
      delete this.checked[k];
      this.unsure.delete(k);
    }
    const repos = new Set(Object.values(this.git).filter(Boolean).map((g) => g.repo));
    for (const [repo, pending] of this.watching) if (!repos.has(JSON.stringify([null, repo]))) {
      this.watching.delete(repo);
      pending.cancel();
    }
    const due = new Set([...keys].filter((k) => !(k in this.git)));
    for (const [id, k] of Object.entries(at)) if (this.at[id] && this.at[id] !== k && keys.has(k)) due.add(k);
    this.at = at;
    const spaceOf = {};
    for (const s of sp) for (const t of s.tabs ?? []) spaceOf[t.id] = s;
    const busy = {};
    for (const p of ps) {
      if (!p.terminal || !spaceOf[p.tab]) continue;
      busy[p.pane] = p.status === "working" || !(p.program?.shell || p.program?.agent);
      if (this.busy[p.pane] && !busy[p.pane]) this.sameRepo(at[spaceOf[p.tab].id], keys).forEach((k) => due.add(k));
    }
    this.busy = busy;
    for (const m of machines()) {
      const ready = m.layout === "ready";
      if (ready && this.ready[m.name] === false) for (const k of keys) if (JSON.parse(k)[0] === m.name) due.add(k);
      this.ready[m.name] = ready;
    }
    if (window().active) {
      for (const k of keys) {
        const asks = this.git[k] || this.unsure.has(k);
        if (asks && !this.folded.has(JSON.parse(k)[0]) && now - (this.checked[k] ?? 0) > FALLBACK) due.add(k);
      }
    }
    return due;
  }

  // `key` and the other places in its repo (a commit there moves their ahead/behind).
  sameRepo(key, keys) {
    const repo = this.git[key]?.repo;
    return [key, ...(repo ? [...keys].filter((k) => this.git[k]?.repo === repo) : [])];
  }

  refreshMachines(cx) {
    cx.spawn(async cx => {
      try { const r = await hc(["machine", "ls", "--json"]); if (r.code === 0) this.machines = (JSON.parse(r.stdout).machines ?? []).filter((m) => !m.temporary); } catch {}
      cx.notify();
    });
  }

  rebuilt(machine) {
    return machine?.state === "unreachable" && /^a different core answers there \("[^"]+"\)$/.test(machine.why ?? "");
  }

  reconnect(machine, cx) {
    const name = machine.name;
    if (this.reconnecting[name]?.busy) return;
    const state = this.reconnecting[name] ??= { machine, removed: false };
    state.busy = true;
    state.error = null;
    localStorage.setItem("machines.reconnecting", JSON.stringify(this.reconnecting));
    cx.notify();
    cx.spawn(async cx => {
      try {
        const ask = async args => {
          const r = await hc([...args, "--json"]);
          if (r.code !== 0) {
            let error;
            try { error = JSON.parse(r.stdout || r.stderr).error?.message; } catch {}
            throw new Error(error || r.stderr || r.stdout || "Couldn't connect.");
          }
        };
        if (!state.removed) {
          await ask(["machine", "rm", name]);
          state.removed = true;
          localStorage.setItem("machines.reconnecting", JSON.stringify(this.reconnecting));
        }
        const args = ["machine", "add"];
        if (machine.via) args.push("--via", machine.target);
        else args.push(machine.target);
        args.push("--name", name);
        if (machine.session) args.push("--session", machine.session);
        if (machine.temporary) args.push("--temp");
        await ask(args);
        this.folded.delete(name);
        localStorage.setItem("machines.folded", JSON.stringify([...this.folded]));
        delete this.reconnecting[name];
      } catch (e) {
        state.error = String(e.message ?? e);
        state.busy = false;
      }
      localStorage.setItem("machines.reconnecting", JSON.stringify(this.reconnecting));
      this.refreshMachines(cx);
      cx.notify();
    });
  }

  // One look at a time, each place once however often it was asked for meanwhile.
  refreshGit(keys, cx) {
    for (const k of keys) this.pending.add(k);
    if (this.refreshing) return;
    this.refreshing = true;
    cx.spawn(async (cx) => {
      while (this.pending.size) {
        const keys = [...this.pending];
        this.pending.clear();
        let next = 0;
        await Promise.all(Array.from({ length: Math.min(AT_ONCE, keys.length) }, async () => {
          while (next < keys.length) await this.check(keys[next++], cx);
        }));
      }
      this.refreshing = false;
      cx.notify();
    });
  }

  // One place's git: branch, ahead/behind, its repo.
  async check(key, cx) {
    const [on, cwd] = JSON.parse(key);
    this.checked[key] = Date.now();
    this.unsure.delete(key);
    try {
      // Ref-only reads avoid scanning the worktree or waking its index watch.
      // Two reads: a folder name may itself contain a newline.
      const [r, common] = await both(
        exec("git", ["rev-parse", "--path-format=absolute", "--show-toplevel"], { cwd, on }),
        exec("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd, on }),
      );
      if (r.code !== 0 || common.code !== 0) {
        // Only git's own "no" is remembered as no repo; anything else is asked again.
        if (!/not a git repository/i.test(r.code !== 0 ? r.stderr : common.stderr)) {
          this.unsure.add(key);
          this.git[key] ??= null;
        } else this.git[key] = null;
        return;
      }
      // The repo's own .git folder names it; a worktree's top differs from it.
      const top = r.stdout.replace(/\n$/, ""), repo = common.stdout.replace(/\n$/, "");
      const [branch, counts] = await both(
        exec("git", ["symbolic-ref", "-q", "HEAD"], { cwd, on }),
        exec("git", ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], { cwd, on }),
      );
      const name = repoName(repo);
      const [ahead, behind] = counts.code === 0 ? counts.stdout.trim().split(/\s+/).map(Number) : [0, 0];
      const info = { name, branch: branch.code === 0 ? branch.stdout.trim().replace(/^refs\/heads\//, "") : "(detached)", ahead, behind, repo: JSON.stringify([on, repo]), linked: repo !== `${top}/.git` };
      this.git[key] = info;
      if (!on) this.watch(repo, cx);
    } catch {
      // Couldn't ask (the machine is away): keep what it showed, look again later.
      this.git[key] ??= null;
      this.unsure.add(key);
    }
  }

  watch(repo, cx) {
    if (this.watching.has(repo)) return;
    const pending = watchFolder(repo);
    this.watching.set(repo, pending);
    cx.spawn(async (cx) => {
      let current = pending;
      try {
        for (;;) {
          await current;
          if (this.watching.get(repo) !== current) return;
          const mine = JSON.stringify([null, repo]);
          const keys = Object.keys(this.git).filter((k) => this.git[k]?.repo === mine);
          if (!keys.length) break;
          this.refreshGit(keys, cx);
          current = watchFolder(repo);
          this.watching.set(repo, current);
        }
      } catch {}
      if (this.watching.get(repo) === current) this.watching.delete(repo);
    });
  }

  menu(w) {
    // A popup is as tall as its items; height is only the most.
    openOverlay({ mod: "menu", kind: "popup", width: 200, height: 260, props: { tab: "space", target: { id: w.id } } });
  }

  // The visible spaces (this render's `view`) in Herdr's order, without those still waiting for their git: a new
  // worktree space appears under its checkout instead of jumping there. Nothing is written back to Herdr.
  // `i` is the place in the visible list: the number a rail cell shows and select-N counts.
  rows() {
    const sp = this.view.visible, ps = panes();
    const ready = (w) => this.shown.has(w.id) || where(w, ps) in this.git || Date.now() - (this.born[w.id] ??= Date.now()) > 1500;
    for (const w of sp) if (!this.shown.has(w.id) && ready(w)) this.shown.add(w.id);
    return sp.map((w, i) => ({ w, i, g: this.git[where(w, ps)] ?? null })).filter((r) => this.shown.has(r.w.id));
  }

  groupsOf(machine, all = shared(GROUPS) ?? {}) {
    const list = all[groupKey(machine)];
    return Array.isArray(list) ? list : [];
  }

  // The only writer of shared `space.groups`: `change(all)` edits a copy.
  editGroups(change) {
    const next = JSON.parse(JSON.stringify(shared(GROUPS) ?? {}));
    change(next);
    setShared(GROUPS, next);
  }

  // The id of the group holding `spaceId` on that machine, "" for Ungrouped.
  groupIdOf(machine, spaceId) {
    return this.groupsOf(machine).find((g) => g.spaces.includes(spaceId))?.id ?? "";
  }

  addGroup(machine, cx) {
    const id = `g${Date.now().toString(36)}`;
    const taken = new Set(this.groupsOf(machine).map((g) => g.name));
    let name = "Group";
    for (let n = 2; taken.has(name); n++) name = `Group ${n}`;
    this.editGroups((all) => { (all[groupKey(machine)] ??= []).push({ id, name, folded: false, spaces: [] }); });
    // A folded machine would hide the new group, and with it the name being typed.
    if (this.folded.delete(machine)) localStorage.setItem("machines.folded", JSON.stringify([...this.folded]));
    this.startRename(machine, id, cx);
  }

  removeGroup(machine, id) {
    if (this.renaming?.id === id) this.finishRename(false);
    this.editGroups((all) => { all[groupKey(machine)] = this.groupsOf(machine, all).filter((g) => g.id !== id); });
  }

  toggleGroup(machine, id) {
    this.editGroups((all) => { const g = this.groupsOf(machine, all).find((k) => k.id === id); if (g) g.folded = !g.folded; });
  }

  // Into a group, or out of every group with "" (a group that is gone leaves the space ungrouped).
  moveToGroup(machine, spaceId, groupId) {
    this.editGroups((all) => {
      const groups = this.groupsOf(machine, all);
      for (const g of groups) g.spaces = g.spaces.filter((id) => id !== spaceId);
      if (groupId) groups.find((g) => g.id === groupId)?.spaces.push(spaceId);
    });
  }

  // Puts group `id` right before group `beforeId` (the end of the machine's groups for END). A move that
  // changes nothing writes nothing.
  moveGroup(machine, id, beforeId) {
    const ids = this.groupsOf(machine).map((g) => g.id);
    const rest = ids.filter((k) => k !== id);
    const at = beforeId === END ? rest.length : rest.indexOf(beforeId);
    if (!ids.includes(id) || at < 0) return;
    rest.splice(at, 0, id);
    if (rest.every((k, n) => k === ids[n])) return;
    this.editGroups((all) => {
      const groups = this.groupsOf(machine, all);
      all[groupKey(machine)] = rest.map((k) => groups.find((g) => g.id === k));
    });
  }

  // Where a drag of a group header is now: over another header of the same machine (`over` = that group's
  // id, END for the machine header) or nowhere it can drop (itself, Ungrouped, another machine).
  dragGroupOver(machine, over, cx) {
    const drag = this.groupDrag;
    if (!drag) return;
    const next = drag.machine === groupKey(machine) && over && over !== drag.id ? over : null;
    if (drag.over === next) return;
    drag.over = next;
    if (next !== null) this.groupMoved = true;
    cx.notify();
  }

  // The drag of a group ends (mouse up on a header, or anywhere in the list): a drop on a header moves it.
  dropGroup(machine, over) {
    const drag = this.groupDrag;
    this.groupDrag = null;
    if (drag && drag.over !== null && drag.over === over && drag.machine === groupKey(machine)) this.moveGroup(machine, drag.id, over);
  }

  startRename(machine, id, cx) {
    const group = this.groupsOf(machine).find((g) => g.id === id);
    if (!group) return;
    this.renaming = { machine: groupKey(machine), id };
    this.nameInput.set_value(group.name);
    this.nameInput.set_selected_range({ start: 0, end: group.name.length });
    this.nameInput.focus();
    this.focusName = true; // the field is drawn only on the next render: ask again then
    cx.notify();
  }

  // Ends the rename (submit, blur, Escape); `renaming` is cleared first so the blur it causes is a no-op.
  finishRename(save) {
    const target = this.renaming;
    if (!target) return;
    this.renaming = null;
    const name = this.nameInput.value().trim();
    if (save && name) this.editGroups((all) => { const g = (all[target.machine] ?? []).find((k) => k.id === target.id); if (g) g.name = name; });
    this.nameInput.blur();
  }

  // Where a drag of a space is now: over a row, a group header, or nowhere it can drop
  // (another machine's). Dragging stays within the machine the space is on.
  dragOver(machine, row, groupId, cx) {
    if (this.pressed === null) return;
    const source = this.view.visible.find((s) => s.id === this.pressed);
    const same = source && (source.machine ?? null) === machine;
    const over = same ? row : null;
    const overGroup = same && groupId !== null ? slot(machine, groupId) : null;
    if (this.over === over && this.overGroup === overGroup) return;
    if (over !== this.pressed || overGroup !== null) this.dragging = true;
    this.over = over;
    this.overGroup = overGroup;
    cx.notify();
  }

  // The rows under a header per machine (this computer's first, always), each machine's spaces
  // in groups. `groups` is the pruned shared("space.groups").
  grouped(t, collapsed, groups) {
    const rows = this.rows();
    // Where the active space's row lands among the children this returns: the list scrolls by its direct
    // children, and machine and group headers, login notes and loading rows count too.
    this.activeAt = -1;
    let before = 0;
    const remote = [...new Set([...rows.map(r => r.w.machine).filter(Boolean), ...this.machines.map(m => m.name), ...Object.keys(this.reconnecting)])].sort();
    const loading = m => {
      const layout = machines().find(k => k.name === m)?.layout;
      return layout === "loading" || (!layout && this.machines.some(k => k.name === m && k.state === "connected"))
        || this.view.visible.some(s => (s.machine ?? null) === m && !this.shown.has(s.id));
    };
    const loadingRow = m => h_flex().id(`machine-loading-${m ?? "local"}`).h(28).flex_none().px(collapsed ? 0 : 8).items_center().when(collapsed, el => el.justify_center()).text_size(12).text_color(t.dim).child(collapsed ? "…" : "Loading…");
    const machineNames = [null, ...remote];
    return machineNames.flatMap((m) => {
      const mine = rows.filter((r) => (r.w.machine ?? null) === m);
      const repair = this.reconnecting[m];
      const machine = this.machines.find(k => k.name === m) ?? repair?.machine;
      const connected = !m || machine?.state === "connected";
      const rebuilt = this.rebuilt(machine) || !!repair;
      const login = machine?.state === "needs_login" ? `hc machine login ${m}` : null;
      const folded = this.folded.has(m);
      const worst = this.worst(mine);
      const icon = svg(`mods/workspace-chrome/${m ? "server" : "monitor"}.svg`).size(13).flex_none();
      const badge = div().absolute().right(-3).bottom(-2).size(6).rounded(3).border_1().border_color(shared(TINT) ?? t.chrome).bg(connected ? t.ansi2 : t.dim);
      // A dragged group dropped on a machine's header goes to the end of its groups.
      const drop = this.groupDrag?.over === END && this.groupDrag.machine === groupKey(m);
      const head = h_flex().id(`machine-${m ?? "local"}`).h(26).flex_none().px(collapsed ? 0 : 8).gap(7).items_center().text_size(11).text_color(collapsed && worst ? stateColor(worst, t) : t.dim)
        .when(drop, el => el.rounded(6).border_1().border_color(t.accent))
        .on_mouse_move((_e, cx) => this.dragGroupOver(m, END, cx))
        .on_mouse_up("left", (_e, cx) => { this.dropGroup(m, END); cx.notify(); })
        .when(collapsed, el => el.mt(4).w(RAIL_CELL).justify_center().rounded(6).tooltip(login ? `${m} needs a login: run ${login}. Click for Copy.` : m ? `${m} · ${connected ? "connected" : (machine?.state ?? "asleep").replace(/_/g, " ")}` : "This computer"))
        .cursor_pointer().hover(s => s.bg(t.hover))
        .on_click((_e, cx) => {
          if (collapsed && login) { run("app.settings", { section: "machines" }); return; }
          folded ? this.folded.delete(m) : this.folded.add(m);
          localStorage.setItem("machines.folded", JSON.stringify([...this.folded]));
          cx.notify();
        })
        .when(machine?.keeps_running_after_logout === false, el => el.child(div().id(`machine-logout-${m}`)
          .flex_none().text_color(t.blocked).tooltip("Agents stop when you disconnect. Click for the fix.")
          .on_click((_e, cx) => { cx.stop_propagation(); openOverlay({ mod: "machine-add", kind: "dialog", width: 560 }); }).child("⚠")))
        .when(!collapsed, el => el.child(icon))
        .when(collapsed, el => el.child(div().relative().flex_none().child(icon).when(Boolean(m), el => el.child(badge))))
        .when(!collapsed, el => el.child(div().flex_1().truncate().child(m ?? "This computer"))
          .when(speaks(worst), el => el.child(stateMarks(worst, t, this.live.now)))
          .when(!connected && !rebuilt && !login, el => el.child(div().flex_none().child((machine?.state ?? "asleep").replace(/_/g, " "))))
          .when(rebuilt, el => el.child(h_flex().flex_none().gap(3).items_center()
            .child(div().max_w(140).truncate().child(repair?.error || "rebuilt ·"))
            .child(div().id(`machine-connect-${m}`).text_color(repair?.error ? t.blocked : t.muted)
              .hover(s => s.text_color(t.foreground))
              .tooltip(repair?.error || "This machine was rebuilt or replaced. Click to connect to the new one.")
              .on_click((_e, cx) => { cx.stop_propagation(); this.reconnect(machine, cx); })
              .child(repair?.busy ? "Connecting…" : "Connect"))))
          .when(connected, el => el.child(div().id(`machine-group-${m ?? "local"}`).flex_none().px(4).py(2).rounded(4).hover(s => s.bg(t.hover).text_color(t.foreground))
            .tooltip("New group")
            .on_click((_e, cx) => { cx.stop_propagation(); this.addGroup(m, cx); }).child(svg("mods/workspace-chrome/folder-plus.svg").size(12))))
          .when(connected, el => el.child(div().id(`machine-add-${m ?? "local"}`).flex_none().px(4).rounded(4).hover(s => s.bg(t.hover).text_color(t.foreground))
            .tooltip("New space")
            .on_click((_e, cx) => { cx.stop_propagation(); run("space.new", { machine: m ?? "" }); }).child("+")))
          .child(div().size(6).rounded(3).flex_none().bg(connected ? t.ansi2 : t.dim))
          .child(svg(`mods/workspace-chrome/chevron-${folded ? "right" : "down"}.svg`).size(11).flex_none()));
      const empty = h_flex().id(`machine-new-${m ?? "local"}`).h(28).flex_none().px(collapsed ? 0 : 8).gap(7).items_center().rounded(7)
        .text_size(12).text_color(t.dim).hover(s => s.bg(t.hover).text_color(t.foreground))
        .on_click(() => run("space.new", { machine: m ?? "" }))
        .when(collapsed, el => el.w(RAIL_CELL).rounded(6).justify_center().tooltip(`New space on ${m ?? "this computer"}`)
          .child(h_flex().size(18).rounded(5).border_1().border_color(t.dim).items_center().justify_center()
            .child(svg("mods/workspace-chrome/plus.svg").size(10))))
        .when(!collapsed, el => el.child(svg("mods/workspace-chrome/plus.svg").size(12)).child(m ? "New space there" : "New space here"));
      if (!collapsed && rebuilt) { before += 1; return [head]; }
      const cell = collapsed ? railCell : (el) => el;
      const guidance = !collapsed && login ? [v_flex().px(8).pb(6).gap(3).flex_none()
        .child(div().text_size(11).text_color(t.blocked).child("needs a login: run"))
        .child(h_flex().gap(5).items_center()
          .child(div().flex_1().min_w(0).truncate().tooltip(login).text_size(11).text_color(t.muted).child(login))
          .child(div().id(`machine-login-copy-${m}`).flex_none().px(5).py(2).rounded(4)
            .text_size(11).text_color(t.foreground).bg(t.raised).cursor_pointer().hover(s => s.bg(t.hover))
            .on_click((_e, cx) => { cx.stop_propagation(); cx.write_to_clipboard(login); this.copiedLogin = login; cx.notify(); })
            .child(this.copiedLogin === login ? "Copied" : "Copy")))] : [];
      const body = folded ? { items: [], active: -1 }
        : mine.length ? this.section(m, mine, groups, t, collapsed)
        : { items: [loading(m) ? loadingRow(m) : cell(empty)], active: -1 };
      const items = [cell(head), ...guidance, ...body.items];
      if (body.active >= 0) this.activeAt = before + 1 + guidance.length + body.active;
      before += items.length;
      return items;
    });
  }

  // The aggregate of the agents in these rows (worstOf), null when there are none.
  worst(rows) {
    return worstOf(rows.flatMap((r) => this.live.bySpace[r.w.id] ?? []));
  }

  // One machine's spaces: flat while it has no groups; with groups, Ungrouped first, then each group.
  // `active` = where the active space's row is among `items` (-1: not drawn).
  section(machine, mine, groups, t, collapsed) {
    const list = this.groupsOf(machine, groups);
    const items = [];
    let active = -1;
    const add = (rows, indent) => {
      for (const r of nest(rows)) {
        if (r.w.active) active = items.length;
        items.push(collapsed ? this.compact(r, t) : this.row(r, t, indent));
      }
    };
    if (!list.length) {
      add(mine, false);
      return { items, active };
    }
    const grouped = new Set(list.flatMap((g) => g.spaces));
    const loose = mine.filter((r) => !grouped.has(r.w.id));
    // The Ungrouped header is a drop place: it shows while it holds spaces, or while a grouped space is dragged.
    // Only a real drag (`dragging`): shown on a plain press, it would push the rows down before the release
    // lands, and the click on the space would be lost.
    if (!collapsed && (loose.length || (this.dragging && this.pressed !== null && grouped.has(this.pressed)))) items.push(this.groupHead(machine, null, loose.length, this.worst(loose), t));
    add(loose, true);
    for (const g of list) {
      const inside = new Set(g.spaces);
      const held = mine.filter((r) => inside.has(r.w.id));
      const worst = this.worst(held);
      // The group may also hold spaces of another profile: the count is of those shown here.
      const count = g.spaces.filter((id) => this.view.ids.has(id)).length;
      items.push(collapsed ? this.groupMark(machine, g, worst, t) : this.groupHead(machine, g, count, worst, t));
      if (!g.folded) add(held, true);
    }
    return { items, active };
  }

  // A group's header, or Ungrouped's with `group` null: a drop place for a dragged space. `worst` = the aggregate
  // agent mark of its spaces. The group's header also folds on click, renames on double-click, deletes with ✕
  // (shown only while the pointer is over the header; hidden, it keeps its width so nothing shifts)
  // and is itself dragged onto another group's header to go right before it.
  groupHead(machine, group, count, worst, t) {
    const id = group?.id ?? "";
    const key = groupKey(machine);
    const over = this.pressed !== null && this.overGroup === slot(machine, id);
    const renaming = group && this.renaming?.machine === key && this.renaming.id === id;
    const drag = this.groupDrag;
    const target = Boolean(group) && drag?.over === id && drag.machine === key; // a dragged group would land before this one
    const lifted = Boolean(group) && drag?.id === id && drag.machine === key && drag.over !== null;
    const hovered = this.hoverGroup === slot(machine, id);
    const stop = (_e, cx) => cx.stop_propagation();
    return h_flex().id(`group-${key}-${id || "none"}`).relative().h(24).flex_none().px(8).gap(6).items_center().rounded(6).text_size(11.5).text_color(t.dim)
      .when(group, el => el.cursor_pointer().hover(s => s.bg(t.hover)).on_hover((on, cx) => {
        if (on) this.hoverGroup = slot(machine, id);
        else if (this.hoverGroup === slot(machine, id)) this.hoverGroup = null;
        cx.notify();
      }))
      .when(over, el => el.border_1().border_color(t.accent))
      .when(lifted, el => el.opacity(0.5))
      .when(target, el => el.child(div().absolute().left(0).right(0).top(0).h(2).bg(t.accent)))
      .on_mouse_move((_e, cx) => { this.dragOver(machine, null, id, cx); this.dragGroupOver(machine, id, cx); })
      .on_mouse_up("left", (_e, cx) => {
        const from = this.pressed, spaceTarget = this.overGroup;
        this.pressed = null;
        this.dragging = false;
        this.over = null;
        this.overGroup = null;
        if (from !== null && spaceTarget === slot(machine, id)) this.moveToGroup(machine, from, id);
        else this.dropGroup(machine, id);
        cx.notify();
      })
      .when(group, el => el.on_mouse_down("left", () => {
        // A press starts a drag unless the name is being edited; whether it ends as a drag or as a click is
        // known only on release (a click folds, a move onto another header drops).
        this.groupMoved = false;
        this.groupDrag = renaming ? null : { machine: key, id, over: null };
      }))
      .when(group, el => el.on_click((e, cx) => {
        if (renaming) return;
        if (this.groupMoved) { this.groupMoved = false; return; } // the release ended a drag, not a click
        // Both clicks of a double-click fold: the pair cancels out, and the second one renames.
        this.toggleGroup(machine, id);
        if (e.click_count === 2) this.startRename(machine, id, cx);
      })
        .child(svg(`mods/workspace-chrome/chevron-${group.folded ? "right" : "down"}.svg`).size(10).flex_none()))
      .child(renaming
        ? div().id(`group-name-${key}-${id}`).flex_1().min_w(0).h(20).px(6).flex().items_center().rounded(4).border_1().border_color(t.accent).cursor_text()
          .on_mouse_down("left", stop)
          .on_key_down((e, cx) => { if (e.key === "escape") { this.finishRename(false); cx.notify(); } })
          .child(Input.new(this.nameInput).h_full().text_size(11.5).text_color(t.foreground))
        : div().flex_1().min_w(0).truncate().child(group?.name ?? "Ungrouped"))
      .when(speaks(worst), el => el.child(stateMarks(worst, t, this.live.now)))
      .child(div().flex_none().text_size(11).child(String(count)))
      .when(group, el => el.child(div().id(`group-delete-${key}-${id}`).flex_none().px(3).py(2).rounded(4).tooltip("Delete group (its spaces become ungrouped)")
        .when(!hovered, el => el.invisible())
        .hover(s => s.bg(t.hover).text_color(t.foreground))
        .on_mouse_down("left", stop)
        .on_click((_e, cx) => { cx.stop_propagation(); this.removeGroup(machine, id); })
        .child(svg("mods/workspace-chrome/x.svg").size(10))));
  }

  // The rail's mark of a group: its initial and fold state, tinted by its worst agent; click folds.
  groupMark(machine, group, worst, t) {
    return railCell(h_flex().id(`group-${groupKey(machine)}-${group.id}`).w(RAIL_CELL).h(20).gap(2).rounded(6).items_center().justify_center()
      .text_size(10).text_color(worst ? stateColor(worst, t) : t.dim).cursor_pointer().hover(s => s.bg(t.hover))
      .tooltip(group.folded ? `${group.name} (folded)` : group.name)
      .on_click((_e, cx) => { this.toggleGroup(machine, group.id); cx.notify(); })
      .child(svg(`mods/workspace-chrome/chevron-${group.folded ? "right" : "down"}.svg`).size(8).flex_none())
      .child([...group.name][0]?.toUpperCase() ?? "?"));
  }

  // Only what's unusual, after the name: "herdr \u00b7 spike \u21912 \u21931". The repo or
  // branch goes when the name already says it (a worktree is named after its branch),
  // and master/main go: nearly every space is on one. When the row is full, it
  // and the name both start from their text's width and give way in proportion.
  gitTail(g, tree, name, t) {
    const raw = g.branch ?? "detached";
    const fullName = name.toLowerCase();
    const emoji = EMOJI.exec(name)?.[0];
    const label = emoji ? name.slice(emoji.length).trim().toLowerCase() : fullName;
    const same = (a) => {
      const text = a.toLowerCase();
      return text === fullName || text === label;
    };
    const branch = same(raw) || raw === "master" || raw === "main" ? "" : raw;
    const repo = tree || same(g.name) ? "" : g.name;
    const where = [repo, branch].filter(Boolean).join(" \u00b7 ");
    return h_flex()
      .flex_auto()
      .min_w(where ? SIDE_MIN : 0)
      .gap(5)
      .justify_end()
      .items_center()
      .text_size(11.5)
      .text_color(t.dim)
      .when(Boolean(where), (el) => el.child(div().min_w(0).truncate().child(where)))
      .when(g.ahead > 0, (el) => el.child(div().flex_none().text_color(t.ansi2).child(`\u2191${g.ahead}`)))
      .when(g.behind > 0, (el) => el.child(div().flex_none().text_color(t.ansi1).child(`\u2193${g.behind}`)));
  }

  // `indent`: the row sits under a group (or Ungrouped) header.
  row({ w, i, g, tree }, t, indent = false) {
    const on = w.active;
    const fromIndex = this.pressed === null ? -1 : this.view.visible.findIndex(s => s.id === this.pressed);
    const colors = shared("space.colors") ?? {};
    const color = colors[w.id] ?? "ansi6";
    const fill = color.startsWith("#") ? color : (MARK[color] ?? t[color]);
    const marks = this.live.bySpace[w.id] ?? [];
    const dim = asleep(marks);
    const shown = marks.map((st) => stateMarks(st, t, this.live.now));
    return v_flex()
      .relative()
      .id(`space-${w.id}`)
      .px(8)
      .when(indent, (el) => el.ml(10))
      .h(rowHeight({ tree }))
      .flex_none()
      .justify_center()
      .rounded(8)
      .when(on, (el) => el.bg(t.selected))
      .when(!on, (el) => el.hover((s) => s.bg(t.hover)))
      .when(this.pressed === w.id && this.over !== w.id, (el) => el.opacity(0.5))
      .when(this.pressed !== null && this.over === w.id && this.pressed !== w.id, (el) => el.child(
        div().absolute().left(0).right(0).h(2).bg(t.accent)
          .when(fromIndex < i, line => line.bottom(0))
          .when(fromIndex > i, line => line.top(0)),
      ))
      .on_mouse_down("left", () => {
        this.pressed = w.id;
        this.over = w.id;
        this.dragging = false;
      })
      .on_mouse_move((_e, cx) => this.dragOver(w.machine ?? null, w.id, null, cx))
      .on_mouse_up("left", (_e, cx) => {
        const from = this.pressed;
        const over = this.over;
        this.pressed = null;
        this.dragging = false;
        this.over = null;
        this.overGroup = null;
        if (from !== null && over === w.id && from !== w.id) {
          // Dropped onto a row of another group: join that group, then take the row's place.
          const machine = w.machine ?? null;
          const target = this.groupIdOf(machine, w.id);
          if (this.groupIdOf(machine, from) !== target) this.moveToGroup(machine, from, target);
          const index = spaces().findIndex(s => s.id === w.id); // Herdr's own index, over every profile's spaces
          if (index !== -1) run("space.move", { id: from, index: index + 1 });
        } else if (from === w.id) run("space.select", { id: w.id });
        cx.notify();
      })
      .on_mouse_down("right", () => this.menu(w))
      .child(
        h_flex()
          .gap(9)
          .items_center()
          .when(Boolean(tree), el => el.pl(22).child(svg("mods/workspace-chrome/branch.svg").size(13).flex_none().text_color(t.dim)))
          .when(!tree, el => el.child(h_flex().size(18).flex_none().rounded(5).bg(fill).when(dim, a => a.opacity(0.45)).items_center().justify_center().text_size(10.5).font_bold().text_color("#ffffff").child(spaceAvatar(w.name))))
          .child(div().flex_initial().min_w(SIDE_MIN).truncate().text_color(dim ? t.dim : t.foreground).child(w.name))
          .child(g ? this.gitTail(g, tree, w.name, t) : div().flex_1())
          .when(Boolean(shown.length), el => el.child(h_flex().flex_none().gap(4).items_center()
            .children(shown.slice(0, MARKS_MAX))
            .when(shown.length > MARKS_MAX, el => el.child(div().flex_none().text_size(10.5).text_color(t.dim).child(`+${shown.length - MARKS_MAX}`))))),
      );
  }

  compact({ w, i }, t) {
    const worst = this.worst([{ w }]);
    return railCell(h_flex()
      .id(`space-${w.id}`)
      .relative()
      .w(RAIL_CELL)
      .h(26)
      .rounded(6)
      .justify_center()
      .items_center()
      .when(w.active, (el) => el.bg(t.selected))
      .when(!w.active, (el) => el.hover((s) => s.bg(t.hover)))
      .on_click(() => run("space.select", { id: w.id }))
      .child(div().text_color(worst?.status === "sleeping" ? t.dim : w.active ? t.foreground : t.muted).child(String(i + 1)))
      .when(speaks(worst), el => el.child(div().absolute().right(5).text_color(stateColor(worst, t)).child("\u25cf"))));
  }

  // The row above the list while the active space belongs to another profile. Not a space of this window, so it
  // is not in the list, not counted and does nothing on click.
  foreignRow(w, t, collapsed) {
    const row = h_flex().id("space-foreign").h(24).flex_none().items_center().rounded(6).bg(t.raised).text_size(11).text_color(t.dim);
    if (collapsed) return railCell(row.w(RAIL_CELL).justify_center().tooltip(`Other profile: ${w.name}`).child("\u2197"));
    return row.mx(8).px(8).gap(6)
      .child(div().flex_none().child("other profile"))
      .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(w.name));
  }

  render() {
    const t = theme();
    const collapsed = narrow();
    const palette = ["ansi6", "ansi3", "ansi4", "ansi5", "ansi2", "ansi1"];
    const sp = spaces();
    const ids = new Set(sp.map(p => p.id));
    const kept = Object.fromEntries(Object.entries(shared("space.colors") ?? {}).filter(([id]) => ids.has(id)));
    let changed = Object.keys(kept).length !== Object.keys(shared("space.colors") ?? {}).length;
    for (const p of sp) {
      if (kept[p.id] || p.name === "~") continue;
      const uses = c => Object.values(kept).filter(v => v === c).length;
      kept[p.id] = palette.reduce((best, c) => uses(c) < uses(best) ? c : best);
      changed = true;
    }
    if (changed) setShared("space.colors", kept);
    // Groups lose the spaces that are gone; with no spaces at all (start-up) there is nothing to compare with.
    const known = shared(GROUPS) ?? {};
    const settled = (key) => key === LOCAL || machines().find((m) => m.name === key)?.layout === "ready";
    const groups = sp.length ? pruneGroups(known, ids, settled) : known;
    if (JSON.stringify(groups) !== JSON.stringify(known)) setShared(GROUPS, groups);
    if (this.focusName && this.renaming) { this.focusName = false; this.nameInput.focus(); }
    // Agent states, read once per render. An agent the sync has not stamped yet shows by Herdr's status.
    const now = Date.now();
    const states = shared(STATE) ?? {};
    const bySpace = {};
    for (const a of agents()) if (a.program?.agent) (bySpace[a.space] ??= []).push(states[a.address] ?? { status: a.status, since: now, cache: null });
    this.live = { bySpace, now };
    // The spaces this window shows, once per render: every list, group, count, drag and key command goes by it.
    // Pruning above stays on all spaces: a late or failed profiles.json read must never wipe the groups.
    const profiled = this.loaded && Boolean(this.profileName);
    const visible = visibleSpaces(sp, this.profiles, profiled ? this.profileName : null);
    const active = sp.find(p => p.active)?.id;
    this.view = { visible, ids: new Set(visible.map((p) => p.id)), activeId: active ?? null };
    // A space of another profile can still be selected here (an agent link, "Open space"): say so, never hide it.
    const foreign = profiled ? sp.find(p => p.active && !this.view.ids.has(p.id)) : null;
    const list = this.grouped(t, collapsed, groups);
    // Bring the active space into view when it, or its place in the list, changes; a row already in view stays put.
    if (active !== this.revealed || this.activeAt !== this.revealedAt) {
      this.revealed = active;
      this.revealedAt = this.activeAt;
      if (this.activeAt >= 0) this.scroll.scroll_to_item(this.activeAt);
    }
    return v_flex().size_full().min_h(0).bg(shared(TINT) ?? t.chrome).text_size(13)
      .child(h_flex().h(31).pl(16).pr(10).items_center().justify_between().text_size(11.5).text_color(t.dim)
        .when(collapsed, el => el.pl(0).pr(0).justify_center())
        .when(!collapsed, el => el.child("Spaces"))
        .child(div().id("space-new").size(26).flex_none().flex().items_center().justify_center().rounded(6).hover(s => s.bg(t.hover).text_color(t.foreground))
          .on_click(() => run("space.new")).child(svg("mods/workspace-chrome/plus.svg").size(14))))
      .when(Boolean(foreign), el => el.child(this.foreignRow(foreign, t, collapsed)))
      .child(v_flex().flex_1().min_h(0).px(collapsed ? 0 : 8).pb(14).overflow_y_scrollbar().mode("always").track_scroll(this.scroll)
        // A block above (another mod's) growing, or the window resizing, shrinks this list and can push the active space out.
        .on_size_change((_size, cx) => { if (this.activeAt >= 0) { this.scroll.scroll_to_item(this.activeAt); cx.notify(); } })
        .on_mouse_up("left", (_e, cx) => { this.pressed = null; this.dragging = false; this.over = null; this.overGroup = null; this.groupDrag = null; cx.notify(); })
        .children(list));
  }
}
