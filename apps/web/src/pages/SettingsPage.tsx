import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink, useParams } from "react-router-dom";

import { type Crs, getHealth, getModelHealth } from "../api/client";
import { CrsPicker } from "../components/CrsPicker";
import { ErrorNotice, Loading, Notice, PageTitle } from "../components/ui";

const tabs: [string, string][] = [
  ["diagnostics", "Diagnostics"],
  ["models", "Models"],
  ["crs", "CRS"],
  ["sources", "Sources"],
  ["storage", "Storage"],
];

function Rows({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="grid max-w-2xl grid-cols-[14rem_1fr] gap-y-1 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-slate-600">{label}</dt>
          <dd className="break-words">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Diagnostics() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  if (health.isLoading) return <Loading label="Running diagnostics…" />;
  if (health.isError) return <ErrorNotice error={health.error} />;
  const data = health.data!;
  return (
    <div className="space-y-3">
      {data.status !== "ok" ? (
        <Notice tone="error" title="A local service is unavailable">
          Code: {String(data.database.error_code ?? data.storage.error_code ?? "UNKNOWN")}
        </Notice>
      ) : null}
      <Rows
        rows={[
          ["Overall status", data.status],
          ["Database reachable", String(data.database.reachable)],
          ["PostGIS version", String(data.database.postgis_version ?? "unknown")],
          ["Schema revision", String(data.database.schema_revision ?? "unknown")],
          ["API bound to", data.bind_host],
          ["Job worker", "Not part of this phase"],
          ["Online sources", data.online.enabled ? "enabled" : "disabled"],
          ["PROJ network access", data.online.proj_network ? "enabled" : "disabled"],
          ...Object.entries(data.versions).map(([name, version]): [string, string] => [`Version: ${name}`, version]),
        ]}
      />
      <p className="text-xs text-slate-600">No passwords, tokens or other secrets are shown here or written to logs.</p>
    </div>
  );
}

function Models() {
  const models = useQuery({ queryKey: ["health-models"], queryFn: getModelHealth });
  if (models.isLoading) return <Loading />;
  if (models.isError) return <ErrorNotice error={models.error} />;
  const data = models.data!;
  return (
    <div className="space-y-3">
      {data.errors.map((error) => (
        <Notice key={error} tone="warning" title={error} />
      ))}
      {data.models.length === 0 ? (
        <Notice title="No analysis models are installed">
          No model has been selected or validated for any pipeline. Analysis is unavailable until models are approved
          and installed.
        </Notice>
      ) : (
        <ul className="list-disc pl-5 text-sm">
          {data.models.map((model) => (
            <li key={`${String(model.name)}-${String(model.version)}`}>
              {String(model.name)} {String(model.version)} — unavailable: {String(model.reason_unavailable)}
            </li>
          ))}
        </ul>
      )}
      <Notice title="Local language model">{String(data.llm_runtime.note)}</Notice>
    </div>
  );
}

function CrsTab() {
  const [selected, setSelected] = useState<Crs | null>(null);
  return (
    <div className="max-w-2xl space-y-3">
      <Notice tone="warning" title="DSM (Defence Series Map) compatibility is not claimed">
        WGS 84 is a datum and LCC is a projection method. “WGS84 + LCC” alone does not define a DSM CRS or any other
        complete CRS. No DSM definition is installed.
      </Notice>
      <p className="text-sm text-slate-600">
        Look up a CRS in the registry installed on this machine. Custom definitions cannot be registered yet.
      </p>
      <label htmlFor="crs-search" className="block text-sm font-medium">
        Search the CRS registry
      </label>
      <CrsPicker selected={selected} onSelect={setSelected} />
    </div>
  );
}

function Storage() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  if (health.isLoading) return <Loading />;
  if (health.isError) return <ErrorNotice error={health.error} />;
  const storage = health.data!.storage;
  const free = typeof storage.free_bytes === "number" ? `${(storage.free_bytes / 1024 ** 3).toFixed(1)} GB` : "unknown";
  return (
    <Rows
      rows={[
        ["Data folder", String(storage.data_root)],
        ["Data folder available", String(storage.data_root_ok)],
        ["Free space", free],
      ]}
    />
  );
}

export function SettingsPage() {
  const { tab = "diagnostics" } = useParams();
  return (
    <div>
      <PageTitle title="Settings" />
      <nav className="mb-4 flex flex-wrap gap-1 border-b border-slate-300" aria-label="Settings sections">
        {tabs.map(([key, label]) => (
          <NavLink
            key={key}
            to={`/settings/${key}`}
            className={({ isActive }) =>
              `rounded-t px-3 py-1.5 text-sm ${isActive ? "bg-white font-medium border border-b-0 border-slate-300" : "hover:bg-slate-100"}`
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>
      {tab === "diagnostics" ? <Diagnostics /> : null}
      {tab === "models" ? <Models /> : null}
      {tab === "crs" ? <CrsTab /> : null}
      {tab === "sources" ? (
        <Notice title="No online sources are configured">
          Online imagery discovery and place search are optional and switched off. Nothing is sent to external services.
        </Notice>
      ) : null}
      {tab === "storage" ? <Storage /> : null}
      {!tabs.some(([key]) => key === tab) ? <Notice tone="error" title="Unknown settings section" /> : null}
    </div>
  );
}
