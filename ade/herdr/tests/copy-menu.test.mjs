// Run: node ade/herdr/tests/copy-menu.test.mjs   (pure logic of mods/file-panel/copy-menu.js and its copy in git-panel)
import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const mods = join(dirname(fileURLToPath(import.meta.url)), "../mods");
const source = join(mods, "file-panel/copy-menu.js"), copy = join(mods, "git-panel/copy-menu.js");

// Mods cannot import each other: git-panel keeps a byte-identical copy of file-panel's module.
assert.ok(readFileSync(source).equals(readFileSync(copy)), "git-panel/copy-menu.js must be a byte-identical copy of file-panel/copy-menu.js");

// The mod file is a plain `.js` module without a package.json next to it: load a `.mjs` copy.
const dir = mkdtempSync(join(tmpdir(), "copy-menu-"));
copyFileSync(source, join(dir, "copy-menu.mjs"));
const { MENU_PAD, MENU_TITLE_H, MENU_ROW_H, relativePath, joinPath, baseName, subjectOf, htmlText, pathMenu, branchMenu, commitMenu, hasItems, menuHeight } = await import(join(dir, "copy-menu.mjs"));

const flat = (m) => m.groups.flatMap((g) => g.items.map((it) => [g.title, it.label, it.text]));

// relativePath: under the root, the root itself, outside it, sibling prefixes, trailing separators, "/" and Windows roots.
assert.equal(relativePath("/r/p/src/a.js", "/r/p"), "src/a.js");
assert.equal(relativePath("/r/p/src/a.js", "/r/p/"), "src/a.js");
assert.equal(relativePath("/r/p", "/r/p"), ".");
assert.equal(relativePath("/r/p/", "/r/p"), ".");
assert.equal(relativePath("/r/p2/a.js", "/r/p"), null, "a sibling with the same prefix is not under the root");
assert.equal(relativePath("/elsewhere/a.js", "/r/p"), null);
assert.equal(relativePath("/etc/hosts", "/"), "etc/hosts");
assert.equal(relativePath("C:\\work\\p\\src\\a.js", "C:\\work\\p"), "src\\a.js");
assert.equal(relativePath("", "/r"), null);
assert.equal(relativePath("/r/a", null), null);

// joinPath keeps the root's separator and never doubles it.
assert.equal(joinPath("/r/p", "src/a.js"), "/r/p/src/a.js");
assert.equal(joinPath("/r/p/", "src/a.js"), "/r/p/src/a.js");
assert.equal(joinPath("/r/p", "."), "/r/p");
assert.equal(joinPath("C:\\work\\p", "a.js"), "C:\\work\\p\\a.js");

assert.equal(baseName("/r/p/src/a.js"), "a.js");
assert.equal(baseName("/r/p/src/"), "src");
assert.equal(baseName("src\\a.js"), "a.js");
assert.equal(baseName(undefined), "");

assert.equal(subjectOf("Fix the thing\n\nLonger body\nmore"), "Fix the thing");
assert.equal(subjectOf("  one line  \n"), "one line");
assert.equal(subjectOf(undefined), "");
assert.equal(htmlText('a < b & "c"\nnext'), "a &lt; b &amp; &quot;c&quot;<br>next");

// Files: full path and path from the project root; git-panel adds the name.
assert.deepEqual(flat(pathMenu("/r/p/src/a.js", "/r/p")), [
  ["Path", "Copy full path", "/r/p/src/a.js"],
  ["Path", "Copy path from project root", "src/a.js"],
]);
assert.deepEqual(flat(pathMenu("/r/p/src", "/r/p")).map((x) => x[2]), ["/r/p/src", "src"], "a folder works the same");
assert.deepEqual(flat(pathMenu("/r/p/src/a.js", "/r/p", { name: true })), [
  ["Path", "Copy full path", "/r/p/src/a.js"],
  ["Path", "Copy path from project root", "src/a.js"],
  ["Path", "Copy name", "a.js"],
]);
assert.equal(flat(pathMenu("/x/a.js", "/r/p"))[1][2], "/x/a.js", "outside the root: the full path stands in");

// Branch and commit menus; empty values leave their item out.
assert.deepEqual(flat(branchMenu("feature/x")), [["Branch", "Copy branch name", "feature/x"]]);
assert.equal(hasItems(branchMenu(null)), false);
const full = "0123456789abcdef0123456789abcdef01234567";
assert.deepEqual(flat(commitMenu({ hash: full, short: "0123456", subject: "Add copy menus" })), [
  ["Commit", "Copy hash", full],
  ["Commit", "Copy short hash", "0123456"],
  ["Commit", "Copy subject", "Add copy menus"],
]);
assert.equal(flat(commitMenu({ hash: full, subject: "s" }))[1][2], "0123456", "short hash falls back to 7 characters");
assert.deepEqual(flat(commitMenu({ hash: full, short: "0123456", subject: "" })).map((x) => x[1]), ["Copy hash", "Copy short hash"]);
assert.equal(hasItems(commitMenu({ hash: "", subject: "" })), false);
assert.equal(hasItems(undefined), false);

// The popup's height: padding, one title per non-empty group, one row per item.
assert.equal(menuHeight(pathMenu("/r/p/a", "/r/p")), MENU_PAD * 2 + MENU_TITLE_H + 2 * MENU_ROW_H);
assert.equal(menuHeight(commitMenu({ hash: full, subject: "s" })), MENU_PAD * 2 + MENU_TITLE_H + 3 * MENU_ROW_H);
assert.equal(menuHeight({ groups: [{ title: "Empty", items: [] }, ...branchMenu("b").groups] }), MENU_PAD * 2 + MENU_TITLE_H + MENU_ROW_H);

// Menus travel to the popup as overlay props: they must survive JSON.
const m = pathMenu("/r/p/a", "/r/p", { name: true });
assert.deepEqual(JSON.parse(JSON.stringify(m)), m);

console.log("copy-menu: ok");
