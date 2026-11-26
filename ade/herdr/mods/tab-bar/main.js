// A clone of Herdr's `tabs` (0.1.18): the Files/Changes toggles are gone (the right panel is fixed,
// see panel-tabs) and the + (new tab) sits right after the last tab. Each tab also shows its agents'
// state (read from shared("agent.state"), written by space-tree) and a click on its program icon renames it
// in place. Model and design: docs/central-window.md and docs/left-panel.md ("Agent state model").
import { View, div, svg, image } from "gpui-kit";
import { h_flex, ScrollHandle, InputState, Input } from "gpui-base";
import { spaces, panes, agents, run, openOverlay, theme, window, layout, settings, shared, checking } from "herdr";

function find(node, id) {
  if (node?.id === id) return node;
  for (const child of node?.children ?? []) {
    const found = find(child, id);
    if (found) return found;
  }
  return null;
}

// Same as the agents mod: drops a status glyph, keeps a leading bracket or quote.
const clean = (s) => (s ?? "").replace(/^[^\p{L}\p{N}\p{Ps}\p{Pi}]+/u, "").replace(/^\[ [!.] \] /, "").trim();

// ---- Agent state marks: a verbatim copy of the block in mods/space-tree/main.js (mod folders cannot
// ---- import each other), plus `worstOf` from mods/space-tree/agent-state.js. Keep the copies identical:
// ---- change them in space-tree first, then here. The model is in docs/left-panel.md ("Agent state model").
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

// What an aggregate (space, group, machine, tab) shows: the most urgent interaction among `states`
// (asks > waits > working > idle > sleeping: not in URGENCY, so a lead only when all sleep) plus the cache chip of the one that lost it first. Null for none.
const URGENCY = { blocked: 4, done: 3, working: 2, idle: 1 };
function worstOf(states) {
  let lead = null;
  let chip = null;
  for (const st of states) {
    if (!lead || (URGENCY[st.status] ?? 0) > (URGENCY[lead.status] ?? 0)) lead = st;
    if (!st.cache) continue;
    if (!chip || (st.cache === "expired" && chip.cache === "warn") || (st.cache === chip.cache && st.since < chip.since)) chip = st;
  }
  return lead && { status: lead.status, since: chip ? chip.since : lead.since, cache: chip ? chip.cache : null };
}
// ---- end of the agent state marks

// A tab Herdr named (unnamed, or after its program) follows its agent's live
// title, as the agents list does; a name the person gave stays.
function label(tab, lead) {
  const prog = lead?.program?.name;
  const base = tab.named ? tab.name : prog ?? "Terminal";
  if (!lead?.program?.agent) return base;
  const live = clean(lead.title);
  // clean() would strip a path's leading ~ or /: test the title with only its status glyph gone.
  const path = /^[~/]|^[A-Za-z]:\\/.test((lead.title ?? "").replace(/^[^\p{L}\p{N}~/]+/u, ""));
  const telling = live && live !== lead.terminal && live !== prog && !path && !live.includes("@") && live.toLowerCase() !== lead.program.key;
  return (tab.name === prog || !tab.named) && telling ? live : base;
}

export default class Tabs extends View {
  init(_props, cx) {
    this.pressed = null;
    this.quiet = false; // the press began on the icon or the rename field: its release must not select the tab
    this.scroll = ScrollHandle.new();
    this.swipe = { acc: 0, steps: 0, at: 0, last: 0 };
    // Inline rename of one tab (started by a click on its icon): one retained text state, drawn in that tab.
    this.renaming = null; // { id, shown } while a tab's name is edited; `shown` = the name it started from
    this.nameInput = InputState.new({ placeholder: "Tab name" });
    this.nameInput.on("submit", (_e, cx) => { this.finishRename(true); cx.notify(); });
    this.nameInput.on("blur", (_e, cx) => { this.finishRename(true); cx.notify(); });
    // A redraw every 30 s so the cache chip's minutes advance (the state itself is written by space-tree).
    if (!checking()) cx.timer.every(30000, (cx) => cx.notify());
  }

  // A click on a tab's icon: rename that tab in place, prefilled with the name it shows (a named tab: its
  // name, marker included), all selected. The field is drawn on the next render, so the focus is asked again then.
  startRename(tab, shown, cx) {
    this.finishRename(true);
    this.renaming = { id: tab.id, shown };
    this.nameInput.set_value(shown);
    this.nameInput.set_selected_range({ start: 0, end: shown.length });
    this.nameInput.focus();
    this.focusName = true;
    cx.notify();
  }

  // Ends the rename (Enter, blur, Escape); `renaming` is cleared first so the blur it causes is a no-op.
  // An empty or unchanged name writes nothing (an unchanged one would turn a live-titled tab into a named one).
  finishRename(save) {
    const target = this.renaming;
    if (!target) return;
    this.renaming = null;
    const name = this.nameInput.value().trim();
    this.nameInput.blur();
    if (save && name && name !== target.shown) run("tab.rename", { id: target.id, name });
  }

  // One tab per wheel notch. A touchpad or smooth-scrolling mouse sends many
  // small events per gesture: a short swipe or one smoothed notch (about
  // 25 px) switches one tab, a longer swipe keeps going a tab per 60 px, at
  // most one every 100 ms so each shows.
  wheel(e) {
    if (e.delta_lines) {
      if (e.delta_lines.y) run(e.delta_lines.y > 0 ? "tab.prev" : "tab.next");
      return;
    }
    const s = this.swipe, now = Date.now();
    // Phases often arrive only as moves (a macOS touchpad included): a pause
    // or a turn starts a new gesture.
    if (e.touch_phase === "started" || now - s.at > 250 || e.delta.y * s.acc < 0) Object.assign(s, { acc: 0, steps: 0 });
    s.at = now;
    s.acc += e.delta.y;
    const need = s.steps ? 60 : 20;
    if (Math.abs(s.acc) < need || now - s.last < 100) return;
    s.acc -= Math.sign(s.acc) * need;
    s.steps++;
    s.last = now;
    run(e.delta.y > 0 ? "tab.prev" : "tab.next");
  }

  render() {
    const t = theme();
    const bar = shared("profile.tint") ?? t.chrome; // the title row: chrome with the profile colour mixed in (space-tree writes it)
    const space = spaces().find(p => p.active);
    const shown = space?.tabs ?? [];
    const active = shown.findIndex(tab => tab.active);
    const selected = shown[active]?.id;
    if (selected !== this.selected || active !== this.selectedIndex) {
      this.selected = selected;
      this.selectedIndex = active;
      if (active >= 0) this.scroll.scroll_to_item(active);
    }
    if (this.renaming && !shown.some(tab => tab.id === this.renaming.id)) this.renaming = null; // its tab or space is gone
    if (this.focusName && this.renaming) { this.focusName = false; this.nameInput.focus(); }
    // Agent states, read once per render: the tab's agents are the agents() entries of that tab. An agent the
    // sidebar has not stamped yet shows by Herdr's own status.
    const now = Date.now();
    const states = shared("agent.state") ?? {};
    const byTab = {};
    for (const a of agents()) if (a.program?.agent) (byTab[a.tab] ??= []).push(states[a.address] ?? { status: a.status, since: now, cache: null });
    const ps = panes();
    const stop = (_e, cx) => cx.stop_propagation();
    const button = (id, label, action) => div().id(id).size(26).flex_none().flex().items_center().justify_center().rounded(6).text_color(t.dim).hover(s => s.bg(t.hover).text_color(t.foreground))
      .on_mouse_down("left", stop).on_click(action).child(label);
    const item = (c, i) => {
      const lead = ps.find(p => p.tab === c.id && p.program?.agent) ?? ps.find(p => p.tab === c.id);
      const program = lead?.program;
      const name = label(c, lead);
      const editing = this.renaming?.id === c.id;
      const worst = byTab[c.id] ? worstOf(byTab[c.id]) : null;
      // A tab stays quiet (no mark for idle) while its agents are idle with the cache fine.
      const marks = worst && (worst.status !== "idle" || worst.cache) ? stateMarks(worst, t, now) : null;
      return h_flex().id(`tab-${c.id}`).h(29).px(10).gap(7).rounded(7).items_center().flex_none().text_size(12.8)
        // A rename or status dot on this or an earlier tab can push the active one out.
        .when(i <= active, el => el.on_size_change((_size, cx) => { this.scroll.scroll_to_item(active); cx.notify(); }))
        .border(1).border_color(c.active ? t.border : bar).bg(c.active ? t.raised : bar).text_color(c.active ? t.foreground : t.muted)
        .hover(s => s.bg(t.hover))
        .on_mouse_down("left", (e, cx) => { cx.stop_propagation(); this.quiet = false; this.pressed = i; if (e.click_count === 2) run("tab.rename", { id: c.id }); })
        .on_mouse_up("left", () => {
          const from = this.pressed; this.pressed = null;
          if (this.quiet) this.quiet = false; // the release of a press on the icon or the field: no select, no move
          else if (from !== null && from !== i) run("tab.move", { id: shown[from].id, index: space.tabs.findIndex(x => x.id === c.id) + 1 });
          else run("tab.select", { id: c.id });
        })
        .on_mouse_down("right", (_e, cx) => { cx.stop_propagation(); openOverlay({ mod: "menu", kind: "popup", width: 200, props: { tab: "tab", target: { id: c.id, space: space.id } } }); })
        // The icon is the quick-rename handle: the press does not reach the tab (no drag, no select).
        .when(Boolean(program?.icon), el => el.child(div().id(`tab-icon-${c.id}`).px(2).py(2).rounded(4).flex_none().cursor_pointer().tooltip("Rename tab")
          .hover(s => s.bg(t.border))
          .on_mouse_down("left", (_e, cx) => { cx.stop_propagation(); this.quiet = true; })
          .on_click((_e, cx) => { cx.stop_propagation(); this.startRename(c, name, cx); })
          .child(program.tint ? svg(program.icon).size(14).text_color(t.muted) : image(program.icon).size(14))))
        .child(editing
          ? div().id(`tab-name-${c.id}`).w(150).h(21).px(6).flex().items_center().rounded(4).border_1().border_color(t.accent).cursor_text()
            .on_mouse_down("left", (_e, cx) => { cx.stop_propagation(); this.quiet = true; })
            .on_key_down((e, cx) => { if (e.key === "escape") { this.finishRename(false); cx.notify(); } })
            .child(Input.new(this.nameInput).h_full().text_size(12.8).text_color(t.foreground))
          : div().max_w(170).truncate().child(c.zoomed ? `${name} Z` : name))
        .when(Boolean(marks), el => el.child(marks))
        .when(c.active, el => el.child(div().id(`close-${c.id}`).text_color(t.dim).on_mouse_down("left", stop).on_click((_e, cx) => { cx.stop_propagation(); run("tab.close", { id: c.id }); }).child(svg("mods/pane/close.svg").size(12))));
    };
    const root = layout(), sidebar = find(root, "sidebar"), w = window();
    // Old saved rails and a dragged-narrow sidebar can be smaller than the window buttons.
    const lightsPadding = w?.platform === "macos" && w.titlebar === "none" && !w.fullscreen && root?.traffic_lights
      ? Math.max(10, (root.traffic_lights[0] + 64) / settings().zoom - (sidebar?.hidden ? 0 : sidebar?.size?.px ?? 0))
      : 10;
    return h_flex().size_full().min_w(0).px(10).pl(lightsPadding).gap(3).items_center().bg(bar).border_b(1).border_color(t.border)
      .on_mouse_up("left", () => { this.pressed = null; this.quiet = false; })
      .on_scroll_wheel(e => this.wheel(e))
      .child(h_flex().id("tab-scroll").flex_1().min_w(0).h_full().items_center().gap(3).overflow_x_scrollbar().track_scroll(this.scroll)
        .on_size_change((_size, cx) => { if (active >= 0) { this.scroll.scroll_to_item(active); cx.notify(); } })
        .children(shown.map(item))
        // New tab: right after the tabs, like a browser.
        .child(button("tab-new", "+", () => openOverlay({ mod: "new-menu", kind: "popup", width: 250, height: 512 }))))
      .when(w?.buttons, el => el.child(button("minimize", w.platform === "windows" ? svg("mods/tab-bar/win-min.svg").size(16) : "−", () => run("window.minimize")))
        .child(button("maximize", w.platform === "windows" ? svg(`mods/tab-bar/${w.maximized ? "win-restore" : "win-max"}.svg`).size(16) : "□", () => run("window.maximize")))
        .child(button("close-window", w.platform === "windows" ? svg("mods/tab-bar/win-close.svg").size(16) : svg("mods/pane/close.svg").size(13), () => run("window.close"))));
  }
}
