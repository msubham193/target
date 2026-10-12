import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";

import { getHealth } from "../api/client";
import { useProfile } from "../lib/profile";

function navClass({ isActive }: { isActive: boolean }) {
  return `rounded px-2 py-1 text-sm ${isActive ? "bg-slate-700 text-white" : "text-slate-200 hover:bg-slate-800"}`;
}

export function AppLayout() {
  const { profile, setProfile } = useProfile();
  const navigate = useNavigate();
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 30_000 });

  let serviceLabel = "Checking services…";
  let serviceTone = "bg-slate-600";
  if (health.isError) {
    serviceLabel = "API unreachable";
    serviceTone = "bg-red-700";
  } else if (health.data) {
    const ok = health.data.status === "ok";
    serviceLabel = ok ? "Services OK" : "Service problem";
    serviceTone = ok ? "bg-emerald-700" : "bg-red-700";
  }
  const online = health.data?.online?.enabled === true;

  return (
    <div className="min-h-screen">
      <header className="bg-slate-900 text-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-4 py-2">
          <Link to="/dashboard" className="font-semibold">
            Geospatial PoC
          </Link>
          <nav className="flex gap-1" aria-label="Main">
            <NavLink to="/dashboard" className={navClass}>
              Dashboard
            </NavLink>
            <NavLink to="/settings/diagnostics" className={navClass}>
              Settings
            </NavLink>
          </nav>
          <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
            <Link to="/settings/diagnostics" className={`rounded px-2 py-1 ${serviceTone}`}>
              {serviceLabel}
            </Link>
            <span className="rounded bg-slate-700 px-2 py-1" title="Online sources are optional and off by default">
              {online ? "Online sources enabled" : "Local only · online sources off"}
            </span>
            <span className="rounded bg-slate-700 px-2 py-1">Profile: {profile?.display_name}</span>
            <button
              type="button"
              className="underline"
              onClick={() => {
                setProfile(null);
                navigate("/login");
              }}
            >
              Switch
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
