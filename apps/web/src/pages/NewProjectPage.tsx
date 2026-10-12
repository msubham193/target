import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";

import { type Crs, createProject, type ProjectCreate } from "../api/client";
import { CrsPicker } from "../components/CrsPicker";
import { Button, ErrorNotice, Field, inputClass, Notice, PageTitle } from "../components/ui";
import { type AoiFields, type AoiMode, buildAoiGeometry, emptyAoiFields, parseTags } from "../lib/aoi";

export function NewProjectPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [withLocation, setWithLocation] = useState(false);
  const [mode, setMode] = useState<AoiMode>("point");
  const [fields, setFields] = useState<AoiFields>(emptyAoiFields);
  const [crs, setCrs] = useState<Crs | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: (body: ProjectCreate) => createProject(body),
    onSuccess: (project) => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      navigate(`/projects/${project.id}`);
    },
  });

  const setField = (key: keyof AoiFields) => (event: { target: { value: string } }) =>
    setFields((current) => ({ ...current, [key]: event.target.value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);
    if (!name.trim()) {
      setFormError("Enter a project name.");
      return;
    }
    const body: ProjectCreate = {
      name: name.trim(),
      description: description.trim() || null,
      tags: parseTags(tags),
      source_preference: "local_only",
    };
    if (withLocation) {
      if (!crs) {
        setFormError("Choose a CRS for the area of interest, or remove the location. A location without a CRS is not accepted.");
        return;
      }
      const built = buildAoiGeometry(mode, fields);
      if (!built.ok) {
        setFormError(built.error);
        return;
      }
      body.aoi = { geometry: built.geometry as Record<string, unknown>, crs_id: crs.identifier };
    }
    create.mutate(body);
  };

  const coordinate = (id: keyof AoiFields, label: string) => (
    <Field label={label} htmlFor={`aoi-${id}`}>
      <input id={`aoi-${id}`} className={inputClass} inputMode="decimal" value={fields[id]} onChange={setField(id)} />
    </Field>
  );

  return (
    <form onSubmit={submit} className="max-w-2xl space-y-5" noValidate>
      <PageTitle title="New project" />

      <Field label="Name" htmlFor="project-name">
        <input id="project-name" className={inputClass} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Description (optional)" htmlFor="project-description">
        <textarea
          id="project-description"
          className={inputClass}
          rows={3}
          value={description}
          maxLength={5000}
          onChange={(e) => setDescription(e.target.value)}
        />
      </Field>
      <Field label="Tags (optional)" htmlFor="project-tags" hint="Separate tags with commas.">
        <input id="project-tags" className={inputClass} value={tags} onChange={(e) => setTags(e.target.value)} />
      </Field>

      <fieldset className="space-y-3 rounded border border-slate-300 bg-white p-4">
        <legend className="px-1 font-semibold">Location (optional)</legend>
        <p className="text-sm text-slate-600">
          An area of interest is context you provide. It is never treated as proof of where an uploaded image or video
          was captured, and it is never used to place a file on the map.
        </p>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={withLocation} onChange={(e) => setWithLocation(e.target.checked)} />
          Add an area of interest
        </label>

        {withLocation ? (
          <div className="space-y-3">
            <Field
              label="Coordinate reference system"
              htmlFor="crs-search"
              hint="Searched in the CRS registry installed on this machine. Works offline."
            >
              <CrsPicker selected={crs} onSelect={setCrs} />
            </Field>

            <div className="flex gap-4 text-sm" role="radiogroup" aria-label="Area type">
              <label className="flex items-center gap-1">
                <input type="radio" name="aoi-mode" checked={mode === "point"} onChange={() => setMode("point")} />
                Single point
              </label>
              <label className="flex items-center gap-1">
                <input type="radio" name="aoi-mode" checked={mode === "bbox"} onChange={() => setMode("bbox")} />
                Bounding box
              </label>
            </div>

            <p className="text-xs text-slate-600">
              Enter coordinates as X then Y in the chosen CRS: X is easting or longitude, Y is northing or latitude.
              {crs ? ` Units: ${crs.units.join(", ")}.` : ""}
            </p>
            {mode === "point" ? (
              <div className="grid grid-cols-2 gap-3">
                {coordinate("x", "X (easting / longitude)")}
                {coordinate("y", "Y (northing / latitude)")}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                {coordinate("minX", "Minimum X")}
                {coordinate("minY", "Minimum Y")}
                {coordinate("maxX", "Maximum X")}
                {coordinate("maxY", "Maximum Y")}
              </div>
            )}
          </div>
        ) : null}

        <Field label="Place search" htmlFor="place-search" hint="Needs an online geocoding service. None is configured, so this is unavailable.">
          <input id="place-search" className={inputClass} disabled placeholder="Unavailable offline" />
        </Field>
      </fieldset>

      <fieldset className="space-y-1 rounded border border-slate-300 bg-white p-4">
        <legend className="px-1 font-semibold">Source preference</legend>
        <label className="flex items-center gap-2 text-sm">
          <input type="radio" name="source-preference" checked readOnly />
          Local files only
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-500">
          <input type="radio" name="source-preference" disabled />
          Allow configured online imagery sources — unavailable: no online source is configured
        </label>
      </fieldset>

      {formError ? <Notice tone="error" title={formError} /> : null}
      {create.isError ? <ErrorNotice error={create.error} /> : null}

      <div className="flex gap-2">
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create project"}
        </Button>
        <Button variant="secondary" onClick={() => navigate("/dashboard")}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
