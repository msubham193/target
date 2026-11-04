import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { getProjectSummary, listProjects, listRecentJobs, type Project } from "../api/client";
import { IconAlert, IconCheck, IconClock, IconFolder, IconImage, IconPlus, IconSatellite, IconSearch, IconVideo } from "../components/icons";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  inputClass,
  LinkButton,
  Loading,
  Panel,
  Skeleton,
} from "../components/ui";
import terrainUrl from "../assets/login-terrain.svg";
import { badgeFor } from "../lib/avatar";
import { formatDateTime } from "../lib/format";
import { isActive, jobTone } from "../lib/jobs";
import { readMapboxToken, satelliteBannerUrl } from "../lib/mapbox";
import { useProfile } from "../lib/profile";
import { useImageReady } from "../lib/useImageReady";

const PAGE_SIZE = 12;

/** Counts read from the project summary endpoint. While they load, a placeholder is shown, never a guess. */
function ProjectCounts({ projectId }: { projectId: string }) {
  const summary = useQuery({ queryKey: ["summary", projectId], queryFn: () => getProjectSummary(projectId) });
  if (summary.isLoading) return <Skeleton className="h-4 w-40" />;
  if (summary.isError || !summary.data) return <span className="text-xs text-fg-subtle">Counts unavailable</span>;
  const data = summary.data;
  const by = data.assets_by_modality;
  const item = (Icon: typeof IconImage, count: number | undefined, label: string) => (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-raised/80 px-2 py-0.5" title={label}>
      <Icon size={13} className="text-accent" />
      <span className="sr-only">{label}:</span>
      {count ?? 0}
    </span>
  );
  return (
    <span className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
      {item(IconImage, by.ground_image, "Photographs")}
      {item(IconSatellite, by.overhead_raster, "Satellite and aerial images")}
      {item(IconVideo, by.video, "Videos")}
      <span>
        {data.observations_total} finding{data.observations_total === 1 ? "" : "s"}
      </span>
      <span className="basis-full text-fg-subtle">Last activity {formatDateTime(data.last_activity_at)}</span>
    </span>
  );
}

function ProjectRow({ project }: { project: Project }) {
  const badge = badgeFor(project.name);
  return (
    <li className="group relative flex flex-col gap-3 rounded-xl border border-line bg-raised/40 p-4 transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/50 hover:bg-raised hover:shadow-[0_10px_28px_rgb(0_0_0/0.32)]">
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className="flex size-10 shrink-0 items-center justify-center rounded-lg text-sm font-semibold"
          style={{ background: `hsl(${badge.hue} 60% 45% / 0.22)`, color: `hsl(${badge.hue} 80% 78%)` }}
        >
          {badge.initials}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {/* The link stretches over the whole card, so the card is one click target and the link name stays the project name. */}
            <Link
              to={`/projects/${project.id}`}
              className="wrap-anywhere text-sm font-semibold text-fg after:absolute after:inset-0 after:rounded-xl group-hover:text-accent"
            >
              {project.name}
            </Link>
            {project.archived_at ? <Badge tone="neutral">Archived</Badge> : <Badge tone="ok">Active</Badge>}
          </div>
          {project.description ? <p className="mt-1 line-clamp-2 text-[13px] text-fg-muted">{project.description}</p> : null}
        </div>
      </div>
      {project.tags.length > 0 ? (
        <ul className="flex flex-wrap gap-1" aria-label="Tags">
          {project.tags.map((tag) => (
            <li key={tag}>
              <Badge>{tag}</Badge>
            </li>
          ))}
        </ul>
      ) : null}
      <ProjectCounts projectId={project.id} />
      <p className="mt-auto border-t border-line/70 pt-2.5 text-xs text-fg-subtle">
        Created {formatDateTime(project.created_at)} by {project.created_by.display_name} · Location:{" "}
        {project.aoi ? `area of interest entered (${String(project.aoi.source.crs_id)})` : "none entered"}
      </p>
    </li>
  );
}

function useRecentJobs() {
  return useQuery({ queryKey: ["jobs", "recent"], queryFn: () => listRecentJobs(8), refetchInterval: 10_000 });
}

function StatTile({
  icon: Icon,
  label,
  value,
  note,
  tone,
}: {
  icon: typeof IconFolder;
  label: string;
  value: string;
  note: string;
  tone: "accent" | "ok" | "warn" | "danger";
}) {
  const colour = { accent: "text-accent bg-accent-soft", ok: "text-ok bg-ok-soft", warn: "text-warn bg-warn-soft", danger: "text-danger bg-danger-soft" }[tone];
  return (
    <div className="rise-in flex items-center gap-3 rounded-xl border border-line/80 bg-surface/80 p-4 backdrop-blur-sm">
      <span className={`flex size-10 shrink-0 items-center justify-center rounded-lg ${colour}`} aria-hidden="true">
        <Icon size={18} />
      </span>
      <div className="min-w-0">
        <p className="text-2xl font-semibold leading-none tracking-tight text-fg">{value}</p>
        <p className="mt-1 text-xs font-medium text-fg-muted">{label}</p>
        <p className="text-[11px] leading-snug text-fg-subtle">{note}</p>
      </div>
    </div>
  );
}

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

/** Plain-language wording for the dashboard. The technical names stay on the screens that need them. */
const FRIENDLY_KIND: Record<string, { what: string; icon: typeof IconImage }> = {
  preview: { what: "Picture prepared for viewing", icon: IconImage },
  analyze: { what: "Photo analysed", icon: IconSearch },
};
const FRIENDLY_STATUS: Record<string, string> = {
  draft: "Not started",
  queued: "Waiting its turn",
  validating: "Getting ready",
  processing: "Working on it",
  needs_review: "Ready for you to review",
  completed: "Done",
  completed_with_warnings: "Done, with notes",
  failed: "Did not finish",
  cancelled: "Stopped",
};

function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return days < 30 ? `${days} day${days === 1 ? "" : "s"} ago` : formatDateTime(iso);
}

function RecentActivity() {
  const jobs = useRecentJobs();
  return (
    <Panel title="Recent activity" description="What the app has been working on, newest first." padded={false}>
      {jobs.isLoading ? <Loading className="p-4" /> : null}
      {jobs.isError ? (
        <div className="p-4">
          <ErrorNotice error={jobs.error} />
        </div>
      ) : null}
      {jobs.isSuccess && jobs.data.items.length === 0 ? (
        <p className="p-4 text-[13px] text-fg-muted">Nothing yet. Uploading a file starts the first piece of work.</p>
      ) : null}
      {jobs.data && jobs.data.items.length > 0 ? (
        <ul aria-label="Recent activity">
          {jobs.data.items.map((job) => {
            const kind = FRIENDLY_KIND[job.kind] ?? { what: "Background task", icon: IconClock };
            const Icon = kind.icon;
            return (
              <li key={job.id} className="flex items-start gap-3 border-b border-line/70 px-4 py-3 text-[13px] transition-colors last:border-b-0 hover:bg-overlay/50">
                <span
                  aria-hidden="true"
                  className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-raised text-accent ring-1 ring-line/70"
                >
                  <Icon size={15} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-fg">{kind.what}</p>
                  {job.asset_filename ? (
                    <p className="truncate">
                      <Link className="text-fg-muted hover:text-accent" to={`/projects/${job.project_id}/assets/${job.asset_id}`}>
                        {job.asset_filename}
                      </Link>
                    </p>
                  ) : null}
                  <p className="truncate text-xs text-fg-subtle">{job.project_name}</p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <Badge tone={jobTone(job.status)}>{FRIENDLY_STATUS[job.status] ?? job.status}</Badge>
                  <span className="text-[11px] text-fg-subtle" title={formatDateTime(job.created_at)}>
                    {timeAgo(job.created_at)}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </Panel>
  );
}

export function DashboardPage() {
  const [searchText, setSearchText] = useState("");
  const [tagText, setTagText] = useState("");
  const [filters, setFilters] = useState({ q: "", tag: "", archived: false });

  const projects = useInfiniteQuery({
    queryKey: ["projects", filters],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      listProjects({
        q: filters.q || undefined,
        tag: filters.tag || undefined,
        archived: filters.archived,
        limit: PAGE_SIZE,
        cursor: pageParam,
      }),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const items = projects.data?.pages.flatMap((page) => page.items) ?? [];
  const { profile } = useProfile();
  // A dark satellite picture in the header when a Mapbox public token is set; the contour pattern alone otherwise.
  const bannerUrl = useMemo(() => {
    const token = readMapboxToken();
    return token.ok ? satelliteBannerUrl(token.token) : null;
  }, []);
  const bannerReady = useImageReady(bannerUrl);
  const recentJobs = useRecentJobs();
  const latest = recentJobs.data?.items ?? [];
  const jobsKnown = recentJobs.isSuccess;
  const inProgress = latest.filter((job) => isActive(job)).length;
  const needsReview = latest.filter((job) => job.status === "needs_review").length;
  const failed = latest.filter((job) => job.status === "failed").length;
  const jobNote = "in the latest activity";
  const filtered = Boolean(filters.q || filters.tag || filters.archived);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setFilters((current) => ({ ...current, q: searchText.trim(), tag: tagText.trim() }));
  };

  return (
    <div className="mx-auto max-w-6xl">
      <header className="rise-in relative isolate mb-5 overflow-hidden rounded-2xl border border-line/80 bg-surface px-6 py-7 sm:px-8">
        {bannerUrl ? (
          <div
            aria-hidden="true"
            data-testid="dashboard-satellite"
            className="pointer-events-none absolute inset-0 -z-20 bg-cover bg-center transition-opacity duration-1000"
            style={{
              backgroundImage: `url("${bannerUrl}")`,
              filter: "brightness(0.5) saturate(0.85) contrast(1.05)",
              opacity: bannerReady ? 1 : 0,
              maskImage: "linear-gradient(to left, black 25%, transparent 90%)",
              WebkitMaskImage: "linear-gradient(to left, black 25%, transparent 90%)",
            }}
          />
        ) : null}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 -z-10 bg-accent opacity-[0.16]"
          style={{
            maskImage: `url(${terrainUrl})`,
            WebkitMaskImage: `url(${terrainUrl})`,
            maskSize: "cover",
            WebkitMaskSize: "cover",
            maskPosition: "center",
            WebkitMaskPosition: "center",
          }}
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 -z-10"
          style={{ background: "linear-gradient(100deg, var(--color-surface) 12%, transparent 70%)" }}
        />
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-[0.16em] text-fg-subtle">
              {greeting()}
              {profile ? `, ${profile.display_name}` : ""}
            </p>
            <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-fg sm:text-3xl">Project Dashboard</h1>
            <p className="mt-1.5 max-w-xl text-[13px] leading-relaxed text-fg-muted">
              Projects in this local workspace, what they contain, and the work running on them.
            </p>
          </div>
          <LinkButton to="/projects/new">
            <IconPlus size={15} />
            Create project
          </LinkButton>
        </div>
        {bannerUrl && bannerReady ? (
          <p className="absolute bottom-1.5 right-3 text-[10px] text-fg-muted/80" data-testid="dashboard-satellite-credit">
            Imagery © Mapbox, © Maxar, © OpenStreetMap contributors
          </p>
        ) : null}
      </header>

      <section aria-label="At a glance" className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          icon={IconFolder}
          label={filtered ? "Projects shown" : "Projects"}
          value={projects.isSuccess ? `${items.length}${projects.hasNextPage ? "+" : ""}` : "–"}
          note={projects.hasNextPage ? "more to load below" : "in this workspace"}
          tone="accent"
        />
        <StatTile icon={IconClock} label="Running now" value={jobsKnown ? String(inProgress) : "–"} note={jobNote} tone="accent" />
        <StatTile icon={IconCheck} label="Ready to review" value={jobsKnown ? String(needsReview) : "–"} note={jobNote} tone="warn" />
        <StatTile icon={IconAlert} label="Needs attention" value={jobsKnown ? String(failed) : "–"} note={jobNote} tone="danger" />
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <section aria-labelledby="projects-heading" className="rise-in min-w-0 rounded-2xl border border-line/80 bg-surface/80 backdrop-blur-sm">
          <div className="border-b border-line/70 px-5 py-4">
            <h2 id="projects-heading" className="text-sm font-semibold text-fg">
              Projects
            </h2>
            <form onSubmit={submit} className="mt-3 flex flex-wrap items-end gap-2" role="search">
              <div className="min-w-44 flex-1">
                <label htmlFor="project-search" className="mb-1 block text-xs text-fg-muted">
                  Search projects
                </label>
                <div className="relative">
                  <IconSearch size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-subtle" />
                  <input id="project-search" className={`${inputClass} pl-8`} value={searchText} onChange={(e) => setSearchText(e.target.value)} />
                </div>
              </div>
              <div className="w-36">
                <label htmlFor="project-tag" className="mb-1 block text-xs text-fg-muted">
                  Tag
                </label>
                <input id="project-tag" className={inputClass} value={tagText} onChange={(e) => setTagText(e.target.value)} />
              </div>
              <Button type="submit" variant="secondary">
                Apply
              </Button>
              <label className="flex h-8.5 items-center gap-1.5 rounded-full border border-line/70 bg-raised/50 px-3 text-[13px] text-fg-muted">
                <input
                  type="checkbox"
                  className="accent-accent"
                  checked={filters.archived}
                  onChange={(event) => setFilters((current) => ({ ...current, archived: event.target.checked }))}
                />
                Show archived
              </label>
            </form>
          </div>

          {projects.isLoading ? (
            <div className="grid gap-3 p-5 sm:grid-cols-2" aria-label="Loading projects">
              <Skeleton className="h-32 w-full rounded-xl" />
              <Skeleton className="h-32 w-full rounded-xl" />
            </div>
          ) : null}
          {projects.isError ? (
            <div className="p-4">
              <ErrorNotice error={projects.error} />
            </div>
          ) : null}
          {projects.isSuccess && items.length === 0 && !filtered ? (
            <EmptyState
              icon={<IconFolder size={28} />}
              title="No projects yet"
              action={
                <LinkButton to="/projects/new" variant="secondary">
                  Create your first project
                </LinkButton>
              }
            >
              A project holds the files you analyse. A location is optional, and one kind of input (photographs,
              rasters or video) is enough.
            </EmptyState>
          ) : null}
          {projects.isSuccess && items.length === 0 && filtered ? (
            <EmptyState title="No projects match these filters">Clear the search or tag to see all projects.</EmptyState>
          ) : null}
          {items.length > 0 ? (
            <ul aria-label="Project list" className="grid gap-3 p-5 sm:grid-cols-2">
              {items.map((project) => (
                <ProjectRow key={project.id} project={project} />
              ))}
            </ul>
          ) : null}
          {projects.hasNextPage ? (
            <div className="border-t border-line/70 p-3">
              <Button variant="secondary" size="sm" onClick={() => void projects.fetchNextPage()} disabled={projects.isFetchingNextPage}>
                Load more
              </Button>
            </div>
          ) : null}
        </section>

        <RecentActivity />
      </div>
    </div>
  );
}
