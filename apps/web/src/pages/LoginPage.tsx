import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { createProfile, getHealth, listProfiles } from "../api/client";
import { Button, ErrorNotice, Field, inputClass, Loading, Notice } from "../components/ui";
import { useProfile } from "../lib/profile";

function ServiceHealth() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  if (health.isLoading) return <Loading label="Checking local services…" />;
  if (health.isError) return <ErrorNotice error={health.error} />;
  const data = health.data!;
  const rows: [string, boolean, string][] = [
    [
      "Database (PostgreSQL + PostGIS)",
      data.database.reachable === true,
      data.database.reachable ? `PostGIS ${String(data.database.postgis_version)}` : "not reachable — is Docker running?",
    ],
    [
      "Local storage",
      data.storage.data_root_ok === true,
      data.storage.data_root_ok ? String(data.storage.data_root) : "data folder missing",
    ],
  ];
  return (
    <ul className="space-y-1 text-sm" aria-label="Service health">
      {rows.map(([label, ok, detail]) => (
        <li key={label}>
          <span className={ok ? "font-medium text-emerald-700" : "font-medium text-red-700"}>{ok ? "OK" : "Problem"}</span>{" "}
          · {label}: {detail}
        </li>
      ))}
      <li className="text-slate-600">Analysis models: none installed yet. Job worker: not part of this phase.</li>
    </ul>
  );
}

export function LoginPage() {
  const { setProfile } = useProfile();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: listProfiles });
  const target = (location.state as { from?: string } | null)?.from ?? "/dashboard";

  const enter = (profile: { id: string; display_name: string }) => {
    setProfile({ id: profile.id, display_name: profile.display_name });
    navigate(target, { replace: true });
  };

  const create = useMutation({
    mutationFn: (displayName: string) => createProfile(displayName),
    onSuccess: (profile) => {
      void queryClient.invalidateQueries({ queryKey: ["profiles"] });
      enter(profile);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (name.trim()) create.mutate(name.trim());
  };

  return (
    <main className="mx-auto max-w-xl space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Geospatial PoC</h1>
        <p className="text-slate-600">Local workspace</p>
      </div>

      <section className="space-y-2 rounded border border-slate-300 bg-white p-4">
        <h2 className="font-semibold">Services on this machine</h2>
        <ServiceHealth />
      </section>

      <section className="space-y-3 rounded border border-slate-300 bg-white p-4">
        <h2 className="font-semibold">Choose a profile</h2>
        <Notice>
          A profile is a name recorded against your changes in the audit history. It is not a login and gives no
          access control: anyone using this computer can pick any profile.
        </Notice>
        {profiles.isLoading ? <Loading /> : null}
        {profiles.isError ? <ErrorNotice error={profiles.error} /> : null}
        {profiles.data && profiles.data.items.length === 0 ? (
          <p className="text-sm text-slate-600">No profiles yet. Create the first one below.</p>
        ) : null}
        {profiles.data && profiles.data.items.length > 0 ? (
          <ul className="space-y-1" aria-label="Existing profiles">
            {profiles.data.items.map((profile) => (
              <li key={profile.id}>
                <Button variant="secondary" onClick={() => enter(profile)}>
                  Continue as {profile.display_name}
                </Button>
              </li>
            ))}
          </ul>
        ) : null}

        <form onSubmit={submit} className="space-y-2">
          <Field label="New profile name" htmlFor="profile-name">
            <input
              id="profile-name"
              className={inputClass}
              value={name}
              maxLength={80}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          {create.isError ? <ErrorNotice error={create.error} /> : null}
          <Button type="submit" disabled={!name.trim() || create.isPending}>
            Create profile and continue
          </Button>
        </form>
      </section>

      <section className="text-sm text-slate-600">
        <h2 className="font-semibold text-slate-800">Working offline</h2>
        <p>
          Projects, local files and review work without an internet connection. Online imagery discovery and place
          search need a configured online source and are switched off.
        </p>
      </section>
    </main>
  );
}
