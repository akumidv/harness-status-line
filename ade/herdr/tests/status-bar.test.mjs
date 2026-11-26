// Run: node ade/herdr/tests/status-bar.test.mjs   (pure logic of mods/status-bar/status.js: fake git output, agents, summaries)
import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// The mod file is a plain `.js` module without a package.json next to it: load a `.mjs` copy.
const here = dirname(fileURLToPath(import.meta.url));
const dir = mkdtempSync(join(tmpdir(), "status-bar-"));
copyFileSync(join(here, "../mods/status-bar/status.js"), join(dir, "status.mjs"));
const {
  parseGitStatus, isForeign, profileAgents, stateOf, countAgents, nextAgent, AGENT_COLOR,
  isClaude, limitsAgent, STALE_MS, HIDDEN_MS, visibleSummary, formatAge, segmentTip, toneKey, terminalFile, summaryFile,
} = await import(join(dir, "status.mjs"));

const MIN = 60000;

// ---- git porcelain v2
const porcelain = [
  "# branch.oid 0123456789abcdef0123456789abcdef01234567",
  "# branch.head main",
  "# branch.upstream origin/main",
  "# branch.ab +2 -3",
  "1 .M N... 100644 100644 100644 aaa bbb src/a.js",
  "2 R. N... 100644 100644 100644 aaa bbb R100 new.js\told.js",
  "u UU N... 100644 100644 100644 100644 aaa bbb ccc conflict.js",
  "? untracked.txt",
  "",
].join("\n");
assert.deepEqual(parseGitStatus(porcelain), { branch: "main", ahead: 2, behind: 3, dirty: 4 });
assert.deepEqual(parseGitStatus("# branch.oid (initial)\n# branch.head feature/x\n"), { branch: "feature/x", ahead: 0, behind: 0, dirty: 0 }, "no upstream");
assert.deepEqual(parseGitStatus("# branch.oid abc\n# branch.head (detached)\n1 M. N... a b c d e f\n"), { branch: "(detached)", ahead: 0, behind: 0, dirty: 1 });
assert.deepEqual(parseGitStatus("# branch.head main\n# branch.ab junk\n! ignored.txt\n"), { branch: "main", ahead: 0, behind: 0, dirty: 0 }, "a bad ab line and ignored entries count nothing");
assert.equal(parseGitStatus(""), null, "no header");
assert.equal(parseGitStatus("fatal: not a git repository"), null);
assert.equal(parseGitStatus(undefined), null);

// ---- agents of this profile
const agent = (address, space, status, extra = {}) => ({ address, space, status, program: { key: "claude", agent: true }, app: "claude-code", focused: false, ...extra });
const list = [
  agent("t1", "s1", "working"),
  agent("t2", "s1", "blocked"),
  agent("t3", "s2", "done"),
  agent("t4", "s9", "blocked"), // another profile's space
  agent("t5", "s2", "blocked"),
  agent("t6", "s1", "idle", { program: { key: "shell", agent: false } }), // not an agent program
  agent("t7", "s2", "idle"),
];

// The copy of agent-list's isForeign is identical to its source.
const source = readFileSync(join(here, "../mods/agent-list/foreign.js"), "utf8");
const copy = readFileSync(join(here, "../mods/status-bar/status.js"), "utf8");
const fn = (text) => /export function isForeign[\s\S]*?\n}\n/.exec(text)[0];
assert.equal(fn(copy), fn(source), "isForeign is a verbatim copy of mods/agent-list/foreign.js");
assert.equal(isForeign(null, "s1"), false);
assert.equal(isForeign(["s1"], "s9"), true);

const addresses = (xs) => xs.map((a) => a.address);
assert.deepEqual(addresses(profileAgents(list, ["s1", "s2"])), ["t1", "t2", "t3", "t5", "t7"], "foreign spaces and non-agents out");
assert.deepEqual(addresses(profileAgents(list, null)), ["t1", "t2", "t3", "t4", "t5", "t7"], "profiles off: every agent program");
assert.deepEqual(profileAgents(list, []), [], "owns nothing");

// The state comes from agent.state when present (space-tree's model), else Herdr's status.
const states = { t3: { status: "idle", since: 0, cache: "warn" }, t7: { status: "idle", since: 0, cache: "expired" }, t1: { status: "working", since: 0, cache: null } };
assert.deepEqual(stateOf(list[2], states), states.t3);
assert.deepEqual(stateOf(list[1], states), { status: "blocked", since: 0, cache: null });
assert.deepEqual(stateOf(list[1], undefined), { status: "blocked", since: 0, cache: null });

const mine = profileAgents(list, ["s1", "s2"]);
assert.deepEqual(countAgents(mine, states), { working: 1, blocked: 2, done: 0, cache: 2 }, "t3 is idle by its state; warn and expired both count");
assert.deepEqual(countAgents(mine, {}), { working: 1, blocked: 2, done: 1, cache: 0 });
assert.deepEqual(countAgents([], states), { working: 0, blocked: 0, done: 0, cache: 0 });

// Cycling: after the focused one, wrapping; the first when the anchor is not of that kind; null when none.
assert.equal(nextAgent(mine, "blocked", states, undefined).address, "t2");
assert.equal(nextAgent(mine, "blocked", states, "t2").address, "t5");
assert.equal(nextAgent(mine, "blocked", states, "t5").address, "t2", "wraps");
assert.equal(nextAgent(mine, "blocked", states, "t1").address, "t2", "anchor of another kind");
assert.equal(nextAgent(mine, "cache", states, "t3").address, "t7");
assert.equal(nextAgent(mine, "cache", states, "t7").address, "t3");
assert.equal(nextAgent(mine, "done", states, null), null, "t3 is idle by its state");
assert.equal(nextAgent(mine, "done", {}, null).address, "t3");
assert.equal(nextAgent([agent("t2", "s1", "blocked")], "blocked", {}, "t2").address, "t2", "a single one stays");
assert.deepEqual(AGENT_COLOR, { working: "working", blocked: "done", done: "working", cache: "blocked" });

// ---- limits: which agent, which harness
assert.equal(isClaude({ program: { key: "claude" }, app: null }), true);
assert.equal(isClaude({ program: { key: "agent" }, app: "claude-code" }), true);
assert.equal(isClaude({ program: { key: "codex" }, app: "codex" }), false);
assert.equal(isClaude(null), false);
const focusedList = [agent("t1", "s1", "idle"), agent("t2", "s1", "idle", { focused: true })];
assert.equal(limitsAgent(focusedList, "t1").address, "t2", "the focused one wins");
assert.equal(limitsAgent([agent("t1", "s1", "idle"), agent("t3", "s1", "idle")], "t3").address, "t3", "else the last focused");
assert.equal(limitsAgent([agent("t1", "s1", "idle")], "t9"), null, "the last focused is gone");
assert.equal(limitsAgent([], null), null);

// ---- summary: staleness, segments, tones
const NOW = 1_800_000_000_000;
const summary = (ageMs, segments) => ({ v: 1, t: (NOW - ageMs) / 1000, account: "personal", segments });
const segs = [
  { id: "burn", text: "◆ 1.0×", tone: "mode-normal" },
  { id: "five", text: "5h 31% 1h23m", tone: "good", tip: "5-hour window" },
  { id: "empty", text: "", tone: "bad" },
  { id: "wk", text: "wk 47%", tone: "strange" },
];
const fresh = visibleSummary(summary(2 * MIN, segs), NOW);
assert.equal(fresh.stale, false);
assert.equal(fresh.account, "personal");
assert.equal(fresh.ageMs, 2 * MIN);
assert.deepEqual(fresh.segments.map((s) => s.id), ["burn", "five", "wk"], "empty text dropped, order kept");
assert.deepEqual(fresh.segments[0], { id: "burn", text: "◆ 1.0×", tone: "mode-normal", tip: "" });
assert.equal(segmentTip(fresh.segments[1], fresh), "5-hour window");
assert.equal(segmentTip(fresh.segments[0], fresh), "");
assert.equal(visibleSummary(summary(STALE_MS, segs), NOW).stale, false, "exactly 10 min is not stale");
const stale = visibleSummary(summary(STALE_MS + MIN, segs), NOW);
assert.equal(stale.stale, true);
assert.equal(segmentTip(stale.segments[1], stale), "5-hour window · updated 11m ago");
assert.equal(segmentTip(stale.segments[0], stale), "updated 11m ago");
assert.notEqual(visibleSummary(summary(HIDDEN_MS, segs), NOW), null, "exactly 1 h still shows");
assert.equal(visibleSummary(summary(HIDDEN_MS + 1000, segs), NOW), null, "older than 1 h hides");
assert.equal(visibleSummary(summary(-5 * MIN, segs), NOW).ageMs, 0, "a clock ahead is age 0");
assert.equal(visibleSummary(summary(MIN, [{ id: "x", text: "" }]), NOW), null, "nothing to show");
for (const bad of [null, "x", {}, { t: 1, segments: "x" }, { segments: [] }, { t: "now", segments: segs }]) assert.equal(visibleSummary(bad, NOW), null);
assert.equal(STALE_MS, 10 * MIN);
assert.equal(HIDDEN_MS, 60 * MIN);

assert.equal(formatAge(0), "0m");
assert.equal(formatAge(59 * MIN + 59000), "59m");
assert.equal(formatAge(65 * MIN), "1h05m");
assert.equal(formatAge(-1), "0m");

const tones = { none: "muted", dim: "dim", good: "ansi2", cool: "ansi6", warn: "ansi3", bad: "ansi1", "mode-eco": "ansi6", "mode-normal": "muted", "mode-peak": "ansi1" };
for (const [tone, key] of Object.entries(tones)) assert.equal(toneKey(tone), key, tone);
assert.equal(toneKey("strange"), "muted");
assert.equal(toneKey("toString"), "muted", "no inherited keys");
assert.equal(toneKey(undefined), "muted");

// ---- file paths
assert.equal(terminalFile("1000", "t32"), "/tmp/claude-sl-1000/terminals/t32.json");
assert.equal(summaryFile({ v: 1, dir: "/tmp/claude-sl-1000" }), "/tmp/claude-sl-1000/summary.json");
assert.equal(summaryFile({ dir: "/tmp/claude-sl-1000/personal/" }), "/tmp/claude-sl-1000/personal/summary.json");
for (const bad of [null, {}, { dir: "relative/dir" }, { dir: 7 }, "x"]) assert.equal(summaryFile(bad), null);

console.log("status-bar: ok");
