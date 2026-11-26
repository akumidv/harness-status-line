// Run: node ade/herdr/tests/agent-state.test.mjs   (pure logic of mods/space-tree/agent-state.js, fake agents and clock)
import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// The mod file is a plain `.js` module without a package.json next to it: load a `.mjs` copy.
const dir = mkdtempSync(join(tmpdir(), "agent-state-"));
copyFileSync(join(dirname(fileURLToPath(import.meta.url)), "../mods/space-tree/agent-state.js"), join(dir, "agent-state.mjs"));
const { CACHE_WARN_MS, CACHE_EXPIRED_MS, cacheLevel, trackSince, publishState, worstOf, quietAfterCompact, COMPACT_GRACE_MS } = await import(join(dir, "agent-state.mjs"));

const MIN = 60000;
const agent = (address, status) => ({ address, status });

// A new status gets a new stamp, an unchanged one keeps it, done <-> idle does not restart.
let r = trackSince(undefined, [agent("t1", "working"), agent("t2", "done")], 1000);
assert.equal(r.changed, true);
assert.deepEqual(r.since, { t1: { s: "working", at: 1000 }, t2: { s: "idle", at: 1000 } });
const first = r.since;
r = trackSince(first, [agent("t1", "working"), agent("t2", "idle")], 5000);
assert.equal(r.changed, false, "unchanged and done<->idle write nothing");
assert.equal(r.since, first, "the same object comes back when nothing changed");
r = trackSince(first, [agent("t1", "idle"), agent("t2", "idle")], 9000);
assert.equal(r.changed, true);
assert.deepEqual(r.since.t1, { s: "idle", at: 9000 });
assert.equal(r.since.t2, first.t2, "other stamps are kept");
r = trackSince(r.since, [agent("t1", "working"), agent("t2", "idle")], 10000);
assert.deepEqual(r.since.t1, { s: "working", at: 10000 }, "back to working restarts");

// Gone terminals: kept while young, kept when the list is empty (start-up), pruned when old.
const old = { t9: { s: "idle", at: 0 }, t1: { s: "idle", at: 0 } };
r = trackSince(old, [agent("t1", "idle")], 40 * 86400000);
assert.equal(r.changed, true);
assert.deepEqual(Object.keys(r.since), ["t1"]);
r = trackSince(old, [], 40 * 86400000);
assert.equal(r.changed, false, "an empty list prunes nothing");
r = trackSince(old, [agent("t1", "idle")], 1000);
assert.equal(r.changed, false, "a young gone terminal stays");
assert.ok("t9" in r.since);
assert.equal(trackSince("junk", [], 1).changed, true, "a broken value is replaced");

// Cache levels at the thresholds.
assert.equal(CACHE_WARN_MS, 40 * MIN);
assert.equal(CACHE_EXPIRED_MS, 60 * MIN);
assert.equal(cacheLevel("working", 0, 99 * MIN), null);
for (const status of ["idle", "done", "blocked"]) {
  assert.equal(cacheLevel(status, 0, CACHE_WARN_MS - 1), null);
  assert.equal(cacheLevel(status, 0, CACHE_WARN_MS), "warn");
  assert.equal(cacheLevel(status, 0, 60 * MIN - 1), "warn");
  assert.equal(cacheLevel(status, 0, 60 * MIN), "expired");
}

// Published state: Herdr's own status, the stamp, the level, sorted keys.
const stamps = { t2: { s: "idle", at: 0 }, t1: { s: "working", at: 0 } };
const state = publishState(stamps, [agent("t2", "done"), agent("t1", "working")], 50 * MIN);
assert.deepEqual(Object.keys(state), ["t1", "t2"]);
assert.deepEqual(state.t1, { status: "working", since: 0, cache: null });
assert.deepEqual(state.t2, { status: "done", since: 0, cache: "warn" });
assert.equal(JSON.stringify(publishState(stamps, [agent("t1", "working"), agent("t2", "done")], 50 * MIN)), JSON.stringify(state));

// After a compact: resting within the grace of the marker (or before it) is quiet, work or asks never.
const MARK = 1000 * MIN;
assert.equal(quietAfterCompact("done", MARK + 5000, MARK), true);
assert.equal(quietAfterCompact("idle", MARK - 3 * 60 * MIN, MARK), true, "already resting before the marker");
assert.equal(quietAfterCompact("idle", MARK + COMPACT_GRACE_MS + 1, MARK), false, "rested again after later work");
assert.equal(quietAfterCompact("working", MARK, MARK), false);
assert.equal(quietAfterCompact("blocked", MARK, MARK), false, "a question is never hidden");
assert.equal(quietAfterCompact("done", 0, undefined), false);
const quiet = publishState({ t1: { s: "idle", at: MARK + 2000 }, t2: { s: "idle", at: 0 } }, [agent("t1", "done"), agent("t2", "done")], MARK + 90 * MIN, { t1: MARK });
assert.deepEqual(quiet.t1, { status: "sleeping", since: MARK + 2000, cache: null });
assert.equal(worstOf([quiet.t1, { status: "sleeping", since: 0, cache: null }]).status, "sleeping", "all asleep: the aggregate sleeps");
assert.equal(worstOf([quiet.t1, { status: "idle", since: 0, cache: null }]).status, "idle", "one awake agent wakes the aggregate");
assert.equal(quiet.t2.status, "done", "no marker, no change");
assert.equal(quiet.t2.cache, "expired");

// Aggregation: asks > waits > working > idle, the chip rides along (expired before warn, older first).
assert.equal(worstOf([]), null);
const st = (status, since = 0, cache = null) => ({ status, since, cache });
assert.deepEqual(worstOf([st("idle"), st("working"), st("done"), st("blocked")]), st("blocked"));
assert.deepEqual(worstOf([st("idle"), st("working")]), st("working"));
assert.deepEqual(worstOf([st("blocked"), st("idle", 5, "warn")]), st("blocked", 5, "warn"), "chip of another agent is kept");
assert.deepEqual(worstOf([st("idle", 5, "warn"), st("done", 9, "expired"), st("idle", 2, "expired")]), st("done", 2, "expired"));

// Mods cannot import each other: tab-bar keeps a verbatim copy of space-tree's agent state marks block.
const mods = join(dirname(fileURLToPath(import.meta.url)), "../mods");
const marksBlock = (file) => /^const MARK_SIZE[\s\S]*?^function stateColor/m.exec(readFileSync(join(mods, file), "utf8"))?.[0];
assert.ok(marksBlock("space-tree/main.js"), "space-tree/main.js has the agent state marks block");
assert.equal(marksBlock("tab-bar/main.js"), marksBlock("space-tree/main.js"), "tab-bar's marks block must equal space-tree's");

console.log("agent-state: ok");
