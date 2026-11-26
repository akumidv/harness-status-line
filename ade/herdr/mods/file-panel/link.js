// Ctrl-click on a file path in a terminal: open it in the right panel's file-panel.
// A folder path (no extension, or a trailing "/") opens as a folder in the Files tree instead.
// Herdr already resolved most paths (link.path); the rest are paths relative to some other
// folder than the terminal's cwd (an agent prints `guardrails/x.md` while sitting in the
// parent folder, or in a worktree). Those are looked up in the space folder, the repo root
// and the cwd's direct subfolders.
import { exec, spaces, setShared, run, home } from "herdr";

const strip = (raw) => {
  let p = raw.replace(/^file:\/\/(localhost)?/, "");
  try { p = decodeURIComponent(p); } catch {}
  const m = /^(.*?)(?::(\d+)(?:[:-](\d+))?|#L(\d+)(?:-L?\d+)?)?$/.exec(p.replace(/^["'`(<]+|["'`)>.,;]+$/g, ""));
  return { path: m[1], line: Number(m[2] ?? m[4]) || undefined, column: Number(m[3]) || undefined };
};

// $1 = the relative path, then candidate folders: each folder itself first, then its direct
// subfolders (and .worktrees/*), so `guardrails/x.md` printed from ~/workspace finds akmon/guardrails/x.md.
// $1 = test flag (-f file, -d folder).
const SCRIPT = `
t=$1; rel=$2; shift 2
for d in "$@"; do [ $t "$d/$rel" ] && exec realpath -s -- "$d/$rel"; done
for d in "$@"; do for s in "$d"/*/ "$d"/.worktrees/*/; do [ $t "$s$rel" ] && exec realpath -s -- "$s$rel"; done; done
exit 1`;

async function resolve(rel, source, test = "-f") {
  if (rel.startsWith("~/")) rel = home() + rel.slice(1);
  if (rel.startsWith("/")) return rel;
  const on = source.machine ?? null, cwd = source.cwd ?? null;
  const space = spaces().find((s) => s.id === source.space);
  const top = cwd ? await exec("git", ["rev-parse", "--show-toplevel"], { cwd, on }).catch(() => null) : null;
  const dirs = [...new Set([cwd, top?.code === 0 ? top.stdout.trim() : null, space?.folder].filter(Boolean))];
  if (!dirs.length) return null;
  const r = await exec("sh", ["-c", SCRIPT, "resolve", test, rel, ...dirs], { on, timeout: 5000 }).catch(() => null);
  return r?.code === 0 ? r.stdout.trim().split("\n")[0] : null;
}

// Same extension-less names the mod.json file rule accepts; any other extension-less path is not ours.
const NAMED = /(^|\/)(Dockerfile|Makefile|Justfile|LICENSE|README|AGENTS|CLAUDE|CHANGELOG)[^/]*$/;

const isDir = async (path, on) => (await exec("test", ["-d", path], { on, timeout: 5000 }).catch(() => null))?.code === 0;

function openInPanel(request) {
  setShared("file-panel.open", { ...request, at: Date.now() });
  run("layout.show", { id: "panel" });
  run("layout.select", { stack: "panel-body", child: "files" });
}

export async function handleLink({ link, source }) {
  if (link.kind !== "file") return "pass";
  const parsed = strip(link.raw ?? "");
  const on = source.machine ?? null;
  const known = link.path ?? (await resolve(parsed.path, source));
  const folder = known ? ((await isDir(known, on)) ? known : null) : await resolve(parsed.path, source, "-d");
  if (folder) { openInPanel({ path: folder.replace(/(.)\/+$/, "$1"), dir: true, on }); return "handled"; }
  const path = known;
  if (!path) return "pass";
  if (!/\.[^/]+$/.test(path) && !NAMED.test(path)) return "pass";
  openInPanel({ path, on, line: link.line ?? parsed.line, column: link.column ?? parsed.column });
  return "handled";
}
