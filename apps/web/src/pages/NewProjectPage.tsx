import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, type Crs, createProject, type ProjectCreate, searchCrs } from "../api/client";
import { CrsPicker } from "../components/CrsPicker";
import { PlaceSearch } from "../components/PlaceSearch";
import {
  Breadcrumbs,
  Button,
  ErrorNotice,
  Field,
  inputClass,
  Notice,
  PageHeader,
  Panel,
  selectClass,
} from "../components/ui";
import { type AoiFields, type AoiMode, buildAoiGeometry, emptyAoiFields, parseTags } from "../lib/aoi";
import { coordinateHeadings, xyAxes } from "../lib/axes";
import type { PlaceMatch } from "../lib/mapbox";
import { ACK_REQUIRED, acknowledgementFromContext } from "../lib/transformation";
import { AccuracyAcknowledgement } from "../components/AccuracyAcknowledgement";

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
  // Chosen only when the API says several registered datum transformations apply to the area. Never pre-selected.
  const [operation, setOperation] = useState<string>("");
  // The person's acknowledgement of a datum shift worse than 10 m, kept with the area of interest. Never pre-ticked.
  const [acknowledged, setAcknowledged] = useState(false);

  const create = useMutation({
    mutationFn: (body: ProjectCreate) => createProject(body),
    onSuccess: (project) => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      navigate(`/projects/${project.id}`);
    },
  });

  // A message about the form stops being true as soon as the form changes.
  useEffect(() => {
    setFormError(null);
  }, [name, withLocation, mode, fields, crs]);
  useEffect(() => {
    setOperation("");
    setAcknowledged(false);
  }, [mode, fields, crs]);

  const headings = coordinateHeadings(crs?.axis_order);

  // A place chosen from the search becomes a single point in EPSG:4326, entered the way this form always takes it:
  // x is whichever of longitude or latitude the CRS lists first once its axes are put in x-then-y order.
  const [placeNote, setPlaceNote] = useState<string | null>(null);
  const [placeError, setPlaceError] = useState<string | null>(null);
  const usePlace = async (match: PlaceMatch) => {
    setPlaceError(null);
    try {
      const found = await searchCrs("EPSG:4326", 10);
      const wgs84 = found.items.find((item) => item.identifier === "EPSG:4326");
      const axes = wgs84 ? xyAxes(wgs84.axis_order) : null;
      if (!wgs84 || !axes) {
        setPlaceError("EPSG:4326 could not be read from the local CRS registry, so the place was not filled in.");
        return;
      }
      const xIsEast = axes[0].direction === "east" || axes[0].direction === "west";
      setWithLocation(true);
      setMode("point");
      setCrs(wgs84);
      setFields({ ...emptyAoiFields, x: String(xIsEast ? match.lon : match.lat), y: String(xIsEast ? match.lat : match.lon) });
      setPlaceNote(`${match.label} (${match.lat.toFixed(5)}, ${match.lon.toFixed(5)})`);
    } catch {
      setPlaceError("The local CRS registry could not be reached, so the place was not filled in.");
    }
  };

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
        setFormError(
          "Choose a CRS for the area of interest, or remove the location. A location without a CRS is not accepted.",
        );
        return;
      }
      const built = buildAoiGeometry(mode, fields);
      if (!built.ok) {
        setFormError(built.error);
        return;
      }
      body.aoi = {
        geometry: built.geometry as Record<string, unknown>,
        crs_id: crs.identifier,
        acknowledge_accuracy: acknowledged,
        ...(operation ? { transformation_operation: operation } : {}),
      };
    }
    create.mutate(body);
  };

  const coordinate = (id: keyof AoiFields, label: string) => (
    <Field label={label} htmlFor={`aoi-${id}`}>
      <input id={`aoi-${id}`} className={inputClass} inputMode="decimal" value={fields[id]} onChange={setField(id)} />
    </Field>
  );

  return (
    <div className="mx-auto max-w-5xl">
      <Breadcrumbs items={[{ label: "Projects", to: "/dashboard" }, { label: "New project" }]} />
      <div className="mt-2">
        <PageHeader
          title="Start a New Project"
          description="Name the project, and optionally record the area it concerns in a coordinate reference system."
        />
      </div>

      <form onSubmit={submit} noValidate className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="min-w-0 space-y-4">
          <Panel title="Project">
            <div className="space-y-4">
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
            </div>
          </Panel>

          <Panel
            title="Location (optional)"
            description="An area of interest you provide. It is context for the project, not a property of any file."
          >
            <div className="space-y-4">
              <label className="flex items-center gap-2 text-[13px] text-fg">
                <input
                  type="checkbox"
                  className="accent-accent"
                  checked={withLocation}
                  onChange={(e) => setWithLocation(e.target.checked)}
                />
                Add an area of interest
              </label>

              {withLocation ? (
                <div className="space-y-4 border-l-2 border-line pl-4">
                  <Field
                    label="Coordinate reference system"
                    htmlFor="crs-search"
                    hint="Searched in the CRS registry installed on this machine. Works offline."
                  >
                    <CrsPicker selected={crs} onSelect={setCrs} />
                  </Field>

                  <fieldset>
                    <legend className="mb-1.5 text-[13px] font-medium">Area type</legend>
                    <div className="flex gap-4 text-[13px]" role="radiogroup" aria-label="Area type">
                      <label className="flex items-center gap-1.5">
                        <input type="radio" className="accent-accent" name="aoi-mode" checked={mode === "point"} onChange={() => setMode("point")} />
                        Single point
                      </label>
                      <label className="flex items-center gap-1.5">
                        <input type="radio" className="accent-accent" name="aoi-mode" checked={mode === "bbox"} onChange={() => setMode("bbox")} />
                        Bounding box
                      </label>
                    </div>
                  </fieldset>

                  <p className="text-xs text-fg-subtle">
                    Enter coordinates as X then Y in the chosen CRS, X being the first coordinate whichever way that axis runs (see
                    the headings once a CRS is chosen).
                    {crs ? ` Units: ${crs.units.join(", ")}.` : " Choose a CRS to see its units."}
                  </p>
                  {mode === "point" ? (
                    <div className="grid gap-3 sm:grid-cols-2">
                      {coordinate("x", headings.x)}
                      {coordinate("y", headings.y)}
                    </div>
                  ) : (
                    <div className="grid gap-3 sm:grid-cols-2">
                      {coordinate("minX", "Minimum X")}
                      {coordinate("minY", "Minimum Y")}
                      {coordinate("maxX", "Maximum X")}
                      {coordinate("maxY", "Maximum Y")}
                    </div>
                  )}
                </div>
              ) : null}

              <Field
                label="Place search"
                htmlFor="place-search"
                hint="Optional shortcut. The words you type are sent to Mapbox (online). Choosing a result fills in a single point above; check it before you create the project."
              >
                <PlaceSearch onPick={(match) => void usePlace(match)} />
              </Field>
              {placeError ? <Notice tone="error" title={placeError} /> : null}
              {placeNote ? (
                <Notice tone="success" title="Place filled in">
                  <p data-testid="place-filled">
                    {placeNote}. Set as a single point in EPSG:4326 above. Search coordinates are Mapbox's, not measured by this app.
                  </p>
                </Notice>
              ) : null}
            </div>
          </Panel>

          <Panel title="Source preference">
            <fieldset className="space-y-1.5 text-[13px]">
              <legend className="sr-only">Source preference</legend>
              <label className="flex items-center gap-2 text-fg">
                <input type="radio" className="accent-accent" name="source-preference" checked readOnly />
                Local files only
              </label>
              <label className="flex items-center gap-2 text-fg-subtle">
                <input type="radio" name="source-preference" disabled />
                Allow configured online imagery sources — unavailable: no online source is configured
              </label>
            </fieldset>
          </Panel>

          {formError ? <Notice tone="error" title={formError} /> : null}
          {create.isError ? <ErrorNotice error={create.error} /> : null}
          {create.error instanceof ApiError && create.error.code === ACK_REQUIRED ? (
            (() => {
              const need = acknowledgementFromContext(create.error.context);
              return need ? (
                <AccuracyAcknowledgement
                  need={need}
                  checked={acknowledged}
                  onChange={setAcknowledged}
                  idPrefix="aoi"
                  where="It is recorded with this area of interest, with your name and the time. Tick it, then create the project again."
                />
              ) : null;
            })()
          ) : null}
          {create.error instanceof ApiError && create.error.code === "TRANSFORMATION_SELECTION_REQUIRED" ? (
            <div className="space-y-1.5 rounded-md border border-line bg-raised p-3" data-testid="aoi-transformation-choice">
              <label htmlFor="aoi-operation" className="block text-[13px] font-medium text-fg">
                Datum transformation to WGS 84
              </label>
              <select
                id="aoi-operation"
                className={selectClass}
                value={operation}
                onChange={(event) => setOperation(event.target.value)}
              >
                <option value="">Choose…</option>
                {create.error.fieldErrors.map((candidate) => (
                  <option key={candidate.field} value={candidate.field}>
                    {candidate.field} · {candidate.message}
                  </option>
                ))}
              </select>
              <p className="text-xs text-fg-subtle">
                Several registered transformations apply here and give different positions. None is chosen for you; choose one, then
                create the project again.
              </p>
            </div>
          ) : null}

          <div className="flex gap-2">
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "Creating…" : "Create project"}
            </Button>
            <Button variant="secondary" onClick={() => navigate("/dashboard")}>
              Cancel
            </Button>
          </div>
        </div>

        <aside className="space-y-3 text-[13px] text-fg-muted lg:sticky lg:top-16 lg:self-start" aria-label="About locations">
          <Panel title="Project location vs. file location">
            <div className="space-y-2">
              <p>
                <span className="font-medium text-fg">Project location</span> is an area you type in. It frames the
                project and is stored with the CRS you chose.
              </p>
              <p>
                <span className="font-medium text-fg">A file's footprint</span> comes only from the file itself: a
                GeoTIFF with a valid CRS and geotransform.
              </p>
              <p>
                Photographs and videos are never given coordinates because the project has a location, and a raster
                without a valid CRS is never placed by assumption.
              </p>
            </div>
          </Panel>
          <Panel title="Why a full CRS is required">
            <p>
              Coordinates mean nothing without their reference system. “WGS84 + LCC” names a datum and a projection
              method but not the projection's parameters, so it is not accepted. Use a registry code such as EPSG:4326.
            </p>
          </Panel>
        </aside>
      </form>
    </div>
  );
}
