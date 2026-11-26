// Run: node ade/herdr/tests/stop.test.mjs   (pure logic of mods/agent-list/stop.js, fake agents and clock)
import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const dir = mkdtempSync(join(tmpdir(), "stop-"));
copyFileSync(join(dirname(fileURLToPath(import.meta.url)), "../mods/agent-list/stop.js"), join(dir, "stop.mjs"));
const { REST_MS, SAVE_TIMEOUT_MS, COMPACT_TIMEOUT_MS, stopTargets, startItem, advance, summary } = await import(join(dir, "stop.mjs"));

// Targets: working, Claude, local, owned. Not blocked / idle / done, not other programs, not remote.
const a = (address, status, over = {}) => ({ address, status, space: "s1", program: { key: "claude" }, ...over });
const list = [a("t1", "working"), a("t2", "blocked"), a("t3", "idle"), a("t4", "done"), a("t5", "working", { program: { key: "codex" } }),
  a("vps/t6", "working"), a("t7", "working", { space: "s2" })];
assert.deepEqual(stopTargets(list, ["s1"]).map((x) => x.address), ["t1"]);
assert.deepEqual(stopTargets(list, null).map((x) => x.address), ["t1", "t7"], "profiles off: every space counts");

// saving: work resets the rest timer; REST_MS of rest sends /compact.
let item = startItem("x", 0);
let r = advance(item, "working", 1000, null);
assert.equal(r.send, null); assert.equal(r.item.phase, "saving");
r = advance(r.item, "idle", 5000, null); assert.equal(r.item.restSince, 5000);
r = advance(r.item, "working", 8000, null); assert.equal(r.item.restSince, null, "a queued message started: rest begins again");
r = advance(r.item, "done", 9000, null);
r = advance(r.item, "done", 9000 + REST_MS - 1, null); assert.equal(r.send, null);
r = advance(r.item, "done", 9000 + REST_MS, null);
assert.equal(r.send, "compact"); assert.equal(r.item.phase, "compacting"); assert.equal(r.item.sentAt, 9000 + REST_MS);
// A question is never answered: blocked keeps waiting and restarts the rest timer.
let b = advance(startItem("x", 0), "idle", 100, null).item;
b = advance(b, "blocked", 200, null); assert.equal(b.item.restSince, null); assert.equal(b.send, null);
// saving timeout
let s = advance(startItem("x", 0), "working", SAVE_TIMEOUT_MS + 1, null);
assert.equal(s.item.phase, "saving", "a working agent is never timed out while it works");
s = advance(startItem("x", 0), "idle", SAVE_TIMEOUT_MS + 1, null);
assert.equal(s.item.phase, "failed", "resting for too long without an answer fails");
assert.equal(advance(startItem("x", 0), "blocked", SAVE_TIMEOUT_MS + 1, null).item.phase, "saving", "blocked: waits for you");

// compacting: a marker finishes it; else work then REST_MS of rest; else a timeout.
const c0 = { name: "x", phase: "compacting", sentAt: 100000, restSince: null, worked: false, note: null };
assert.equal(advance(c0, "idle", 101000, 100500).item.phase, "ready", "marker after the send");
assert.equal(advance(c0, "idle", 101000, 98000).item.phase, "ready", "marker within the slack");
assert.equal(advance(c0, "idle", 101000, 50000).item.phase, "compacting", "an old marker does not count");
let c = advance(c0, "working", 102000, null).item; assert.equal(c.worked, true);
c = advance(c, "idle", 103000, null).item; assert.equal(c.phase, "compacting");
c = advance(c, "idle", 103000 + REST_MS, null).item; assert.equal(c.phase, "ready"); assert.match(c.note, /no compact marker/);
assert.equal(advance(c0, "idle", 100000 + COMPACT_TIMEOUT_MS + 1, null).item.phase, "failed");
assert.equal(advance({ ...c0, phase: "ready" }, "idle", 1, null).item.phase, "ready", "ready stays");

// summary
const sm = summary({ a: { phase: "ready" }, b: { phase: "compacting" }, c: { phase: "failed" } });
assert.deepEqual(sm, { ready: 1, total: 3, over: false });
assert.equal(summary({ a: { phase: "ready" }, c: { phase: "failed" } }).over, true);
assert.equal(summary({}).over, true);
assert.deepEqual(summary({ a: { phase: "ready" }, b: { phase: "skipped" } }), { ready: 1, total: 1, over: true }, "skipped is not counted");

console.log("stop: ok");
