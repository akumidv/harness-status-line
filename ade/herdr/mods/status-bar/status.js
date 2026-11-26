// Pure logic of the status bar (docs/central-window.md "Status bar"). No Herdr imports, so it runs under node
// (ade/herdr/tests/status-bar.test.mjs); main.js reads Herdr and files and feeds them here.

// ---- Git of the active space -------------------------------------------------------------------

// `git status --porcelain=v2 --branch` output -> { branch, ahead, behind, dirty }. `branch` is the head's name,
// "(detached)" without one; ahead/behind are 0 without an upstream; `dirty` counts changed, renamed,
// unmerged and untracked entries (what the Changes panel lists). Null for text that has no branch header.
export function parseGitStatus(text) {
  if (typeof text !== "string") return null;
  let branch = null, ahead = 0, behind = 0, dirty = 0;
  for (const line of text.split("\n")) {
    if (line.startsWith("# branch.head ")) branch = line.slice("# branch.head ".length).trim();
    else if (line.startsWith("# branch.ab ")) {
      const ab = /^\+(\d+) -(\d+)$/.exec(line.slice("# branch.ab ".length).trim());
      if (ab) { ahead = Number(ab[1]); behind = Number(ab[2]); }
    } else if (/^[12u?] /.test(line)) dirty++;
  }
  return branch ? { branch, ahead, behind, dirty } : null;
}

// ---- Agents of this profile ----------------------------------------------------------------------

// ---- Copied verbatim from mods/agent-list/foreign.js (mod folders cannot import each other; keep the
// ---- copies identical). `owned` = shared("space.owned"), written by space-tree from its profile filter
// ---- (profile.js visibleSpaces): the ids this window owns, null/undefined when profiles are off.
export function isForeign(owned, spaceId) {
  return Array.isArray(owned) && !owned.includes(spaceId);
}
// ---- end of the copy

// The agents this window counts: agent programs (as space-tree and tab-bar count them) of spaces this
// profile owns, in agents() order.
export function profileAgents(list, owned) {
  return list.filter((a) => a.program?.agent && !isForeign(owned, a.space));
}

// An agent's state: shared("agent.state")'s entry (space-tree's model), else Herdr's own status.
export function stateOf(agent, states) {
  return states?.[agent.address] ?? { status: agent.status, since: 0, cache: null };
}

// Whether `agent` belongs to `kind`: "blocked" (needs you), "done", "working" by its state's status;
// "cache" when its cache is warn or expired.
export function isKind(agent, kind, states) {
  const st = stateOf(agent, states);
  return kind === "cache" ? st.cache === "warn" || st.cache === "expired" : st.status === kind;
}

// { working, blocked, done, cache } counts over `list` (already the profile's agents).
export function countAgents(list, states) {
  const counts = { working: 0, blocked: 0, done: 0, cache: 0 };
  for (const a of list) for (const kind of Object.keys(counts)) if (isKind(a, kind, states)) counts[kind]++;
  return counts;
}

// The agent to focus for a click on `kind`: the one after `anchor` (an address) among the matching
// agents of `list`, wrapping; the first when `anchor` is not one of them. Null when none matches.
export function nextAgent(list, kind, states, anchor) {
  const matches = list.filter((a) => isKind(a, kind, states));
  if (!matches.length) return null;
  const at = matches.findIndex((a) => a.address === anchor);
  return matches[(at + 1) % matches.length];
}

// Theme key per count in the agents block, as in the sidebar's agent-state model (docs/left-panel.md):
// blue (t.done) for asks, yellow (t.working, the sidebar's hourglass) for waits, red (t.blocked, Herdr's red) only for
// the cache chip.
export const AGENT_COLOR = Object.freeze({ working: "working", blocked: "done", done: "working", cache: "blocked" });

// ---- Limits of the focused agent's harness ----------------------------------------------------------

// A harness whose limits the bar shows: Claude Code only for now.
export function isClaude(agent) {
  return agent?.program?.key === "claude" || agent?.app === "claude-code";
}

// The agent whose limits are shown: the focused one, else the last focused one (`last`, an address) while it
// still exists. Null when there is neither.
export function limitsAgent(list, last) {
  return list.find((a) => a.focused) ?? list.find((a) => a.address === last) ?? null;
}

export const STALE_MS = 10 * 60 * 1000;
export const HIDDEN_MS = 60 * 60 * 1000;

// summary.json (parsed) -> { account, ageMs, stale, segments: [{ id, text, tone, tip }] } at `now` (ms), or null
// when it is not a summary, has nothing to show or is older than an hour. `t` is epoch seconds.
export function visibleSummary(summary, now) {
  if (!summary || typeof summary !== "object" || !Array.isArray(summary.segments) || !Number.isFinite(summary.t)) return null;
  const ageMs = Math.max(0, now - summary.t * 1000);
  if (ageMs > HIDDEN_MS) return null;
  const segments = summary.segments
    .filter((s) => s && typeof s.text === "string" && s.text !== "")
    .map((s) => ({ id: String(s.id ?? ""), text: s.text, tone: typeof s.tone === "string" ? s.tone : "none", tip: typeof s.tip === "string" ? s.tip : "" }));
  if (!segments.length) return null;
  return { account: typeof summary.account === "string" ? summary.account : "", ageMs, stale: ageMs > STALE_MS, segments };
}

// "12m" under an hour, "1h05m" from it.
export function formatAge(ms) {
  const min = Math.floor(Math.max(0, ms) / 60000);
  return min < 60 ? `${min}m` : `${Math.floor(min / 60)}h${String(min % 60).padStart(2, "0")}m`;
}

// A segment's tooltip: its tip, plus the age when the summary is stale.
export function segmentTip(segment, view) {
  const age = view.stale ? `updated ${formatAge(view.ageMs)} ago` : "";
  return [segment.tip, age].filter(Boolean).join(" · ");
}

// Theme key per summary tone (the tones and their thresholds are the status-line script's).
const TONE_KEY = Object.freeze({
  none: "muted", dim: "dim", good: "ansi2", cool: "ansi6", warn: "ansi3", bad: "ansi1",
  "mode-eco": "ansi6", "mode-normal": "muted", "mode-peak": "ansi1",
});
export function toneKey(tone) {
  return Object.hasOwn(TONE_KEY, tone) ? TONE_KEY[tone] : "muted";
}

// The terminal's pointer file and the summary it names. `dir` must be an absolute path.
export function terminalFile(uid, address) {
  return `/tmp/claude-sl-${uid}/terminals/${address}.json`;
}
export function summaryFile(pointer) {
  const dir = pointer && typeof pointer === "object" ? pointer.dir : null;
  return typeof dir === "string" && dir.startsWith("/") ? `${dir.replace(/\/+$/, "")}/summary.json` : null;
}
