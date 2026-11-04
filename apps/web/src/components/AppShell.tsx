import { useQuery } from "@tanstack/react-query";
import { type ComponentType, type ReactNode, useEffect, useState } from "react";
import { Link, Outlet, useLocation, useMatch, useNavigate } from "react-router-dom";

import { listProjects } from "../api/client";
import { useHealth } from "../lib/queries";
import {
  type LastProject,
  loadLastProject,
  loadSidebarCollapsed,
  saveLastProject,
  saveSidebarCollapsed,
} from "../lib/preferences";
import { badgeFor } from "../lib/avatar";
import { signOut } from "../lib/auth";
import { useProfile } from "../lib/profile";
import {
  IconChevronLeft,
  IconChevronRight,
  IconClose,
  IconCube,
  IconDashboard,
  IconFolderPlus,
  IconImage,
  IconList,
  IconMap,
  IconMenu,
  IconReport,
  IconSatellite,
  IconSettings,
  IconUpload,
  IconUser,
  IconVideo,
  IconWifiOff,
} from "./icons";
import { Badge, IconButton, selectClass } from "./ui";

interface NavEntry {
  label: string;
  icon: ComponentType<{ size?: number }>;
  /** A path, or a path below the current project. */
  to: string | ((projectId: string) => string);
  /** Whether this entry is the current screen, given the part of the path after /projects/:id. */
  matches?: (rest: string) => boolean;
  planned?: boolean;
  /** One plain sentence about the screen, shown when the pointer rests on the entry. */
  hint?: string;
}

const GROUPS: { label: string; entries: NavEntry[] }[] = [
  {
    label: "Start",
    entries: [
      { label: "Project Dashboard", icon: IconDashboard, to: "/dashboard", hint: "Your projects and what has happened recently" },
      { label: "New Project", icon: IconFolderPlus, to: "/projects/new", hint: "Start a project. A location is optional" },
    ],
  },
  {
    label: "Your files",
    entries: [
      {
        label: "Add Files",
        hint: "Upload photos, maps, satellite images or video",
        icon: IconUpload,
        to: (id) => `/projects/${id}/assets/new`,
        // the upload page, the asset list and a file's page
        matches: (rest) =>
          rest === "/assets/new" || rest === "/assets" || /^\/assets\/[^/]+$/.test(rest),
      },
      {
        label: "Map",
        hint: "See your files and photos on a map",
        icon: IconMap,
        to: (id) => `/projects/${id}/map`,
        matches: (r) => r.startsWith("/map"),
      },
      {
        label: "Photos",
        hint: "Look through your photos and where they were taken",
        icon: IconImage,
        to: (id) => `/projects/${id}/workspace`,
        matches: (r) => r.startsWith("/workspace"),
      },
    ],
  },
  {
    label: "Explore and check",
    entries: [
      {
        label: "Photo Analysis",
        hint: "Find objects in a photo and review what was found",
        icon: IconImage,
        to: (id) => `/projects/${id}/ground`,
        matches: (r) => r === "/ground" || /\/ground$/.test(r),
      },
      {
        label: "Satellite Images",
        hint: "Look at satellite and aerial images",
        icon: IconSatellite,
        to: (id) => `/projects/${id}/satellite`,
        matches: (r) => r === "/satellite" || /\/overhead$/.test(r),
      },
      {
        label: "Videos",
        hint: "Check video frames for objects",
        icon: IconVideo,
        to: (id) => `/projects/${id}/video`,
        matches: (r) => r === "/video" || /^\/assets\/[^/]+\/video$/.test(r),
      },
      {
        label: "3D Terrain",
        hint: "See the ground in three dimensions",
        icon: IconCube,
        to: (id) => `/projects/${id}/3d`,
        matches: (r) => r.startsWith("/3d"),
      },
      {
        label: "Findings",
        hint: "Everything found so far, ready to review",
        icon: IconList,
        to: (id) => `/projects/${id}/observations`,
        matches: (r) => r.startsWith("/observations"),
      },
      {
        label: "Reports",
        hint: "Save your results to share",
        icon: IconReport,
        to: (id) => `/projects/${id}/reports`,
        matches: (r) => r.startsWith("/reports"),
      },
    ],
  },
  {
    label: "Setup",
    entries: [{ label: "Settings", icon: IconSettings, to: "/settings/models", hint: "Models, coordinate systems and connections" }],
  },
];

/** The project the user is working in: the one in the address, or the last one opened. */
function useProjectContext(): { current: LastProject | null; fromRoute: string | null } {
  const match = useMatch("/projects/:projectId/*");
  const routeId = match?.params.projectId && match.params.projectId !== "new" ? match.params.projectId : null;
  const [last, setLast] = useState<LastProject | null>(() => loadLastProject());
  const projects = useQuery({ queryKey: ["projects", "selector"], queryFn: () => listProjects({ limit: 100 }) });

  useEffect(() => {
    if (!routeId) return;
    const found = projects.data?.items.find((project) => project.id === routeId);
    if (found && (last?.id !== found.id || last.name !== found.name)) {
      const next = { id: found.id, name: found.name };
      saveLastProject(next);
      setLast(next);
    }
  }, [routeId, projects.data, last]);

  // A remembered project that no longer exists is forgotten rather than linked to.
  useEffect(() => {
    if (last && projects.data && !projects.data.items.some((project) => project.id === last.id)) {
      saveLastProject(null);
      setLast(null);
    }
  }, [last, projects.data]);

  return { current: routeId ? (last?.id === routeId ? last : { id: routeId, name: "" }) : last, fromRoute: routeId };
}

function ProjectSelector({ current, id = "project-selector" }: { current: LastProject | null; id?: string }) {
  const navigate = useNavigate();
  const projects = useQuery({ queryKey: ["projects", "selector"], queryFn: () => listProjects({ limit: 100 }) });
  const items = projects.data?.items ?? [];
  return (
    <div className="px-3 pb-3">
      <label htmlFor={id} className="mb-1 block text-[11px] font-medium tracking-wide text-fg-subtle uppercase">
        Project
      </label>
      <select
        id={id}
        className={`${selectClass} h-8 py-1 text-[13px]`}
        value={current?.id ?? ""}
        onChange={(event) => {
          if (event.target.value) navigate(`/projects/${event.target.value}`);
        }}
        disabled={projects.isLoading || items.length === 0}
      >
        {!current ? <option value="">{items.length === 0 ? "No projects yet" : "Choose a project…"}</option> : null}
        {items.map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
      </select>
    </div>
  );
}

function SidebarNav({
  collapsed,
  projectId,
  rest,
  onNavigate,
}: {
  collapsed: boolean;
  projectId: string | null;
  rest: string;
  onNavigate?: () => void;
}) {
  const location = useLocation();
  return (
    <nav aria-label="Main" className="flex-1 overflow-y-auto px-2 pb-4">
      {GROUPS.map((group) => (
        <div key={group.label} className="mt-3 first:mt-0">
          {collapsed ? (
            <div className="mx-2 my-2 border-t border-line" aria-hidden="true" />
          ) : (
            <p className="px-2 pb-1 text-[11px] font-medium tracking-wide text-fg-subtle uppercase">{group.label}</p>
          )}
          <ul className="space-y-0.5">
            {group.entries.map((entry) => {
              const Icon = entry.icon;
              const target = typeof entry.to === "function" ? (projectId ? entry.to(projectId) : null) : entry.to;
              const current = entry.matches
                ? Boolean(projectId) && location.pathname.startsWith(`/projects/${projectId}`) && entry.matches(rest)
                : location.pathname === target || (target === "/settings/models" && location.pathname.startsWith("/settings"));
              const content = (
                <>
                  <Icon size={17} />
                  {collapsed ? null : <span className="min-w-0 flex-1 truncate">{entry.label}</span>}
                  {!collapsed && entry.planned ? <span className="text-[10px] text-fg-subtle">planned</span> : null}
                </>
              );
              const base = `relative flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-[13px] transition-colors ${collapsed ? "justify-center" : ""}`;
              if (!target) {
                return (
                  <li key={entry.label}>
                    <span
                      className={`${base} cursor-not-allowed text-fg-subtle/70`}
                      aria-disabled="true"
                      title={`${entry.label} — open a project first`}
                    >
                      {content}
                      {collapsed ? <span className="sr-only">{entry.label} (open a project first)</span> : null}
                    </span>
                  </li>
                );
              }
              return (
                <li key={entry.label}>
                  <Link
                    to={target}
                    onClick={onNavigate}
                    aria-current={current ? "page" : undefined}
                    title={collapsed ? entry.label : entry.hint}
                    className={`${base} ${
                      current
                        ? "bg-accent-soft font-medium text-fg before:absolute before:inset-y-2 before:left-0 before:w-0.5 before:rounded-full before:bg-accent"
                        : "text-fg-muted hover:bg-overlay hover:text-fg"
                    }`}
                  >
                    {content}
                    {collapsed ? <span className="sr-only">{entry.label}</span> : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function StatusIndicators() {
  const health = useHealth();
  let label = "Checking services…";
  let tone: "neutral" | "ok" | "danger" | "warn" = "neutral";
  if (health.isError) {
    label = "API unreachable";
    tone = "danger";
  } else if (health.data) {
    if (health.data.status !== "ok") {
      label = "Service problem";
      tone = "danger";
    } else if (!health.data.worker.running) {
      label = "Worker not running";
      tone = "warn";
    } else {
      label = "Services OK";
      tone = "ok";
    }
  }
  const online = health.data?.online?.enabled === true;
  return (
    <div className="flex items-center gap-2">
      <Link to="/settings/diagnostics" className="rounded-sm" title="Open diagnostics">
        <Badge tone={tone}>
          <span className={`size-1.5 rounded-full ${tone === "ok" ? "bg-ok" : tone === "danger" ? "bg-danger" : tone === "warn" ? "bg-warn" : "bg-fg-subtle"}`} />
          {label}
        </Badge>
      </Link>
      <Link to="/settings/network" className="hidden rounded-sm sm:inline-flex" title="Network and online sources">
        <Badge tone="neutral">
          {online ? null : <IconWifiOff size={12} />}
          {online ? "Online sources on" : "Local only"}
        </Badge>
      </Link>
    </div>
  );
}

/**
 * The application frame: grouped navigation in a collapsible sidebar (a drawer on narrow screens), the project
 * selector, and a top bar with status and profile. Each screen draws its own header and content.
 */
export function AppShell({ children }: { children?: ReactNode }) {
  const { profile, setProfile } = useProfile();
  const navigate = useNavigate();
  const location = useLocation();
  const { current } = useProjectContext();
  const [collapsed, setCollapsed] = useState(loadSidebarCollapsed);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => setDrawerOpen(false), [location.pathname]);

  // The drawer is a modal dialog: Escape closes it.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  const projectId = current?.id ?? null;
  const rest = projectId ? location.pathname.slice(`/projects/${projectId}`.length).replace(/\/$/, "") : "";

  const toggle = () => {
    setCollapsed((value) => {
      saveSidebarCollapsed(!value);
      return !value;
    });
  };

  const brand = (compact: boolean) => (
    <Link to="/dashboard" className="flex h-12 items-center gap-2 whitespace-nowrap px-3 text-[13px] font-semibold tracking-tight text-fg">
      <span className="flex size-7 items-center justify-center rounded-lg bg-accent text-[12px] font-bold text-accent-fg shadow-[0_0_16px_rgb(91_155_255/0.45)]" aria-hidden="true">
        I
      </span>
      {compact ? <span className="sr-only">INT and TGT Development</span> : "INT and TGT Development"}
    </Link>
  );

  return (
    <div
      className="flex min-h-screen bg-canvas"
      style={{ backgroundImage: "radial-gradient(ellipse 60% 40% at 85% -5%, rgb(91 155 255 / 0.07), transparent 70%)" }}
    >
      <a href="#main" className="sr-only print:hidden z-50 rounded bg-accent px-3 py-2 text-accent-fg focus:not-sr-only focus:fixed focus:top-2 focus:left-2">
        Skip to content
      </a>

      {/* desktop sidebar */}
      <aside
        className={`sticky top-0 hidden h-screen shrink-0 flex-col print:hidden! border-r border-line/80 bg-gradient-to-b from-surface to-canvas transition-[width] md:flex ${collapsed ? "w-14" : "w-60"}`}
      >
        {brand(collapsed)}
        {collapsed ? null : <ProjectSelector current={current} />}
        <SidebarNav collapsed={collapsed} projectId={projectId} rest={rest} />
        <div className="border-t border-line p-2">
          <IconButton label={collapsed ? "Expand sidebar" : "Collapse sidebar"} onClick={toggle} aria-expanded={!collapsed}>
            {collapsed ? <IconChevronRight /> : <IconChevronLeft />}
          </IconButton>
        </div>
      </aside>

      {/* narrow-screen drawer */}
      {drawerOpen ? (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <button type="button" tabIndex={-1} className="absolute inset-0 bg-black/60" aria-label="Close navigation" onClick={() => setDrawerOpen(false)} />
          <aside className="relative flex h-full w-72 max-w-[85vw] flex-col border-r border-line bg-surface">
            <div className="flex items-center justify-between pr-2">
              {brand(false)}
              <IconButton label="Close navigation" autoFocus onClick={() => setDrawerOpen(false)}>
                <IconClose />
              </IconButton>
            </div>
            <ProjectSelector current={current} id="project-selector-drawer" />
            <SidebarNav collapsed={false} projectId={projectId} rest={rest} onNavigate={() => setDrawerOpen(false)} />
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 print:hidden flex h-12 items-center gap-3 border-b border-line/70 bg-canvas/70 px-4 backdrop-blur-xl">
          <IconButton label="Open navigation" className="md:hidden" onClick={() => setDrawerOpen(true)}>
            <IconMenu />
          </IconButton>
          <div className="min-w-0 flex-1 truncate text-[13px] text-fg-muted">
            {current?.name ? (
              <Link to={`/projects/${current.id}`} className="truncate hover:text-fg" title="Project overview">
                {current.name}
              </Link>
            ) : null}
          </div>
          <StatusIndicators />
          <div className="flex items-center gap-1.5 border-l border-line pl-3 text-[13px]">
            {profile ? (
              <span
                aria-hidden="true"
                className="flex size-6 items-center justify-center rounded-full text-[10px] font-semibold"
                style={{
                  background: `hsl(${badgeFor(profile.display_name).hue} 60% 45% / 0.22)`,
                  color: `hsl(${badgeFor(profile.display_name).hue} 80% 78%)`,
                }}
              >
                {badgeFor(profile.display_name).initials}
              </span>
            ) : (
              <IconUser size={15} className="text-fg-subtle" />
            )}
            <span className="hidden max-w-32 truncate text-fg-muted sm:inline" title="Local profile (not a login)">
              {profile?.display_name}
            </span>
            <button
              type="button"
              className="rounded px-1.5 py-1 text-xs text-fg-subtle hover:bg-overlay hover:text-fg"
              onClick={() => {
                setProfile(null);
                navigate("/login");
              }}
            >
              Switch profile
            </button>
            <button
              type="button"
              className="rounded px-1.5 py-1 text-xs text-fg-subtle hover:bg-overlay hover:text-fg"
              onClick={() => {
                signOut();
                setProfile(null);
                navigate("/login");
              }}
            >
              Sign out
            </button>
          </div>
        </header>
        <main id="main" className="min-w-0 flex-1 px-4 py-5 sm:px-6">
          {children ?? <Outlet />}
        </main>
      </div>
    </div>
  );
}
