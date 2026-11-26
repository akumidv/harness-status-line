// Window tint: a profile's colour mixed into the theme's chrome colour, so a profile's window is told apart at a
// glance (taskbar preview, overlapping windows). Pure functions, no Herdr imports, so they run under node
// (ade/herdr/tests/tint.test.mjs). space-tree computes it and publishes shared("profile.tint"); tab-bar,
// agent-list and sidebar-chrome only read it. Design: docs/profiles.md ("Window tint").

// How much of the profile colour goes into the chrome colour: enough to read as the colour, dark enough to stay quiet.
export const TINT_SHARE = 0.24;

// "#rgb" / "#rrggbb" / "#rrggbbaa" -> [r, g, b] (0..255), or null for anything else.
export function parseHex(text) {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(typeof text === "string" ? text.trim() : "");
  if (!m) return null;
  let hex = m[1];
  if (hex.length === 3) hex = [...hex].map((c) => c + c).join("");
  return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
}

// The chrome colour with `share` of `color` mixed in, as "#rrggbb"; null when either is not a hex colour
// (the caller then keeps the plain chrome colour).
export function tintChrome(chrome, color, share = TINT_SHARE) {
  const base = parseHex(chrome);
  const mix = parseHex(color);
  if (!base || !mix) return null;
  const to = (i) => Math.round(base[i] * (1 - share) + mix[i] * share).toString(16).padStart(2, "0");
  return `#${to(0)}${to(1)}${to(2)}`;
}
