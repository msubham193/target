import { useInfiniteQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";

import { listProjects, type Project } from "../api/client";
import { Button, ErrorNotice, inputClass, Loading, Notice, PageTitle } from "../components/ui";

const PAGE_SIZE = 12;

function ProjectCard({ project }: { project: Project }) {
  return (
    <li className="rounded border border-slate-300 bg-white p-3">
      <Link to={`/projects/${project.id}`} className="font-semibold text-blue-800 underline">
        {project.name}
      </Link>
      {project.description ? <p className="mt-1 line-clamp-2 text-sm text-slate-700">{project.description}</p> : null}
      <p className="mt-2 text-xs text-slate-600">
        Created {new Date(project.created_at).toLocaleString()} by {project.created_by.display_name}
      </p>
      <p className="text-xs text-slate-600">
        Location: {project.aoi ? `area of interest entered (${String(project.aoi.source.crs_id)})` : "none entered"}
      </p>
      {project.tags.length > 0 ? (
        <ul className="mt-2 flex flex-wrap gap-1" aria-label="Tags">
          {project.tags.map((tag) => (
            <li key={tag} className="rounded bg-slate-200 px-1.5 py-0.5 text-xs">
              {tag}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
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
  const filtered = Boolean(filters.q || filters.tag || filters.archived);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setFilters((current) => ({ ...current, q: searchText.trim(), tag: tagText.trim() }));
  };

  return (
    <div>
      <PageTitle
        title="Dashboard"
        actions={
          <Link to="/projects/new" className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700">
            Create project
          </Link>
        }
      />

      <form onSubmit={submit} className="mb-4 flex flex-wrap items-end gap-2" role="search">
        <div className="grow">
          <label htmlFor="project-search" className="block text-sm font-medium">
            Search projects
          </label>
          <input
            id="project-search"
            className={inputClass}
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
          />
        </div>
        <div>
          <label htmlFor="project-tag" className="block text-sm font-medium">
            Tag
          </label>
          <input id="project-tag" className={inputClass} value={tagText} onChange={(event) => setTagText(event.target.value)} />
        </div>
        <Button type="submit" variant="secondary">
          Apply
        </Button>
        <label className="flex items-center gap-1 text-sm">
          <input
            type="checkbox"
            checked={filters.archived}
            onChange={(event) => setFilters((current) => ({ ...current, archived: event.target.checked }))}
          />
          Show archived
        </label>
      </form>

      <section aria-labelledby="projects-heading" className="space-y-3">
        <h2 id="projects-heading" className="font-semibold">
          Projects
        </h2>
        {projects.isLoading ? <Loading label="Loading projects…" /> : null}
        {projects.isError ? <ErrorNotice error={projects.error} /> : null}
        {projects.isSuccess && items.length === 0 && !filtered ? (
          <Notice title="No projects yet">
            <p>Create a project to start. A location is optional, and one kind of input (photos, rasters or video) is enough.</p>
            <Link to="/projects/new" className="text-blue-700 underline">
              Create your first project
            </Link>
          </Notice>
        ) : null}
        {projects.isSuccess && items.length === 0 && filtered ? (
          <Notice title="No projects match these filters" />
        ) : null}
        {items.length > 0 ? (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Project list">
            {items.map((project) => (
              <ProjectCard key={project.id} project={project} />
            ))}
          </ul>
        ) : null}
        {projects.hasNextPage ? (
          <Button variant="secondary" onClick={() => void projects.fetchNextPage()} disabled={projects.isFetchingNextPage}>
            Load more
          </Button>
        ) : null}
      </section>

      <section aria-labelledby="activity-heading" className="mt-8 space-y-2">
        <h2 id="activity-heading" className="font-semibold">
          Jobs and recent activity
        </h2>
        <Notice>Processing jobs are not part of this phase, so there is nothing to show here yet.</Notice>
      </section>
    </div>
  );
}
