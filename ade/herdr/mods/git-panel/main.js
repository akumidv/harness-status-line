// Git panel: a VS Code–style Source Control view for the focused folder.
// Changes (staged / unstaged / conflicts, wrapped inline or side-by-side diff, commit),
// A "Files" button on the open diff asks the Files panel for the file (shared "file-panel.open"); Files asks
// for a file's diff the other way: shared "git-panel.open" {path (absolute), on, at}, taken by takeRequest().
// History (commits, unpushed marks, commit diff) and Branches (worktrees, local branches).
// A right-click on a file, branch or commit opens the copy menu (copy-menu.js, a copy of file-panel's):
// this mod again, as a popup with props {menu}. The opened commit's hash, subject and message are selectable.
import { View, div, list, svg, PathBuilder } from "gpui-kit";
import { v_flex, h_flex, TextareaState, Textarea, Scrollbar, TextView } from "gpui-base";
import { spaces, panes, focused, exec, readTextFile, watchFolder, theme, settings, window, run, nextCommand, shared, setShared, checking, props, openOverlay, closeOverlay } from "herdr";
import { MENU_WIDTH, MENU_PAD, MENU_TITLE_H, MENU_ROW_H, pathMenu, branchMenu, commitMenu, joinPath, subjectOf, htmlText, hasItems, menuHeight } from "./copy-menu.js";

// Reads write nothing under .git (no index refresh or lock, no fsmonitor cookie).
const READ = ["--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "diff.autoRefreshIndex=false", "-c", "color.ui=false", "-c", "core.quotepath=false"];
const DIFF = ["--no-color", "--no-ext-diff", "--no-textconv", "--no-renames"];
const MAX_ROWS = 6000, MAX_LINE = 3000, MAX_FILES = 2000, POLL = 5000;

// The dock's terminals (panel-shell) aren't project panes: while one has focus,
// keep showing the folder of the pane that had it before (or the space's folder).
let lastLoc = null;
function location() {
  const space = spaces().find((s) => s.active), f = focused();
  if (f && !f.pane && f.terminal && (shared("panel-shell.addrs") ?? []).includes(f.terminal))
    return lastLoc?.space === space?.id ? lastLoc.loc : { cwd: space?.folder ?? null, on: space?.machine ?? null };
  const pane = f?.pane ? panes().find(p => p.pane === f.pane) : null;
  const on = f?.pane === null ? (f.terminal?.includes("/") ? f.terminal.split("/")[0] : null) : space?.machine ?? null;
  const loc = { cwd: pane?.cwd || f?.cwd || space?.folder || null, on };
  lastLoc = { space: space?.id, loc };
  return loc;
}
const key = () => JSON.stringify(location());

const alpha = (c, a) => typeof c === "string" && /^#[0-9a-f]{6}$/i.test(c) ? c + a : c;
// Tabs to 4-column stops, in one pass (a regex here goes quadratic on long tab-free lines).
const untab = (text) => {
  if (!text.includes("\t")) return text;
  const parts = text.split("\t");
  let out = parts[0];
  for (let i = 1; i < parts.length; i++) out = out.padEnd(out.length - out.length % 4 + 4) + parts[i];
  return out;
};
const base = (p) => p.replace(/\/+$/, "").split("/").pop();
const dir = (p) => p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "";
const norm = (p) => (p ?? "").replace(/\/+$/, "");
const clip = (line) => line.length > MAX_LINE ? line.slice(0, MAX_LINE) + " … (line truncated)" : line;

// The copy menu as a popup at the pointer (`event.position`, window-relative like `at`).

// Opened in the click's own call (as the tab bar's menu), at the pointer.
function openCopyMenu(menu, _event, extra = {}) {
  if (!hasItems(menu)) return;
  openOverlay({ mod: "git-panel", kind: "popup", width: MENU_WIDTH, height: menuHeight(menu), at: "pointer", props: { menu, ...extra } });
}

// A right-button handler that opens the menu `make()` builds (nothing when it builds none).
const copyOn = (make) => (e, cx) => { cx.stop_propagation(); const m = make(); if (m) openCopyMenu(m, e); };

// The popup copy of this mod: group titles, one row per item; a click copies the item's text and closes.
function copyMenuView(menu, t, handle) {
  const close = () => closeOverlay(props()?.overlay);
  return v_flex().id("copy-menu").track_focus(handle).w_full().p(MENU_PAD).bg(t.popover).text_size(12.8)
    .on_key_down((e) => { if (e.key === "escape") close(); })
    .children(menu.groups.filter((g) => g.items.length).flatMap((g, gi) => [
      div().h(MENU_TITLE_H).flex_none().px(8).flex().items_center().text_size(11).text_color(t.dim).child(g.title),
      ...g.items.map((it, i) => h_flex().id(`copy-${gi}-${i}`).h(MENU_ROW_H).flex_none().px(8).rounded(7).items_center().cursor_pointer()
        .text_color(t.foreground).hover((s) => s.bg(t.hover)).tooltip(it.text)
        .on_click((_e, cx) => { cx.write_to_clipboard(it.text); close(); })
        .child(div().flex_1().min_w(0).truncate().child(it.label))),
    ]));
}

const PREFS = "git-panel.prefs";
function loadPrefs() {
  let p = {};
  try { p = JSON.parse(localStorage.getItem(PREFS) || "{}") ?? {}; } catch { p = {}; }
  return { size: 12, wrap: true, mode: "auto", tab: "changes", tree: true, ...p };
}

// Unified diff → rows. `single`: one file, its header is not shown.
function parseDiff(text, single) {
  const rows = [];
  let o = 0, n = 0, file = null, inHunk = false;
  for (const raw of text.replace(/\n$/, "").split("\n")) {
    if (rows.length >= MAX_ROWS) { rows.push({ t: "meta", text: "… diff truncated" }); break; }
    if (raw.startsWith("diff --git ")) {
      const m = / b\/(.*)$/.exec(raw);
      file = { t: "file", path: m ? m[1] : raw.slice(11), add: 0, del: 0, tags: [] };
      if (!single) rows.push(file);
      inHunk = false;
      continue;
    }
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)$/.exec(raw);
    if (h) { o = +h[1]; n = +h[2]; inHunk = true; rows.push({ t: "hunk", text: raw }); continue; }
    if (!inHunk) {
      if (raw.startsWith("new file mode")) file?.tags.push("new");
      else if (raw.startsWith("deleted file mode")) file?.tags.push("deleted");
      else if (raw.startsWith("old mode") || raw.startsWith("new mode")) file?.tags.push(raw);
      else if (raw.startsWith("Binary files")) rows.push({ t: "meta", text: "Binary file changed" });
      continue;
    }
    const c = raw[0], body = untab(clip(raw.slice(1)));
    if (c === "+") { rows.push({ t: "add", n: n++, text: body }); if (file) file.add++; }
    else if (c === "-") { rows.push({ t: "del", o: o++, text: body }); if (file) file.del++; }
    else if (c === "\\") rows.push({ t: "meta", text: raw.slice(2) });
    else rows.push({ t: "ctx", o: o++, n: n++, text: body });
  }
  return rows;
}

// Inline rows → side-by-side pairs: a run of removals faces the additions after it.
function pairRows(rows) {
  const out = [];
  let dels = [], adds = [];
  const flush = () => {
    for (let i = 0; i < Math.max(dels.length, adds.length); i++) out.push({ t: "pair", l: dels[i] ?? null, r: adds[i] ?? null });
    dels = []; adds = [];
  };
  for (const row of rows) {
    if (row.t === "del") { if (adds.length) flush(); dels.push(row); }
    else if (row.t === "add") adds.push(row);
    else { flush(); out.push(row.t === "ctx" ? { t: "pair", l: row, r: row } : row); }
  }
  flush();
  return out;
}

function textRows(text, kind) {
  const rows = [];
  text.slice(0, 600000).replace(/\n$/, "").split("\n").slice(0, MAX_ROWS).forEach((line, i) => {
    const body = untab(clip(line));
    if (kind === "add") rows.push({ t: "add", n: i + 1, text: body });
    else if (/^(<{7}|={7}|>{7}|\|{7})( |$)/.test(line)) rows.push({ t: "conflict", n: i + 1, text: body });
    else rows.push({ t: "ctx", o: i + 1, n: i + 1, text: body });
  });
  return rows;
}

function parseWorktrees(text) {
  const out = [];
  for (const block of text.split("\n\n")) {
    const w = {};
    for (const line of block.split("\n")) {
      const [k, ...rest] = line.split(" "), v = rest.join(" ");
      if (k === "worktree") w.path = v;
      else if (k === "HEAD") w.head = v;
      else if (k === "branch") w.branch = v.replace(/^refs\/heads\//, "");
      else if (k === "detached") w.detached = true;
      else if (k === "bare") w.bare = true;
      else if (k === "locked") w.locked = v || true;
      else if (k === "prunable") w.prunable = v || true;
    }
    if (w.path) out.push(w);
  }
  return out;
}

const LETTER = { M: "M", T: "M", A: "A", D: "D", R: "R", C: "A" };

// Commit graph: each commit gets a lane; lanes hold the hash they wait for next.
const LANE_W = 12, ROW_H = 24, MAX_LANES = 14;
function layoutGraph(log) {
  const lanes = [];
  let width = 1;
  for (const cm of log) {
    const before = lanes.slice();
    let idx = lanes.indexOf(cm.hash);
    if (idx < 0) { idx = lanes.indexOf(null); if (idx < 0) { idx = lanes.length; lanes.push(null); } }
    const conv = [];
    lanes.forEach((h, j) => { if (j !== idx && h === cm.hash) { conv.push(j); lanes[j] = null; } });
    const [first, ...rest] = cm.parents;
    lanes[idx] = first ?? null;
    const merges = [];
    for (const p of rest) {
      let k = lanes.indexOf(p);
      if (k < 0) { k = lanes.indexOf(null); if (k < 0) { k = lanes.length; lanes.push(null); } lanes[k] = p; }
      merges.push(k);
    }
    while (lanes.length && lanes[lanes.length - 1] === null) lanes.pop();
    cm.g = { idx, before, after: lanes.slice(), conv, merges, top: before[idx] === cm.hash };
    width = Math.max(width, before.length, lanes.length, idx + 1);
  }
  return width;
}

export default class GitPanel extends View {
  init(_props, cx) {
    if (props()?.menu) {
      this.menu = props().menu;
      this.menuFocus = cx.focus_handle(); this.menuFocus.focus();
      this.resolveMenuRoot?.(cx);
      return;
    }
    this.prefs = loadPrefs();
    this.w = 400; this.h = 800; this.collapsed = {}; this.diffGen = 0;
    // A multi-line message, as in VS Code: Enter adds a line (the field grows to 3 rows, then scrolls),
    // Ctrl+Enter (Cmd+Enter on macOS) commits.
    this.input = TextareaState.new({ placeholder: "Message (Ctrl+Enter to commit)" });
    this.input.set_auto_grow(1, 3);
    this.reset();
    this.input.on("change", (_e, cx) => {
      const had = Boolean(this.message.trim());
      this.message = this.input.value();
      if (had !== Boolean(this.message.trim())) cx.notify();
    });
    this.input.on("submit", (e, cx) => { if (e?.secondary) this.commit(cx); });
    this.input.on("focus", (_e, cx) => { this.typing = true; cx.notify(); });
    this.input.on("blur", (_e, cx) => { this.typing = false; cx.notify(); });
    this.handledOpen = shared("git-panel.open")?.at ?? 0; // requests older than this start are not replayed
    this.want = null; // {path, on} of a file Files asked for, applied when the lists are loaded
    this.refresh(cx, true);
    cx.timer.every(1000, (cx) => this.tick(cx));
    cx.timer.every(200, (cx) => this.takeRequest(cx)); // only a shared read, so the panel reacts at once
    // Commands: git-panel.changes / .history / .branches / .graph-all / .graph-head (keys.json, palette, `herdr-app do`).
    cx.spawn(async (cx) => {
      for (;;) {
        const c = await nextCommand();
        if (["changes", "history", "branches"].includes(c)) this.setTab(c, cx);
        else if (c === "tree" || c === "list") this.setPrefs({ tree: c === "tree" }, cx);
        else if (c === "graph-all" || c === "graph-head") { if (Boolean(this.prefs.allRefs) !== (c === "graph-all")) this.toggleAllRefs(cx); }
        cx.notify();
      }
    });
  }

  // shared "git-panel.open" (written by file-panel): show this file's diff in Changes. The newest `at` wins;
  // refresh() applies `want` once the lists are loaded (the file may sit in another repo or have no changes).
  takeRequest(cx) {
    const req = shared("git-panel.open");
    if (checking() || !(req?.at > this.handledOpen) || typeof req.path !== "string") return;
    this.handledOpen = req.at;
    this.want = { path: req.path, on: req.on ?? null };
    this.note = null;
    if (this.prefs.tab !== "changes") this.setTab("changes", cx);
    this.refresh(cx, true);
  }

  // Selects the wanted file among the changes, or says why it cannot.
  applyWant(all, repo) {
    const { path, on } = this.want;
    this.want = null;
    const root = `${repo.root}/`;
    if ((on ?? null) !== (repo.on ?? null) || !path.startsWith(root)) { this.note = `${base(path)} is outside this repository`; return; }
    const rel = path.slice(root.length);
    const hit = ["unstaged", "staged", "conflict"].map((g) => all.find(([group, f]) => group === g && f.path === rel)).find(Boolean);
    if (!hit) { this.note = `${base(rel)} has no changes`; return; }
    this.sel = { group: hit[0], path: rel }; this.closed = false;
    this.diff = []; this.diffNote = null; this.diffGen++;
  }

  // The open diff's file, final version, in the Files panel.
  openInFiles(path) {
    if (!this.repo) return;
    setShared("file-panel.open", { path: `${this.repo.root}/${path}`, on: this.repo.on ?? null, at: Date.now() });
    run("layout.select", { stack: "panel-body", child: "files" });
  }

  reset() {
    this.ctxKey = key(); this.repo = null; this.error = null; this.note = null; this.confirm = null;
    this.conflicts = []; this.staged = []; this.unstaged = []; this.limited = false;
    this.sel = null; this.closed = false; this.diff = []; this.diffGen++; this.diffNote = null;
    this.log = null; this.logOid = null; this.commitSel = null; this.commitInfo = null; this.commitDiff = []; this.commitNote = null;
    this.branches = null; this.worktreeDirty = {}; this.cmp = null; this.brSel = null; this.base = null; this.vsCache = new Map();
    this.message = ""; this.input.set_value("");
    this.loading = false; this.again = false; this.dirtyAt = 0; this.nextPoll = 0;
    this.watching?.pending?.cancel?.(); this.watching = null;
  }

  setPrefs(patch, cx) {
    this.prefs = { ...this.prefs, ...patch };
    try { localStorage.setItem(PREFS, JSON.stringify(this.prefs)); } catch {}
    this.diffGen++;
    cx.notify();
  }

  stale(k) { return this.ctxKey !== k || key() !== k; }

  tick(cx) {
    if (key() !== this.ctxKey) return this.refresh(cx, true);
    if (window()?.active === false || this.loading || this.busy) return;
    const now = Date.now();
    if ((this.dirtyAt && now >= this.dirtyAt) || now >= this.nextPoll) this.refresh(cx);
  }

  watch(root, on, k, cx) {
    if (on || this.watching?.root === root) return;
    this.watching?.pending?.cancel?.();
    const w = { root };
    this.watching = w;
    cx.spawn(async () => {
      try {
        while (this.watching === w && !this.stale(k)) {
          w.pending = watchFolder(root);
          const paths = await w.pending;
          if (paths.length && this.watching === w) this.dirtyAt = Date.now() + 300;
        }
      } catch { /* too many folders to watch: polling covers it */ }
    });
  }

  refresh(cx, force = false) {
    if (key() !== this.ctxKey) this.reset();
    const { cwd, on } = location(), k = this.ctxKey;
    this.dirtyAt = 0; this.nextPoll = Date.now() + POLL;
    if (!cwd) { this.error = "No folder"; cx.notify(); return; }
    if (this.loading) { this.again = true; return; }
    this.loading = true;
    cx.spawn(async (cx) => {
      try {
        const top = await exec("git", ["rev-parse", "--show-toplevel"], { cwd, on });
        if (this.stale(k)) return;
        if (top.code !== 0) { this.repo = null; this.error = "Not a git repository"; return; }
        const root = top.stdout.trim(), git = (args) => exec("git", [...READ, ...args], { cwd: root, on });
        this.watch(root, on, k, cx);
        const [st, cachedStat, workStat, head, dirs, wt, stash] = await Promise.all([
          git(["status", "--porcelain=v2", "--branch", "-z", "--no-renames", "--untracked-files=all"]),
          git(["diff", "--cached", "--numstat", "-z", "--no-renames"]),
          git(["diff", "--numstat", "-z", "--no-renames"]),
          git(["log", "-1", "--format=%h%x1f%s%x1f%ar%x1f%an"]),
          git(["rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"]),
          git(["worktree", "list", "--porcelain"]),
          git(["stash", "list", "--format=%h"]),
        ]);
        if (this.stale(k)) return;
        if (st.code !== 0) throw new Error(st.stderr.trim());
        const repo = { root, on, name: base(root), branch: null, oid: null, upstream: null, ahead: 0, behind: 0 };
        const conflicts = [], staged = [], unstaged = [];
        let total = 0;
        for (const e of st.stdout.split("\0")) {
          if (!e) continue;
          if (e.startsWith("# ")) {
            const [name, ...v] = e.slice(2).split(" ");
            if (name === "branch.oid") repo.oid = v[0] === "(initial)" ? null : v[0];
            else if (name === "branch.head") repo.branch = v[0] === "(detached)" ? null : v[0];
            else if (name === "branch.upstream") repo.upstream = v[0];
            else if (name === "branch.ab") { repo.ahead = Math.abs(+v[0]); repo.behind = Math.abs(+v[1]); }
            continue;
          }
          if (++total > MAX_FILES) continue;
          const f = e.split(" ");
          if (e[0] === "?") unstaged.push({ path: e.slice(2), s: "U", untracked: true });
          else if (e[0] === "1" || e[0] === "2") {
            const path = f.slice(8).join(" "), xy = f[1];
            if (xy[0] !== ".") staged.push({ path, s: LETTER[xy[0]] ?? xy[0] });
            if (xy[1] !== ".") unstaged.push({ path, s: LETTER[xy[1]] ?? xy[1] });
          } else if (e[0] === "u") conflicts.push({ path: f.slice(10).join(" "), s: "!" });
        }
        const stats = (r) => {
          const m = {};
          if (r.code !== 0) return m;
          for (const entry of r.stdout.split("\0")) {
            const [a, d, ...parts] = entry.split("\t");
            if (parts.length) m[parts.join("\t")] = { add: Number(a) || 0, del: Number(d) || 0, binary: a === "-" };
          }
          return m;
        };
        const cs = stats(cachedStat), ws = stats(workStat);
        for (const f of staged) Object.assign(f, cs[f.path] ?? {});
        for (const f of unstaged) Object.assign(f, ws[f.path] ?? {});
        if (head.code === 0 && head.stdout.trim()) {
          const [h, subject, when, author] = head.stdout.trim().split("\x1f");
          repo.head = { h, subject, when, author };
        }
        if (dirs.code === 0) {
          const [gitDir, common] = dirs.stdout.trim().split("\n");
          repo.gitDir = gitDir; repo.linked = norm(gitDir) !== norm(common);
          repo.mainPath = /\/\.git\/?$/.test(common) ? common.replace(/\/\.git\/?$/, "") : common;
          const ops = await exec("sh", ["-c", 'for f in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG; do [ -e "$1/$f" ] && echo "$f"; done; true', "sh", gitDir], { cwd: root, on });
          const found = ops.stdout.split("\n").filter(Boolean);
          repo.op = found.some(x => x.startsWith("rebase")) ? "REBASING" : found.includes("MERGE_HEAD") ? "MERGING"
            : found.includes("CHERRY_PICK_HEAD") ? "CHERRY-PICKING" : found.includes("REVERT_HEAD") ? "REVERTING" : found.includes("BISECT_LOG") ? "BISECTING" : null;
        }
        repo.worktrees = wt.code === 0 ? parseWorktrees(wt.stdout) : [];
        repo.stash = stash.code === 0 ? stash.stdout.split("\n").filter(Boolean).length : 0;
        if (this.stale(k)) return;
        this.repo = repo; this.error = null; this.limited = total > MAX_FILES;
        this.conflicts = conflicts; this.staged = staged; this.unstaged = unstaged;
        const all = [...conflicts.map(f => ["conflict", f]), ...staged.map(f => ["staged", f]), ...unstaged.map(f => ["unstaged", f])];
        if (this.want) this.applyWant(all, repo);
        if (this.sel && !all.some(([g, f]) => g === this.sel.group && f.path === this.sel.path)) this.sel = null;
        if (!this.sel && !this.closed && all.length) this.sel = { group: all[0][0], path: all[0][1].path };
        if (!this.sel) { this.diff = []; this.diffNote = null; }
        await this.loadSelected(k);
        if (this.prefs.tab === "history") await this.loadLog(k);
        if (this.prefs.tab === "branches") await this.loadBranches(k);
      } catch (e) { if (!this.stale(k)) this.error = String(e?.message ?? e); }
      finally {
        if (this.ctxKey === k) this.loading = false;
        if (this.again) { this.again = false; this.dirtyAt = Date.now(); }
        cx.notify();
      }
    });
  }

  file(group, path) {
    const files = group === "conflict" ? this.conflicts : group === "staged" ? this.staged : this.unstaged;
    return files.find(f => f.path === path);
  }

  async loadSelected(k) {
    const sel = this.sel, repo = this.repo;
    if (!sel || !repo) return;
    const f = this.file(sel.group, sel.path);
    if (!f) return;
    let rows, note = null;
    try {
      if (sel.group === "conflict" || f.untracked) {
        const text = await readTextFile(`${repo.root}/${sel.path}`, { on: repo.on }).catch(() => null);
        if (text === null) note = "Cannot preview this file (binary, too large, or unreadable)";
        else if (text.includes("\0")) note = "Binary file · no text preview";
        else { rows = textRows(text, f.untracked ? "add" : "conflict"); if (f.untracked) f.add = rows.length; }
      } else {
        const r = await exec("git", [...READ, "diff", ...DIFF, ...(sel.group === "staged" ? ["--cached"] : []), "--", sel.path], { cwd: repo.root, on: repo.on });
        if (r.code !== 0) throw new Error(r.stderr.trim());
        rows = parseDiff(r.stdout, true);
        if (!rows.length) note = f.binary ? "Binary file changed" : f.s === "D" ? "File deleted" : "No text changes (mode or whitespace only)";
      }
    } catch (e) { note = `Cannot preview this diff: ${e?.message ?? e}`; }
    if (this.stale(k) || this.sel !== sel) return;
    this.diff = rows ?? []; this.diffNote = note; this.diffGen++;
  }

  async loadLog(k, force = false) {
    const repo = this.repo;
    if (!repo) return;
    const git = (args) => exec("git", [...READ, ...args], { cwd: repo.root, on: repo.on });
    const all = Boolean(this.prefs.allRefs);
    // Any ref moving (a commit in another worktree, a fetch) redraws the graph.
    const refs = await git(["for-each-ref", "--format=%(objectname)", "refs/heads", "refs/remotes", "refs/tags"]);
    const sig = `${all}:${repo.oid}:${refs.stdout}`;
    if (!force && this.log && this.logSig === sig) return;
    const [log, ahead] = await Promise.all([
      git(["log", "-n", "300", "--topo-order", ...(all ? ["--all"] : []), "--format=%H%x1f%h%x1f%P%x1f%s%x1f%an%x1f%ar%x1f%D%x1e"]),
      repo.upstream ? git(["rev-list", "@{u}..HEAD"]) : Promise.resolve({ code: 1, stdout: "" }),
    ]);
    if (this.stale(k)) return;
    const unpushed = new Set(ahead.code === 0 ? ahead.stdout.split("\n").filter(Boolean) : []);
    this.log = log.code !== 0 ? [] : log.stdout.split("\x1e").map(s => s.replace(/^\n/, "")).filter(Boolean).map(s => {
      const [hash, h, parents, subject, author, when, refs] = s.split("\x1f");
      return { hash, h, parents: parents ? parents.split(" ") : [], subject, author, when, head: (refs ?? "").includes("HEAD -> ") || (refs ?? "") === "HEAD",
        refs: refs ? refs.split(", ").filter(r => r && r !== "HEAD") : [], unpushed: unpushed.has(hash) };
    });
    this.lanes = layoutGraph(this.log);
    this.logSig = sig;
  }

  toggleAllRefs(cx) {
    this.setPrefs({ allRefs: !this.prefs.allRefs }, cx);
    const k = this.ctxKey;
    cx.spawn(async (cx) => { await this.loadLog(k, true); cx.notify(); });
  }

  // One row of the graph: lanes passing through, lines into and out of the commit, its dot.
  graphCell(cm, t) {
    const g = cm.g, colors = [t.ansi4, t.ansi2, t.ansi5, t.ansi3, t.ansi6, t.ansi1, t.accent];
    const color = (i) => colors[i % colors.length];
    if (!cm.paths) {
      const x = (i) => 7 + i * LANE_W, mid = ROW_H / 2, H = ROW_H, show = (i) => i < MAX_LANES, segs = [];
      const line = () => PathBuilder.stroke(1.6);
      g.before.forEach((h, i) => {
        if (h && i !== g.idx && !g.conv.includes(i) && show(i)) segs.push([i, line().move_to(x(i), 0).line_to(x(i), H).build()]);
      });
      if (show(g.idx)) {
        if (g.top) segs.push([g.idx, line().move_to(x(g.idx), 0).line_to(x(g.idx), mid).build()]);
        if (g.after[g.idx]) segs.push([g.idx, line().move_to(x(g.idx), mid).line_to(x(g.idx), H).build()]);
      }
      for (const j of g.conv) if (show(j)) segs.push([j, line().move_to(x(j), 0).cubic_bezier_to(x(g.idx), mid, x(j), mid * 0.8, x(g.idx), mid * 0.3).build()]);
      for (const k of g.merges) if (show(k)) segs.push([k, line().move_to(x(g.idx), mid).cubic_bezier_to(x(k), H, x(g.idx), mid + (H - mid) * 0.7, x(k), mid + (H - mid) * 0.2).build()]);
      const circle = (b, r) => b.add_polygon(Array.from({ length: 16 }, (_, n) => [x(g.idx) + r * Math.cos(n * Math.PI / 8), mid + r * Math.sin(n * Math.PI / 8)]), true).build();
      if (show(g.idx)) {
        const merge = cm.parents.length > 1, r = cm.head ? 4.5 : 3.6;
        segs.push(["bg", circle(PathBuilder.fill(), r + 1.5)]);
        segs.push([g.idx, circle(merge || cm.head ? PathBuilder.stroke(1.8) : PathBuilder.fill(), r)]);
      }
      cm.paths = segs;
    }
    return div().relative().flex_none().w(10 + Math.min(this.lanes ?? 1, MAX_LANES) * LANE_W).h(ROW_H)
      .children(cm.paths.map(([lane, path]) => globalThis.window.paint_path(path, lane === "bg" ? t.surface : color(lane)).absolute().top(0).left(0).size_full()));
  }

  async loadCommit(hash, k) {
    const repo = this.repo;
    if (!repo) return;
    const git = (args) => exec("git", [...READ, ...args], { cwd: repo.root, on: repo.on });
    const [info, patch] = await Promise.all([
      git(["show", "-s", "--format=%H%x1f%an%x1f%ae%x1f%ad%x1f%B", "--date=format:%Y-%m-%d %H:%M", hash]),
      git(["show", "--format=", ...DIFF, "--diff-merges=first-parent", "-p", hash]),
    ]);
    if (this.stale(k) || this.commitSel !== hash) return;
    if (info.code === 0) {
      const [full, author, email, date, body] = info.stdout.split("\x1f");
      this.commitInfo = { full, author, email, date, body: (body ?? "").trim() };
    }
    this.commitDiff = patch.code === 0 ? parseDiff(patch.stdout, false) : [];
    this.commitNote = patch.code !== 0 ? patch.stderr.trim() : this.commitDiff.length ? null : "No changes in this commit";
    this.diffGen++;
  }

  // The base every branch is measured against: the default branch (local or remote) or HEAD.
  async resolveBase(git) {
    const sym = await git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"]);
    let remote = sym.code === 0 ? sym.stdout.trim() : null;
    const exists = async (ref) => (await git(["rev-parse", "--verify", "--quiet", `${ref}^{commit}`])).code === 0;
    if (!remote) for (const c of ["origin/main", "origin/master"]) if (await exists(c)) { remote = c; break; }
    let local = remote ? remote.replace(/^[^/]+\//, "") : null;
    if (!local || !(await exists(`refs/heads/${local}`))) {
      local = null;
      for (const c of ["main", "master", "trunk", "develop"]) if (await exists(`refs/heads/${c}`)) { local = c; break; }
    }
    const pick = this.prefs.base ?? "default";
    const ref = pick === "current" ? "HEAD" : pick === "remote" ? (remote ?? local ?? "HEAD") : (local ?? remote ?? "HEAD");
    const label = ref === "HEAD" ? (this.repo?.branch ?? "HEAD") : ref;
    return { ref, label, local, remote };
  }

  // Where a branch stands against the base: commits ahead/behind, and whether its work is already in.
  // merged: its tip is in the base. rebased: every commit has a same-patch twin in the base (git cherry).
  // squashed: the branch's whole change matches one base commit (patch-id of a throwaway squash commit).
  // content: a trial merge (git merge-tree, nothing touched) leaves the base tree unchanged.
  async branchStatus(git, base, baseTree, name) {
    const st = { ahead: 0, behind: 0, unique: 0, state: "unmerged", conflicts: false };
    const counts = await git(["rev-list", "--left-right", "--count", `${base}...${name}`]);
    if (counts.code !== 0) { st.state = "unknown"; return st; }
    const [behind, ahead] = counts.stdout.trim().split(/\s+/).map(Number);
    Object.assign(st, { ahead, behind });
    if (!ahead) { st.state = "merged"; return st; }
    const cherry = await git(["cherry", base, name]);
    const lines = cherry.code === 0 ? cherry.stdout.split("\n").filter(Boolean) : [];
    st.unique = lines.filter(l => l.startsWith("+")).length;
    if (cherry.code === 0 && st.unique === 0) { st.state = "rebased"; return st; }
    const mb = await git(["merge-base", base, name]);
    if (mb.code === 0) {
      const squash = await git(["commit-tree", `${name}^{tree}`, "-p", mb.stdout.trim(), "-m", "git-panel squash probe"]);
      if (squash.code === 0) {
        const probe = await git(["cherry", base, squash.stdout.trim()]);
        if (probe.code === 0 && probe.stdout.startsWith("-")) { st.state = "squashed"; return st; }
      }
    }
    const mt = await git(["merge-tree", "--write-tree", "--no-messages", "--name-only", base, name]);
    if (mt.code === 0 && mt.stdout.split("\n")[0].trim() === baseTree) { st.state = "content"; return st; }
    st.conflicts = mt.code === 1;
    return st;
  }

  async loadBranches(k) {
    const repo = this.repo;
    if (!repo) return;
    const git = (args) => exec("git", [...READ, ...args], { cwd: repo.root, on: repo.on });
    const fmt = ["%(HEAD)", "%(refname:short)", "%(upstream:short)", "%(upstream:track,nobracket)", "%(committerdate:relative)", "%(contents:subject)", "%(worktreepath)", "%(objectname)"].join("%1f");
    const [r, base] = await Promise.all([
      git(["for-each-ref", "refs/heads", "--sort=-committerdate", "--count=150", `--format=${fmt}`]),
      this.resolveBase(git),
    ]);
    const dirty = {};
    await Promise.all(repo.worktrees.filter(w => !w.bare && !w.prunable).slice(0, 30).map(async w => {
      const s = await exec("git", [...READ, "status", "--porcelain", "--untracked-files=normal"], { cwd: w.path, on: repo.on }).catch(() => null);
      dirty[w.path] = s?.code === 0 ? s.stdout.split("\n").filter(Boolean).length : null;
    }));
    if (this.stale(k)) return;
    const branches = r.code !== 0 ? [] : r.stdout.split("\n").filter(Boolean).map(line => {
      const [head, name, upstream, track, when, subject, wtPath, tip] = line.split("\x1f");
      const ahead = /ahead (\d+)/.exec(track ?? "")?.[1], behind = /behind (\d+)/.exec(track ?? "")?.[1];
      return { current: head === "*", name, upstream, gone: track === "gone", upAhead: +(ahead ?? 0), upBehind: +(behind ?? 0), when, subject, wtPath, tip };
    });
    // Show the list at once; statuses fill in as they are computed.
    const keep = this.branches && this.base?.ref === base.ref ? new Map(this.branches.map(b => [b.name, b.vs])) : new Map();
    for (const b of branches) b.vs = keep.get(b.name) ?? null;
    this.worktreeDirty = dirty; this.base = base; this.branches = branches;
    const baseTree = (await git(["rev-parse", `${base.ref}^{tree}`])).stdout.trim();
    const baseOid = (await git(["rev-parse", base.ref])).stdout.trim();
    const todo = branches.filter(b => b.name !== base.ref && !(base.ref === "HEAD" && b.current)).slice(0, 80);
    let next = 0;
    await Promise.all(Array.from({ length: 6 }, async () => {
      while (next < todo.length && !this.stale(k)) {
        const b = todo[next++];
        const ck = `${baseOid}:${b.tip}`;
        b.vs = this.vsCache.get(ck) ?? await this.branchStatus(git, baseOid || base.ref, baseTree, b.name).catch(() => ({ state: "unknown" }));
        if (b.vs.state !== "unknown") this.vsCache.set(ck, b.vs);
      }
    }));
  }

  async loadCompare(k) {
    const repo = this.repo, c = this.cmp, base = this.base?.ref ?? "HEAD";
    if (!repo || !c) return;
    const git = (args) => exec("git", [...READ, ...args], { cwd: repo.root, on: repo.on });
    const range = `${base}...${c.name}`;
    const [log, mt, behind] = await Promise.all([
      git(["log", "-n", "500", "--cherry-mark", "--right-only", "--no-merges", "--format=%m%x1f%H%x1f%h%x1f%s%x1f%an%x1f%ar", range]),
      git(["merge-tree", "--write-tree", "--no-messages", "--name-only", base, c.name]),
      git(["rev-list", "--count", `${c.name}..${base}`]),
    ]);
    const [tree, ...conflicted] = mt.stdout.split("\n").map(s => s.trim()).filter(Boolean);
    const merge = mt.code === 0 || mt.code === 1 ? { tree, conflicts: mt.code === 1 ? conflicted : [] } : null;
    let mode = c.mode;
    let note = null;
    if (mode === "merge" && (!merge || merge.conflicts.length)) { note = merge ? `A merge would conflict in ${merge.conflicts.length} file(s); showing the branch's own changes instead` : "Trial merge unavailable; showing the branch's own changes"; mode = "fork"; }
    const args = mode === "merge" ? ["diff", ...DIFF, base, merge.tree] : mode === "full" ? ["diff", ...DIFF, base, c.name] : ["diff", ...DIFF, range];
    const d = await git(args);
    if (this.stale(k) || this.cmp !== c) return;
    c.commits = log.code !== 0 ? [] : log.stdout.split("\n").filter(Boolean).map(line => {
      const [m, hash, h, subject, author, when] = line.split("\x1f");
      return { same: m === "=", hash, h, subject, author, when };
    });
    c.behind = Number(behind.stdout.trim()) || 0;
    c.merge = merge; c.note = note;
    c.diff = d.code === 0 ? parseDiff(d.stdout, false) : [];
    c.empty = d.code === 0 && !c.diff.length;
    if (d.code !== 0) c.note = d.stderr.trim();
    this.diffGen++;
  }

  // A commit in the comparison: its files open below it; a file's own diff replaces the comparison's.
  toggleCommitFiles(x, cx) {
    const c = this.cmp;
    if (!c) return;
    c.open ??= new Set(); c.files ??= {};
    if (c.open.has(x.hash)) { c.open.delete(x.hash); cx.notify(); return; }
    c.open.add(x.hash);
    if (!c.files[x.hash]) {
      const repo = this.repo, k = this.ctxKey;
      const git = (args) => exec("git", [...READ, ...args], { cwd: repo.root, on: repo.on });
      cx.spawn(async (cx) => {
        const [num, names] = await Promise.all([
          git(["diff-tree", "-r", "--root", "--no-commit-id", "--no-renames", "-z", "--numstat", x.hash]),
          git(["diff-tree", "-r", "--root", "--no-commit-id", "--no-renames", "-z", "--name-status", x.hash]),
        ]);
        if (this.stale(k) || this.cmp !== c) return;
        const stat = {};
        for (const entry of num.stdout.split("\0")) {
          const [a, d, ...rest] = entry.split("\t");
          if (rest.length) stat[rest.join("\t")] = { add: Number(a) || 0, del: Number(d) || 0 };
        }
        const parts = names.stdout.split("\0"), files = [];
        for (let i = 0; i + 1 < parts.length; i += 2) if (parts[i]) files.push({ s: LETTER[parts[i][0]] ?? parts[i][0], path: parts[i + 1], ...(stat[parts[i + 1]] ?? {}) });
        c.files[x.hash] = files;
        cx.notify();
      });
    }
    cx.notify();
  }

  pickCommitFile(x, path, cx) {
    const c = this.cmp;
    if (!c) return;
    if (c.fileSel?.hash === x.hash && c.fileSel?.path === path) { c.fileSel = null; this.diffGen++; cx.notify(); return; }
    c.fileSel = { hash: x.hash, h: x.h, path }; c.fileDiff = []; c.fileNote = "Loading…"; this.diffGen++;
    const repo = this.repo, k = this.ctxKey, sel = c.fileSel;
    cx.spawn(async (cx) => {
      const r = await exec("git", [...READ, "show", "--format=", ...DIFF, x.hash, "--", path], { cwd: repo.root, on: repo.on });
      if (this.stale(k) || this.cmp !== c || c.fileSel !== sel) return;
      c.fileDiff = r.code === 0 ? parseDiff(r.stdout, true) : [];
      c.fileNote = r.code !== 0 ? r.stderr.trim() : c.fileDiff.length ? null : "No text changes (binary or mode only)";
      this.diffGen++;
      cx.notify();
    });
    cx.notify();
  }

  compare(name, cx) {
    this.cmp = { name, mode: this.cmp?.mode ?? "merge", commits: null, diff: [] };
    this.diffGen++;
    const k = this.ctxKey;
    cx.spawn(async (cx) => { await this.loadCompare(k); cx.notify(); });
    cx.notify();
  }

  setCompareMode(mode, cx) {
    if (!this.cmp) return;
    this.cmp = { ...this.cmp, mode, diff: [] };
    const k = this.ctxKey;
    cx.spawn(async (cx) => { await this.loadCompare(k); cx.notify(); });
    cx.notify();
  }

  setBase(base, cx) {
    this.setPrefs({ base }, cx);
    this.branches = null; this.cmp = null;
    const k = this.ctxKey;
    cx.spawn(async (cx) => { await this.loadBranches(k); cx.notify(); });
  }

  // Two clicks for anything that drops work: the first arms the button.
  armed(id, cx) {
    if (this.confirm === id) return true;
    this.confirm = id; cx.notify();
    return false;
  }

  branchOps(b, cx, op) {
    const id = `${op}:${b.name}`, wt = b.wtPath ? this.repo?.worktrees.find(w => norm(w.path) === norm(b.wtPath)) : null;
    const merged = ["merged", "rebased", "squashed", "content"].includes(b.vs?.state);
    switch (op) {
      case "switch": return this.act([["switch", b.name]], cx);
      case "merge": if (this.armed(id, cx)) this.act([["merge", "--no-edit", b.name]], cx); return;
      case "delete": if (this.armed(id, cx)) this.act([["branch", b.vs?.state === "merged" ? "-d" : "-D", b.name]], cx); return;
      case "remove-wt": if (wt && this.armed(id, cx)) this.act([["worktree", "remove", wt.path]], cx); return;
      case "cleanup": if (wt && merged && this.armed(id, cx)) this.act([["worktree", "remove", wt.path], ["branch", b.vs.state === "merged" ? "-d" : "-D", b.name]], cx); return;
    }
  }

  // ---- actions ---------------------------------------------------------------

  // opts.bypass: run as `env ALLOW_PRIVATE_TERMS=1 git …` (the owner's private-terms hooks let it through; exec has no env option).
  act(argsList, cx, after, { bypass = false, timeout } = {}) {
    const repo = this.repo, k = this.ctxKey;
    if (!repo || this.busy) return;
    this.busy = true; this.note = null; this.info = null; this.confirm = null;
    cx.spawn(async (cx) => {
      try {
        for (const args of argsList) {
          const r = bypass
            ? await exec("env", ["ALLOW_PRIVATE_TERMS=1", "git", ...args], { cwd: repo.root, on: repo.on, timeout })
            : await exec("git", args, { cwd: repo.root, on: repo.on, timeout });
          if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim());
        }
        if (!this.stale(k)) after?.();
      } catch (e) { if (!this.stale(k)) this.note = String(e?.message ?? e); }
      finally { this.busy = false; this.refresh(cx, true); cx.notify(); }
    });
    cx.notify();
  }

  stage(f, cx) { this.act([["add", "--", f.path]], cx); }
  unstage(f, cx) { this.act([this.repo?.oid ? ["restore", "--staged", "--", f.path] : ["rm", "--cached", "-q", "--", f.path]], cx); }
  discard(f, cx) {
    const id = `discard:${f.path}`;
    if (this.confirm !== id) { this.confirm = id; cx.notify(); return; }
    this.act([f.untracked ? ["clean", "-f", "-q", "--", f.path] : ["restore", "--worktree", "--", f.path]], cx);
  }
  stageAll(cx) { this.act([["add", "-A"]], cx); }
  unstageAll(cx) { this.act([this.repo?.oid ? ["reset", "-q"] : ["rm", "-r", "--cached", "-q", "."]], cx); }
  fetch(cx) {
    const at = () => new Date().toTimeString().slice(0, 5);
    this.act([["fetch", "--prune"]], cx, () => { this.log = null; this.info = `Fetched at ${at()}`; }, { timeout: 120000 });
  }

  commit(cx) {
    const message = this.message.trim();
    if (!message || !this.repo || this.busy || this.loading) return;
    if (!this.staged.length && !this.unstaged.length && !this.conflicts.length) return;
    const steps = this.staged.length ? [] : [["add", "-A"]];
    this.act([...steps, ["commit", "-m", message]], cx, () => { this.message = ""; this.input.set_value(""); this.log = null; this.allowTerms = false; },
      { bypass: this.allowTerms });
  }

  // Push the current branch; without an upstream publish it (`push -u <remote> HEAD`). With the bypass on, two clicks.
  push(cx) {
    const r = this.repo;
    if (!r?.branch || this.busy) return;
    if (this.allowTerms && !this.armed("push", cx)) return;
    const bypass = this.allowTerms, timeout = 120000;
    const after = () => { this.log = null; this.allowTerms = false; this.info = `Pushed at ${new Date().toTimeString().slice(0, 5)}`; };
    if (r.upstream) return this.act([["push"]], cx, after, { bypass, timeout });
    cx.spawn(async (cx) => {
      const out = await exec("git", [...READ, "remote"], { cwd: r.root, on: r.on }).catch(() => null);
      const remotes = out?.code === 0 ? out.stdout.split("\n").filter(Boolean) : [];
      const remote = remotes.includes("origin") ? "origin" : remotes.length === 1 ? remotes[0] : null;
      if (!remote) { this.note = remotes.length ? `Several remotes and no origin: push from a terminal (${remotes.join(", ")})` : "No remote to push to"; cx.notify(); return; }
      this.act([["push", "-u", remote, "HEAD"]], cx, after, { bypass, timeout });
    });
  }

  pick(group, path, cx) {
    const same = this.sel?.group === group && this.sel?.path === path;
    this.sel = same ? null : { group, path }; this.closed = same; this.confirm = null; this.note = null;
    this.diff = []; this.diffNote = null; this.diffGen++;
    const k = this.ctxKey;
    if (this.sel) cx.spawn(async (cx) => { await this.loadSelected(k); cx.notify(); });
    cx.notify();
  }

  pickCommit(hash, cx) {
    this.commitSel = this.commitSel === hash ? null : hash;
    this.commitInfo = null; this.commitDiff = []; this.commitNote = null; this.diffGen++;
    const k = this.ctxKey;
    if (this.commitSel) cx.spawn(async (cx) => { await this.loadCommit(hash, k); cx.notify(); });
    cx.notify();
  }

  setTab(tab, cx) {
    this.setPrefs({ tab }, cx);
    const k = this.ctxKey;
    cx.spawn(async (cx) => {
      if (tab === "history") await this.loadLog(k);
      if (tab === "branches") await this.loadBranches(k);
      cx.notify();
    });
  }

  // ---- rendering ---------------------------------------------------------------

  // The copy menu of a repo-relative file path (null without a repository).
  fileMenu(rel) {
    const root = this.repo?.root;
    return root ? pathMenu(joinPath(root, rel), root, { name: true }) : null;
  }

  split() { return this.prefs.mode === "split" || (this.prefs.mode === "auto" && this.w >= 1000); }

  button(id, label, t, onClick, { active = false, tip, color } = {}) {
    return div().id(id).h(22).min_w(22).px(6).flex().items_center().justify_center().rounded(5).cursor_pointer().text_size(12)
      .text_color(color ?? (active ? t.foreground : t.muted)).when(active, e => e.bg(t.raised)).hover(s => s.bg(t.hover))
      .when(Boolean(tip), e => e.tooltip(tip)).on_click((_e, cx) => onClick(cx)).child(label);
  }

  tabButton(id, label, count, t) {
    const on = this.prefs.tab === id;
    return h_flex().id(`tab-${id}`).h_full().px(8).gap(5).items_center().cursor_pointer().border_b(2).border_color(on ? t.accent : "#00000000")
      .text_color(on ? t.foreground : t.muted).hover(s => s.text_color(t.foreground)).on_click((_e, cx) => this.setTab(id, cx))
      .child(div().text_size(12).font_semibold().child(label))
      .when(count > 0, e => e.child(div().px(5).rounded(8).bg(t.raised).text_size(10).text_color(t.muted).child(String(count))));
  }

  badge(text, color, t) {
    return div().flex_none().px(5).rounded(4).border_1().border_color(alpha(color, "66")).text_color(color).text_size(10).child(text);
  }

  infoBlock(t) {
    const r = this.repo;
    if (!r) return div();
    const branch = r.branch ?? `detached @ ${r.oid?.slice(0, 7) ?? "?"}`;
    const wt = r.worktrees ?? [];
    return v_flex().flex_none().px(12).py(8).gap(4).border_b(1).border_color(t.border).text_size(12)
      .child(h_flex().gap(6).items_center().min_w(0)
        .child(div().text_color(t.accent).child("⎇"))
        .child(div().id("head-branch").font_semibold().truncate().child(branch)
          .when(Boolean(r.branch), e => e.on_mouse_down("right", copyOn(() => branchMenu(r.branch)))))
        .when(Boolean(r.op), e => e.child(this.badge(r.op, t.ansi1, t)))
        .when(Boolean(r.upstream), e => e.child(div().truncate().text_color(t.dim).child(`→ ${r.upstream}`)))
        .when(!r.upstream && Boolean(r.branch), e => e.child(div().text_color(t.dim).child("no upstream")))
        .child(div().flex_1())
        .when(r.ahead > 0, e => e.child(div().text_color(t.ansi2).child(`↑${r.ahead}`)))
        .when(r.behind > 0, e => e.child(div().text_color(t.ansi3).child(`↓${r.behind}`)))
        .when(Boolean(r.branch), e => e.child(this.button("push",
          this.confirm === "push" ? "Push without guard? click again" : !r.upstream ? "Publish" : r.ahead > 0 ? `Push ↑${r.ahead}` : "Push", t, cx => this.push(cx),
          { tip: (r.upstream ? "git push" : "git push -u <remote> HEAD") + (this.allowTerms ? " with ALLOW_PRIVATE_TERMS=1" : ""),
            color: this.allowTerms ? t.ansi3 : t.accent })))
        .child(this.button("fetch", this.busy ? "…" : "Fetch", t, cx => this.fetch(cx), { tip: "git fetch --prune" })))
      .child(h_flex().gap(6).items_center().min_w(0).text_color(t.muted)
        .child(div().flex_none().child(r.linked ? "⑂ worktree" : "▣ repo"))
        .child(div().flex_1().min_w(0).truncate().text_color(t.dim).child(r.linked ? `${r.root}  ·  main: ${r.mainPath}` : r.root))
        .when(wt.length > 1, e => e.child(div().id("wt-count").cursor_pointer().text_color(t.accent).on_click((_e, cx) => this.setTab("branches", cx)).child(`${wt.length} worktrees`)))
        .when(r.stash > 0, e => e.child(div().text_color(t.ansi3).child(`stash ${r.stash}`))))
      .when(Boolean(r.head), e => e.child(h_flex().id("head-commit").gap(6).min_w(0).text_color(t.dim)
        .on_mouse_down("right", copyOn(() => commitMenu({ hash: r.oid ?? "", short: r.head.h, subject: r.head.subject })))
        .child(div().flex_none().text_color(t.ansi4).child(r.head.h))
        .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(r.head.subject))
        .child(div().flex_none().child(r.head.when))));
  }

  // One changed-file row, VS Code style: name, folder, counts, actions, status letter.
  fileRow(group, f, t, depth = null) {
    const tree = depth !== null, sel = this.sel?.group === group && this.sel?.path === f.path;
    const color = f.s === "A" || f.s === "U" ? t.ansi2 : f.s === "D" || f.s === "!" ? t.ansi1 : t.ansi3;
    const id = `${group}:${f.path}`, confirming = this.confirm === `discard:${f.path}`;
    return h_flex().id(`f-${id}`).h(24).flex_none().pl(tree ? 20 + depth * 14 : 18).pr(8).gap(6).items_center().rounded(5).cursor_pointer()
      .when(sel, e => e.bg(alpha(t.accent, "33"))).hover(s => s.bg(sel ? alpha(t.accent, "44") : t.hover))
      .on_click((_e, cx) => this.pick(group, f.path, cx))
      .on_mouse_down("right", copyOn(() => this.fileMenu(f.path)))
      .when(tree, e => e.child(svg("mods/files/file.svg").size(13).flex_none().text_color(t.dim)))
      .child(div().flex_none().when(!tree, e => e.max_w(260)).when(tree, e => e.flex_1().min_w(0)).truncate().text_color(f.s === "D" ? t.muted : t.foreground).child(base(f.path)))
      .when(!tree, e => e.child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim).child(dir(f.path))))
      .when((f.add ?? 0) > 0, e => e.child(div().flex_none().text_size(10).text_color(t.ansi2).child(`+${f.add}`)))
      .when((f.del ?? 0) > 0, e => e.child(div().flex_none().text_size(10).text_color(t.ansi1).child(`−${f.del}`)))
      .when(group === "unstaged", e => e
        .child(this.button(`d-${id}`, confirming ? "discard?" : "↶", t, cx => this.discard(f, cx), { tip: f.untracked ? "Delete untracked file" : "Discard changes", color: confirming ? t.ansi1 : undefined }))
        .child(this.button(`s-${id}`, "+", t, cx => this.stage(f, cx), { tip: "Stage" })))
      .when(group === "conflict", e => e.child(this.button(`s-${id}`, "+", t, cx => this.stage(f, cx), { tip: "Mark resolved (stage)" })))
      .when(group === "staged", e => e.child(this.button(`u-${id}`, "−", t, cx => this.unstage(f, cx), { tip: "Unstage" })))
      .child(div().w(12).flex_none().text_center().text_size(11).font_semibold().text_color(color).child(f.s));
  }

  section(id, title, files, t, actions) {
    if (!files.length) return [];
    const closed = this.collapsed[id];
    const head = h_flex().id(`sec-${id}`).h(24).flex_none().px(4).gap(4).items_center().cursor_pointer().text_size(11).font_semibold().text_color(t.muted)
      .on_click((_e, cx) => { this.collapsed[id] = !closed; cx.notify(); })
      .child(div().w(12).child(closed ? "›" : "⌄")).child(div().child(title.toUpperCase()))
      .child(div().px(5).rounded(8).bg(t.raised).text_size(10).child(String(files.length))).child(div().flex_1()).children(actions ?? []);
    return [head, ...(closed ? [] : this.prefs.tree ? this.treeRows(id, files, t) : files.map(f => this.fileRow(id, f, t)))];
  }

  // Files as folders, VS Code style: single-child folder chains are compacted (src/app/ui).
  treeRows(group, files, t) {
    const root = { dirs: new Map(), files: [] };
    for (const f of files) {
      let node = root;
      for (const d of f.path.split("/").slice(0, -1)) {
        if (!node.dirs.has(d)) node.dirs.set(d, { dirs: new Map(), files: [] });
        node = node.dirs.get(d);
      }
      node.files.push(f);
    }
    const count = (n) => n.files.length + [...n.dirs.values()].reduce((m, c) => m + count(c), 0);
    const out = [];
    const walk = (node, prefix, depth) => {
      for (const [name, child] of [...node.dirs].sort((a, b) => a[0].localeCompare(b[0]))) {
        let label = name, path = prefix ? `${prefix}/${name}` : name, c = child;
        while (!c.files.length && c.dirs.size === 1) { const [n2, c2] = [...c.dirs][0]; label += `/${n2}`; path += `/${n2}`; c = c2; }
        const id = `${group}:dir:${path}`, closed = Boolean(this.collapsed[id]);
        out.push(this.dirRow(group, id, label, path, count(c), depth, closed, t));
        if (!closed) walk(c, path, depth + 1);
      }
      for (const f of [...node.files].sort((a, b) => base(a.path).localeCompare(base(b.path)))) out.push(this.fileRow(group, f, t, depth));
    };
    walk(root, "", 0);
    return out;
  }

  dirRow(group, id, label, path, n, depth, closed, t) {
    return h_flex().id(`dir-${id}`).h(24).flex_none().pl(6 + depth * 14).pr(8).gap(5).items_center().rounded(5).cursor_pointer().hover(s => s.bg(t.hover))
      .on_click((_e, cx) => { this.collapsed[id] = !closed; cx.notify(); })
      .child(svg(`mods/files/chevron-${closed ? "right" : "down"}.svg`).size(12).flex_none().text_color(t.dim))
      .child(svg("mods/files/folder.svg").size(14).flex_none().text_color(t.muted))
      .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(label))
      .child(div().flex_none().text_size(10).text_color(t.dim).child(String(n)))
      .when(group === "unstaged" || group === "conflict", e => e.child(this.button(`ds-${id}`, "+", t, cx => this.act([["add", "--", path]], cx), { tip: "Stage folder" })))
      .when(group === "staged", e => e.child(this.button(`du-${id}`, "−", t, cx => this.act([this.repo?.oid ? ["restore", "--staged", "--", path] : ["rm", "-r", "--cached", "-q", "--", path]], cx), { tip: "Unstage folder" })))
      .child(div().w(12).flex_none());
  }

  // One-shot ALLOW_PRIVATE_TERMS=1 for the next commit / push; highlighted after the hooks blocked one.
  termsToggle(t) {
    const on = Boolean(this.allowTerms), blocked = /allasso guard/.test(this.note ?? "");
    return h_flex().id("allow-terms").flex_none().mx(10).mb(4).gap(6).items_center().text_size(11).cursor_pointer()
      .text_color(on || blocked ? t.ansi3 : t.dim)
      .tooltip("Run the next commit / push with ALLOW_PRIVATE_TERMS=1 (the private-terms hooks let it through); clears after use")
      .on_click((_e, cx) => { this.allowTerms = !on; this.confirm = null; cx.notify(); })
      .child(div().flex_none().size(12).rounded(3).border_1().border_color(on || blocked ? t.ansi3 : t.border)
        .flex().items_center().justify_center().text_size(10).child(on ? "✓" : ""))
      .child(div().child("Allow private terms"));
  }

  changesView(t) {
    const enabled = Boolean(this.message.trim()) && !this.busy && (this.staged.length + this.unstaged.length) > 0;
    const sel = this.sel, f = sel ? this.file(sel.group, sel.path) : null;
    const rows = [
      ...this.section("conflict", "Merge changes", this.conflicts, t),
      ...this.section("staged", "Staged changes", this.staged, t, [this.button("unstage-all", "−", t, cx => this.unstageAll(cx), { tip: "Unstage all" })]),
      ...this.section("unstaged", "Changes", this.unstaged, t, [this.button("stage-all", "+", t, cx => this.stageAll(cx), { tip: "Stage all" })]),
    ];
    const listMax = Math.max(120, Math.floor(this.h * 0.38));
    return v_flex().flex_1().min_h(0)
      .child(h_flex().flex_none().items_start().px(10).pt(8).pb(6).gap(6)
        .child(div().id("commit-message").flex_1().min_w(0).min_h(30).px(10).py(5).flex().items_center().rounded(6).border_1().border_color(this.typing ? t.accent : t.border).cursor_text()
          .on_mouse_down("left", (_e, cx) => cx.stop_propagation()).child(Textarea.new(this.input).w_full().text_size(12).text_color(t.foreground)))
        .child(div().id("commit").h(30).px(12).flex().items_center().rounded(6).text_size(12).font_semibold()
          .bg(enabled ? t.solid : t.raised).text_color(enabled ? t.solid_foreground : t.dim).when(enabled, e => e.cursor_pointer())
          .tooltip(this.staged.length ? "Commit staged changes" : "Stage all and commit")
          .on_click((_e, cx) => this.commit(cx)).child(this.busy ? "…" : "✓ Commit")))
      .child(this.termsToggle(t))
      .child(v_flex().id("git-files").flex_none().h(Math.min(listMax, Math.max(1, rows.length) * 24 + 6)).overflow_y_scrollbar().px(6).pb(4).children(rows)
        .when(!rows.length, e => e.child(div().px(8).py(6).text_size(12).text_color(t.dim).child(this.loading && !this.repo ? "Loading…" : "No changes"))))
      .when(this.limited, e => e.child(div().px(12).text_size(11).text_color(t.dim).child(`Showing the first ${MAX_FILES} changed files`)))
      .when(Boolean(f), e => e.child(this.diffTitle(f.path, sel.group === "staged" ? "Index" : sel.group === "conflict" ? "Conflict" : f.untracked ? "Untracked" : "Working tree", f, t)))
      .child(this.diffArea("git-diff", this.diff, !f ? "Select a file to view its changes" : this.diffNote, t));
  }

  diffTitle(path, where, f, t) {
    return h_flex().flex_none().h(30).px(12).gap(8).items_center().border_t(1).border_b(1).border_color(t.border).bg(t.raised).text_size(12)
      .child(div().flex_none().font_semibold().child(base(path)))
      .child(div().flex_1().min_w(0).truncate().text_color(t.dim).child(dir(path)))
      .child(this.badge(where, t.muted, t))
      .when(f?.s !== "D" && Boolean(this.repo), e => e.child(this.button("to-files", "Files", t, () => this.openInFiles(path), { tip: "Show this file as it is now in Files" })))
      .when((f?.add ?? 0) > 0, e => e.child(div().text_size(11).text_color(t.ansi2).child(`+${f.add}`)))
      .when((f?.del ?? 0) > 0, e => e.child(div().text_size(11).text_color(t.ansi1).child(`−${f.del}`)));
  }

  historyView(t) {
    const log = this.log ?? [];
    const c = this.commitInfo, listMax = Math.max(140, Math.floor(this.h * 0.4));
    return v_flex().flex_1().min_h(0)
      .child(h_flex().flex_none().h(30).px(10).gap(4).items_center().border_b(1).border_color(t.border).text_size(11).text_color(t.muted)
        .child(div().font_semibold().child("GRAPH"))
        .child(div().flex_1())
        .child(this.button("log-head", "Current branch", t, cx => { if (this.prefs.allRefs) this.toggleAllRefs(cx); }, { active: !this.prefs.allRefs }))
        .child(this.button("log-all", "All branches", t, cx => { if (!this.prefs.allRefs) this.toggleAllRefs(cx); }, { active: Boolean(this.prefs.allRefs) })))
      // A virtual list: up to 300 rows each draw graph paths, and building them all in one render overran the
      // script's time budget ("This view could not be re-rendered: interrupted"); only the rows on screen are built.
      .child(div().id("git-log-box").flex_none().h(Math.min(listMax, Math.max(1, log.length) * ROW_H + 12)).relative().py(6).px(4)
        .when(log.length > 0, e => e
          .child(list("git-log", log.length, (i) => log[i].hash, (i) => {
        const cm = log[i], sel = this.commitSel === cm.hash;
        return h_flex().id(`c-${cm.hash}`).h(ROW_H).flex_none().pr(6).gap(6).items_center().rounded(5).cursor_pointer().text_size(12)
          .when(sel, e => e.bg(alpha(t.accent, "33"))).hover(s => s.bg(sel ? alpha(t.accent, "44") : t.hover))
          .child(this.graphCell(cm, t))
          .when(cm.unpushed, e => e.child(div().flex_none().text_color(t.ansi2).child("↑")))
          .child(div().flex_1().min_w(0).truncate().when(cm.head, e => e.font_semibold()).child(cm.subject))
          .children(cm.refs.slice(0, 3).map(r => this.badge(r.replace(/^HEAD -> /, ""), r.startsWith("tag: ") ? t.ansi3 : r.includes("/") ? t.ansi5 : t.ansi4, t)))
          .child(div().flex_none().max_w(110).truncate().text_size(11).text_color(t.dim).child(cm.author))
          .child(div().flex_none().text_size(11).text_color(t.dim).child(cm.when));
      }).size_full()
            // A virtual list's rows are rebuilt every frame and may not register handlers: the list takes the clicks.
            .on_item_click((hash, cx) => this.pickCommit(hash, cx))
            .on_item_secondary_click((hash, ev, cx) => {
              const cm = log.find((c) => c.hash === hash);
              if (cm) copyOn(() => commitMenu({ hash: cm.hash, short: cm.h, subject: cm.subject }))(ev, cx);
            }))
          .child(Scrollbar.vertical("git-log").absolute().inset_0()))
        .when(!log.length, e => e.child(div().p(8).text_size(12).text_color(t.dim).child(this.log ? "No commits yet" : "Loading history…"))))
      .when(Boolean(c), e => e.child(this.commitDetail(c, log, t)))
      .child(this.diffArea("git-commit-diff", this.commitDiff, !this.commitSel ? "Select a commit to view its changes" : this.commitNote ?? (c ? null : "Loading…"), t));
  }

  // The opened commit: hash, author, date and the message (subject first); the hash and the message are
  // selectable text (TextView), the rest are labels. The hash cell has a fixed width: it sits in a row.
  commitDetail(c, log, t) {
    const short = log.find(cm => cm.hash === c.full)?.h;
    const text = (id, value) => TextView.html(`gp-${id}-${c.full}`, `<span>${htmlText(value)}</span>`).selectable(true);
    return v_flex().id("commit-detail").flex_none().px(12).py(8).gap(3).border_t(1).border_b(1).border_color(t.border).bg(t.raised).text_size(12)
      .on_mouse_down("right", copyOn(() => commitMenu({ hash: c.full, short, subject: subjectOf(c.body) })))
      .child(h_flex().gap(8).child(div().w(84).flex_none().text_color(t.ansi4).child(text("hash", c.full.slice(0, 10)))).child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(`${c.author} <${c.email}>`)).child(div().flex_none().text_color(t.dim).child(c.date)))
      .child(div().max_h(110).overflow_hidden().child(text("message", c.body)));
  }

  statusChip(b, t) {
    const vs = b.vs;
    if (b.name === this.base?.ref) return this.badge("base", t.accent, t).tooltip("Branches are compared with this one");
    if (!vs) return div().flex_none().text_size(11).text_color(t.dim).child("…");
    const ok = { merged: ["✓ merged", "Its tip is already in the base history"], rebased: ["✓ rebased in", "Every commit has a same-patch twin in the base (rebase / cherry-pick)"],
      squashed: ["✓ squashed in", "The branch's whole change matches one base commit (squash merge)"], content: ["✓ content in base", "A trial merge changes nothing in the base"] }[vs.state];
    if (ok) return this.badge(ok[0], t.ansi2, t).tooltip(ok[1]);
    if (vs.state === "unknown") return div().flex_none().text_size(11).text_color(t.dim).child("?");
    return h_flex().flex_none().gap(4)
      .child(this.badge(`${vs.unique || vs.ahead} not in base`, t.ansi3, t).tooltip(`${vs.ahead} commits ahead, ${vs.unique} with changes the base doesn't have`))
      .when(vs.conflicts, e => e.child(this.badge("conflicts", t.ansi1, t).tooltip("Merging into the base would conflict")));
  }

  aheadBehind(b, t) {
    const vs = b.vs;
    return h_flex().flex_none().gap(4).text_size(11)
      .when((vs?.ahead ?? 0) > 0, e => e.child(div().text_color(t.ansi2).tooltip("commits the base doesn't have").child(`↑${vs.ahead}`)))
      .when((vs?.behind ?? 0) > 0, e => e.child(div().text_color(t.dim).tooltip("base commits the branch doesn't have").child(`↓${vs.behind}`)));
  }

  actionsRow(b, t, depth) {
    const r = this.repo, wt = b.wtPath ? r.worktrees.find(w => norm(w.path) === norm(b.wtPath)) : null;
    const here = wt && norm(wt.path) === norm(r.root), space = wt ? spaces().find(s => norm(s.folder) === norm(wt.path)) : null;
    const merged = ["merged", "rebased", "squashed", "content"].includes(b.vs?.state);
    const cur = r.branch ?? "HEAD", armed = (op) => this.confirm === `${op}:${b.name}`;
    const op = (id, label, tip, opts = {}) => this.button(`op-${id}-${b.name}`, armed(id) ? `${label}? click again` : label, t, cx => this.branchOps(b, cx, id), { tip, color: armed(id) || opts.danger ? t.ansi1 : opts.color });
    return h_flex().flex_none().pl(28 + depth).pr(8).pb(4).gap(4).flex_wrap()
      .when(!b.current && !b.wtPath, e => e.child(op("switch", "Switch to", `git switch ${b.name}`)))
      .when(Boolean(space) && !here, e => e.child(this.button(`op-space-${b.name}`, "Open space", t, () => run("space.select", { id: space.id }))))
      .when(!b.current, e => e.child(op("merge", `Merge into ${cur}`, `git merge --no-edit ${b.name}`)))
      .when(Boolean(wt) && !here && merged, e => e.child(op("cleanup", "Clean up worktree + branch", `git worktree remove ${wt.path} && git branch -${b.vs?.state === "merged" ? "d" : "D"} ${b.name}`, { color: t.ansi2 })))
      .when(Boolean(wt) && !here, e => e.child(op("remove-wt", "Remove worktree", `git worktree remove ${wt.path} (refuses if it has changes)`, { danger: !merged })))
      .when(!b.current && !b.wtPath, e => e.child(op("delete", merged ? "Delete branch" : "Force delete branch", `git branch -${b.vs?.state === "merged" ? "d" : "D"} ${b.name}`, { danger: !merged })));
  }

  branchRow(b, t, { label, path, dirty, current, prefix } = {}) {
    const open = this.brSel === b.name;
    return v_flex().flex_none().rounded(5).when(open, e => e.bg(t.raised))
      .child(h_flex().id(`${prefix}-${b.name}-${path ?? ""}`).min_h(26).px(8).py(3).gap(6).items_center().rounded(5).text_size(12).cursor_pointer()
        .when(Boolean(current), e => e.bg(alpha(t.accent, "22"))).hover(s => s.bg(t.hover))
        .on_click((_e, cx) => { this.brSel = open ? null : b.name; this.confirm = null; cx.notify(); })
        .on_mouse_down("right", copyOn(() => branchMenu(b.name)))
        .child(div().flex_none().text_color(current ? t.accent : t.dim).child(current ? "●" : path ? "○" : "⎇"))
        .child(div().flex_none().max_w(260).truncate().font_semibold().child(label ?? b.name))
        .child(this.statusChip(b, t))
        .child(this.aheadBehind(b, t))
        .when(Boolean(b.wtPath) && !path && norm(b.wtPath) !== norm(this.repo?.root ?? ""), e => e.child(this.badge("worktree", t.ansi5, t)))
        .when(b.gone, e => e.child(this.badge("upstream gone", t.ansi1, t)))
        .when(dirty > 0, e => e.child(this.badge(`${dirty} uncommitted`, t.ansi3, t)))
        .child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim).child(path ?? b.subject ?? ""))
        .when(!path, e => e.child(div().flex_none().text_size(11).text_color(t.dim).child(b.when ?? "")))
        .when(b.name !== this.base?.ref, e => e.child(this.button(`cmp-${prefix}-${b.name}-${path ?? ""}`, "Compare", t, cx => this.compare(b.name, cx), { tip: `Compare with ${this.base?.label ?? "base"}`, color: t.accent }))))
      .when(open, e => e.child(this.actionsRow(b, t, 0)));
  }

  branchesView(t) {
    if (this.cmp) return this.compareView(t);
    const r = this.repo, wts = r?.worktrees ?? [], brs = this.branches ?? [], base = this.base;
    const byName = new Map(brs.map(b => [b.name, b]));
    const pick = this.prefs.base ?? "default";
    const head = (title, n, extra) => h_flex().h(26).flex_none().px(4).gap(6).items_center().text_size(11).font_semibold().text_color(t.muted)
      .child(div().child(title.toUpperCase())).child(div().px(5).rounded(8).bg(t.raised).text_size(10).child(String(n))).child(div().flex_1()).children(extra ?? []);
    const wtRows = wts.map(w => {
      const current = norm(w.path) === norm(r.root), dirty = this.worktreeDirty[w.path];
      const b = w.branch && byName.get(w.branch);
      if (b) return this.branchRow(b, t, { label: w.branch, path: w.path, dirty, current, prefix: "wt" });
      return h_flex().min_h(26).flex_none().px(8).py(3).gap(6).items_center().text_size(12)
        .child(div().flex_none().text_color(t.dim).child("○"))
        .child(div().flex_none().font_semibold().child(w.bare ? "(bare)" : `detached @ ${w.head?.slice(0, 7)}`))
        .child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim).child(w.path))
        .when(Boolean(w.prunable), e => e.child(this.badge("prunable", t.ansi1, t)));
    });
    // The main checkout is a worktree too: list the section only when there are others, otherwise
    // the current branch goes first in Local branches (with its uncommitted count).
    const showWts = wts.length > 1;
    const localBrs = showWts ? brs.filter(b => !b.wtPath || !wts.some(w => w.branch === b.name))
      : [...brs.filter(b => b.current), ...brs.filter(b => !b.current)];
    const prunable = wts.some(w => w.prunable);
    const unmergedWts = wts.filter(w => w.branch && byName.get(w.branch)?.vs && !["merged", "rebased", "squashed", "content"].includes(byName.get(w.branch).vs.state)).length;
    return v_flex().flex_1().min_h(0)
      .child(h_flex().flex_none().h(32).px(10).gap(4).items_center().border_b(1).border_color(t.border).text_size(11).text_color(t.muted)
        .child(div().child("Compare with"))
        .when(Boolean(base?.local), e => e.child(this.button("base-default", base.local, t, cx => this.setBase("default", cx), { active: pick === "default" })))
        .when(Boolean(base?.remote), e => e.child(this.button("base-remote", base.remote, t, cx => this.setBase("remote", cx), { active: pick === "remote" })))
        .child(this.button("base-current", `current (${r.branch ?? "HEAD"})`, t, cx => this.setBase("current", cx), { active: pick === "current" }))
        .child(div().flex_1())
        .child(div().text_color(t.dim).tooltip("merged: tip in base · rebased: same patches · squashed: whole diff matches a base commit · content: trial merge changes nothing").child("how status is found ⓘ")))
      .child(v_flex().id("git-branches").flex_1().min_h(0).overflow_y_scrollbar().p(6)
        .when(showWts, e => e.child(head("Worktrees", wts.length, [
          ...(unmergedWts ? [div().text_size(10).text_color(t.ansi3).child(`${unmergedWts} not merged`)] : []),
          ...(prunable ? [this.button("prune", "Prune stale", t, cx => this.act([["worktree", "prune"]], cx), { tip: "git worktree prune" })] : []),
        ])).children(wtRows)
        .child(div().h(8).flex_none()))
        .child(head("Local branches", brs.length)).children(localBrs.map(b => this.branchRow(b, t, { current: b.current, prefix: "br", dirty: b.current && !showWts ? this.worktreeDirty[wts[0]?.path] : undefined })))
        .when(!this.branches, e => e.child(div().p(8).text_size(12).text_color(t.dim).child("Loading branches…"))));
  }

  compareView(t) {
    const c = this.cmp, base = this.base?.label ?? "base", b = this.branches?.find(x => x.name === c.name);
    const commits = c.commits ?? [], same = commits.filter(x => x.same).length, listMax = Math.max(100, Math.floor(this.h * 0.28));
    const modes = [["merge", "If merged", `What merging into ${base} would actually change`], ["fork", "Branch changes", `Everything the branch changed since it forked (${base}...${c.name})`], ["full", "Full diff", `Tree difference ${base}..${c.name}, including ${base}'s newer work`]];
    const fs = c.fileSel, base_ = (p) => p.replace(/\/+$/, "").split("/").pop();
    const rows = [];
    for (const x of commits) {
      const open = c.open?.has(x.hash);
      rows.push(h_flex().id(`cmp-c-${x.hash}`).h(22).flex_none().gap(6).items_center().rounded(4).text_size(12).cursor_pointer().hover(s => s.bg(t.hover))
        .on_click((_e, cx) => this.toggleCommitFiles(x, cx))
        .child(svg(`mods/files/chevron-${open ? "down" : "right"}.svg`).size(11).flex_none().text_color(t.dim))
        .child(div().w(14).flex_none().text_center().text_color(x.same ? t.ansi2 : t.ansi3).tooltip(x.same ? `same change already in ${base}` : `not in ${base}`).child(x.same ? "✓" : "+"))
        .child(div().flex_none().text_color(t.ansi4).child(x.h))
        .child(div().flex_1().min_w(0).truncate().text_color(x.same ? t.dim : t.foreground).child(x.subject))
        .child(div().flex_none().text_size(11).text_color(t.dim).child(x.when)));
      if (!open) continue;
      const files = c.files?.[x.hash];
      if (!files) { rows.push(div().h(22).flex_none().pl(44).text_size(11).text_color(t.dim).child("Loading files…")); continue; }
      for (const f of files) {
        const sel = fs?.hash === x.hash && fs?.path === f.path;
        const color = f.s === "A" ? t.ansi2 : f.s === "D" ? t.ansi1 : t.ansi3;
        rows.push(h_flex().id(`cmp-f-${x.hash}-${f.path}`).h(22).flex_none().pl(40).pr(4).gap(6).items_center().rounded(4).text_size(12).cursor_pointer()
          .when(sel, e => e.bg(alpha(t.accent, "33"))).hover(s => s.bg(sel ? alpha(t.accent, "44") : t.hover))
          .on_click((_e, cx) => this.pickCommitFile(x, f.path, cx))
          .on_mouse_down("right", copyOn(() => this.fileMenu(f.path)))
          .child(svg("mods/files/file.svg").size(12).flex_none().text_color(t.dim))
          .child(div().flex_none().max_w(260).truncate().child(base_(f.path)))
          .child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim).child(dir(f.path)))
          .when((f.add ?? 0) > 0, e => e.child(div().flex_none().text_size(10).text_color(t.ansi2).child(`+${f.add}`)))
          .when((f.del ?? 0) > 0, e => e.child(div().flex_none().text_size(10).text_color(t.ansi1).child(`−${f.del}`)))
          .child(div().w(12).flex_none().text_center().text_size(11).font_semibold().text_color(color).child(f.s)));
      }
      if (!files.length) rows.push(div().h(22).flex_none().pl(44).text_size(11).text_color(t.dim).child("No file changes"));
    }
    const note = c.note ?? (c.commits === null ? "Loading…" : c.empty ? (c.mode === "merge" ? `Merging changes nothing: the branch's work is already in ${base}.` : "No differences") : null);
    return v_flex().flex_1().min_h(0)
      .child(h_flex().flex_none().h(34).px(8).gap(6).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(this.button("cmp-back", "← Branches", t, cx => { this.cmp = null; cx.notify(); }))
        .child(div().flex_none().font_semibold().max_w(280).truncate().child(c.name))
        .child(div().flex_none().text_color(t.dim).child("vs"))
        .child(div().flex_none().font_semibold().child(base))
        .when(Boolean(b), e => e.child(this.statusChip(b, t)))
        .child(div().flex_1())
        .children(modes.map(([id, label, tip]) => this.button(`cmp-mode-${id}`, label, t, cx => this.setCompareMode(id, cx), { active: c.mode === id, tip }))))
      .child(h_flex().flex_none().px(12).py(6).gap(10).text_size(12).text_color(t.muted)
        .child(div().child(`↑ ${commits.length} commit${commits.length === 1 ? "" : "s"} on the branch`))
        .when(same > 0, e => e.child(div().text_color(t.ansi2).child(`${same} already in ${base} (same patch)`)))
        .when(commits.length - same > 0, e => e.child(div().text_color(t.ansi3).child(`${commits.length - same} not in ${base}`)))
        .child(div().text_color(t.dim).child(`↓ ${c.behind ?? 0} new on ${base}`))
        .when(Boolean(c.merge?.conflicts?.length), e => e.child(div().text_color(t.ansi1).truncate().tooltip(c.merge.conflicts.join("\n")).child(`conflicts: ${c.merge.conflicts.slice(0, 3).join(", ")}${c.merge.conflicts.length > 3 ? "…" : ""}`))))
      .when(commits.length > 0, e => e.child(v_flex().id("cmp-commits").flex_none().h(Math.min(listMax, rows.length * 22 + 8)).overflow_y_scrollbar().px(8).pb(6).border_b(1).border_color(t.border)
        .children(rows)))
      .when(Boolean(fs), e => e.child(h_flex().flex_none().h(30).px(12).gap(8).items_center().border_b(1).border_color(t.border).bg(t.raised).text_size(12)
        .child(div().flex_none().text_color(t.ansi4).child(fs.h))
        .child(div().flex_none().font_semibold().child(base_(fs.path)))
        .child(div().flex_1().min_w(0).truncate().text_color(t.dim).child(dir(fs.path)))
        .child(this.button("cmp-file-close", "× back to comparison", t, cx => { c.fileSel = null; this.diffGen++; cx.notify(); }))))
      .child(fs ? this.diffArea("cmp-file-diff", c.fileDiff ?? [], c.fileNote, t) : this.diffArea("cmp-diff", c.diff ?? [], note, t));
  }

  // ---- diff ---------------------------------------------------------------------

  diffArea(id, rows, note, t) {
    const size = this.prefs.size, font = settings().font_family ?? "JetBrains Mono";
    const items = this.split() ? pairRows(rows) : rows;
    const digits = Math.max(3, String(rows.reduce((m, r) => Math.max(m, r.o ?? 0, r.n ?? 0), 0)).length);
    const g = Math.ceil(digits * size * 0.62) + 10, gen = this.diffGen;
    return div().flex_1().min_h(0).relative().font_family(font).text_size(size).line_height(1.45)
      .when(items.length > 0, e => e
        .child(list(id, items.length, (i) => `${gen}:${i}`, (i) => this.diffItem(items[i], g, t)).size_full()
          // A file's header row in a multi-file diff (an opened commit, a comparison): its copy menu.
          .on_item_secondary_click((k, ev, cx) => {
            const row = items[Number(k.split(":")[1])];
            if (row?.t === "file") copyOn(() => this.fileMenu(row.path))(ev, cx);
          }))
        .child(Scrollbar.vertical(id).absolute().inset_0()))
      .when(Boolean(note), e => e.child(div().absolute().top(8).px(12).text_size(12).text_color(t.dim).child(note)));
  }

  code(text) {
    const el = div().flex_1().min_w(0).pl(4).pr(10);
    return (this.prefs.wrap ? el.whitespace_normal() : el.truncate()).child(text);
  }

  num(value, g, t, bg) {
    return div().w(g).flex_none().pr(6).text_right().text_color(t.dim).when(Boolean(bg), e => e.bg(bg)).child(value === undefined ? "" : String(value));
  }

  diffItem(row, g, t) {
    const addBg = alpha(t.ansi2, "1f"), delBg = alpha(t.ansi1, "1f"), addG = alpha(t.ansi2, "38"), delG = alpha(t.ansi1, "38");
    switch (row.t) {
      case "file":
        return h_flex().w_full().mt(6).px(10).py(5).gap(8).items_center().bg(t.raised).border_t(1).border_b(1).border_color(t.border)
          .child(div().flex_none().font_semibold().child(base(row.path)))
          .child(div().flex_1().min_w(0).truncate().text_color(t.dim).child(dir(row.path)))
          .children(row.tags.map(tag => this.badge(tag, tag === "deleted" ? t.ansi1 : tag === "new" ? t.ansi2 : t.muted, t)))
          .when(row.add > 0, e => e.child(div().text_color(t.ansi2).child(`+${row.add}`)))
          .when(row.del > 0, e => e.child(div().text_color(t.ansi1).child(`−${row.del}`)));
      case "hunk":
        return div().w_full().px(10).py(1).bg(alpha(t.accent, "14")).text_color(t.dim).whitespace_normal().child(row.text);
      case "meta":
        return div().w_full().px(10).py(1).text_color(t.dim).italic().child(row.text);
      case "pair": {
        const half = (r, side) => {
          if (!r) return div().flex_1().min_w(0).bg(alpha(t.raised, "aa"));
          const kind = r.t === "ctx" ? null : r.t, bg = kind === "add" ? addBg : kind === "del" ? delBg : null;
          return h_flex().flex_1().min_w(0).items_start().when(Boolean(bg), e => e.bg(bg))
            .child(this.num(side === "l" ? r.o : r.n, g, t, kind === "add" ? addG : kind === "del" ? delG : null))
            .child(this.code(r.text));
        };
        return h_flex().w_full().items_stretch()
          .child(half(row.l, "l").border_r(1).border_color(t.border))
          .child(half(row.r, "r"));
      }
      default: {
        const kind = row.t, bg = kind === "add" ? addBg : kind === "del" ? delBg : kind === "conflict" ? alpha(t.ansi5, "30") : null;
        const gb = kind === "add" ? addG : kind === "del" ? delG : null;
        return h_flex().w_full().items_start().when(Boolean(bg), e => e.bg(bg))
          .child(this.num(row.o, g, t, gb))
          .child(this.num(row.n, g, t, gb))
          .child(div().w(14).flex_none().text_center().text_color(kind === "add" ? t.ansi2 : kind === "del" ? t.ansi1 : t.dim).child(kind === "add" ? "+" : kind === "del" ? "−" : " "))
          .child(this.code(row.text));
      }
    }
  }

  render(cx) {
    if (props()?.menu) return copyMenuView(this.menu ?? props().menu, theme(), this.menuFocus);
    if (key() !== this.ctxKey) this.refresh(cx, true);
    const t = theme(), p = this.prefs, changes = this.conflicts.length + this.staged.length + this.unstaged.length;
    const unpushed = this.repo?.ahead ?? 0;
    return v_flex().id("git-panel").size_full().min_h(0).rounded(10).overflow_hidden().bg(t.surface).border_1().border_color(t.border).text_color(t.foreground).text_size(13)
      .on_size_change((size, cx) => { if (size.width !== this.w || size.height !== this.h) { this.w = size.width; this.h = size.height; this.diffGen++; cx.notify(); } })
      .child(h_flex().h(38).flex_none().pl(6).pr(8).gap(2).items_center().border_b(1).border_color(t.border)
        .child(this.tabButton("changes", "Changes", changes, t))
        .child(this.tabButton("history", "History", unpushed, t))
        .child(this.tabButton("branches", "Branches", 0, t))
        .child(div().flex_1())
        .child(this.button("font-down", "A−", t, cx => this.setPrefs({ size: Math.max(9, p.size - 1) }, cx), { tip: `Diff font ${p.size}px` }))
        .child(this.button("font-up", "A+", t, cx => this.setPrefs({ size: Math.min(20, p.size + 1) }, cx), { tip: `Diff font ${p.size}px` }))
        .when(p.tab === "changes", e => e.child(this.button("view-tree", p.tree ? "Tree" : "List", t, cx => this.setPrefs({ tree: !p.tree }, cx), { active: p.tree, tip: p.tree ? "Files as tree (click for list)" : "Files as list (click for tree)" })))
        .child(this.button("wrap", "↵", t, cx => this.setPrefs({ wrap: !p.wrap }, cx), { active: p.wrap, tip: p.wrap ? "Word wrap: on" : "Word wrap: off" }))
        .child(this.button("mode", p.mode === "auto" ? "◫ auto" : p.mode === "split" ? "◫" : "☰", t,
          cx => this.setPrefs({ mode: p.mode === "auto" ? "inline" : p.mode === "inline" ? "split" : "auto" }, cx),
          { active: p.mode !== "auto", tip: `Diff view: ${p.mode} (auto = side-by-side when wide)` }))
        .child(this.button("refresh", "⟳", t, cx => { this.log = null; this.branches = null; this.refresh(cx, true); }, { tip: "Refresh" })))
      .when(Boolean(this.error), e => e.child(div().p(12).text_size(12).text_color(t.dim).child(this.error)))
      .when(!this.error, e => e.child(this.infoBlock(t)))
      .when(Boolean(this.note), e => e.child(div().px(12).py(6).text_size(11).text_color(t.ansi1).whitespace_normal().child(this.note)))
      .when(Boolean(this.info) && !this.note, e => e.child(div().px(12).py(6).text_size(11).text_color(t.dim).whitespace_normal().child(this.info)))
      .when(!this.error && Boolean(this.repo), e => e.child(
        p.tab === "history" ? this.historyView(t) : p.tab === "branches" ? this.branchesView(t) : this.changesView(t)));
  }
}
