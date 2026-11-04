import { useSyncExternalStore } from "react";

/**
 * A placeholder sign-in for demonstrations: one fixed email and password, checked in the browser.
 * It protects nothing. The credentials are in this file, which is sent to every browser, and there is no server-side
 * session, no one-time code and no account store. Real authentication would be a backend feature.
 */
export const DEMO_EMAIL = "demo@intgt.dev";
export const DEMO_PASSWORD = "Demo@1234";

const KEY = "geopoc.demo-signin";
const listeners = new Set<() => void>();
// Used only when browser storage is unavailable, so a sign-in still lasts for this page load.
let inMemory = false;

function read(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return inMemory;
  }
}

function write(value: boolean): void {
  inMemory = value;
  try {
    if (value) localStorage.setItem(KEY, "1");
    else localStorage.removeItem(KEY);
  } catch {
    // the sign-in then lasts only for this page load
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === KEY || event.key === null) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function credentialsMatch(email: string, password: string): boolean {
  return email.trim().toLowerCase() === DEMO_EMAIL && password === DEMO_PASSWORD;
}

/** Returns whether the credentials were accepted; signs in only when they were. */
export function signIn(email: string, password: string): boolean {
  if (!credentialsMatch(email, password)) return false;
  write(true);
  return true;
}

export function signOut(): void {
  write(false);
}

export function useSignedIn(): boolean {
  return useSyncExternalStore(subscribe, read, () => false);
}
