// Run: node ade/herdr/tests/profile-filter.test.mjs   (pure logic of mods/space-tree/profile.js: fake spaces and profiles)
import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// The mod file is a plain `.js` module without a package.json next to it: load a `.mjs` copy.
const dir = mkdtempSync(join(tmpdir(), "profile-filter-"));
copyFileSync(join(dirname(fileURLToPath(import.meta.url)), "../mods/space-tree/profile.js"), join(dir, "profile.mjs"));
const { parseProfiles, validProfilesText, ownerOf, visibleSpaces, claimable, withClaims, pickByIndex, pickNext } = await import(join(dir, "profile.mjs"));

const space = (id, active = false) => ({ id, name: `n-${id}`, active });
const NOBODY = { default: null, spaces: {} };

// parseProfiles: a bad file is "nobody owns anything", never an exception; bad entries are dropped, other keys kept.
for (const bad of ["", "not json", "null", "[]", "42", '"x"', "{"]) assert.deepEqual(parseProfiles(bad), NOBODY, `bad text: ${bad}`);
assert.deepEqual(parseProfiles('{"default":"a","spaces":{"s1":"b"}}'), { default: "a", spaces: { s1: "b" } });
assert.deepEqual(parseProfiles('{"default":3,"spaces":[1]}'), NOBODY, "wrong shapes");
assert.deepEqual(parseProfiles('{"default":"","spaces":{"s1":"","s2":7,"s3":null,"s4":"b"}}'), { default: null, spaces: { s4: "b" } });
assert.deepEqual(parseProfiles('{"default":"a","launchers":{"b":"x"}}'), { default: "a", spaces: {}, launchers: { b: "x" } }, "other keys survive");
assert.notEqual(parseProfiles("").spaces, parseProfiles("").spaces, "the empty result is not shared");
assert.equal(validProfilesText('{"default":"a"}'), true);
assert.equal(validProfilesText("{"), false);
assert.equal(validProfilesText("[]"), false);

// ownerOf: an entry wins, else the default (which may be null).
const p = { default: "main", spaces: { s2: "work-a" } };
assert.equal(ownerOf(p, "s1"), "main");
assert.equal(ownerOf(p, "s2"), "work-a");
assert.equal(ownerOf(NOBODY, "s1"), null);
assert.equal(ownerOf({ default: "a", spaces: parseProfiles('{"spaces":{"__proto__":"x"}}').spaces }, "toString"), "a", "no inherited keys");

// visibleSpaces: no name = every space (the same list); a name keeps the owned ones in order; nobody owns = nothing.
const all = [space("s1"), space("s2"), space("s3"), space("s4")];
const q = { default: "main", spaces: { s2: "work-a", s4: "work-a" } };
assert.equal(visibleSpaces(all, q, null), all);
assert.equal(visibleSpaces(all, q, ""), all);
assert.deepEqual(visibleSpaces(all, q, "main").map((s) => s.id), ["s1", "s3"]);
assert.deepEqual(visibleSpaces(all, q, "work-a").map((s) => s.id), ["s2", "s4"]);
assert.deepEqual(visibleSpaces(all, q, "work-b"), []);
assert.deepEqual(visibleSpaces(all, NOBODY, "work-a"), [], "an unreadable file shows nothing in a profiled window");

// claimable: active, not at start, no entry, a profiled window that is not the default.
const start = new Set(["s1", "s2"]);
const fresh = [space("s1", true), space("s2"), space("s3", true), space("s4"), space("s5", true)];
const owners = { default: "main", spaces: { s5: "work-b" } };
assert.deepEqual(claimable(fresh, owners, "work-a", start), ["s3"], "s1 was there at start, s4 is not active, s5 has an owner");
assert.deepEqual(claimable(fresh, owners, "main", start), [], "the default profile claims nothing");
assert.deepEqual(claimable(fresh, owners, null, start), [], "profiles off claims nothing");
assert.deepEqual(claimable(fresh, owners, "", start), []);
assert.deepEqual(claimable([space("s3")], owners, "work-a", start), [], "not active here");
assert.deepEqual(claimable(fresh, owners, "work-a", ["s1", "s3"]), [], "an array of start ids works too");
assert.deepEqual(claimable(fresh, NOBODY, "work-a", start), ["s3", "s5"], "no default: every new active space is claimable");

// withClaims: a new object, the argument and its nested map untouched, other keys and old entries kept.
const before = { default: "main", spaces: { s5: "work-b" }, launchers: { cto: "x" } };
const frozen = JSON.stringify(before);
const claimed = withClaims(before, ["s3", "s6"], "work-a");
assert.deepEqual(claimed, { default: "main", spaces: { s5: "work-b", s3: "work-a", s6: "work-a" }, launchers: { cto: "x" } });
assert.equal(JSON.stringify(before), frozen);
assert.notEqual(claimed, before);
assert.notEqual(claimed.spaces, before.spaces);
assert.deepEqual(withClaims(before, [], "work-a"), before);

// pickByIndex: 1-based; out of range, zero and junk give null.
const visible = [space("s1"), space("s3"), space("s7")];
assert.equal(pickByIndex(visible, 1), "s1");
assert.equal(pickByIndex(visible, 3), "s7");
assert.equal(pickByIndex(visible, 4), null);
assert.equal(pickByIndex(visible, 0), null);
assert.equal(pickByIndex(visible, -1), null);
assert.equal(pickByIndex(visible, 1.5), null);
assert.equal(pickByIndex([], 1), null);

// pickNext: wraps both ways; a foreign (or no) active space gives the first for +1 and the last for -1.
assert.equal(pickNext(visible, "s1", 1), "s3");
assert.equal(pickNext(visible, "s7", 1), "s1", "wraps forward");
assert.equal(pickNext(visible, "s3", -1), "s1");
assert.equal(pickNext(visible, "s1", -1), "s7", "wraps back");
assert.equal(pickNext(visible, "s9", 1), "s1", "foreign active: next is the first");
assert.equal(pickNext(visible, "s9", -1), "s7", "foreign active: previous is the last");
assert.equal(pickNext(visible, null, 1), "s1");
assert.equal(pickNext([space("s1")], "s1", 1), "s1", "a single space stays");
assert.equal(pickNext([space("s1")], "s1", -1), "s1");
assert.equal(pickNext([], "s1", 1), null);

console.log("profile-filter: ok");
