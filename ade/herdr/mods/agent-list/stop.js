// "Prepare to stop": send the profile's checkpoint prompt to every working Claude Code agent, then `/compact`,
// and say when each one is ready. Pure functions, no Herdr imports, so they run under node
// (ade/herdr/tests/stop.test.mjs). The mod (main.js) does the sending; design: docs/left-panel.md.

// Used when the profile has no `stop_prompt` (herdr-profiles.json, written by the launcher as <home>/profile.stop_prompt).
export const DEFAULT_PROMPT =
  "Save a checkpoint of the current work (what is done, what is in progress, next steps, open questions) where this project keeps its handoff, then stop. Start no new work.";
// An agent counts as finished with a step after resting this long (a queued message starts within a second or two).
export const REST_MS = 10 * 1000;
export const SAVE_TIMEOUT_MS = 20 * 60 * 1000;
export const COMPACT_TIMEOUT_MS = 5 * 60 * 1000;
// A compact marker written at most this long before `/compact` was sent still counts (clock and file-time slack).
const MARKER_SLACK_MS = 2000;

// The agents to prepare: working Claude Code agents on this computer in this window's spaces. Not blocked ones
// (typed text would answer their question), not idle or done ones, not other harnesses (`/compact` is Claude's).
// `owned` = shared("space.owned") (null: profiles off, every space counts).
export function stopTargets(list, owned) {
  return list.filter((a) => a.status === "working" && a.program?.key === "claude" && !a.address.includes("/") && (!Array.isArray(owned) || owned.includes(a.space)));
}

// A fresh item for a target whose prompt was just sent.
export const startItem = (name, now) => ({ name, phase: "saving", sentAt: now, restSince: null, worked: false, note: null });

const resting = (status) => status !== "working" && status !== "blocked";

// One step of an item's life. `status` = the agent's status now, `markerMs` = its compact marker time (ms) or null.
// Returns { item, send }: `send` is "compact" when `/compact` must be sent now (the item is already "compacting").
// phases: saving -> compacting -> ready; failed ends an item that got no answer in time or could not be sent to;
// skipped is set by the mod (stopped before its prompt was sent).
export function advance(item, status, now, markerMs) {
  if (item.phase === "saving") {
    if (!resting(status)) return { item: { ...item, restSince: null }, send: null };
    if (status === "blocked") return { item: { ...item, restSince: null }, send: null };
    const restSince = item.restSince ?? now;
    if (now - restSince >= REST_MS) return { item: { ...item, phase: "compacting", sentAt: now, restSince: null, worked: false }, send: "compact" };
    if (now - item.sentAt > SAVE_TIMEOUT_MS) return { item: { ...item, phase: "failed", note: "no answer to the checkpoint prompt" }, send: null };
    return { item: { ...item, restSince }, send: null };
  }
  if (item.phase === "compacting") {
    if (Number.isFinite(markerMs) && markerMs >= item.sentAt - MARKER_SLACK_MS) return { item: { ...item, phase: "ready" }, send: null };
    if (status === "working") return { item: { ...item, worked: true, restSince: null }, send: null };
    if (resting(status) && item.worked) {
      const restSince = item.restSince ?? now;
      if (now - restSince >= REST_MS) return { item: { ...item, phase: "ready", note: "no compact marker, but it is resting" }, send: null };
      return { item: { ...item, restSince }, send: null };
    }
    if (now - item.sentAt > COMPACT_TIMEOUT_MS) return { item: { ...item, phase: "failed", note: "no confirmation of /compact" }, send: null };
  }
  return { item, send: null };
}

// "2/3 ready" and whether the run is over (every item ready, failed or skipped). A skipped item (the agent had
// stopped before the prompt could be sent) is not counted: it needed nothing.
export function summary(items) {
  const all = Object.values(items);
  const counted = all.filter((i) => i.phase !== "skipped");
  const ready = counted.filter((i) => i.phase === "ready").length;
  return { ready, total: counted.length, over: all.every((i) => i.phase === "ready" || i.phase === "failed" || i.phase === "skipped") };
}
