/** Initials and a steady hue for a name, so each profile or project has its own recognisable badge. */
export function badgeFor(name: string): { initials: string; hue: number } {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const initials = ((words[0]?.[0] ?? "?") + (words.length > 1 ? (words[words.length - 1]?.[0] ?? "") : "")).toUpperCase();
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return { initials, hue: hash };
}
