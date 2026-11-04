import { useState } from "react";
import { NavLink, useParams } from "react-router-dom";

import { type Crs } from "../api/client";
import { useHealth, useModelHealth } from "../lib/queries";
import { CrsPicker } from "../components/CrsPicker";
import { IconCpu, IconDatabase, IconGlobe, IconSettings, IconSliders } from "../components/icons";
import {
  Badge,
  Button,
  DescriptionList,
  ErrorNotice,
  Loading,
  Notice,
  PageHeader,
  Panel,
  PlannedBadge,
} from "../components/ui";
import { MODALITY_NAMES } from "../lib/ingestion";
import { loadSidebarCollapsed, saveLastProject, saveSidebarCollapsed } from "../lib/preferences";

const SECTIONS = [
  { key: "models", label: "Models", icon: IconCpu },
  { key: "crs", label: "Coordinate systems", icon: IconGlobe },
  { key: "sources", label: "Data sources & storage", icon: IconDatabase },
  { key: "network", label: "Offline & network", icon: IconSettings },
  { key: "diagnostics", label: "Preferences & diagnostics", icon: IconSliders },
] as const;

const readOnly = <Badge tone="neutral">Read-only</Badge>;

function Models() {
  const models = useModelHealth();
  if (models.isLoading) return <Loading />;
  if (models.isError) return <ErrorNotice error={models.error} />;
  const data = models.data!;
  const capabilities = data.worker.capabilities as { cuda_available?: boolean; cuda_device?: string; torch?: string } | null;
  return (
    <div className="space-y-4">
      {data.errors.map((error) => (
        <Notice key={error} tone="warning" title={error} />
      ))}
      <Panel
        title="Analysis models"
        description="From models/manifest.yaml. Models are added by editing the manifest and installing their weights."
        actions={readOnly}
      >
        {data.models.length === 0 ? (
          <p className="text-[13px] text-fg-muted">No analysis models are installed. Analysis is unavailable until one is.</p>
        ) : (
          <ul className="space-y-3" aria-label="Models">
            {data.models.map((model) => (
              <li key={`${model.name}-${model.version}`} className="rounded-md border border-line bg-raised p-3 text-[13px]">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="wrap-anywhere font-medium text-fg">
                    {model.name} {model.version}
                  </p>
                  {model.available ? <Badge tone="ok">Available</Badge> : <Badge tone="danger">Unavailable</Badge>}
                </div>
                {model.reason_unavailable ? <p className="mt-1 text-danger">{model.reason_unavailable}</p> : null}
                <div className="mt-2 space-y-1 text-fg-muted">
                  <p>
                    For: {(MODALITY_NAMES[model.pipeline] ?? model.pipeline).toLowerCase()}s · task:{" "}
                    {model.task.replaceAll("_", " ")} · runs on this computer
                  </p>
                  <p>Score: {model.score_semantics}</p>
                  <p>
                    {model.score_calibrated
                      ? "Calibrated."
                      : "Not calibrated and not evaluated on representative data: no accuracy is claimed, and every finding needs review."}
                  </p>
                  <p>Licence: {model.licence}</p>
                  {model.weights_sha256 ? (
                    <p className="wrap-anywhere font-mono text-[11px] text-fg-subtle">Weights SHA-256: {model.weights_sha256}</p>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Panel title="Worker" actions={readOnly}>
        <p className="text-[13px] text-fg-muted">
          {data.worker.running
            ? `Running. ${capabilities?.cuda_available ? `GPU: ${String(capabilities.cuda_device)}.` : "No GPU is available to it; models run on the CPU."}${capabilities?.torch ? ` PyTorch ${capabilities.torch}.` : ""}`
            : "Not running. It is not known whether models can be run until a worker starts."}
        </p>
      </Panel>
      <Panel title="Local language model" actions={<PlannedBadge />}>
        <p className="text-[13px] text-fg-muted">{String(data.llm_runtime.note)}</p>
      </Panel>
    </div>
  );
}

function CoordinateSystems() {
  const [selected, setSelected] = useState<Crs | null>(null);
  return (
    <div className="space-y-4">
      <Panel title="CRS registry" description="The PROJ database installed on this machine. Works offline." actions={readOnly}>
        <div className="max-w-2xl space-y-3">
          <label htmlFor="crs-search" className="block text-[13px] font-medium">
            Search the CRS registry
          </label>
          <CrsPicker selected={selected} onSelect={setSelected} />
        </div>
      </Panel>
      <Notice tone="warning" title="DSM (Defence Series Map) compatibility is not claimed">
        WGS 84 is a datum and LCC is a projection method. “WGS84 + LCC” alone does not define a DSM CRS or any other
        complete CRS. No DSM definition is installed.
      </Notice>
      <Panel title="Custom CRS definitions" actions={<PlannedBadge />}>
        <p className="text-[13px] text-fg-muted">Registering a custom CRS with a recorded source is planned. Only registry codes are accepted today.</p>
      </Panel>
    </div>
  );
}

function SourcesAndStorage() {
  const health = useHealth();
  return (
    <div className="space-y-4">
      <Panel title="Data sources" actions={<PlannedBadge label="Online sources planned" />}>
        <p className="text-[13px] text-fg-muted">
          No online sources are configured. Files enter the application only by upload from this computer. Online imagery
          discovery would be optional, configured, and recorded with its source and licence.
        </p>
      </Panel>
      <Panel title="Local storage" actions={readOnly}>
        {health.isLoading ? <Loading /> : null}
        {health.isError ? <ErrorNotice error={health.error} /> : null}
        {health.data ? (
          <DescriptionList
            labelWidth="md"
            rows={[
              ["Data folder", String(health.data.storage.data_root)],
              ["Data folder available", health.data.storage.data_root_ok ? "yes" : "no"],
              [
                "Free space",
                typeof health.data.storage.free_bytes === "number"
                  ? `${(health.data.storage.free_bytes / 1024 ** 3).toFixed(1)} GB`
                  : "unknown",
              ],
              ["Originals", "Stored once per checksum, read-only, never modified"],
              ["Previews", "Derived pictures, regenerable"],
            ]}
          />
        ) : null}
        <p className="mt-3 text-xs text-fg-subtle">The data folder is set by GEOPOC_DATA_ROOT in the .env file and cannot be changed here.</p>
      </Panel>
    </div>
  );
}

function OfflineAndNetwork() {
  const health = useHealth();
  if (health.isLoading) return <Loading />;
  if (health.isError) return <ErrorNotice error={health.error} />;
  const data = health.data!;
  return (
    <div className="space-y-4">
      <Panel title="Network use" actions={readOnly}>
        <DescriptionList
          labelWidth="md"
          rows={[
            ["Online sources", data.online.enabled ? "enabled" : "disabled"],
            ["PROJ network access", data.online.proj_network ? "enabled" : "disabled"],
            ["Cloud AI services", "none: models run on this computer"],
            ["Map tiles and geocoding", "none configured"],
            ["API listens on", data.bind_host],
          ]}
        />
      </Panel>
      <Notice title="What “offline” means here">
        In normal use the application makes no connection outside this computer. It does not block the network at the
        operating-system level; to guarantee isolation, disconnect the machine or use a firewall. These settings are
        read-only: they come from the .env file.
      </Notice>
    </div>
  );
}

function PreferencesAndDiagnostics() {
  const health = useHealth();
  const [collapsed, setCollapsed] = useState(loadSidebarCollapsed);
  const [forgot, setForgot] = useState(false);
  return (
    <div className="space-y-4">
      <Panel title="Preferences" description="Stored in this browser only.">
        <div className="space-y-3 text-[13px]">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              className="accent-accent"
              checked={collapsed}
              onChange={(event) => {
                setCollapsed(event.target.checked);
                saveSidebarCollapsed(event.target.checked);
              }}
            />
            Start with the sidebar collapsed (takes effect when the page is next opened)
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                saveLastProject(null);
                setForgot(true);
              }}
            >
              Forget the last opened project
            </Button>
            {forgot ? (
              <span role="status" className="text-xs text-ok">
                Forgotten. Reload to update the sidebar.
              </span>
            ) : null}
          </div>
        </div>
      </Panel>
      <Panel title="Diagnostics" actions={readOnly}>
        {health.isLoading ? <Loading label="Running diagnostics…" /> : null}
        {health.isError ? <ErrorNotice error={health.error} /> : null}
        {health.data ? (
          <div className="space-y-3">
            {health.data.status !== "ok" ? (
              <Notice tone="error" title="A local service is unavailable">
                Code: {String(health.data.database.error_code ?? health.data.storage.error_code ?? "UNKNOWN")}
              </Notice>
            ) : null}
            <DescriptionList
              labelWidth="md"
              rows={[
                ["Overall status", health.data.status],
                ["Database reachable", health.data.database.reachable ? "yes" : "no"],
                ["PostGIS version", String(health.data.database.postgis_version ?? "unknown")],
                ["Schema revision", String(health.data.database.schema_revision ?? "unknown")],
                ["API bound to", health.data.bind_host],
                [
                  "Background worker",
                  health.data.worker.running
                    ? "running"
                    : "not running — queued jobs (previews, analysis) wait. Start it with scripts\\dev.ps1.",
                ],
                ...Object.entries(health.data.versions).map(([name, version]): [string, string] => [`Version: ${name}`, version]),
              ]}
            />
            <p className="text-xs text-fg-subtle">No passwords, tokens or other secrets are shown here or written to logs.</p>
          </div>
        ) : null}
      </Panel>
    </div>
  );
}

export function SettingsPage() {
  const { tab = "models" } = useParams();
  const known = SECTIONS.some((section) => section.key === tab);
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Settings" description="Models, coordinate systems, data sources, network use and diagnostics." />
      <div className="grid grid-cols-1 gap-4 md:grid-cols-[13rem_minmax(0,1fr)]">
        <nav aria-label="Settings sections" className="flex gap-1 overflow-x-auto md:flex-col">
          {SECTIONS.map((section) => {
            const Icon = section.icon;
            return (
              <NavLink
                key={section.key}
                to={`/settings/${section.key}`}
                className={({ isActive }) =>
                  `flex shrink-0 items-center gap-2 rounded-md px-2.5 py-1.5 text-[13px] whitespace-nowrap ${
                    isActive ? "bg-accent-soft font-medium text-fg" : "text-fg-muted hover:bg-overlay hover:text-fg"
                  }`
                }
              >
                <Icon size={15} />
                {section.label}
              </NavLink>
            );
          })}
        </nav>
        <div className="min-w-0">
          {tab === "models" ? <Models /> : null}
          {tab === "crs" ? <CoordinateSystems /> : null}
          {tab === "sources" ? <SourcesAndStorage /> : null}
          {tab === "network" ? <OfflineAndNetwork /> : null}
          {tab === "diagnostics" ? <PreferencesAndDiagnostics /> : null}
          {!known ? <Notice tone="error" title="Unknown settings section" /> : null}
        </div>
      </div>
    </div>
  );
}
