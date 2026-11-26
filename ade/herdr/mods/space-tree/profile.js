// Profiles: which spaces a window shows. Pure functions, no Herdr imports, so they run under node
// (ade/herdr/tests/profile-filter.test.mjs). The design is in docs/profiles.md; space-tree/main.js reads
// `<appHome>/profile.name` and `~/.local/state/ai_env/herdr/profiles.json` and feeds them here.
//
// profiles.json = { "default": "<profile>", "spaces": { "<spaceId>": "<profile>" } }
// owner(id) = spaces[id] ?? default. Spaces are core-wide, so every window lists all of them; a window
// with a profile name shows only the spaces it owns, a window without one shows everything.

const isObject = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const isName = (value) => typeof value === "string" && value !== "";

// A missing, unreadable or invalid file owns nothing: a profiled window then shows nothing rather than everything.
const EMPTY = Object.freeze({ default: null, spaces: Object.freeze({}) });

// Whether `text` is a JSON object, i.e. something parseProfiles reads rather than discards.
export function validProfilesText(text) {
  try {
    return isObject(JSON.parse(text));
  } catch {
    return false;
  }
}

// The file's text -> { default: name | null, spaces: { id: name }, ...other keys kept as they are }. Never throws;
// entries of the wrong shape are dropped. Unknown top-level keys survive so a rewrite does not lose them.
export function parseProfiles(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    return { ...EMPTY, spaces: {} };
  }
  if (!isObject(data)) return { ...EMPTY, spaces: {} };
  const owned = isObject(data.spaces) ? Object.entries(data.spaces).filter(([, name]) => isName(name)) : [];
  return { ...data, default: isName(data.default) ? data.default : null, spaces: Object.fromEntries(owned) };
}

// The profile that owns space `id`, or null when nobody does.
export function ownerOf(profiles, id) {
  return Object.hasOwn(profiles.spaces, id) ? profiles.spaces[id] : profiles.default;
}

// `spaces` = spaces() in Herdr's order; the ones `name` owns, same order. No name: profiles are off and
// the list itself comes back (callers do not change it).
export function visibleSpaces(spaces, profiles, name) {
  return name ? spaces.filter((s) => ownerOf(profiles, s.id) === name) : spaces;
}

// Ids this window should claim: spaces that are active here, did not exist when the mod started
// (`startIds`, a Set of ids), have no owner entry yet; only in a profiled window that is not the default one
// (the default owns every unlisted space already).
export function claimable(spaces, profiles, name, startIds) {
  if (!name || name === profiles.default) return [];
  const start = startIds instanceof Set ? startIds : new Set(startIds);
  return spaces.filter((s) => s.active && !start.has(s.id) && !Object.hasOwn(profiles.spaces, s.id)).map((s) => s.id);
}

// A copy of `profiles` in which `name` owns `ids`; the argument is left alone.
export function withClaims(profiles, ids, name) {
  return { ...profiles, spaces: { ...profiles.spaces, ...Object.fromEntries(ids.map((id) => [id, name])) } };
}

// The id of the `n`th (from 1) space of `visible`, null when there is none.
export function pickByIndex(visible, n) {
  return Number.isInteger(n) && n >= 1 ? (visible[n - 1]?.id ?? null) : null;
}

// The space after (`dir` > 0) or before (`dir` < 0) `activeId` in `visible`, wrapping around. An active space that
// is not in the list (another profile's) gives the first for next and the last for previous. Null for an empty list.
export function pickNext(visible, activeId, dir) {
  if (!visible.length) return null;
  const at = visible.findIndex((s) => s.id === activeId);
  if (at < 0) return (dir > 0 ? visible[0] : visible[visible.length - 1]).id;
  return visible[(at + (dir > 0 ? 1 : -1) + visible.length) % visible.length].id;
}
