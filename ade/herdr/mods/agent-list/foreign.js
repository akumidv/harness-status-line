// Agents of spaces that another profile owns (see docs/profiles.md). Pure: no Herdr imports, so a test can load it.

// `owned` = shared("space.owned"): the ids this window owns, or null/undefined when profile mode is off or not
// loaded yet (then nothing is foreign).
export function isForeign(owned, spaceId) {
  return Array.isArray(owned) && !owned.includes(spaceId);
}

// A new array: own agents first, foreign agents last, the order inside each part kept.
export function foreignLast(list, owned) {
  if (!Array.isArray(owned)) return [...list];
  return [...list.filter((a) => !isForeign(owned, a.space)), ...list.filter((a) => isForeign(owned, a.space))];
}
