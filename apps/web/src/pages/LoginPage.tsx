import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ApiError, createProfile, listProfiles } from "../api/client";
import { useHealth } from "../lib/queries";
import {
  IconChevronRight,
  IconCube,
  IconImage,
  IconList,
  IconMap,
  IconReport,
  IconSatellite,
  IconVideo,
  IconWifiOff,
} from "../components/icons";
import { Button, ErrorNotice, Field, inputClass, Loading, Notice } from "../components/ui";
import { badgeFor } from "../lib/avatar";
import { DEMO_EMAIL, DEMO_PASSWORD, signIn, useSignedIn } from "../lib/auth";
import { readMapboxToken, satelliteBackdropUrl } from "../lib/mapbox";
import { useProfile } from "../lib/profile";
import terrainUrl from "../assets/login-terrain.svg";

function ServiceHealth() {
  const health = useHealth();
  if (health.isLoading) return <Loading label="Checking local services…" />;
  if (health.isError) return <ErrorNotice error={health.error} />;
  const data = health.data!;
  const rows: [string, boolean | null, string][] = [
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
    [
      "Background worker",
      data.worker.running ? true : null,
      data.worker.running ? "running" : "not running: previews and analysis wait until it is started",
    ],
  ];
  return (
    <ul className="space-y-1.5 text-[13px]" aria-label="Service health">
      {rows.map(([label, ok, detail]) => (
        <li key={label} className="flex items-start gap-2.5 rounded-md border border-line/60 bg-raised/50 px-3 py-2">
          <span
            aria-hidden="true"
            className={`mt-1.5 size-2 shrink-0 rounded-full ${ok === true ? "bg-ok shadow-[0_0_8px_var(--color-ok)]" : ok === false ? "bg-danger" : "bg-warn"}`}
          />
          <span className={`w-20 shrink-0 font-medium ${ok === true ? "text-ok" : ok === false ? "text-danger" : "text-warn"}`}>
            {ok === true ? "OK" : ok === false ? "Problem" : "Not running"}
          </span>{" "}
          <span className="min-w-0 wrap-anywhere text-fg-muted">
            · {label}: {detail}
          </span>
        </li>
      ))}
    </ul>
  );
}

const CAPABILITIES = [
  { label: "Map", icon: IconMap },
  { label: "Photos", icon: IconImage },
  { label: "Satellite Images", icon: IconSatellite },
  { label: "Videos", icon: IconVideo },
  { label: "3D Terrain", icon: IconCube },
  { label: "Findings", icon: IconList },
  { label: "Reports", icon: IconReport },
];

/**
 * Placeholder email and password sign-in. One fixed demo account, checked in the browser: it is for show and protects
 * nothing, and the screen says so.
 */
function SignInCard() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [touched, setTouched] = useState(false);
  const [rejected, setRejected] = useState(false);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (!email.trim() || !password) return;
    setRejected(!signIn(email, password));
  };
  const emailError = touched && !email.trim() ? "Enter your email." : null;
  const passwordError = touched && !password ? "Enter your password." : null;

  return (
    <section
      className="rounded-2xl border border-line/80 bg-surface/80 p-6 shadow-panel ring-1 ring-white/5 backdrop-blur-xl"
      aria-labelledby="signin-heading"
    >
      <h2 id="signin-heading" className="text-lg font-semibold text-fg">
        Sign in
      </h2>
      <p className="mt-1.5 text-[13px] leading-relaxed text-fg-muted">Use the demo account to open the workspace.</p>
      <form onSubmit={submit} className="mt-5 space-y-3" noValidate aria-label="Sign in">
        <Field label="Email" htmlFor="signin-email" error={emailError}>
          <input
            id="signin-email"
            type="email"
            className={inputClass}
            value={email}
            autoComplete="username"
            aria-invalid={Boolean(emailError)}
            onChange={(event) => {
              setEmail(event.target.value);
              setRejected(false);
            }}
          />
        </Field>
        <Field label="Password" htmlFor="signin-password" error={passwordError}>
          <div className="relative">
            <input
              id="signin-password"
              type={visible ? "text" : "password"}
              className={`${inputClass} pr-16`}
              value={password}
              autoComplete="current-password"
              aria-invalid={Boolean(passwordError)}
              onChange={(event) => {
                setPassword(event.target.value);
                setRejected(false);
              }}
            />
            <button
              type="button"
              className="absolute inset-y-0 right-2 my-auto h-7 rounded px-2 text-xs text-fg-subtle hover:text-fg"
              aria-pressed={visible}
              onClick={() => setVisible((value) => !value)}
            >
              {visible ? "Hide" : "Show"}
            </button>
          </div>
        </Field>
        {rejected ? (
          <p className="text-xs text-danger" role="alert">
            The email or password is not correct.
          </p>
        ) : null}
        <Button type="submit" className="w-full">
          Sign in
        </Button>
      </form>
      <div className="mt-4 rounded-lg border border-line/70 bg-raised/50 p-3 text-xs text-fg-muted" data-testid="demo-credentials">
        <p>
          Demo account: <span className="font-medium text-fg">{DEMO_EMAIL}</span> · <span className="font-medium text-fg">{DEMO_PASSWORD}</span>
        </p>
        <p className="mt-1 text-fg-subtle">
          A placeholder for demonstrations. These details are in the page's code, so this sign-in protects nothing.
        </p>
        <button
          type="button"
          className="mt-2 text-xs font-medium text-accent hover:underline"
          onClick={() => {
            setEmail(DEMO_EMAIL);
            setPassword(DEMO_PASSWORD);
            setRejected(false);
          }}
        >
          Fill in the demo details
        </button>
      </div>
    </section>
  );
}

/**
 * Entry to the local workspace. There is no authentication in this application: a profile is only a name
 * recorded against changes in the audit history, so this screen has no password and claims no security.
 */
export function LoginPage() {
  const { setProfile } = useProfile();
  const signedIn = useSignedIn();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [touched, setTouched] = useState(false);
  // Dark satellite picture behind the page when a Mapbox public token is set and the picture arrives; otherwise the contour
  // pattern alone. Nothing here depends on it, and the app itself still works offline.
  const backdropUrl = useMemo(() => {
    const token = readMapboxToken();
    return token.ok ? satelliteBackdropUrl(token.token) : null;
  }, []);
  const [backdropReady, setBackdropReady] = useState(false);
  useEffect(() => {
    if (!backdropUrl) return;
    const image = new Image();
    image.onload = () => setBackdropReady(true);
    image.src = backdropUrl;
    return () => {
      image.onload = null;
    };
  }, [backdropUrl]);
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

  const trimmed = name.trim();
  const validation = touched && !trimmed ? "Enter a name for the profile." : null;
  const conflict = create.error instanceof ApiError && create.error.code === "PROFILE_EXISTS";

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (trimmed) create.mutate(trimmed);
  };

  return (
    <div className="relative isolate min-h-screen overflow-hidden bg-canvas text-fg">
      {backdropUrl ? (
        <div
          aria-hidden="true"
          data-testid="login-satellite"
          className="login-drift pointer-events-none absolute inset-0 -z-20 bg-cover bg-center transition-opacity duration-1000"
          style={{
            backgroundImage: `url("${backdropUrl}")`,
            filter: "brightness(0.32) saturate(0.75) contrast(1.05)",
            opacity: backdropReady ? 1 : 0,
          }}
        />
      ) : null}
      {/* A locally drawn contour map in the accent colour: no image is downloaded, and it follows the theme. */}
      <div
        aria-hidden="true"
        data-testid="login-background"
        className="pointer-events-none absolute inset-0 -z-10 bg-accent opacity-30"
        style={{
          maskImage: `url(${terrainUrl})`,
          WebkitMaskImage: `url(${terrainUrl})`,
          maskSize: "cover",
          WebkitMaskSize: "cover",
          maskPosition: "center",
          WebkitMaskPosition: "center",
          maskRepeat: "no-repeat",
          WebkitMaskRepeat: "no-repeat",
        }}
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          background:
            "radial-gradient(ellipse at 20% 30%, rgb(91 155 255 / 0.10), transparent 55%), radial-gradient(ellipse at 50% 40%, transparent 35%, var(--color-canvas) 115%)",
        }}
      />

      <div className="mx-auto grid min-h-screen w-full max-w-6xl items-center gap-10 px-5 py-10 lg:grid-cols-[minmax(0,1fr)_26rem] lg:gap-16 lg:px-8">
        <header className="login-rise space-y-6">
          <div className="flex items-center gap-3">
            <span
              className="flex size-11 items-center justify-center rounded-xl bg-accent text-base font-bold text-accent-fg shadow-[0_0_28px_rgb(91_155_255/0.45)]"
              aria-hidden="true"
            >
              I
            </span>
            <p className="text-[13px] font-medium uppercase tracking-[0.18em] text-fg-muted">Local workspace</p>
          </div>
          <div className="space-y-3">
            <h1 className="text-3xl font-semibold leading-tight tracking-tight text-fg sm:text-4xl lg:text-5xl">
              INT and TGT <span className="bg-gradient-to-r from-accent to-ok bg-clip-text text-transparent">Development</span>
            </h1>
            <p className="max-w-xl text-[15px] leading-relaxed text-fg-muted">
              Geospatial observation, imagery analysis and evidence review in one place. Your projects and files stay on this computer.
            </p>
          </div>
          <ul className="hidden flex-wrap gap-2 lg:flex" aria-label="What you can do here">
            {CAPABILITIES.map(({ label, icon: Icon }, index) => (
              <li
                key={label}
                className="login-rise flex items-center gap-2 rounded-full border border-line/70 bg-surface/60 px-3 py-1.5 text-xs text-fg-muted backdrop-blur-sm"
                style={{ animationDelay: `${150 + index * 60}ms` }}
              >
                <Icon size={14} className="text-accent" />
                {label}
              </li>
            ))}
          </ul>
          <p className="hidden items-center gap-2 text-xs text-fg-subtle lg:flex">
            <IconWifiOff size={14} />
            Works offline. An internet connection is only used for the optional online basemap.
          </p>
        </header>

        <main className="login-rise w-full space-y-4" style={{ animationDelay: "120ms" }}>
          {!signedIn ? <SignInCard /> : null}
          {signedIn ? (
          <section
            className="rounded-2xl border border-line/80 bg-surface/80 p-6 shadow-panel ring-1 ring-white/5 backdrop-blur-xl"
            aria-labelledby="profile-heading"
          >
            <h2 id="profile-heading" className="text-lg font-semibold text-fg">
              Choose a profile
            </h2>
            <p className="mt-1.5 text-[13px] leading-relaxed text-fg-muted">
              A profile is a name recorded against your changes in the audit history. It is not a login and gives no
              access control: anyone using this computer can pick any profile. There is no password.
            </p>

            <div className="mt-5 space-y-2">
              {profiles.isLoading ? <Loading label="Loading profiles…" /> : null}
              {profiles.isError ? <ErrorNotice error={profiles.error} /> : null}
              {profiles.data && profiles.data.items.length === 0 ? (
                <p className="text-[13px] text-fg-subtle">No profiles yet. Create the first one below.</p>
              ) : null}
              {profiles.data && profiles.data.items.length > 0 ? (
                <ul className="space-y-2" aria-label="Existing profiles">
                  {profiles.data.items.map((profile) => {
                    const badge = badgeFor(profile.display_name);
                    return (
                      <li key={profile.id}>
                        <button
                          type="button"
                          onClick={() => enter(profile)}
                          className="group flex w-full items-center gap-3 rounded-xl border border-line bg-raised/70 px-3 py-2.5 text-left text-[13px] text-fg transition-all duration-200 hover:-translate-y-px hover:border-accent/60 hover:bg-overlay hover:shadow-[0_6px_18px_rgb(0_0_0/0.3)]"
                        >
                          <span
                            aria-hidden="true"
                            className="flex size-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
                            style={{ background: `hsl(${badge.hue} 60% 45% / 0.22)`, color: `hsl(${badge.hue} 80% 78%)` }}
                          >
                            {badge.initials}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-sm">Continue as {profile.display_name}</span>
                          <IconChevronRight
                            size={16}
                            className="shrink-0 text-fg-subtle transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-accent"
                          />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </div>

            <form onSubmit={submit} className="mt-5 space-y-3 border-t border-line pt-5" noValidate>
              <Field label="New profile name" htmlFor="profile-name" error={validation} hint="1 to 80 characters. Shown in the audit history.">
                <input
                  id="profile-name"
                  className={inputClass}
                  value={name}
                  maxLength={80}
                  autoComplete="off"
                  aria-invalid={Boolean(validation)}
                  onChange={(event) => setName(event.target.value)}
                  onBlur={() => setTouched(Boolean(name))}
                />
              </Field>
              {conflict ? (
                <Notice tone="error" title="A profile with this name already exists">
                  Choose it from the list above, or pick another name.
                </Notice>
              ) : create.isError ? (
                <ErrorNotice error={create.error} />
              ) : null}
              <Button type="submit" className="w-full" disabled={create.isPending}>
                {create.isPending ? "Creating…" : "Create profile and continue"}
              </Button>
            </form>
          </section>
          ) : null}

          <section className="rounded-2xl border border-line/80 bg-surface/70 p-5 backdrop-blur-xl" aria-labelledby="services-heading">
            <h2 id="services-heading" className="mb-3 text-sm font-semibold text-fg">
              Services on this machine
            </h2>
            <ServiceHealth />
          </section>

          <p className="px-1 text-xs leading-relaxed text-fg-subtle">
            Working offline: projects, local files and analysis work without an internet connection. Online
            imagery discovery and place search need a configured online source and are switched off.
          </p>
          {backdropUrl && backdropReady ? (
            <p className="px-1 text-[11px] text-fg-subtle" data-testid="login-satellite-credit">
              Background: satellite imagery © Mapbox, © Maxar, © OpenStreetMap contributors. Loaded from Mapbox; the app does not need it.
            </p>
          ) : null}
        </main>
      </div>
    </div>
  );
}
