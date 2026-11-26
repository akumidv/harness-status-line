// Run: node ade/herdr/tests/tint.test.mjs   (pure logic of mods/space-tree/tint.js)
import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const dir = mkdtempSync(join(tmpdir(), "tint-"));
copyFileSync(join(dirname(fileURLToPath(import.meta.url)), "../mods/space-tree/tint.js"), join(dir, "tint.mjs"));
const { TINT_SHARE, parseHex, tintChrome } = await import(join(dir, "tint.mjs"));

assert.deepEqual(parseHex("#3b6fd4"), [59, 111, 212]);
assert.deepEqual(parseHex("#fff"), [255, 255, 255]);
assert.deepEqual(parseHex(" #102030ff "), [16, 32, 48], "alpha is ignored");
for (const bad of ["", "red", "#12", "#12345", "rgb(1,2,3)", null, undefined, 5]) assert.equal(parseHex(bad), null, String(bad));

assert.equal(tintChrome("#000000", "#ffffff", 0.5), "#808080");
assert.equal(tintChrome("#101010", "#101010"), "#101010", "mixing a colour with itself changes nothing");
assert.equal(tintChrome("#151515", "#3b6fd4"), "#1e2b43", "default share on a dark chrome");
assert.equal(TINT_SHARE, 0.24);
assert.equal(tintChrome("#151515", "nope"), null);
assert.equal(tintChrome(undefined, "#3b6fd4"), null);
assert.equal(tintChrome("#151515", "#3b6fd4", 0), "#151515");

console.log("tint: ok");
