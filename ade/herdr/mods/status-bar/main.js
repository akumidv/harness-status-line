// Clone of Herdr's `workspace-status` (0.1.18): the bottom bar of the window. Left to right: the machine, the git
// of the active space, the agents of this window's profile by status, the limits of the focused Claude agent's
// account, the palette hint. Design: docs/central-window.md "Status bar". Pure logic in ./status.js.
//
// Files and exec run only from the 1 s tick (never from render), and only while the window is visible: git of the
// active space on a space change and every 10 s, the limits summary on a change of the shown agent and every 15 s.
import { View, div, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { spaces, agents, theme, run, window, keys, shared, exec, readTextFile, checking } from "herdr";
import {
  parseGitStatus, profileAgents, countAgents, nextAgent, AGENT_COLOR,
  isClaude, limitsAgent, visibleSummary, segmentTip, toneKey, terminalFile, summaryFile,
} from "./status.js";

const TICK_MS = 1000;
const GIT_EVERY_MS = 10000;
const LIMITS_EVERY_MS = 15000;
const GIT_TIMEOUT_MS = 8000;

const parseJson = (text) => { try { return JSON.parse(text); } catch { return null; } };

// `items` with a dim dot between each two.
const withSeparators = (items, t) => items.flatMap((el, i) => (i ? [div().text_color(t.dim).child("·"), el] : [el]));

// What identifies the active space's git: a change of any part asks git again at once.
const gitKey = (space) => JSON.stringify([space.id, space.machine, space.folder]);

export default class StatusBar extends View {
  init(_props, cx) {
    this.git = null; // { key, info: parseGitStatus result | null }
    this.gitAt = 0;
    this.gitBusy = false;
    this.limits = null; // { address, summary: parsed summary.json | null }
    this.limitsAt = 0;
    this.limitsBusy = false;
    this.lastAgent = null; // address of the last agent seen focused in this window
    this.uid = null;
    if (checking()) return;
    this.tick(cx);
    cx.timer.every(TICK_MS, (cx) => this.tick(cx));
  }

  tick(cx) {
    if (window()?.visible === false) return;
    const now = Date.now();
    const sp = spaces();
    const space = sp.find((s) => s.active);
    const key = space ? gitKey(space) : null;
    if (key !== (this.git?.key ?? null) || now - this.gitAt >= GIT_EVERY_MS) this.refreshGit(cx, space, key);
    const list = agents();
    const focused = list.find((a) => a.focused);
    if (focused && focused.address !== this.lastAgent) { this.lastAgent = focused.address; cx.notify(); }
    const address = this.limitsAddress(list, sp);
    if (address !== (this.limits?.address ?? null) || now - this.limitsAt >= LIMITS_EVERY_MS) this.refreshLimits(cx, address);
  }

  // The address whose limits are shown: a Claude agent of this computer (focused, else the last focused), else null.
  limitsAddress(list, sp) {
    const agent = limitsAgent(list, this.lastAgent);
    if (!agent || !isClaude(agent)) return null;
    const space = sp.find((s) => s.id === agent.space);
    return space && !space.machine ? agent.address : null;
  }

  refreshGit(cx, space, key) {
    if (this.gitBusy) return;
    this.gitAt = Date.now();
    if (!space) { this.git = { key, info: null }; return; }
    this.gitBusy = true;
    cx.spawn(async (cx) => {
      let info = null;
      try {
        const r = await exec("git", ["status", "--porcelain=v2", "--branch"], { cwd: space.folder || undefined, on: space.machine, timeout: GIT_TIMEOUT_MS });
        info = r.code === 0 ? parseGitStatus(r.stdout) : null;
      } catch {
        info = this.git?.key === key ? this.git.info : null; // could not ask (machine away): keep what was shown
      }
      this.git = { key, info };
      this.gitBusy = false;
      cx.notify();
    });
  }

  refreshLimits(cx, address) {
    if (this.limitsBusy) return;
    this.limitsAt = Date.now();
    if (!address) { if (this.limits) { this.limits = null; cx.notify(); } return; }
    this.limitsBusy = true;
    cx.spawn(async (cx) => {
      let summary = null;
      try {
        if (this.uid === null) {
          const r = await exec("id", ["-u"]);
          if (r.code === 0 && /^\d+$/.test(r.stdout.trim())) this.uid = r.stdout.trim();
        }
        if (this.uid !== null) {
          const file = summaryFile(parseJson((await readTextFile(terminalFile(this.uid, address))) ?? ""));
          if (file) summary = parseJson((await readTextFile(file)) ?? "");
        }
      } catch {
        summary = null; // unreadable: the block hides until the next read
      }
      this.limits = { address, summary };
      this.limitsBusy = false;
      cx.notify();
    });
  }

  // The palette key as actually bound, not a hardcoded one; without a
  // binding the hint stays useful ("commands") instead of lying.
  hint(t) {
    const mac = window()?.platform === "macos";
    const mod = { cmd: mac ? "⌘" : "Ctrl+", ctrl: mac ? "⌃" : "Ctrl+", shift: mac ? "⇧" : "Shift+", alt: mac ? "⌥" : "Alt+", option: mac ? "⌥" : "Alt+" };
    const b = keys().find((k) => k.section === "global" && k.action === "app.palette");
    const parts = b && b.keys.endsWith("--") ? [...b.keys.slice(0, -2).split("-"), "-"] : b?.keys.split("-");
    const key = parts && parts.map((p) => mod[p.toLowerCase()] ?? p.toUpperCase()).join("");
    return key ? `${key}  commands` : "commands";
  }

  // Shows the right panel's Changes stack, then the git-panel view (`branches` or `changes`).
  openGit(view) {
    run("layout.show", { id: "panel" });
    run("layout.select", { stack: "panel-body", child: "changes" });
    run(`git-panel.${view}`);
  }

  // Focuses the next agent of `kind` after the focused one (the first when the focused one is not of that kind),
  // so repeated clicks cycle through them. Read at click time: the render's list may be old by then.
  focusNext(kind) {
    const list = profileAgents(agents(), shared("space.owned"));
    const agent = nextAgent(list, kind, shared("agent.state") ?? {}, list.find((a) => a.focused)?.address);
    if (agent) run("agent.focus", { terminal: agent.address });
  }

  gitBlock(t, space) {
    const info = this.git?.key === gitKey(space) ? this.git.info : null;
    const branch = info?.branch ?? space.branch; // before the first answer, or outside a repo: Herdr's own branch
    if (!branch) return null;
    const link = (id, tip, action) => h_flex().id(id).gap(6).items_center().cursor_pointer().hover((s) => s.text_color(t.foreground)).tooltip(tip).on_click(action);
    return h_flex().gap(8).items_center()
      .child(link("git-branch", "Branches & worktrees", () => this.openGit("branches"))
        .child(svg("mods/workspace-chrome/branch.svg").size(13)).child(branch))
      .when((info?.ahead ?? 0) > 0, (el) => el.child(div().text_color(t.ansi2).child(`↑${info.ahead}`)))
      .when((info?.behind ?? 0) > 0, (el) => el.child(div().text_color(t.ansi1).child(`↓${info.behind}`)))
      .when((info?.dirty ?? 0) > 0, (el) => el.child(link("git-dirty", "Changes", () => this.openGit("changes"))
        .child(div().text_color(t.ansi3).child(`● ${info.dirty}`))));
  }

  agentsBlock(t, list, states) {
    const counts = countAgents(list, states);
    const color = (kind) => t[AGENT_COLOR[kind]] ?? t.muted;
    const part = (kind, label, clickable) => {
      // "done" shares working's yellow, so it takes the sidebar's hourglass to tell them apart.
      const mark = kind === "cache" ? div().text_color(color(kind)).child("◷")
        : kind === "done" ? svg("mods/space-tree/hourglass.svg").size(11).flex_none().text_color(color(kind))
        : div().size(6).rounded(3).flex_none().bg(color(kind));
      const el = h_flex().id(`agents-${kind}`).gap(5).items_center().child(mark).child(label);
      return clickable ? el.cursor_pointer().hover((s) => s.text_color(t.foreground)).tooltip("Focus the next one").on_click(() => this.focusNext(kind)) : el;
    };
    const shown = [
      counts.working > 0 && part("working", `${counts.working} working`, false),
      counts.blocked > 0 && part("blocked", `${counts.blocked} needs you`, true),
      counts.done > 0 && part("done", `${counts.done} done`, true),
      counts.cache > 0 && part("cache", `${counts.cache}`, true),
    ].filter(Boolean);
    const row = h_flex().gap(6).items_center();
    if (!shown.length) return row.child(div().size(6).rounded(3).flex_none().bg(t.dim)).child(list.length ? `${list.length} idle` : "no agents");
    return row.children(withSeparators(shown, t));
  }

  limitsBlock(t, list, sp) {
    const address = this.limitsAddress(list, sp);
    if (!address || this.limits?.address !== address) return null;
    const view = visibleSummary(this.limits.summary, Date.now());
    if (!view) return null;
    const segments = view.segments.map((s, i) => {
      const tip = segmentTip(s, view);
      return div().id(`limit-${i}-${s.id}`).text_color(t[toneKey(s.tone)] ?? t.muted).when(Boolean(tip), (el) => el.tooltip(tip)).child(s.text);
    });
    return h_flex().gap(6).items_center().when(view.stale, (el) => el.opacity(0.5)).children(withSeparators(segments, t));
  }

  render() {
    const t = theme();
    const sp = spaces();
    const space = sp.find((s) => s.active);
    const all = agents();
    const mine = profileAgents(all, shared("space.owned"));
    const label = (icon, text) => h_flex().gap(6).items_center().child(svg(`mods/workspace-chrome/${icon}.svg`).size(13)).child(text);
    const git = space ? this.gitBlock(t, space) : null;
    const limits = this.limitsBlock(t, all, sp);
    return h_flex().size_full().px(14).gap(16).items_center().bg(t.chrome).border_t(1).border_color(t.border).text_size(11.8).text_color(t.muted)
      .child(label(space?.machine ? "server" : "monitor", space?.machine ?? "This computer"))
      .when(Boolean(git), (el) => el.child(git))
      .child(this.agentsBlock(t, mine, shared("agent.state") ?? {}))
      .when(Boolean(limits), (el) => el.child(limits))
      .child(div().flex_1())
      .child(div().id("palette").text_color(t.dim).on_click(() => run("app.palette")).child(this.hint(t)));
  }
}
