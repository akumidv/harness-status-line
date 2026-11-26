// Agent state: when each agent's status last changed, and from that the prompt-cache level.
// Pure functions, no Herdr imports, so they run under node (ade/herdr/tests/agent-state.test.mjs).
// The model is in docs/left-panel.md ("Agent state model"). shared("agent.since") and
// shared("agent.state") are written by the sidebar mod only; this file just computes them.

// The prompt cache is assumed to live an hour: warn at 40 min of not working, expired at 60.
export const CACHE_WARN_MS = 40 * 60 * 1000;
export const CACHE_EXPIRED_MS = 60 * 60 * 1000;
// After `/compact`, `/clear` or a new session the context is small, so a cold prompt cache costs next to nothing
// and "waiting for you" is no news. A PostCompact hook and a SessionStart hook (startup|clear) leave a marker
// (the time, per terminal); an agent that went to rest
// at most this long after the marker, or was already resting, sleeps (status "sleeping", no cache chip) until it
// works again.
export const COMPACT_GRACE_MS = 60 * 1000;
// A gone terminal's stamp is kept this long (it may come back after a restart).
const FORGET_MS = 30 * 86400000;

// done and idle count as one: looking at a finished agent turns done into idle, and looking is not
// the agent doing anything (same rule as the shipped `agents` mod).
const folded = (status) => (status === "done" ? "idle" : status);

// null while working or under 40 min since the last change, then "warn", then "expired".
export function cacheLevel(status, at, now) {
  if (status === "working") return null;
  const idle = now - at;
  if (idle >= CACHE_EXPIRED_MS) return "expired";
  return idle >= CACHE_WARN_MS ? "warn" : null;
}

// True when an agent resting since `at` (ms) is covered by the compact marker `marker` (ms, or undefined).
export function quietAfterCompact(status, at, marker) {
  if (!Number.isFinite(marker) || status === "working" || status === "blocked") return false;
  return at <= marker + COMPACT_GRACE_MS;
}

// `known` = shared("agent.since") {<address>: {s, at}}, `list` = agents(). Returns the next map and
// whether it differs: a new status gets a new `at`, an unchanged one keeps its stamp. Terminals that
// are gone and old are dropped, except while the list is empty (right after a start).
export function trackSince(known, list, now) {
  const base = known && typeof known === "object" && !Array.isArray(known) ? known : {};
  const next = {};
  let changed = base !== known;
  for (const a of list) {
    const s = folded(a.status);
    const old = base[a.address];
    if (old && old.s === s && Number.isFinite(old.at)) next[a.address] = old;
    else {
      next[a.address] = { s, at: now };
      changed = true;
    }
  }
  const listed = new Set(list.map((a) => a.address));
  for (const [address, stamp] of Object.entries(base)) {
    if (listed.has(address)) continue;
    if (list.length && now - (stamp?.at ?? 0) > FORGET_MS) changed = true;
    else next[address] = stamp;
  }
  return { since: changed ? next : known, changed };
}

// shared("agent.state") {<address>: {status, since, cache}} for the listed agents, keys sorted so
// an unchanged state compares equal as text. `status` is Herdr's own (done stays done), except for an
// agent quiet after a compact: it is "sleeping" with no cache chip. `compacted` = {<address>: marker ms}.
export function publishState(since, list, now, compacted = {}) {
  const out = {};
  for (const a of [...list].sort((x, y) => (x.address < y.address ? -1 : 1))) {
    const at = since[a.address]?.at ?? now;
    if (quietAfterCompact(a.status, at, compacted[a.address])) out[a.address] = { status: "sleeping", since: at, cache: null };
    else out[a.address] = { status: a.status, since: at, cache: cacheLevel(a.status, at, now) };
  }
  return out;
}

// What an aggregate (space, group, machine) shows: the most urgent interaction among `states`
// (asks > waits > working > idle > sleeping: not in URGENCY, so a lead only when all sleep) plus the cache chip
// of the one that lost it first. Null for none.
const URGENCY = { blocked: 4, done: 3, working: 2, idle: 1 };
export function worstOf(states) {
  let lead = null;
  let chip = null;
  for (const st of states) {
    if (!lead || (URGENCY[st.status] ?? 0) > (URGENCY[lead.status] ?? 0)) lead = st;
    if (!st.cache) continue;
    if (!chip || (st.cache === "expired" && chip.cache === "warn") || (st.cache === chip.cache && st.since < chip.since)) chip = st;
  }
  return lead && { status: lead.status, since: chip ? chip.since : lead.since, cache: chip ? chip.cache : null };
}
