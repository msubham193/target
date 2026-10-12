import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";

export interface StoredProfile {
  id: string;
  display_name: string;
}

const KEY = "geopoc.profile";

export function loadProfile(): StoredProfile | null {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === "object" && "id" in parsed && "display_name" in parsed) {
      return parsed as StoredProfile;
    }
  } catch {
    // unreadable storage means no remembered profile
  }
  return null;
}

function storeProfile(profile: StoredProfile | null): void {
  try {
    if (profile) localStorage.setItem(KEY, JSON.stringify(profile));
    else localStorage.removeItem(KEY);
  } catch {
    // the profile then lasts only for this page load
  }
}

interface ProfileContextValue {
  profile: StoredProfile | null;
  setProfile: (profile: StoredProfile | null) => void;
}

const ProfileContext = createContext<ProfileContextValue | null>(null);

export function ProfileProvider({ children }: { children: ReactNode }) {
  const [profile, setState] = useState<StoredProfile | null>(() => loadProfile());
  const setProfile = useCallback((next: StoredProfile | null) => {
    storeProfile(next);
    setState(next);
  }, []);
  const value = useMemo(() => ({ profile, setProfile }), [profile, setProfile]);
  return <ProfileContext.Provider value={value}>{children}</ProfileContext.Provider>;
}

export function useProfile(): ProfileContextValue {
  const value = useContext(ProfileContext);
  if (!value) throw new Error("useProfile must be used inside ProfileProvider");
  return value;
}
