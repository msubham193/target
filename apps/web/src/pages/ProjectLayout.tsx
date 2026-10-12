import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Outlet, useOutletContext, useParams } from "react-router-dom";

import { ApiError, getProject, type Project } from "../api/client";
import { ErrorNotice, Loading, Notice } from "../components/ui";

const links: [string, string][] = [
  ["", "Overview"],
  ["assets/new", "Upload"],
  ["assets", "Assets"],
  ["map", "Map"],
  ["observations", "Observations"],
  ["reports", "Reports"],
  ["3d", "3D"],
];

export function useProject(): Project {
  return useOutletContext<Project>();
}

export function ProjectLayout() {
  const { projectId = "" } = useParams();
  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => getProject(projectId) });

  if (project.isLoading) return <Loading label="Opening project…" />;
  if (project.isError) {
    const missing = project.error instanceof ApiError && [404, 422].includes(project.error.status);
    return (
      <div className="space-y-2">
        {missing ? <Notice tone="error" title="This project does not exist" /> : <ErrorNotice error={project.error} />}
        <Link to="/dashboard" className="text-blue-700 underline">
          Back to the dashboard
        </Link>
      </div>
    );
  }
  const data = project.data!;

  return (
    <div className="grid gap-6 md:grid-cols-[12rem_1fr]">
      <aside>
        <p className="mb-2 break-words font-semibold">{data.name}</p>
        {data.archived_at ? <p className="mb-2 text-xs text-amber-800">Archived</p> : null}
        <nav className="flex flex-col gap-1" aria-label="Project">
          {links.map(([to, label]) => (
            <NavLink
              key={label}
              to={to}
              end={to === "" || to === "assets/new"}
              className={({ isActive }) =>
                `rounded px-2 py-1 text-sm ${isActive ? "bg-slate-200 font-medium" : "hover:bg-slate-100"}`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <section>
        <Outlet context={data} />
      </section>
    </div>
  );
}
