// Files + preview: the focused pane's folder as a tree with the selected file previewed below it.
// Markdown renders (relative links open here too) or shows as source; code is read-only with
// line numbers. Terminal Ctrl-clicks on file paths land here through link.js
// (shared "file-panel.open": {path, on, line, at}; {dir: true} for a folder; git-panel's "Files" button writes it too).
// The "Changes" button asks git-panel for this file's diff (shared "git-panel.open": {path, on, at}).
// A right-click on a tree row opens the copy menu (copy-menu.js): this mod again, as a popup with props {menu}.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, Editor, EditorState, TextView, ScrollHandle } from "gpui-base";
import { spaces, panes, focused, exec, readTextFile, watchFolder, theme, settings, window, shared, setShared, run, nextCommand, home, checking, props, openOverlay, closeOverlay } from "herdr";
import { MENU_WIDTH, MENU_PAD, MENU_TITLE_H, MENU_ROW_H, pathMenu, hasItems, menuHeight } from "./copy-menu.js";

const PER_FOLDER = 500, TOTAL = 5000, MAX_TEXT = 400000;
const LANG = { ts: "js", tsx: "js", mts: "js", cts: "js", jsx: "js", mjs: "js", cjs: "js", toml: "yaml", yml: "yaml", jsonc: "json", json5: "json", svelte: "html", vue: "html", htm: "html", markdown: "md", mdx: "md", bash: "sh", zsh: "sh" };
const MD = /\.(md|mdx|markdown)$/i;
const SPLITS = [0.35, 0.55, 0]; // tree share of the height while a file is shown; 0 = tree hidden

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

const short = (p) => { const h = home(); return !p ? "" : p === h ? "~" : p.startsWith(h + "/") ? "~" + p.slice(h.length) : p; };
const dirname = (p) => p.replace(/\/[^/]*$/, "") || "/";
function normalize(p) {
  const out = [];
  for (const part of p.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") out.pop(); else out.push(part);
  }
  return "/" + out.join("/");
}

async function directory(path, on) {
  const script = 'n=0; for p in "$1"/* "$1"/.[!.]* "$1"/..?*; do [ -e "$p" ] || [ -L "$p" ] || continue; name=${p##*/}; [ "$name" = .git ] && continue; if [ -d "$p" ]; then kind=d; else kind=f; fi; printf "%s\\0%s\\0" "$kind" "$name"; n=$((n+1)); [ "$n" -gt 500 ] && break; done; test -d "$1"';
  const r = await exec("sh", ["-c", script, "files", path], { on });
  if (r.code !== 0) throw new Error(r.stderr || "Cannot list this folder");
  const fields = r.stdout.split("\0"), out = [];
  for (let i = 0; i + 1 < fields.length; i += 2) out.push({ name: fields[i + 1], dir: fields[i] === "d" });
  return out;
}

// The copy menu as a popup at the pointer (`event.position`, window-relative like `at`).

// Opened in the click's own call (as the tab bar's menu), at the pointer.
function openCopyMenu(menu, _event, extra = {}) {
  if (!hasItems(menu)) return;
  openOverlay({ mod: "file-panel", kind: "popup", width: MENU_WIDTH, height: menuHeight(menu), at: "pointer", props: { menu, ...extra } });
}

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

function prefsLoad() { try { return JSON.parse(localStorage.getItem("file-panel.prefs") ?? "{}"); } catch { return {}; } }

// The open file and its back/forward lists belong to the space: {[spaceId]: {file, history, ahead, at}}.
// Per viewer (localStorage), paths only; the newest KEEP spaces stay (spaces() can be briefly empty
// or lack a disconnected machine's spaces, so nothing is pruned by "does it still exist").
const KEEP = 60;
const slim = (f) => f && { path: f.path, on: f.on ?? null, line: f.line, source: f.source };
function bySpaceLoad() { try { return JSON.parse(localStorage.getItem("file-panel.spaces") ?? "{}"); } catch { return {}; } }

export default class FilePanel extends View {
  init(p, cx) {
    if (props()?.menu) {
      this.menu = props().menu;
      this.menuFocus = cx.focus_handle(); this.menuFocus.focus();
      this.resolveMenuRoot?.(cx);
      return;
    }
    this.slot = p?.slot ?? "top";
    this.prefs = { split: 0, wrap: true, rendered: true, ...prefsLoad() };
    this.entries = new Map(); this.folders = new Set(); this.pending = new Set(); this.marks = {};
    this.file = null; this.history = []; this.ahead = [];
    this.bySpace = bySpaceLoad(); this.spaceId = undefined;
    this.handled = shared("file-panel.open")?.at ?? 0;
    this.h = 600;
    this.treeScroll = ScrollHandle.new();
    if (checking()) return;
    this.followSpace(cx);
    this.refresh(cx);
    cx.timer.every(1000, cx => this.tick(cx));
    cx.spawn(async cx => {
      for (;;) {
        const c = await nextCommand();
        if (c === "back") this.back(cx);
        else if (c === "show") { run("layout.show", { id: "panel" }); run("layout.select", { stack: "panel-body", child: "files" }); }
        cx.notify();
      }
    });
  }

  poke(cx) { if (!this.rendering) cx?.notify(); }

  setPrefs(p, cx) {
    Object.assign(this.prefs, p);
    try { localStorage.setItem("file-panel.prefs", JSON.stringify(this.prefs)); } catch {}
    cx?.notify();
  }

  // ---- tree -------------------------------------------------------------------------------

  key() { const l = location(); return JSON.stringify([this.up ?? l.cwd, l.on]); }

  // Another space became active: swap in its file and lists (an empty preview if it had none).
  followSpace(cx) {
    const id = spaces().find((s) => s.active)?.id ?? null;
    if (!id || id === this.spaceId) return;
    const saved = this.bySpace[id];
    this.spaceId = id;
    this.up = null; this.release();
    this.file = null;
    this.history = (saved?.history ?? []).map((f) => ({ ...f }));
    this.ahead = (saved?.ahead ?? []).map((f) => ({ ...f }));
    if (saved?.file) this.open(saved.file.path, cx, { on: saved.file.on, line: saved.file.line, source: saved.file.source, push: false });
    this.poke(cx);
  }

  remember() {
    if (!this.spaceId) return;
    this.bySpace[this.spaceId] = { file: slim(this.file), history: this.history.map(slim), ahead: this.ahead.map(slim), at: Date.now() };
    const keep = Object.entries(this.bySpace).sort((a, b) => (b[1].at ?? 0) - (a[1].at ?? 0)).slice(0, KEEP);
    this.bySpace = Object.fromEntries(keep);
    try { localStorage.setItem("file-panel.spaces", JSON.stringify(this.bySpace)); } catch {}
  }

  tick(cx) {
    if (window()?.active === false || this.loading) return;
    this.followSpace(cx);
    if (this.key() !== this.ctx?.key || this.dirty || Date.now() > (this.pollAt ?? 0)) this.refresh(cx);
  }

  refresh(cx) {
    const l = location(), next = this.key();
    if (next !== this.ctx?.key) {
      // Following another pane drops a manual "up".
      if (this.up && this.upFor !== l.cwd) { this.up = null; return this.refresh(cx); }
      this.ctx?.watch?.cancel();
      const root = this.up ?? l.cwd;
      this.ctx = { key: next, root, on: l.on };
      this.entries = new Map(); this.folders = new Set(); this.marks = {}; this.error = null;
      if (this.file && root && this.file.path.startsWith(root + "/")) this.reveal(this.file.path, cx, false);
    }
    const ctx = this.ctx;
    if (!ctx.root) return;
    this.loading = true; this.dirty = false; this.pollAt = Date.now() + 10000;
    cx.spawn(async cx => {
      try {
        if (!ctx.on && !ctx.watching) this.watch(ctx, cx);
        for (const path of ["", ...this.folders]) {
          if (this.ctx !== ctx) return;
          await this.loadFolder(path, ctx);
        }
        const st = await exec("git", ["--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.quotepath=false", "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all"], { cwd: ctx.root, on: ctx.on }).catch(() => null);
        const top = st?.code === 0 ? await exec("git", ["rev-parse", "--show-toplevel"], { cwd: ctx.root, on: ctx.on }).catch(() => null) : null;
        if (this.ctx !== ctx) return;
        const marks = {}, root = top?.stdout.trim();
        if (root) for (const e of st.stdout.split("\0").filter(Boolean).slice(0, TOTAL)) {
          const xy = e.slice(0, 2), m = xy === "??" || xy.includes("A") ? "A" : xy.includes("U") ? "U" : xy.includes("D") ? "D" : "M";
          const abs = `${root}/${e.slice(3)}`;
          marks[abs] = m;
          // Folders show that something inside changed.
          for (let d = dirname(abs); d.length > ctx.root.length && d.startsWith(ctx.root); d = dirname(d)) marks[d] ??= "•";
        }
        this.marks = marks; this.error = null;
      } catch (e) { if (this.ctx === ctx) this.error = String(e?.message ?? e); }
      finally { if (this.ctx === ctx) this.loading = false; cx.notify(); }
    });
  }

  watch(ctx, cx) {
    ctx.watching = true;
    cx.spawn(async cx => {
      try {
        while (this.ctx === ctx) {
          ctx.watch = watchFolder(ctx.root);
          const paths = await ctx.watch;
          if (this.ctx !== ctx) return;
          if (paths.some(p => !p.includes("/.git/"))) { this.dirty = true; }
          if (this.file && paths.includes(this.file.path)) this.load(this.file, cx, true);
        }
      } catch { ctx.watching = false; }
    });
  }

  async loadFolder(path, ctx) {
    const entries = await directory(path ? `${ctx.root}/${path}` : ctx.root, ctx.on);
    if (this.ctx !== ctx || (path && !this.folders.has(path))) return;
    entries.sort((a, b) => Number(b.dir) - Number(a.dir) || a.name.localeCompare(b.name));
    this.entries.set(path, entries.slice(0, PER_FOLDER));
    this.limited = entries.length > PER_FOLDER;
  }

  toggle(path, cx, open) {
    const ctx = this.ctx;
    if (this.folders.has(path) && open !== true) {
      for (const f of [...this.folders]) if (f === path || f.startsWith(path + "/")) { this.folders.delete(f); this.entries.delete(f); }
    } else if (!this.folders.has(path)) {
      this.folders.add(path);
      if (!this.entries.has(path) && !this.pending.has(path)) {
        this.pending.add(path);
        cx.spawn(async cx => {
          try { await this.loadFolder(path, ctx); }
          catch (e) { if (this.ctx === ctx) this.error = String(e?.message ?? e); }
          finally { this.pending.delete(path); this.poke(cx); }
        });
      }
    }
    this.poke(cx);
  }

  // Expand the tree down to a file under the root (and scroll there on the next render).
  reveal(abs, cx, notify = true) {
    const root = this.ctx?.root;
    if (!root || !abs.startsWith(root + "/")) return false;
    const parts = abs.slice(root.length + 1).split("/");
    for (let i = 1; i < parts.length; i++) this.toggle(parts.slice(0, i).join("/"), cx, true);
    this.scrollTo = abs;
    if (notify) this.poke(cx);
    return true;
  }

  // Show a file in the tree: expand its folders and scroll to it. A file outside the tree's folder
  // moves the tree to the file's repo root (or its folder); "follow pane" goes back.
  showInTree(abs, cx) {
    if (this.reveal(abs, cx)) return;
    const on = this.file?.on ?? this.ctx?.on ?? null;
    cx.spawn(async (cx) => {
      const top = await exec("git", ["rev-parse", "--show-toplevel"], { cwd: dirname(abs), on }).catch(() => null);
      this.up = top?.code === 0 ? top.stdout.trim() : dirname(abs);
      this.upFor = location().cwd;
      this.refresh(cx); // the new root reveals the open file
      this.scrollTo = abs;
      this.poke(cx);
    });
  }

  // A folder link from a terminal: expand it in the tree when it is under the tree's root, else make it the root.
  showFolder(abs, cx) {
    if (this.prefs.split === 2) this.setPrefs({ split: 0 }, cx); // the tree is hidden while a file is open
    const root = this.ctx?.root;
    if (root && abs === root) return;
    if (root && abs.startsWith(root + "/")) {
      const parts = abs.slice(root.length + 1).split("/");
      for (let i = 1; i <= parts.length; i++) this.toggle(parts.slice(0, i).join("/"), cx, true);
      this.scrollTo = abs;
      this.poke(cx);
      return;
    }
    this.up = abs; this.upFor = location().cwd;
    this.refresh(cx);
  }

  goUp(cx) {
    const root = this.ctx?.root;
    if (!root || root === "/") return;
    this.up = dirname(root); this.upFor = location().cwd;
    this.refresh(cx);
  }

  // ---- preview ----------------------------------------------------------------------------

  open(path, cx, { line, on, push = true, source } = {}) {
    if (push && this.file && this.file.path !== path) { this.history.push(this.file); this.ahead = []; }
    if (this.history.length > 50) this.history.shift();
    const f = { path, on: on ?? this.ctx?.on ?? null, line, text: null, error: null };
    this.release();
    this.file = f;
    if (source !== undefined) f.source = source;
    if (line && MD.test(path)) f.source = true; // a line number means the source view
    this.remember();
    this.load(f, cx);
    this.poke(cx);
  }

  release() { this.code?.release(); this.code = null; }

  load(f, cx, reload = false) {
    cx.spawn(async cx => {
      try {
        const text = await readTextFile(f.path, { on: f.on });
        if (this.file !== f) return;
        if (text === null) { f.error = "File not found"; return; }
        if (text.includes("\0")) { f.error = "Binary file · no preview"; return; }
        if (reload && text === f.text) return;
        f.text = text.length > MAX_TEXT ? text.slice(0, MAX_TEXT) + "\n… (file truncated)" : text;
        f.error = null;
        this.makeEditor(f);
      } catch (e) { if (this.file === f) f.error = "Cannot preview this file: " + String(e?.message ?? e); }
      finally { cx.notify(); }
    });
  }

  makeEditor(f) {
    this.release();
    if (this.showRendered(f)) return;
    const t = theme(), ext = f.path.includes(".") ? f.path.split(".").pop().toLowerCase() : "txt";
    const s = settings();
    this.code = EditorState.new({
      language: LANG[ext] ?? ext, readonly: true, value: f.text, soft_wrap: this.prefs.wrap,
      font_family: s.font_family,
      syntax: { keyword: t.ansi5, string: t.ansi2, comment: t.dim, function: t.ansi4, type: t.ansi3, number: t.ansi1, constant: t.ansi6, heading: t.ansi4, link: t.ansi6 },
    });
    if (f.line) {
      let at = 0;
      for (let i = 1; i < f.line && at >= 0; i++) at = f.text.indexOf("\n", at) + 1 || -1;
      if (at >= 0) {
        const end = f.text.indexOf("\n", at);
        this.code.set_selected_range({ start: at, end: end < 0 ? f.text.length : end });
      }
    }
  }

  showRendered(f) { return MD.test(f.path) && this.prefs.rendered && !f.source; }

  back(cx) {
    const prev = this.history.pop();
    if (!prev) return;
    if (this.file) this.ahead.push(this.file);
    this.open(prev.path, cx, { on: prev.on, push: false });
  }

  forward(cx) {
    const next = this.ahead.pop();
    if (!next) return;
    if (this.file) this.history.push(this.file);
    this.open(next.path, cx, { on: next.on, push: false });
  }

  // A link inside a rendered Markdown file: relative paths open here, the rest go to the system.
  followLink(url, cx) {
    if (/^[a-z][a-z0-9+.-]*:/i.test(url) && !url.startsWith("file:")) { exec("xdg-open", [url]).catch(() => {}); return; }
    let p = url.replace(/^file:\/\/(localhost)?/, "");
    const hash = /#L?(\d+)/.exec(p);
    p = decodeURIComponent(p.replace(/[#?].*$/, ""));
    if (!p) return; // an in-page anchor
    const abs = p.startsWith("/") ? normalize(p) : normalize(`${dirname(this.file.path)}/${p}`);
    if (p.endsWith("/")) { // a folder: show it in the tree
      if (!this.reveal(abs + "/.", cx)) { this.up = abs; this.upFor = location().cwd; this.refresh(cx); }
      return;
    }
    this.open(abs, cx, { line: hash ? Number(hash[1]) : undefined, on: this.file.on });
    this.showInTree(abs, cx);
  }

  openExternal(f) {
    const at = f.line ? `${f.path}:${f.line}` : f.path;
    exec("sh", ["-lc", 'command -v code >/dev/null && exec code -g "$1" || exec xdg-open "$2"', "open", at, f.path], { on: f.on }).catch(() => {});
  }

  // The system's default app for the file (an .html opens in the browser): `open` on macOS, the host's
  // explorer from a Linux guest on Windows (xdg-open would find no handler there), xdg-open elsewhere.
  // Asks the Changes panel (git-panel takeRequest) to show this file's diff, then shows that panel.
  openInChanges(f) {
    setShared("git-panel.open", { path: f.path, on: f.on ?? null, at: Date.now() });
    run("layout.select", { stack: "panel-body", child: "changes" });
  }

  openDefault(f) {
    const script = 'f=$1; if [ "$(uname)" = Darwin ]; then exec open "$f"; '
      + 'elif command -v wslpath >/dev/null 2>&1 && command -v explorer.exe >/dev/null 2>&1; then explorer.exe "$(wslpath -w "$f")"; exit 0; '
      + 'else exec xdg-open "$f"; fi';
    exec("sh", ["-c", script, "open", f.path], { timeout: 10000 }).catch(() => {});
  }

  // Right-click on a tree row: the copy menu, with the path from the git repository root that holds the
  // item (asked in the item's folder, on its machine), else from the Files root.
  copyMenu(abs, event, _cx) {
    const ctx = this.ctx;
    if (!ctx?.root) return;
    openCopyMenu(pathMenu(abs, ctx.root), event, { resolve: { abs, on: ctx.on ?? null } });
  }

  // In the popup copy: the path from the git repository that holds the item (asked in its folder), else the Files root.
  resolveMenuRoot(cx) {
    const r = props()?.resolve;
    if (!r) return;
    cx.spawn(async (cx) => {
      const top = await exec("git", ["rev-parse", "--show-toplevel"], { cwd: dirname(r.abs), on: r.on }).catch(() => null);
      if (top?.code === 0 && top.stdout.trim()) { this.menu = pathMenu(r.abs, top.stdout.trim()); cx.notify(); }
    });
  }

  // ---- render -----------------------------------------------------------------------------

  button(id, label, t, fn, { tip, active, disabled } = {}) {
    return div().id(id).h(22).px(7).flex().flex_none().items_center().rounded(5).text_size(11)
      .text_color(disabled ? t.dim : active ? t.foreground : t.muted).when(Boolean(active), e => e.bg(t.raised))
      .when(!disabled, e => e.cursor_pointer().hover(s => s.bg(t.hover).text_color(t.foreground)).on_click((_e, cx) => fn(cx)))
      .when(Boolean(tip), e => e.tooltip(tip)).child(label);
  }

  rows(prefix, depth, out, t) {
    if (out.length >= TOTAL || depth > 40) return out;
    const root = this.ctx.root;
    for (const entry of this.entries.get(prefix) ?? []) {
      if (out.length >= TOTAL) break;
      const path = prefix ? `${prefix}/${entry.name}` : entry.name, abs = `${root}/${path}`;
      const open = this.folders.has(path), mark = this.marks[abs], sel = this.file?.path === abs;
      if (abs === this.scrollTo) this.scrollIdx = out.length;
      out.push(h_flex().id(`fp-${path}`).h(24).flex_none().pl(6 + depth * 14).pr(8).gap(6).items_center().rounded(5).cursor_pointer()
        .when(sel, e => e.bg(t.raised).text_color(t.foreground)).hover(s => s.bg(t.hover))
        .on_click((_e, cx) => entry.dir ? this.toggle(path, cx) : this.open(abs, cx, { on: this.ctx.on }))
        .on_mouse_down("right", (e, cx) => { cx.stop_propagation(); this.copyMenu(abs, e, cx); })
        .child(entry.dir ? svg(`mods/file-panel/chevron-${open ? "down" : "right"}.svg`).size(12).flex_none().text_color(t.dim) : div().w(12).flex_none())
        .child(svg(`mods/file-panel/${entry.dir ? "folder" : "file"}.svg`).size(14).flex_none().text_color(entry.dir ? t.muted : t.dim))
        .child(div().flex_1().min_w(0).truncate().text_color(mark === "A" ? t.ansi2 : mark === "D" || mark === "U" ? t.ansi1 : mark && mark !== "•" ? t.ansi3 : t.foreground).child(entry.name))
        .when(Boolean(mark), e => e.child(div().flex_none().text_size(10).text_color(mark === "A" ? t.ansi2 : mark === "D" || mark === "U" ? t.ansi1 : mark === "•" ? t.dim : t.ansi3).child(mark))));
      if (entry.dir && open) this.rows(path, depth + 1, out, t);
    }
    return out;
  }

  treeView(t, h) {
    const root = this.ctx?.root;
    this.scrollIdx = null;
    const rows = root ? this.rows("", 0, [], t) : [];
    // Scroll once the row exists (its folders may still be loading).
    if (this.scrollTo && this.scrollIdx != null) { this.treeScroll.scroll_to_item(this.scrollIdx); this.scrollTo = null; }
    return v_flex().flex_none().when(h != null, e => e.h(h)).when(h == null, e => e.flex_1()).min_h(0)
      .child(h_flex().h(30).flex_none().px(8).gap(4).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(this.button("fp-up", "↑", t, cx => this.goUp(cx), { tip: "Parent folder", disabled: !root || root === "/" }))
        .child(div().flex_1().min_w(0).truncate().text_color(t.muted).tooltip(root || "-").child(short(root) || "No folder"))
        .when(Boolean(this.up), e => e.child(this.button("fp-follow", "follow pane", t, cx => { this.up = null; this.refresh(cx); }, { tip: "Back to the focused pane's folder" })))
        .child(this.button("fp-collapse", "⊟", t, cx => { this.folders.clear(); for (const k of [...this.entries.keys()]) if (k) this.entries.delete(k); cx.notify(); }, { tip: "Collapse all" }))
        .child(this.button("fp-refresh", "⟳", t, cx => this.refresh(cx), { tip: "Refresh" })))
      .child(v_flex().id("fp-tree").flex_1().min_h(0).overflow_y_scrollbar().track_scroll(this.treeScroll).p(4).text_size(12).children(rows)
        .when(Boolean(this.error) || !this.entries.get("")?.length, e => e.child(div().p(8).text_size(12).text_color(t.dim).child(this.error || (this.loading ? "Loading files…" : root ? "No files" : "No folder"))))
        .when(Boolean(this.limited), e => e.child(div().p(8).text_size(11).text_color(t.dim).child("Folders show up to 500 entries"))));
  }

  previewView(t) {
    const f = this.file, md = MD.test(f.path), rendered = this.showRendered(f), s = settings();
    const name = f.path.split("/").pop(), dir = short(dirname(f.path));
    const body = f.error ? div().p(12).text_size(12).text_color(t.dim).child(f.error)
      : f.text === null ? div().p(12).text_size(12).text_color(t.dim).child("Loading…")
      : rendered ? v_flex().id("fp-md").flex_1().min_h(0).overflow_y_scrollbar().px(14).py(10).text_size(13)
          .child(TextView.markdown(`fp-md-${f.path}`, f.text).selectable(true).on_link_click((url, cx) => this.followLink(url, cx)))
      : this.code ? Editor.new(this.code).font_family(s.font_family).text_size(Math.max(11, (s.font_size ?? 14) - 2)).flex_1().min_h(0)
      : div().p(12).text_size(12).text_color(t.dim).child("…");
    return v_flex().flex_1().min_h(0)
      .child(h_flex().h(30).flex_none().px(8).gap(4).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(this.button("fp-back", "←", t, cx => this.back(cx), { tip: "Back", disabled: !this.history.length }))
        .child(this.button("fp-fwd", "→", t, cx => this.forward(cx), { tip: "Forward", disabled: !this.ahead.length }))
        .child(div().flex_none().font_semibold().max_w(240).truncate().child(name))
        .when(Boolean(f.line), e => e.child(div().flex_none().text_color(t.ansi4).child(`:${f.line}`)))
        .child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim).tooltip(f.path || "-").child(dir))
        .when(md, e => e.child(this.button("fp-mode", rendered ? "Source" : "Rendered", t, cx => {
          f.source = rendered;
          if (!rendered) this.setPrefs({ rendered: true });
          this.makeEditor(f); this.remember(); cx.notify();
        }, { tip: rendered ? "Show the Markdown source" : "Render the Markdown" })))
        .when(!rendered, e => e.child(this.button("fp-wrap", "Wrap", t, cx => { this.setPrefs({ wrap: !this.prefs.wrap }); this.code?.set_soft_wrap(this.prefs.wrap); cx.notify(); }, { active: this.prefs.wrap, tip: "Wrap long lines" })))
        .child(this.button("fp-changes", "Changes", t, () => this.openInChanges(f), { tip: "Show this file's changes in Changes" }))
        .child(this.button("fp-reveal", "⌖", t, cx => this.showInTree(f.path, cx), { tip: "Show in the tree" }))
        .when(!f.on, e => e.child(this.button("fp-open", "Open", t, () => this.openDefault(f), { tip: "Open with the default app (an .html in the browser)" })))
        .child(this.button("fp-ext", "↗", t, () => this.openExternal(f), { tip: "Open in VS Code (code -g)" }))
        .child(this.button("fp-close", "×", t, cx => { this.release(); if (this.file) this.history.push(this.file); this.file = null; this.remember(); cx.notify(); }, { tip: "Close the preview" })))
      .child(body);
  }

  render(cx) {
    if (props()?.menu) return copyMenuView(this.menu ?? props().menu, theme(), this.menuFocus);
    this.rendering = true;
    try { return this.draw(cx); } finally { this.rendering = false; }
  }

  draw(cx) {
    if (!checking()) this.followSpace(cx);
    // A terminal Ctrl-click (link.js) asks for a file.
    const req = shared("file-panel.open");
    if (req?.at > this.handled && (req.target ?? "top") === this.slot && !checking()) {
      this.handled = req.at;
      if (req.dir) this.showFolder(req.path, cx);
      else {
        this.open(req.path, cx, { line: req.line, on: req.on });
        this.showInTree(req.path, cx);
      }
    }
    if (this.key() !== this.ctx?.key && !checking()) this.refresh(cx);
    const t = theme(), f = this.file;
    const share = SPLITS[this.prefs.split] ?? SPLITS[0];
    const treeH = f ? (share ? Math.max(90, Math.floor((this.h - 32) * share)) : 0) : null;
    return v_flex().id("file-panel").size_full().min_h(0).rounded(10).overflow_hidden().bg(t.surface).border_1().border_color(t.border).text_color(t.foreground).text_size(13)
      .on_size_change((size, cx) => { if (Math.abs(size.height - this.h) > 4) { this.h = size.height; cx.notify(); } })
      .child(h_flex().h(32).flex_none().px(12).gap(6).items_center().border_b(1).border_color(t.border)
        .child(div().font_semibold().child("Files"))
        .child(div().flex_1())
        .when(Boolean(f), e => e.child(this.button("fp-split", ["Tree 35%", "Tree 55%", "Tree hidden"][this.prefs.split] ?? "Tree", t,
          cx => this.setPrefs({ split: (this.prefs.split + 1) % SPLITS.length }, cx), { tip: "How much height the tree takes while a file is open" }))))
      .when(treeH !== 0, e => e.child(this.treeView(t, treeH)))
      .when(Boolean(f), e => e.child(div().h(1).flex_none().bg(t.border)).child(this.previewView(t)));
  }
}
