import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { getMapSheetAssessment, type MapSheetAssessment, type MapSheetNoteRequest, recordMapSheetNote } from "../api/client";
import {
  FACT_STATE_LABELS,
  FACT_STATE_TONES,
  factValue,
  INTAKE_LABELS,
  NOTE_FIELDS,
  SERIES_LABELS,
  UNRESOLVED_LABELS,
  UNRESOLVED_TONES,
} from "../lib/mapsheet";
import { Badge, Button, ErrorNotice, Field, inputClass, Loading, Notice, Panel, selectClass } from "./ui";

function Declaration({ data }: { data: MapSheetAssessment }) {
  const { declaration, intake, georeferencing_basis_label, placed } = data.info;
  return (
    <section aria-label="Declared by the uploader" className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="accent">{SERIES_LABELS[declaration.series]}</Badge>
        <Badge tone="neutral">{INTAKE_LABELS[intake]}</Badge>
        <Badge tone={placed ? "ok" : "warn"}>{placed ? "Placed on the map" : "Not placed"}</Badge>
      </div>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-[13px] sm:grid-cols-2">
        <div>
          <dt className="text-xs text-fg-subtle">Sheet</dt>
          <dd>{declaration.sheet_label ?? <span className="text-fg-subtle">not stated</span>}</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-subtle">Scale</dt>
          <dd>{declaration.scale_denominator ? `1 : ${declaration.scale_denominator.toLocaleString("en")}` : <span className="text-fg-subtle">not stated</span>}</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-subtle">Edition</dt>
          <dd>{declaration.edition ?? <span className="text-fg-subtle">not stated</span>}</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-subtle">Position established by</dt>
          <dd>{georeferencing_basis_label}</dd>
        </div>
      </dl>
      <p className="text-xs text-fg-muted">{data.series_note}</p>
    </section>
  );
}

function Facts({ data }: { data: MapSheetAssessment }) {
  return (
    <section aria-label="What the file states">
      <h3 className="mb-1 text-[13px] font-semibold text-fg">What the file states</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <caption className="sr-only">Datum, ellipsoid, projection, parameters, units, grid and extent of the sheet</caption>
          <thead>
            <tr className="text-fg-subtle">
              <th scope="col" className="py-1 pr-3 font-medium">Property</th>
              <th scope="col" className="py-1 pr-3 font-medium">Source</th>
              <th scope="col" className="py-1 font-medium">Value</th>
            </tr>
          </thead>
          <tbody>
            {data.facts.map((fact) => {
              const value = factValue(fact.field, fact.value);
              return (
                <tr key={fact.field} className="border-t border-line align-top" data-testid={`fact-${fact.field}`}>
                  <th scope="row" className="py-1.5 pr-3 font-medium text-fg">{fact.label}</th>
                  <td className="py-1.5 pr-3">
                    <Badge tone={FACT_STATE_TONES[fact.state]}>{FACT_STATE_LABELS[fact.state]}</Badge>
                  </td>
                  <td className="py-1.5 text-fg-muted">
                    {value ? <span className="block text-fg">{value}</span> : null}
                    <span className="block">{fact.detail}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Unresolved({ data }: { data: MapSheetAssessment }) {
  return (
    <section aria-label="Unresolved metadata" className="space-y-2">
      <h3 className="text-[13px] font-semibold text-fg">Unresolved</h3>
      {data.unresolved.length === 0 ? (
        <p className="text-xs text-fg-muted">Nothing is unresolved in what the file states. This does not show the sheet is placed correctly.</p>
      ) : (
        <ul className="space-y-2" aria-label="Unresolved items">
          {data.unresolved.map((item, index) => (
            <li key={`${item.field}-${index}`} className="rounded-md border border-line bg-raised p-2.5 text-xs" data-testid="unresolved">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-fg">{item.label}</span>
                <Badge tone={UNRESOLVED_TONES[item.status]}>{UNRESOLVED_LABELS[item.status]}</Badge>
              </div>
              <p className="mt-1 text-fg-muted">{item.detail}</p>
              <p className="mt-0.5 text-fg-subtle">Needed: {item.required}</p>
            </li>
          ))}
        </ul>
      )}
      {data.family_hints.length > 0 ? (
        <Notice tone="warning" title="Names that are not definitions">
          <ul className="list-disc space-y-1 pl-4 text-xs">
            {data.family_hints.map((hint) => (
              <li key={`${hint.family}-${hint.matched}`}>
                <span className="font-medium">{hint.family}</span> ({hint.matched}). {hint.meaning}
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}
      {data.required_information.length > 0 ? (
        <div>
          <h4 className="text-xs font-semibold text-fg">Information still required</h4>
          <ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-fg-muted" aria-label="Information still required">
            {data.required_information.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function PdfFacts({ pdf }: { pdf: Record<string, unknown> }) {
  const size = pdf.first_page_size as { width_mm: number; height_mm: number } | null;
  const points = pdf.declared_corner_points as { latitude: number; longitude: number }[] | null;
  return (
    <section aria-label="PDF facts" className="space-y-1">
      <h3 className="text-[13px] font-semibold text-fg">The PDF</h3>
      <p className="text-xs text-fg-muted">{String(pdf.scan_note)}</p>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-[13px] sm:grid-cols-3">
        <div>
          <dt className="text-xs text-fg-subtle">PDF version</dt>
          <dd>{String(pdf.pdf_version ?? "unknown")}</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-subtle">Pages</dt>
          <dd>{pdf.page_count != null ? String(pdf.page_count) : "unknown"}</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-subtle">First page</dt>
          <dd>{size ? `${size.width_mm} × ${size.height_mm} mm` : "unknown"}</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-subtle">Embedded pictures found</dt>
          <dd>{String(pdf.embedded_images_found)}</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-subtle">Encrypted</dt>
          <dd>{pdf.encrypted ? "yes" : "no"}</dd>
        </div>
      </dl>
      {points || (pdf.geo_markers as string[]).length > 0 ? (
        <Notice tone="warning" title="The PDF declares geospatial metadata. It is not applied.">
          <p className="text-xs">
            {(pdf.geo_markers as string[]).join(", ")}
            {pdf.declared_epsg ? ` · declared EPSG:${String(pdf.declared_epsg)}` : ""}
            {points ? ` · corner points (lat, lon): ${points.map((p) => `${p.latitude}, ${p.longitude}`).join(" | ")}` : ""}. Treat
            these as unverified evidence only.
          </p>
        </Notice>
      ) : null}
    </section>
  );
}

function Notes({ data, assetId }: { data: MapSheetAssessment; assetId: string }) {
  const queryClient = useQueryClient();
  const [field, setField] = useState<MapSheetNoteRequest["field"]>("datum");
  const [value, setValue] = useState("");
  const [source, setSource] = useState("");
  const [status, setStatus] = useState<MapSheetNoteRequest["status"]>("unverified");
  const record = useMutation({
    mutationFn: () => recordMapSheetNote(assetId, { field, value, source, status }),
    onSuccess: () => {
      setValue("");
      setSource("");
      void queryClient.invalidateQueries({ queryKey: ["map-sheet", assetId] });
    },
  });
  const ready = value.trim().length > 0 && source.trim().length > 0;
  return (
    <section aria-label="Notes on unresolved metadata" className="space-y-2">
      <h3 className="text-[13px] font-semibold text-fg">Record what you have learned</h3>
      <p className="text-xs text-fg-muted">
        A note keeps what a person found out (for example a datum printed in the sheet's margin) together with where it came from. It
        is a record for review. It is never applied: it does not define a CRS, change a transformation or place the sheet.
      </p>
      {data.notes.length > 0 ? (
        <ul className="space-y-1.5" aria-label="Recorded notes">
          {data.notes.map((note) => (
            <li key={note.id} className="rounded-md border border-line bg-raised p-2 text-xs" data-testid="note">
              <span className="font-medium text-fg">{NOTE_FIELDS.find((f) => f.value === note.field)?.label ?? note.field}:</span>{" "}
              {note.value}
              <span className="block text-fg-subtle">
                Source: {note.source} · {note.status === "needs_authority" ? "needs an authoritative source" : "unverified"} · recorded by{" "}
                {note.recorded_by ?? "unknown"} · not applied
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      <form
        className="grid grid-cols-1 gap-3 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (ready) record.mutate();
        }}
      >
        <Field label="About" htmlFor="note-field">
          <select id="note-field" className={selectClass} value={field} onChange={(e) => setField(e.target.value as typeof field)}>
            {NOTE_FIELDS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Status" htmlFor="note-status">
          <select id="note-status" className={selectClass} value={status} onChange={(e) => setStatus(e.target.value as typeof status)}>
            <option value="unverified">Unverified</option>
            <option value="needs_authority">Needs an authoritative source</option>
          </select>
        </Field>
        <div className="sm:col-span-2">
          <Field label="What you found" htmlFor="note-value">
            <textarea id="note-value" className={`${inputClass} min-h-16`} value={value} maxLength={2000} onChange={(e) => setValue(e.target.value)} />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Field label="Where it comes from" htmlFor="note-source" hint="Required: a document, the sheet's margin, a supplier.">
            <input id="note-source" className={inputClass} value={source} maxLength={1000} onChange={(e) => setSource(e.target.value)} />
          </Field>
        </div>
        <div className="sm:col-span-2">
          <Button type="submit" variant="secondary" disabled={!ready || record.isPending}>
            Record note
          </Button>
        </div>
      </form>
      {record.isError ? <ErrorNotice error={record.error} title="The note could not be recorded" /> : null}
    </section>
  );
}

/**
 * The metadata assessment of a map sheet (OSM / ESM): what the file states, what it does not, what is unresolved and what
 * is needed. Read-only except for notes, which are records and are never applied.
 */
export function MapSheetPanel({ assetId }: { assetId: string }) {
  const query = useQuery({ queryKey: ["map-sheet", assetId], queryFn: () => getMapSheetAssessment(assetId) });
  return (
    <Panel
      title="Map sheet"
      description="What this sheet's file states about its coordinate system, and what is still unknown. Nothing is assigned from the series, the file name or the picture."
    >
      <div className="space-y-5" id="map-sheet">
        {query.isLoading ? <Loading label="Assessing the sheet…" /> : null}
        {query.isError ? <ErrorNotice error={query.error} title="The map-sheet assessment could not be loaded" /> : null}
        {query.data ? (
          <>
            <Declaration data={query.data} />
            {query.data.pdf ? <PdfFacts pdf={query.data.pdf} /> : null}
            <Facts data={query.data} />
            <Unresolved data={query.data} />
            {query.data.sidecars.length > 0 ? (
              <section aria-label="Sidecar files">
                <h3 className="text-[13px] font-semibold text-fg">Sidecar files received</h3>
                <ul className="mt-1 text-xs text-fg-muted">
                  {query.data.sidecars.map((sidecar) => (
                    <li key={sidecar.id}>
                      {sidecar.filename} · {sidecar.kind} · {sidecar.upload_status}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            <Notes data={query.data} assetId={assetId} />
            <details className="text-xs text-fg-muted">
              <summary className="cursor-pointer font-medium text-fg">How this upload was processed</summary>
              <ol className="mt-1 list-decimal space-y-1 pl-4">
                {query.data.info.processing.map((step, index) => (
                  <li key={index}>
                    <span className="font-medium text-fg">{String(step.step).replace(/_/g, " ")}</span>
                    {step.tool ? ` · ${String(step.tool)}` : ""}
                    {step.note ? ` — ${String(step.note)}` : ""}
                    {step.status ? ` — ${String(step.status)}` : ""}
                  </li>
                ))}
              </ol>
            </details>
            <details className="text-xs text-fg-muted">
              <summary className="cursor-pointer font-medium text-fg">Limitations</summary>
              <ul className="mt-1 list-disc space-y-1 pl-4">
                {query.data.limitations.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </details>
          </>
        ) : null}
      </div>
    </Panel>
  );
}
