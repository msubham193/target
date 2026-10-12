import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { type Project, type ProjectUpdate, updateProject } from "../api/client";
import { Button, ErrorNotice, Field, inputClass, Notice, PageTitle } from "../components/ui";
import { parseTags } from "../lib/aoi";
import { useProject } from "./ProjectLayout";

function AoiSummary({ project }: { project: Project }) {
  if (!project.aoi) {
    return <p className="text-sm text-slate-600">No location entered. A project does not need one.</p>;
  }
  const source = project.aoi.source as {
    crs_id?: string;
    geometry?: unknown;
    transformation?: { definition?: string; proj_version?: string; accuracy_m?: number | null };
  };
  return (
    <div className="space-y-2 text-sm">
      <p>
        Entered in <span className="font-medium">{source.crs_id}</span> (X, Y order as typed):
      </p>
      <pre className="overflow-auto rounded bg-slate-100 p-2 text-xs">{JSON.stringify(source.geometry)}</pre>
      <p>Display copy in EPSG:4326 (longitude, latitude), derived by transformation:</p>
      <pre className="overflow-auto rounded bg-slate-100 p-2 text-xs">{JSON.stringify(project.aoi.geometry_4326)}</pre>
      <p className="text-xs text-slate-600">
        Transformation: {source.transformation?.definition ?? "not recorded"} · PROJ{" "}
        {source.transformation?.proj_version ?? "unknown"} · stated accuracy:{" "}
        {source.transformation?.accuracy_m == null ? "not stated" : `${source.transformation.accuracy_m} m`}
      </p>
      {project.aoi.warnings.includes("AOI_OUTSIDE_CRS_AREA_OF_USE") ? (
        <Notice tone="warning" title="The coordinates fall outside this CRS's stated area of use">
          They were stored exactly as entered. Check that the CRS and the coordinate order are right.
        </Notice>
      ) : null}
      <p className="text-xs text-slate-600">
        This area is context you entered. It does not show where any uploaded file was captured.
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
    <div className="space-y-6">
      <PageTitle
        title={project.name}
        actions={
          <div className="flex gap-2">
            {!editing ? (
              <Button variant="secondary" onClick={() => setEditing(true)}>
                Edit details
              </Button>
            ) : null}
            <Button
              variant="secondary"
              disabled={update.isPending}
              onClick={() => update.mutate({ archived: !project.archived_at })}
            >
              {project.archived_at ? "Restore project" : "Archive project"}
            </Button>
          </div>
        }
      />
      {update.isError ? <ErrorNotice error={update.error} /> : null}

      {editing ? (
        <form onSubmit={save} className="max-w-xl space-y-3 rounded border border-slate-300 bg-white p-4">
          <Field label="Name" htmlFor="edit-name">
            <input id="edit-name" className={inputClass} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Description" htmlFor="edit-description">
            <textarea
              id="edit-description"
              className={inputClass}
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
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
      ) : (
        <dl className="grid max-w-2xl grid-cols-[10rem_1fr] gap-y-1 text-sm">
          <dt className="text-slate-600">Description</dt>
          <dd>{project.description || "—"}</dd>
          <dt className="text-slate-600">Tags</dt>
          <dd>{project.tags.length > 0 ? project.tags.join(", ") : "—"}</dd>
          <dt className="text-slate-600">Created</dt>
          <dd>
            {new Date(project.created_at).toLocaleString()} by {project.created_by.display_name}
          </dd>
          <dt className="text-slate-600">Last changed</dt>
          <dd>{new Date(project.updated_at).toLocaleString()}</dd>
          <dt className="text-slate-600">Sources</dt>
          <dd>{project.source_preference === "local_only" ? "Local files only" : "Online sources allowed"}</dd>
          <dt className="text-slate-600">Project ID</dt>
          <dd className="font-mono text-xs">{project.id}</dd>
        </dl>
      )}

      <section className="max-w-2xl space-y-2">
        <h2 className="font-semibold">Location</h2>
        <AoiSummary project={project} />
      </section>

      <section className="max-w-2xl space-y-2">
        <h2 className="font-semibold">Assets and analysis</h2>
        <Notice>
          Uploading files and running analysis are not part of this phase. This project has no assets, jobs or
          observations.
        </Notice>
      </section>
    </div>
  );
}
