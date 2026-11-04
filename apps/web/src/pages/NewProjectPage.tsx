import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useEffect, useId, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, type Crs, createProject, type ProjectCreate, searchCrs } from "../api/client";
import { AccuracyAcknowledgement } from "../components/AccuracyAcknowledgement";
import { CrsPicker } from "../components/CrsPicker";
import { IconCheck, IconFolderPlus, IconGlobe, IconInfo, IconMap } from "../components/icons";
import { MapBackdrop } from "../components/MapBackdrop";
import { PlaceSearch } from "../components/PlaceSearch";
import { Badge, Breadcrumbs, Button, ErrorNotice, Field, inputClass, Notice, selectClass } from "../components/ui";
import { type AoiFields, type AoiMode, buildAoiGeometry, emptyAoiFields, parseTags } from "../lib/aoi";
import { coordinateHeadings, xyAxes } from "../lib/axes";
import type { PlaceMatch } from "../lib/mapbox";
import { ACK_REQUIRED, acknowledgementFromContext } from "../lib/transformation";

/** One numbered step of the form, drawn as a frosted card so the map behind it stays visible. */
function Step({
  step,
  title,
  description,
  children,
}: {
  step: number;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  const headingId = useId();
  return (
    <section
      aria-labelledby={headingId}
      className="rise-in rounded-2xl border border-white/10 bg-surface/70 shadow-panel backdrop-blur-xl"
    >
      <div className="flex items-start gap-3 border-b border-white/10 px-5 py-4">
        <span
          aria-hidden="true"
          className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent ring-1 ring-accent/30"
        >
          {step}
        </span>
        <div className="min-w-0">
          <h2 id={headingId} className="text-sm font-semibold text-fg">
            {title}
          </h2>
          {description ? <p className="mt-0.5 text-xs text-fg-muted">{description}</p> : null}
        </div>
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

/** A choice drawn as a card. The input keeps its plain accessible name; the card only changes how it looks. */
function ChoiceCard({
  name,
  label,
  checked,
  onChange,
  icon,
}: {
  name: string;
  label: string;
  checked: boolean;
  onChange: () => void;
  icon: ReactNode;
}) {
  return (
    <label
      className={`relative flex cursor-pointer items-center gap-2.5 rounded-xl border px-3.5 py-3 text-[13px] transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent/50 ${
        checked
          ? "border-accent/60 bg-accent-soft text-fg"
          : "border-line-strong bg-raised/60 text-fg-muted hover:border-fg-subtle hover:text-fg"
      }`}
    >
      <input type="radio" className="sr-only" name={name} aria-label={label} checked={checked} onChange={onChange} />
      <span className={checked ? "text-accent" : "text-fg-subtle"}>{icon}</span>
      <span className="font-medium">{label}</span>
      {checked ? <IconCheck size={14} className="ml-auto text-accent" /> : null}
    </label>
  );
}

const glassCard = "rise-in rounded-2xl border border-white/10 bg-surface/70 shadow-panel backdrop-blur-xl";

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

  const tagList = parseTags(tags);
  const locationSummary = !withLocation
    ? null
    : mode === "point"
      ? fields.x.trim() && fields.y.trim()
        ? `${fields.x.trim()}, ${fields.y.trim()}`
        : "point not entered yet"
      : "bounding box";
  const ready = name.trim().length > 0;

  return (
    <div className="relative isolate -mx-4 -my-5 min-h-[calc(100vh-3rem)] px-4 py-8 sm:-mx-6 sm:px-6">
      <MapBackdrop />
      <div className="mx-auto max-w-5xl">
        <Breadcrumbs items={[{ label: "Projects", to: "/dashboard" }, { label: "New project" }]} />

        <header className="rise-in mb-7 mt-4 flex items-start gap-4">
          <span
            aria-hidden="true"
            className="flex size-12 shrink-0 items-center justify-center rounded-2xl bg-accent text-accent-fg shadow-[0_0_32px_rgb(91_155_255/0.45)]"
          >
            <IconFolderPlus size={22} />
          </span>
          <div className="min-w-0">
            <h1 className="text-3xl font-semibold tracking-tight text-fg">
              Start a <span className="bg-gradient-to-r from-accent to-ok bg-clip-text text-transparent">New Project</span>
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-fg-muted">
              Name the project, and optionally record the area it concerns in a coordinate reference system.
            </p>
          </div>
        </header>

        <form onSubmit={submit} noValidate className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_19rem]">
          <div className="min-w-0 space-y-5">
            <Step step={1} title="Project" description="What you will call it, and anything that helps you find it later.">
              <div className="space-y-4">
                <Field label="Name" htmlFor="project-name">
                  <input
                    id="project-name"
                    className={`${inputClass} text-[15px]`}
                    placeholder="e.g. Harbour survey"
                    value={name}
                    maxLength={200}
                    onChange={(e) => setName(e.target.value)}
                  />
                </Field>
                <Field label="Description (optional)" htmlFor="project-description">
                  <textarea
                    id="project-description"
                    className={`${inputClass} resize-y`}
                    rows={3}
                    placeholder="What is this project about?"
                    value={description}
                    maxLength={5000}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </Field>
                <Field label="Tags (optional)" htmlFor="project-tags" hint="Separate tags with commas.">
                  <input
                    id="project-tags"
                    className={inputClass}
                    placeholder="coast, demo"
                    value={tags}
                    onChange={(e) => setTags(e.target.value)}
                  />
                </Field>
                {tagList.length > 0 ? (
                  <ul className="-mt-1 flex flex-wrap gap-1.5" aria-label="Tags so far">
                    {tagList.map((tag) => (
                      <li key={tag}>
                        <Badge tone="accent" size="md">
                          {tag}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            </Step>

            <Step
              step={2}
              title="Location (optional)"
              description="An area of interest you provide. It is context for the project, not a property of any file."
            >
              <div className="space-y-5">
                <div className="space-y-3 rounded-xl border border-white/10 bg-canvas/40 p-4">
                  <Field
                    label="Place search"
                    htmlFor="place-search"
                    hint="Optional shortcut. The words you type are sent to Mapbox (online). Choosing a result fills in a single point below; check it before you create the project."
                  >
                    <PlaceSearch onPick={(match) => void usePlace(match)} />
                  </Field>
                  {placeError ? <Notice tone="error" title={placeError} /> : null}
                  {placeNote ? (
                    <Notice tone="success" title="Place filled in">
                      <p data-testid="place-filled">
                        {placeNote}. Set as a single point in EPSG:4326 below. Search coordinates are Mapbox's, not measured by this app.
                      </p>
                    </Notice>
                  ) : null}
                </div>

                <div className="flex items-center justify-between gap-4 rounded-xl border border-line-strong bg-raised/60 px-4 py-3">
                  <div className="min-w-0">
                    <label htmlFor="aoi-toggle" className="block cursor-pointer text-[13px] font-medium text-fg">
                      Add an area of interest
                    </label>
                    <p className="text-xs text-fg-muted">Enter a point or box in a coordinate reference system.</p>
                  </div>
                  <span className="relative inline-flex shrink-0 items-center">
                    <input
                      id="aoi-toggle"
                      type="checkbox"
                      className="peer absolute inset-0 z-10 m-0 size-full cursor-pointer opacity-0"
                      checked={withLocation}
                      onChange={(e) => setWithLocation(e.target.checked)}
                    />
                    <span className="h-6 w-11 rounded-full bg-overlay ring-1 ring-line-strong transition-colors peer-checked:bg-accent peer-checked:ring-accent peer-focus-visible:ring-2 peer-focus-visible:ring-accent/60" />
                    <span className="pointer-events-none absolute left-0.5 size-5 rounded-full bg-fg shadow transition-transform peer-checked:translate-x-5 peer-checked:bg-accent-fg" />
                  </span>
                </div>

                {withLocation ? (
                  <div className="rise-in space-y-5 rounded-xl border border-accent/25 bg-accent-soft/40 p-4">
                    <Field
                      label="Coordinate reference system"
                      htmlFor="crs-search"
                      hint="Searched in the CRS registry installed on this machine. Works offline."
                    >
                      <CrsPicker selected={crs} onSelect={setCrs} />
                    </Field>

                    <fieldset>
                      <legend className="mb-1.5 text-[13px] font-medium">Area type</legend>
                      <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Area type">
                        <ChoiceCard
                          name="aoi-mode"
                          label="Single point"
                          checked={mode === "point"}
                          onChange={() => setMode("point")}
                          icon={<IconGlobe size={16} />}
                        />
                        <ChoiceCard
                          name="aoi-mode"
                          label="Bounding box"
                          checked={mode === "bbox"}
                          onChange={() => setMode("bbox")}
                          icon={<IconMap size={16} />}
                        />
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
              </div>
            </Step>

            <Step step={3} title="Source preference" description="Where imagery for this project may come from.">
              <fieldset className="grid gap-2 text-[13px] sm:grid-cols-2">
                <legend className="sr-only">Source preference</legend>
                <label className="flex items-center gap-2.5 rounded-xl border border-accent/60 bg-accent-soft px-3.5 py-3 text-fg">
                  <input type="radio" className="accent-accent" name="source-preference" checked readOnly />
                  <span className="font-medium">Local files only</span>
                </label>
                <label className="flex items-start gap-2.5 rounded-xl border border-dashed border-line-strong px-3.5 py-3 text-fg-subtle">
                  <input type="radio" className="mt-0.5" name="source-preference" disabled />
                  <span>Allow configured online imagery sources — unavailable: no online source is configured</span>
                </label>
              </fieldset>
            </Step>

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
              <div className="space-y-1.5 rounded-xl border border-line bg-raised p-4" data-testid="aoi-transformation-choice">
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

            <div className="sticky bottom-4 z-20 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/10 bg-surface/80 px-4 py-3 shadow-panel backdrop-blur-xl">
              <p className={`flex items-center gap-1.5 text-xs ${ready ? "text-ok" : "text-fg-muted"}`}>
                {ready ? <IconCheck size={14} /> : <IconInfo size={14} />}
                {ready ? "Ready to create." : "Give the project a name to continue."}
              </p>
              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => navigate("/dashboard")}>
                  Cancel
                </Button>
                <Button type="submit" disabled={create.isPending}>
                  {create.isPending ? "Creating…" : "Create project"}
                </Button>
              </div>
            </div>
          </div>

          <aside className="space-y-4 text-[13px] text-fg-muted lg:sticky lg:top-16 lg:self-start" aria-label="About locations">
            <section aria-label="Project summary" className={`${glassCard} p-5`}>
              <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-fg-subtle">Summary</p>
              <p className={`mt-2 wrap-anywhere text-base font-semibold ${ready ? "text-fg" : "text-fg-subtle"}`}>
                {ready ? name.trim() : "Untitled project"}
              </p>
              <dl className="mt-3 space-y-2.5 text-xs">
                <div className="flex justify-between gap-3">
                  <dt className="text-fg-subtle">Location</dt>
                  <dd className="min-w-0 text-right text-fg-muted wrap-anywhere">
                    {locationSummary ? (
                      <>
                        {crs ? <span className="font-mono">{crs.identifier}</span> : "CRS not chosen"}
                        <br />
                        {locationSummary}
                      </>
                    ) : (
                      "None"
                    )}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-fg-subtle">Tags</dt>
                  <dd className="text-fg-muted">{tagList.length > 0 ? tagList.length : "None"}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-fg-subtle">Sources</dt>
                  <dd className="text-fg-muted">Local files only</dd>
                </div>
              </dl>
            </section>

            <section aria-label="Project location versus file location" className={`${glassCard} space-y-2 p-5`}>
              <h2 className="text-sm font-semibold text-fg">Project location vs. file location</h2>
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
            </section>

            <section aria-label="Why a full CRS is required" className={`${glassCard} p-5`}>
              <h2 className="text-sm font-semibold text-fg">Why a full CRS is required</h2>
              <p className="mt-2">
                Coordinates mean nothing without their reference system. “WGS84 + LCC” names a datum and a projection
                method but not the projection's parameters, so it is not accepted. Use a registry code such as EPSG:4326.
              </p>
            </section>
          </aside>
        </form>
      </div>
    </div>
  );
}
