import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";

import { getProjectSummary, type Project, type ProjectUpdate, updateProject } from "../api/client";
import { IconChevronRight, IconImage, IconList, IconMap, IconReport, IconSatellite, IconUpload, IconVideo } from "../components/icons";
import {
  Breadcrumbs,
  Button,
  DescriptionList,
  ErrorNotice,
  Field,
  inputClass,
  LinkButton,
  Notice,
  PageHeader,
  Panel,
  Skeleton,
} from "../components/ui";
import { parseTags } from "../lib/aoi";
import { describeGeometry, formatDateTime } from "../lib/format";
import { REVIEW_STATUS_LABELS } from "../lib/jobs";
import { projectCrumbs, useProject } from "./ProjectLayout";

function AoiSummary({ project }: { project: Project }) {
  if (!project.aoi) {
    return <p className="text-[13px] text-fg-muted">No location entered. A project does not need one.</p>;
  }
  const source = project.aoi.source as {
    crs_id?: string;
    geometry?: unknown;
    transformation?: { definition?: string; proj_version?: string; accuracy_m?: number | null };
  };
  const entered = (source.geometry ?? {}) as { type?: unknown; coordinates?: unknown };
  return (
    <div className="space-y-3 text-[13px]">
      <DescriptionList
        rows={[
          ["Kind", entered.type === "Point" ? "Single point" : "Area"],
          [
            "As entered",
            <>
              {describeGeometry(entered, { x: "X", y: "Y" }) ?? "not readable"}{" "}
              <span className="text-fg-subtle">in {source.crs_id}</span>
            </>,
          ],
          [
            "As latitude and longitude",
            <>
              {describeGeometry(project.aoi.geometry_4326, { x: "longitude", y: "latitude" }, 6) ?? "not readable"}{" "}
              <span className="text-fg-subtle">(worked out from the entry above, to 6 decimal places; EPSG:4326)</span>
            </>,
          ],
        ]}
      />
      <details className="text-xs text-fg-subtle">
        <summary className="cursor-pointer text-fg-muted">How the latitude and longitude were worked out</summary>
        <p className="mt-1 wrap-anywhere">
          Transformation: {source.transformation?.definition ?? "not recorded"} · PROJ{" "}
          {source.transformation?.proj_version ?? "unknown"} · stated accuracy:{" "}
          {source.transformation?.accuracy_m == null ? "not stated" : `${source.transformation.accuracy_m} m`}
        </p>
      </details>
      {project.aoi.warnings.includes("AOI_OUTSIDE_CRS_AREA_OF_USE") ? (
        <Notice tone="warning" title="The coordinates fall outside this CRS's stated area of use">
          They were stored exactly as entered. Check that the CRS and the coordinate order are right.
        </Notice>
      ) : null}
      <p className="text-xs text-fg-subtle">This area is context you entered. It does not show where any uploaded file was captured.</p>
    </div>
  );
}

/** The next things a person is likely to want, in plain words, one click each. */
function NextSteps({ project }: { project: Project }) {
  const base = `/projects/${project.id}`;
  const steps = [
    { to: `${base}/assets/new`, icon: IconUpload, title: "Add files", text: "Upload photos, satellite images or video." },
    { to: `${base}/map`, icon: IconMap, title: "Look at the map", text: "See your files and photos where they belong." },
    { to: `${base}/workspace`, icon: IconImage, title: "Work with photos", text: "Find objects in a photo and check the results." },
    { to: `${base}/observations`, icon: IconList, title: "Review what was found", text: "Go through the findings so far." },
    { to: `${base}/reports`, icon: IconReport, title: "Make a report", text: "Save your results so you can share them." },
  ];
  return (
    <section aria-labelledby="next-steps-heading" className="rise-in">
      <h2 id="next-steps-heading" className="mb-2.5 text-sm font-semibold text-fg">
        What would you like to do?
      </h2>
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5" aria-label="Things to do next">
        {steps.map(({ to, icon: Icon, title, text }) => (
          <li key={to}>
            <Link
              to={to}
              className="group flex h-full flex-col gap-2 rounded-xl border border-line/80 bg-surface p-4 transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/60 hover:shadow-[0_10px_28px_rgb(0_0_0/0.3)]"
            >
              <span className="flex size-9 items-center justify-center rounded-lg bg-accent-soft text-accent" aria-hidden="true">
                <Icon size={18} />
              </span>
              <span className="flex items-center gap-1 text-sm font-medium text-fg">
                {title}
                <IconChevronRight size={14} className="text-fg-subtle transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
              </span>
              <span className="text-xs leading-relaxed text-fg-muted">{text}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Contents({ project }: { project: Project }) {
  const summary = useQuery({ queryKey: ["summary", project.id], queryFn: () => getProjectSummary(project.id) });
  if (summary.isLoading) return <Skeleton className="h-20 w-full" />;
  if (summary.isError) return <ErrorNotice error={summary.error} />;
  const data = summary.data!;
  const base = `/projects/${project.id}`;
  const tile = (Icon: typeof IconImage, label: string, count: number, to: string) => (
    <Link to={to} className="flex items-center gap-3 rounded-md border border-line bg-raised px-3 py-2.5 hover:border-accent/50">
      <Icon size={18} className="text-fg-subtle" />
      <span className="min-w-0 flex-1">
        <span className="block text-lg leading-tight font-semibold text-fg">{count}</span>
        <span className="block truncate text-xs text-fg-muted">{label}</span>
      </span>
    </Link>
  );
  const reviews = Object.entries(data.observations_by_review_status);
  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {tile(IconImage, "Photographs", data.assets_by_modality.ground_image ?? 0, `${base}/ground`)}
        {tile(IconSatellite, "Satellite and aerial images", data.assets_by_modality.overhead_raster ?? 0, `${base}/satellite`)}
        {tile(IconVideo, "Videos", data.assets_by_modality.video ?? 0, `${base}/video`)}
        {tile(IconList, "Findings", data.observations_total, `${base}/observations`)}
      </div>
      <p className="text-xs text-fg-subtle">
        {reviews.length > 0
          ? `Findings by review state: ${reviews.map(([status, n]) => `${REVIEW_STATUS_LABELS[status] ?? status} ${n}`).join(", ")}. `
          : ""}
        Last activity {formatDateTime(data.last_activity_at)}.
      </p>
    </div>
  );
}

export function ProjectOverviewPage() {
  const project = useProject();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? "");
  const [tags, setTags] = useState(project.tags.join(", "));

  const update = useMutation({
    mutationFn: (body: ProjectUpdate) => updateProject(project.id, body),
    onSuccess: (updated) => {
      queryClient.setQueryData(["project", project.id], updated);
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      setEditing(false);
    },
  });

  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    update.mutate({ name: name.trim(), description: description.trim() || null, tags: parseTags(tags) });
  };

  return (
    <div className="mx-auto max-w-5xl">
      <Breadcrumbs items={projectCrumbs(project)} />
      <div className="mt-2">
        <PageHeader
          title={project.name}
          description={project.description || undefined}
          meta={
            <>
              {project.archived_at ? <span className="text-warn">Archived</span> : null}
              <span>
                Created {formatDateTime(project.created_at)} by {project.created_by.display_name}
              </span>
              {project.tags.length > 0 ? <span>Tags: {project.tags.join(", ")}</span> : null}
            </>
          }
          actions={
            <>
              <LinkButton to={`/projects/${project.id}/assets/new`}>
                <IconUpload size={15} />
                Upload files
              </LinkButton>
              {!editing ? (
                <Button variant="secondary" onClick={() => setEditing(true)}>
                  Edit details
                </Button>
              ) : null}
              <Button variant="secondary" disabled={update.isPending} onClick={() => update.mutate({ archived: !project.archived_at })}>
                {project.archived_at ? "Restore project" : "Archive project"}
              </Button>
            </>
          }
        />
      </div>
      {update.isError ? <ErrorNotice error={update.error} /> : null}

      <div className="space-y-4">
        {editing ? (
          <Panel title="Edit details">
            <form onSubmit={save} className="max-w-xl space-y-3">
              <Field label="Name" htmlFor="edit-name">
                <input id="edit-name" className={inputClass} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="Description" htmlFor="edit-description">
                <textarea id="edit-description" className={inputClass} rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
              </Field>
              <Field label="Tags" htmlFor="edit-tags" hint="Separate tags with commas.">
                <input id="edit-tags" className={inputClass} value={tags} onChange={(e) => setTags(e.target.value)} />
              </Field>
              <div className="flex gap-2">
                <Button type="submit" disabled={update.isPending || !name.trim()}>
                  Save
                </Button>
                <Button variant="secondary" onClick={() => setEditing(false)}>
                  Cancel
                </Button>
              </div>
            </form>
          </Panel>
        ) : null}

        <NextSteps project={project} />

        <Panel title="Contents" description="Counted from what is stored.">
          <Contents project={project} />
        </Panel>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel title="Location">
            <AoiSummary project={project} />
          </Panel>
          <Panel title="Details">
            <DescriptionList
              rows={[
                ["Sources", project.source_preference === "local_only" ? "Local files only" : "Online sources allowed"],
                ["Last changed", formatDateTime(project.updated_at)],
                ["Project ID", <span className="font-mono text-xs">{project.id}</span>],
              ]}
            />
          </Panel>
        </div>
      </div>
    </div>
  );
}
