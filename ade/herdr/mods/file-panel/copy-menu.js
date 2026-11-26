// Right-click copy menus of the right panel: what each menu offers and the text each item copies.
// Pure (no herdr / gpui imports). file-panel/copy-menu.js is the source; git-panel/copy-menu.js is a
// byte-identical copy (mods cannot import each other; tests/copy-menu.test.mjs keeps them identical).
// A menu is {groups: [{title, items: [{label, text}]}]}; it travels to the popup as overlay props.

// The popup's geometry: its width and the heights its rows are drawn with (menuHeight sums them).
export const MENU_WIDTH = 240, MENU_PAD = 5, MENU_TITLE_H = 22, MENU_ROW_H = 28;

const SEP = /[\\/]+$/;

// `path` relative to `root` ("." for the root itself), or null when it is not under it.
// Either separator is accepted, so a Windows-style root works as well as a POSIX one.
export function relativePath(path, root) {
  if (!path || !root) return null;
  const base = root.replace(SEP, "") || root;
  if (path === base || path.replace(SEP, "") === base) return ".";
  for (const sep of ["/", "\\"]) {
    const head = base.endsWith(sep) ? base : base + sep;
    if (path.startsWith(head)) return path.slice(head.length);
  }
  return null;
}

// `rel` under `root`, with the root's own separator (a root with backslashes only gets "\").
export function joinPath(root, rel) {
  const sep = root.includes("\\") && !root.includes("/") ? "\\" : "/";
  return rel === "." || !rel ? root : root.replace(SEP, "") + sep + rel;
}

// The last part of a path ("" for none).
export function baseName(path) {
  return (path ?? "").replace(SEP, "").split(/[\\/]/).pop() ?? "";
}

// The first line of a commit message.
export function subjectOf(message) {
  return (message ?? "").trim().split("\n")[0].trim();
}

// Plain text as TextView HTML: escaped, line breaks kept.
export function htmlText(text) {
  return (text ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/\n/g, "<br>");
}

const menu = (title, items) => ({ groups: [{ title, items: items.filter((it) => it.text) }] });

// A file or folder: its full path and its path from the project root (`root`: the git repository root
// that holds it, else the Files root); `name: true` adds the bare name.
export function pathMenu(abs, root, { name = false } = {}) {
  const rel = relativePath(abs, root) ?? abs;
  return menu("Path", [
    { label: "Copy full path", text: abs },
    { label: "Copy path from project root", text: rel },
    ...(name ? [{ label: "Copy name", text: baseName(abs) }] : []),
  ]);
}

export function branchMenu(name) {
  return menu("Branch", [{ label: "Copy branch name", text: name ?? "" }]);
}

// A commit: {hash (full), short (git's abbreviation; first 7 when absent), subject}.
export function commitMenu({ hash, short, subject }) {
  return menu("Commit", [
    { label: "Copy hash", text: hash ?? "" },
    { label: "Copy short hash", text: short || (hash ?? "").slice(0, 7) },
    { label: "Copy subject", text: subject ?? "" },
  ]);
}

export function hasItems(m) {
  return Boolean(m?.groups?.some((g) => g.items.length));
}

// The popup's height for a menu: padding, one title per group, one row per item.
export function menuHeight(m) {
  const groups = m.groups.filter((g) => g.items.length);
  return MENU_PAD * 2 + groups.reduce((h, g) => h + MENU_TITLE_H + g.items.length * MENU_ROW_H, 0);
}
