/**
 * Per-browser conveniences kept in localStorage: the last opened project and whether the sidebar is collapsed.
 * Nothing here is shared, sent anywhere, or relied on for correctness; unreadable storage just means defaults.
 */
const LAST_PROJECT = "geopoc.lastProject";
const SIDEBAR = "geopoc.sidebarCollapsed";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // the preference lasts only for this page load
  }
}

export interface LastProject {
  id: string;
  name: string;
}

export function loadLastProject(): LastProject | null {
  const raw = read(LAST_PROJECT);
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (value && typeof value === "object" && "id" in value && "name" in value) return value as LastProject;
  } catch {
    // ignore a malformed entry
  }
  return null;
}

export function saveLastProject(project: LastProject | null): void {
  write(LAST_PROJECT, project ? JSON.stringify(project) : null);
}

export function loadSidebarCollapsed(): boolean {
  return read(SIDEBAR) === "true";
}

export function saveSidebarCollapsed(collapsed: boolean): void {
  write(SIDEBAR, collapsed ? "true" : "false");
}
