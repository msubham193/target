import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, listMapSheets, type MapSeries, type MapSheetUpload, uploadMapSheet } from "../api/client";
import { Badge, Breadcrumbs, Button, ErrorNotice, Field, inputClass, Loading, Notice, PageHeader, Panel, selectClass } from "../components/ui";
import { formatBytes } from "../lib/ingestion";
import { INTAKE_LABELS, SERIES_LABELS } from "../lib/mapsheet";
import { IngestionNav } from "./ingestionNav";
import { projectCrumbs, useProject } from "./ProjectLayout";

export const MAP_SHEET_EXTENSIONS = [".tif", ".tiff", ".png", ".jpg", ".jpeg", ".pdf"];

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

/**
 * Taking in a map sheet: a georeferenced GeoTIFF, a scanned picture or a PDF. The series is a label; it never gives the
 * sheet a CRS. A sheet whose file has no georeferencing is stored unplaced and is georeferenced afterwards.
 */
export function MapSheetsPage() {
  const project = useProject();
  const queryClient = useQueryClient();
  const [series, setSeries] = useState<MapSeries>("osm");
  const [label, setLabel] = useState("");
  const [scale, setScale] = useState("");
  const [edition, setEdition] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const [last, setLast] = useState<MapSheetUpload | null>(null);
  const sheets = useQuery({ queryKey: ["map-sheets", project.id], queryFn: () => listMapSheets(project.id) });

  const extension = file ? extensionOf(file.name) : "";
  const fileProblem =
    file && !MAP_SHEET_EXTENSIONS.includes(extension)
      ? `A map sheet is ${MAP_SHEET_EXTENSIONS.join(", ")}; this file ends in ${extension || "(no extension)"}.`
      : file && file.size === 0
        ? "The file is empty."
        : null;
  const scaleNumber = scale.trim() === "" ? undefined : Number(scale);
  const scaleProblem = scaleNumber !== undefined && (!Number.isInteger(scaleNumber) || scaleNumber < 1) ? "Enter the N of a 1 : N scale as a whole number." : null;

  const upload = useMutation({
    mutationFn: () =>
      uploadMapSheet(project.id, file!, { series, sheetLabel: label, scaleDenominator: scaleNumber, edition }, setProgress),
    onMutate: () => {
      setProgress(0);
      setLast(null);
    },
    onSuccess: (result) => {
      setLast(result);
      setFile(null);
      void queryClient.invalidateQueries({ queryKey: ["map-sheets", project.id] });
      void queryClient.invalidateQueries({ queryKey: ["assets", project.id] });
      void queryClient.invalidateQueries({ queryKey: ["layers", project.id] });
      void queryClient.invalidateQueries({ queryKey: ["summary", project.id] });
    },
  });

  const ready = Boolean(file) && !fileProblem && !scaleProblem && !project.archived_at && !upload.isPending;

  return (
    <div className="mx-auto max-w-5xl">
      <Breadcrumbs items={projectCrumbs(project, { label: "Map sheets" })} />
      <div className="mt-2">
        <PageHeader
          title="Map sheets"
          description="Open Series (OSM) and Everest Series (ESM) sheets as GeoTIFF, scanned pictures or PDF. The original is stored unmodified."
        />
      </div>
      <IngestionNav projectId={project.id} />

      {project.archived_at ? (
        <Notice tone="warning" title="This project is archived" className="mb-4">
          Restore it from the project overview before adding files.
        </Notice>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_17rem]">
        <div className="min-w-0 space-y-4">
          <Panel title="Add a map sheet">
            <form
              className="grid grid-cols-1 gap-3 sm:grid-cols-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (ready) upload.mutate();
              }}
            >
              <Field label="Series" htmlFor="sheet-series" hint="A label only. It does not give the sheet a CRS, datum or projection.">
                <select id="sheet-series" className={selectClass} value={series} onChange={(event) => setSeries(event.target.value as MapSeries)}>
                  {(Object.keys(SERIES_LABELS) as MapSeries[]).map((key) => (
                    <option key={key} value={key}>
                      {SERIES_LABELS[key]}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Sheet number or name" htmlFor="sheet-label" hint="As printed on the sheet.">
                <input id="sheet-label" className={inputClass} value={label} maxLength={120} onChange={(event) => setLabel(event.target.value)} />
              </Field>
              <Field label="Printed scale: 1 :" htmlFor="sheet-scale" error={scaleProblem} hint="Optional.">
                <input id="sheet-scale" className={inputClass} inputMode="numeric" value={scale} onChange={(event) => setScale(event.target.value)} />
              </Field>
              <Field label="Edition" htmlFor="sheet-edition" hint="Optional.">
                <input id="sheet-edition" className={inputClass} value={edition} maxLength={60} onChange={(event) => setEdition(event.target.value)} />
              </Field>
              <div className="sm:col-span-2">
                <Field
                  label="File"
                  htmlFor="sheet-file"
                  error={fileProblem}
                  hint={`${MAP_SHEET_EXTENSIONS.join(", ")}. Add a world file or .prj for a TIFF from its asset page.`}
                >
                  <input
                    id="sheet-file"
                    type="file"
                    accept={MAP_SHEET_EXTENSIONS.join(",")}
                    className={inputClass}
                    disabled={Boolean(project.archived_at)}
                    onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                  />
                </Field>
                {file ? <p className="mt-1 text-xs text-fg-subtle">{file.name} · {formatBytes(file.size)}</p> : null}
              </div>
              <div className="sm:col-span-2">
                <Button type="submit" disabled={!ready}>
                  {upload.isPending ? "Uploading and checking…" : "Upload map sheet"}
                </Button>
                {upload.isPending ? <progress className="mt-2 block h-1.5 w-full" max={1} value={progress} aria-label="Upload progress" /> : null}
              </div>
            </form>
            {upload.isError ? (
              <div className="mt-3">
                <ErrorNotice
                  error={upload.error}
                  title={upload.error instanceof ApiError && upload.error.status < 500 ? "The file was not accepted" : "The upload failed"}
                />
              </div>
            ) : null}
            {last ? (
              <div className="mt-3 space-y-1 text-[13px]" role="status" data-testid="upload-result">
                <p className="font-medium text-fg">{last.message}</p>
                <p className="text-xs text-fg-muted">
                  {INTAKE_LABELS[last.map_sheet.intake]} · {last.map_sheet.placed ? "placed from its own metadata" : "not placed on the map"}
                </p>
                <p className="text-xs">
                  <Link className="text-accent hover:underline" to={`/projects/${project.id}/assets/${last.asset.id}#map-sheet`}>
                    {last.map_sheet.placed ? "Review its metadata" : "Review what is missing and georeference it"}
                  </Link>
                </p>
              </div>
            ) : null}
          </Panel>

          <Panel title="Map sheets in this project" padded={false}>
            <div className="p-4">
              {sheets.isLoading ? <Loading label="Loading map sheets…" /> : null}
              {sheets.isError ? <ErrorNotice error={sheets.error} /> : null}
              {sheets.data && sheets.data.items.length === 0 ? <p className="text-[13px] text-fg-subtle">No map sheets yet.</p> : null}
              {sheets.data && sheets.data.items.length > 0 ? (
                <ul className="space-y-2" aria-label="Map sheets">
                  {sheets.data.items.map((sheet) => (
                    <li key={sheet.asset_id} className="rounded-md border border-line bg-raised p-3 text-[13px]" aria-label={sheet.filename}>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="min-w-0 wrap-anywhere font-medium text-fg">{sheet.filename}</span>
                        <Badge tone="accent">{SERIES_LABELS[sheet.info.declaration.series]}</Badge>
                        <Badge tone={sheet.info.placed ? "ok" : "warn"}>{sheet.info.placed ? "Placed" : "Not placed"}</Badge>
                      </div>
                      <p className="mt-1 text-xs text-fg-muted">
                        {INTAKE_LABELS[sheet.info.intake]}
                        {sheet.info.declaration.sheet_label ? ` · ${sheet.info.declaration.sheet_label}` : ""}
                        {sheet.crs_id ? ` · ${sheet.crs_id}` : ""} · {sheet.info.georeferencing_basis_label}
                      </p>
                      <p className="text-xs text-fg-subtle">{sheet.next_step}</p>
                      <Link className="text-xs text-accent hover:underline" to={`/projects/${project.id}/assets/${sheet.asset_id}#map-sheet`}>
                        Open
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </Panel>
        </div>

        <aside aria-label="How map sheets are taken in" className="space-y-3 lg:sticky lg:top-16 lg:self-start">
          <Panel title="How map sheets are taken in">
            <ul className="list-disc space-y-1.5 pl-4 text-xs text-fg-muted">
              <li>A GeoTIFF with a valid CRS and geotransform is placed from its own metadata.</li>
              <li>A scanned picture, or a TIFF without georeferencing, is stored unplaced. Place it with a confirmed world file and .prj, or with control points you enter.</li>
              <li>A PDF is stored and its structure scanned. It cannot be georeferenced here: convert a page to TIFF or PNG first. Any georeferencing it claims is shown as unverified.</li>
              <li>The series label never assigns a CRS. Everest-56, Polyconic and LCC are names, not definitions.</li>
              <li>Defence Series Maps are not supported.</li>
            </ul>
          </Panel>
        </aside>
      </div>
    </div>
  );
}
