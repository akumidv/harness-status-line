// The dock under the right panel: its own tabs of terminals, each tab split right/down like the
// central area. One dock per space, kept in shared "panel-shell.dock":
//   { [spaceId]: { tabs: [{ id, name, tree }], active, seq } }
//   tree = { addr } | { dir: "row" | "column", children: [tree, ...] }
// Terminals are core terminals (hc run --keep, named ps-<space>-<n>, started in the space's
// folder), so they outlive Herdr and come back as shells in the same folder after a reboot.
// This mod is the bar (layout block `panel-shell-bar`); it rewrites the stack `panel-shell-term`
// in the layout file (never layout.set: that drops the layout's clicks) and selects the tab.
import { View, div, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { spaces, panes, focused, hc, exec, run, theme, home, appHome, readTextFile, writeTextFile, shared, setShared, window, checking, nextCommand, layout } from "herdr";

const STACK = "panel-shell-term";
const sid = (spaceId) => spaceId.replace(/^sp/, "").slice(0, 16);
const docks = () => shared("panel-shell.dock") ?? {};
const clone = (x) => JSON.parse(JSON.stringify(x));

function find(node, id) {
  if (node?.id === id) return node;
  for (const c of node?.children ?? []) { const f = find(c, id); if (f) return f; }
  return null;
}
const leaves = (tree) => !tree ? [] : tree.addr ? [tree.addr] : tree.children.flatMap(leaves);

// Drop terminals that are gone; a split left with one child collapses into it.
function prune(tree, live) {
  if (!tree) return null;
  if (tree.addr) return live.has(tree.addr) ? tree : null;
  const children = tree.children.map((c) => prune(c, live)).filter(Boolean);
  return children.length === 0 ? null : children.length === 1 ? children[0] : { ...tree, children };
}

// Put `addr` beside `at` (right: row, down: column).
function split(tree, at, addr, dir) {
  if (tree.addr) return tree.addr === at ? { dir, children: [tree, { addr }] } : tree;
  const i = tree.children.findIndex((c) => c.addr === at);
  if (i >= 0 && tree.dir === dir) { const children = [...tree.children]; children.splice(i + 1, 0, { addr }); return { ...tree, children }; }
  return { ...tree, children: tree.children.map((c) => split(c, at, addr, dir)) };
}

function toNode(tree, id) {
  if (tree.addr) return { id, type: "block", padding: [4, 6], content: { terminal: tree.addr } };
  return { id, type: tree.dir, gap: 1, divider: { width: 1, color: "border" },
    children: tree.children.map((c, i) => toNode(c, c.addr ? `dock-${c.addr}` : `${id}-${i}`)) };
}

function button(t, id, label, tip, fn, { danger } = {}) {
  return div().id(id).h(22).px(6).flex().flex_none().items_center().rounded(5).cursor_pointer().text_size(12).text_color(t.muted)
    .hover((st) => st.bg(t.hover).text_color(danger ? t.ansi1 : t.foreground)).tooltip(tip)
    .on_mouse_down("left", (_e, cx) => cx.stop_propagation()).on_click((_e, cx) => fn(cx)).child(label);
}

export default class PanelShell extends View {
  init(_props, cx) {
    this.busy = false; this.error = null; this.written = null; this.lastFocus = {};
    if (checking()) return;
    this.sync(cx);
    cx.timer.every(1500, (cx) => this.sync(cx));
    // Commands (palette, keys.json, `herdr-app do`): panel-shell.new-tab / .split-right / .split-down / .close-pane / .next-tab
    cx.spawn(async (cx) => {
      for (;;) {
        const c = await nextCommand();
        if (c === "new-tab") this.newTab(cx);
        else if (c === "split-right") this.split("row", cx);
        else if (c === "split-down") this.split("column", cx);
        else if (c === "close-pane") this.closePane(cx);
        else if (c === "next-tab") this.cycle(cx);
        cx.notify();
      }
    });
  }

  space() { return spaces().find((s) => s.active) ?? null; }
  dock(s) { return docks()[s.id] ?? null; }
  save(s, dock) { setShared("panel-shell.dock", { ...docks(), [s.id]: dock }); }
  activeTab(dock) { return dock?.tabs.find((x) => x.id === dock.active) ?? dock?.tabs[0] ?? null; }

  // The space's workspace folder = where its panes work (what Files / Changes show): the repo root (or
  // folder) of its focused pane, else of its first pane away from home. `space.folder` is only the
  // folder the space was created in and goes stale (the name follows the pane, the folder does not),
  // so it is the fallback when no pane is away from home.
  async folder(s) {
    const tabs = new Set(s.tabs.map((x) => x.id)), mine = panes().filter((p) => tabs.has(p.tab) && p.cwd);
    const away = mine.filter((p) => p.cwd !== home());
    if (!away.length && s.folder && s.folder !== home()) return s.folder; // home = nobody chose one
    const first = away.find((p) => p.focused) ?? away[0] ?? mine[0];
    if (!first) return null;
    const top = await exec("git", ["rev-parse", "--show-toplevel"], { cwd: first.cwd, on: s.machine }).catch(() => null);
    return top?.code === 0 ? top.stdout.trim() : first.cwd;
  }

  async newTerminal(s, dock) {
    const n = (dock.seq = (dock.seq ?? 0) + 1), on = s.machine ? ["--on", s.machine] : [], dir = await this.folder(s);
    const r = await hc(["run", "--bg", "--json", "--no-parent", "--keep", "--name", `ps-${sid(s.id)}-${n}`, ...on,
      ...(dir ? ["--cwd", dir] : []), "--label", `herdr.panel-shell=${s.id}`]);
    if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim());
    return JSON.parse(r.stdout).address;
  }

  // The dock terminal to act on: the focused one if it is in this tab, else the last focused, else the first.
  current(tab) {
    const f = focused(), all = leaves(tab?.tree);
    if (f && !f.pane && all.includes(f.terminal)) return f.terminal;
    return all.includes(this.lastFocus[tab?.id]) ? this.lastFocus[tab.id] : all[0] ?? null;
  }

  // Bring the dock in line with what is alive, then show it.
  sync(cx) {
    const s = this.space();
    if (!s || this.busy) return;
    // A space another profile owns has its dock in that window; creating one here would clash with its terminals.
    this.foreign = shared("space.foreign") === s.id;
    if (this.foreign) { this.error = null; cx.notify(); return; }
    // In the background a slower pace is enough (a shell that ended still has to go).
    if (window()?.active === false && this.shownSpace === s.id && Date.now() - (this.syncedAt ?? 0) < 10000) return;
    this.syncedAt = Date.now();
    this.busy = true;
    cx.spawn(async (cx) => {
      try {
        const ls = await hc(["ls", "--json"]);
        if (ls.code !== 0) throw new Error((ls.stderr || ls.stdout).trim());
        const terms = JSON.parse(ls.stdout).terminals;
        const live = new Set(terms.filter((x) => !x.state || x.state === "live").map((x) => x.address));
        const dock = clone(this.dock(s) ?? { tabs: [], active: null, seq: 0 });
        const before = JSON.stringify(dock);
        for (const tab of dock.tabs) tab.tree = prune(tab.tree, live);
        dock.tabs = dock.tabs.filter((x) => x.tree);
        if (!dock.tabs.length) {
          // The shell of the old one-terminal version, if it is still there.
          const old = terms.find((x) => x.name === `ps-${sid(s.id)}` && live.has(x.address));
          const addr = old?.address ?? await this.newTerminal(s, dock);
          dock.tabs.push({ id: `d${++dock.seq}`, name: null, tree: { addr } });
        }
        if (!dock.tabs.some((x) => x.id === dock.active)) dock.active = dock.tabs[0].id;
        if (JSON.stringify(dock) !== before) this.save(s, dock);
        const f = focused(), tab = this.activeTab(dock);
        if (f && !f.pane && leaves(tab.tree).includes(f.terminal)) this.lastFocus[tab.id] = f.terminal;
        // git-panel / file-panel ignore focus on dock terminals (they aren't project panes).
        const addrs = Object.values(docks()).flatMap((d) => d.tabs.flatMap((x) => leaves(x.tree))).sort();
        if (JSON.stringify(shared("panel-shell.addrs")) !== JSON.stringify(addrs)) setShared("panel-shell.addrs", addrs);
        await this.show(dock);
        this.shownSpace = s.id; this.error = null;
      } catch (e) { this.error = String(e?.message ?? e); }
      finally { this.busy = false; cx.notify(); }
    });
  }

  // Write every tab of the shown space's dock as a child of the stack, then select the active one.
  async show(dock) {
    const active = `dock-tab-${dock.active}`;
    const children = dock.tabs.map((x) => toNode(x.tree, `dock-tab-${x.id}`)), want = JSON.stringify(children);
    if (want !== this.written) {
      const path = `${appHome()}/layouts/${shared("layout") ?? "my-main"}.json`;
      const text = await readTextFile(path);
      if (!text) return;
      const tree = JSON.parse(text), node = find(tree, STACK);
      if (!node) return;
      if (node.type !== "stack" || JSON.stringify(node.children ?? null) !== want) {
        for (const k of Object.keys(node)) if (!["id", "size", "min", "max", "background", "radius"].includes(k)) delete node[k];
        Object.assign(node, { type: "stack", active, children });
        await writeTextFile(path, JSON.stringify(tree, null, 2) + "\n", text);
      }
      this.written = want;
    }
    // Herdr picks up the file a moment later; until the tab is there, the file's `active` stands
    // and the next sync selects it.
    const live = find(layout(), STACK);
    if (live?.active !== active && live?.children?.some((c) => c.id === active)) run("layout.select", { stack: STACK, child: active });
  }

  edit(cx, fn) {
    const s = this.space(), dock = s && this.dock(s);
    if (!dock || this.busy) return;
    this.busy = true;
    cx.spawn(async (cx) => {
      try {
        const next = clone(dock);
        const focus = await fn(s, next);
        this.save(s, next);
        await this.show(next);
        if (focus) { await cx.sleep(150); run("pane.focus", { terminal: focus }); }
        this.error = null;
      } catch (e) { this.error = String(e?.message ?? e); }
      finally { this.busy = false; cx.notify(); }
    });
  }

  newTab(cx) {
    run("layout.show", { id: "panel-shell" });
    this.edit(cx, async (s, dock) => {
      const addr = await this.newTerminal(s, dock), id = `d${++dock.seq}`;
      const i = dock.tabs.findIndex((x) => x.id === dock.active);
      dock.tabs.splice(i + 1, 0, { id, name: null, tree: { addr } });
      dock.active = id;
      return addr;
    });
  }

  split(dir, cx) {
    this.edit(cx, async (s, dock) => {
      const tab = this.activeTab(dock), at = this.current(tab);
      if (!tab || !at) return null;
      const addr = await this.newTerminal(s, dock);
      tab.tree = split(tab.tree, at, addr, dir);
      this.lastFocus[tab.id] = addr;
      return addr;
    });
  }

  closePane(cx) {
    const s = this.space(), tab = this.activeTab(s && this.dock(s)), at = this.current(tab);
    if (at) this.kill([at], cx);
  }

  closeTab(id, cx) {
    const s = this.space(), tab = s && this.dock(s)?.tabs.find((x) => x.id === id);
    if (tab) this.kill(leaves(tab.tree), cx);
  }

  kill(addrs, cx) {
    this.edit(cx, async (_s, dock) => {
      for (const a of addrs) { await hc(["kill", a]).catch(() => null); await hc(["rm", a]).catch(() => null); }
      const gone = new Set(addrs);
      for (const tab of dock.tabs) tab.tree = prune(tab.tree, { has: (a) => !gone.has(a) });
      const i = dock.tabs.findIndex((x) => x.id === dock.active);
      dock.tabs = dock.tabs.filter((x) => x.tree);
      if (!dock.tabs.some((x) => x.id === dock.active)) dock.active = dock.tabs[Math.max(0, Math.min(i, dock.tabs.length - 1))]?.id ?? null;
      return null; // no tab left: the next sync starts a fresh one
    });
  }

  select(id, cx) { this.edit(cx, async (_s, dock) => { dock.active = id; return null; }); }

  cycle(cx) {
    const s = this.space(), dock = s && this.dock(s);
    if (!dock?.tabs.length) return;
    const i = dock.tabs.findIndex((x) => x.id === dock.active);
    this.select(dock.tabs[(i + 1) % dock.tabs.length].id, cx);
  }

  render(cx) {
    const t = theme(), s = this.space(), dock = s && !this.foreign ? this.dock(s) : null, tab = this.activeTab(dock);
    const titleOf = (x, i) => {
      if (x.name) return x.name;
      const n = leaves(x.tree).length;
      return `bash ${i + 1}${n > 1 ? ` ·${n}` : ""}`;
    };
    const pill = (x, i) => {
      const on = x.id === tab?.id;
      return h_flex().id(`ps-tab-${x.id}`).h(24).px(8).gap(6).flex_none().items_center().rounded(6).cursor_pointer().text_size(12)
        .border(1).border_color(on ? t.border : t.chrome).bg(on ? t.raised : t.chrome).text_color(on ? t.foreground : t.muted)
        .hover((st) => st.bg(t.hover)).on_click((_e, cx) => this.select(x.id, cx))
        .child(div().max_w(140).truncate().child(titleOf(x, i)))
        .when(on, (e) => e.child(div().id(`ps-close-${x.id}`).text_color(t.dim).hover((st) => st.text_color(t.foreground))
          .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
          .on_click((_e, cx) => { cx.stop_propagation(); this.closeTab(x.id, cx); }).child(svg("mods/pane/close.svg").size(11))));
    };
    return h_flex().id("panel-shell-bar").size_full().px(6).gap(3).items_center().text_size(12).bg(t.chrome).border_b(1).border_color(t.border)
      .child(h_flex().id("ps-tabs").flex_1().min_w(0).h_full().gap(3).items_center().overflow_x_scrollbar()
        .children((dock?.tabs ?? []).map(pill))
        .child(button(t, "ps-new", "+", "New bash tab (in the workspace folder)", (cx) => this.newTab(cx))))
      .when(Boolean(this.foreign), (e) => e.child(div().flex_1().truncate().text_size(11).text_color(t.dim).child("other profile's space: its dock is in that window")))
      .when(Boolean(this.error), (e) => e.child(div().flex_none().max_w(160).truncate().text_size(11).text_color(t.ansi1).tooltip(this.error).child(this.error)))
      .child(button(t, "ps-split-r", "◫", "Split right: a new bash beside the current one", (cx) => this.split("row", cx)))
      .child(button(t, "ps-split-d", "⬓", "Split down: a new bash under the current one", (cx) => this.split("column", cx)))
      .child(button(t, "ps-cd", "cd", "cd the current dock terminal to the focused pane's folder", () => {
        const f = focused(), pane = f?.pane ? panes().find((p) => p.pane === f.pane) : null, at = this.current(tab);
        const d = pane?.cwd || s?.folder;
        if (at && d) hc(["send", at, `cd ${JSON.stringify(d)}`]);
      }))
      .child(button(t, "ps-close-pane", "✕", "Close the current dock terminal", (cx) => this.closePane(cx), { danger: true }))
      .child(button(t, "ps-hide", "⌄", "Hide the dock (palette: Toggle panel terminal)", () => run("layout.hide", { id: "panel-shell" })));
  }
}
