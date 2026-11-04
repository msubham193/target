import { useQuery } from "@tanstack/react-query";
import { Outlet, useOutletContext, useParams } from "react-router-dom";

import { ApiError, getProject, type Project } from "../api/client";
import { EmptyState, ErrorNotice, LinkButton, Loading } from "../components/ui";

export function useProject(): Project {
  return useOutletContext<Project>();
}

/** Breadcrumb trail for a screen inside a project. */
export function projectCrumbs(project: Project, ...rest: { label: string; to?: string }[]) {
  return [{ label: "Projects", to: "/dashboard" }, { label: project.name, to: `/projects/${project.id}` }, ...rest];
}

/** Loads the project once for every screen below /projects/:id and hands it down. */
export function ProjectLayout() {
  const { projectId = "" } = useParams();
  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => getProject(projectId) });

  if (project.isLoading) return <Loading label="Opening project…" />;
  if (project.isError) {
    const missing = project.error instanceof ApiError && [404, 422].includes(project.error.status);
    return missing ? (
      <EmptyState
        title="This project does not exist"
        pageTitle
        action={
          <LinkButton to="/dashboard" variant="secondary">
            Back to the dashboard
          </LinkButton>
        }
      >
        It may have been removed, or the link is wrong.
      </EmptyState>
    ) : (
      <ErrorNotice error={project.error} />
    );
  }
  return <Outlet context={project.data!} />;
}
