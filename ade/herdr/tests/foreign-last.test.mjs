// Run: node ade/herdr/tests/foreign-last.test.mjs   (pure logic of mods/agent-list/foreign.js)
import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// The mod file is a plain `.js` module without a package.json next to it: load a `.mjs` copy.
const dir = mkdtempSync(join(tmpdir(), "foreign-last-"));
copyFileSync(join(dirname(fileURLToPath(import.meta.url)), "../mods/agent-list/foreign.js"), join(dir, "foreign.mjs"));
const { isForeign, foreignLast } = await import(join(dir, "foreign.mjs"));

const agent = (address, space, pinned = false) => ({ address, space, pinned });
const addresses = (list) => list.map((a) => a.address);

// isForeign: only an array that lacks the space makes it foreign.
assert.equal(isForeign(null, "s1"), false, "profile mode off: nothing is foreign");
assert.equal(isForeign(undefined, "s1"), false, "not loaded yet: nothing is foreign");
assert.equal(isForeign(["s1", "s2"], "s1"), false);
assert.equal(isForeign(["s1", "s2"], "s9"), true);
assert.equal(isForeign([], "s1"), true, "an empty owned list owns nothing");

const list = [agent("a", "s9"), agent("b", "s1"), agent("c", "s2"), agent("d", "s8"), agent("e", "s1")];

// owned null or undefined: same order, but a new array.
for (const owned of [null, undefined]) {
  const same = foreignLast(list, owned);
  assert.deepEqual(addresses(same), ["a", "b", "c", "d", "e"]);
  assert.notEqual(same, list, "a new array comes back");
}

// Stable partition: own agents first, foreign last, the order inside each part kept.
const sorted = foreignLast(list, ["s1", "s2"]);
assert.deepEqual(addresses(sorted), ["b", "c", "e", "a", "d"]);
assert.deepEqual(addresses(list), ["a", "b", "c", "d", "e"], "the input is not changed");

// A pinned foreign agent still goes last, behind an unpinned own one.
const pinned = foreignLast([agent("p", "s9", true), agent("q", "s1"), agent("r", "s1", true)], ["s1"]);
assert.deepEqual(addresses(pinned), ["q", "r", "p"]);

// Everything foreign / everything own keeps the order.
assert.deepEqual(addresses(foreignLast(list, [])), ["a", "b", "c", "d", "e"]);
assert.deepEqual(addresses(foreignLast(list, ["s1", "s2", "s8", "s9"])), ["a", "b", "c", "d", "e"]);

// Empty list.
assert.deepEqual(foreignLast([], ["s1"]), []);
assert.deepEqual(foreignLast([], null), []);

console.log("foreign-last: ok");
