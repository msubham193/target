import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { type CrsAssessment, getCrsAssessment } from "../api/client";
import { formatGeotransform } from "./GeoreferencingPanel";
import { TransformationPlanView } from "./TransformationPlan";
import { Badge, DescriptionList, ErrorNotice, Loading, muted, Notice, Panel, type Tone } from "./ui";

// How worrying it is when a status holds.
const STATUS_TONES: Record<CrsAssessment["statuses"][number]["code"], Tone> = {
  georeferenced: "ok",
  crs_identified: "ok",
  crs_missing: "warn",
  crs_ambiguous: "warn",
  transformation_available: "ok",
  transformation_unavailable: "danger",
  georeferencing_required: "warn",
  validation_failed: "danger",
};

function number(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toPrecision(12).replace(/\.?0+$/, "");
}

/**
 * What a raster's coordinate system is and what moving its coordinates would involve. Read-only: it shows the CRS
 * the raster already has, never one it might have, and it changes nothing. A transformation can be previewed here;
 * to use one, record it with the georeferencing or choose it for the map layer.
 */
export function CrsAssessmentPanel({ assetId }: { assetId: string }) {
  const [preview, setPreview] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["crs-assessment", assetId, preview],
    queryFn: () => getCrsAssessment(assetId, preview),
  });

  if (query.isLoading) return <Loading label="Assessing the coordinate reference system…" />;
  if (query.isError) return <ErrorNotice error={query.error} />;
  const data = query.data!;
  const { crs } = data;
  const active = data.statuses.filter((status) => status.active);

  return (
    <Panel
      title="Coordinate reference system"
      description="What the raster's CRS is, and what moving its coordinates to longitude and latitude would involve. Nothing here changes the file or assigns a CRS."
      actions={<Badge tone={active.some((s) => s.code === "validation_failed" || s.code === "transformation_unavailable") ? "danger" : "neutral"}>{data.summary}</Badge>}
    >
      <div className="space-y-5" id="crs-assessment">
        <section aria-label="Assessment statuses">
          <p className="mb-1.5 text-xs font-medium text-fg-muted">Status</p>
          <ul className="grid grid-cols-1 gap-1.5 sm:grid-cols-2" aria-label="CRS assessment statuses">
            {data.statuses.map((status) => (
              <li
                key={status.code}
                data-testid={`status-${status.code}`}
                data-active={status.active ? "true" : "false"}
                className={`rounded-md border px-2.5 py-1.5 text-xs ${status.active ? "border-line-strong bg-raised" : "border-line opacity-70"}`}
              >
                <span className="flex items-center gap-2">
                  <Badge tone={status.active ? STATUS_TONES[status.code] : "neutral"}>{status.active ? "Yes" : "No"}</Badge>
                  <span className="font-medium text-fg">{status.label}</span>
                </span>
                <span className="mt-0.5 block text-fg-muted">{status.message}</span>
              </li>
            ))}
          </ul>
        </section>

        {crs ? (
          <section aria-label="What the CRS says">
            <p className="mb-1.5 text-xs font-medium text-fg-muted">What the CRS definition says</p>
            <DescriptionList
              labelWidth="md"
              rows={[
                ["CRS", `${crs.identifier ?? "no single registry code"} — ${crs.name}`],
                ["Kind", crs.kind],
                [
                  "Datum",
                  crs.datum.name ? (
                    <>
                      {crs.datum.name}
                      {crs.datum.is_ensemble ? <span className="block text-xs text-fg-subtle">A datum ensemble of: {crs.datum.ensemble_members.join(", ")}</span> : null}
                    </>
                  ) : (
                    muted
                  ),
                ],
                [
                  "Ellipsoid",
                  crs.ellipsoid
                    ? `${crs.ellipsoid.name}: semi-major axis ${number(crs.ellipsoid.semi_major_m)} m${crs.ellipsoid.inverse_flattening ? `, inverse flattening ${number(crs.ellipsoid.inverse_flattening)}` : ", a sphere"}`
                    : muted,
                ],
                ["Prime meridian", crs.prime_meridian ? `${crs.prime_meridian.name} (${number(crs.prime_meridian.longitude_degrees)}°)` : muted],
                [
                  "Projection",
                  crs.projection ? (
                    <>
                      {crs.projection.method}
                      <ul className="mt-1 space-y-0.5 text-xs text-fg-muted" aria-label="Projection parameters">
                        {crs.projection.parameters.map((parameter) => (
                          <li key={parameter.name}>
                            {parameter.name}: <span className="font-mono">{number(parameter.value)}</span> {parameter.unit}
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : crs.kind === "geographic" ? (
                    "none (geographic coordinates)"
                  ) : (
                    muted
                  ),
                ],
                ["Coordinate units", crs.coordinate_units.join(", ")],
                ["Axes", crs.axes.map((axis) => `${axis.name} (${axis.direction}, ${axis.unit})`).join("; ")],
                ["Area of use", crs.area_of_use ? `${crs.area_of_use.name ?? "unnamed"} (${crs.area_of_use.west}, ${crs.area_of_use.south}, ${crs.area_of_use.east}, ${crs.area_of_use.north})` : muted],
                ["Registry matches", crs.registry_matches.length > 0 ? crs.registry_matches.join(", ") : "none"],
                ...(crs.towgs84.length > 0 ? [["Datum shift in the CRS", crs.towgs84.map(number).join(", ")] as [string, string]] : []),
                ...(crs.deprecated ? [["Registry", "This CRS is marked deprecated"] as [string, string]] : []),
              ]}
            />
          </section>
        ) : (
          <Notice tone="warning" title="No usable CRS">
            The raster has no usable coordinate reference system, so there is no datum, projection or unit to show. None is assumed or
            taken from the file name; record georeferencing under Spatial placement.
          </Notice>
        )}

        <section aria-label="Raster facts">
          <p className="mb-1.5 text-xs font-medium text-fg-muted">Raster</p>
          <DescriptionList
            labelWidth="md"
            rows={[
              ["Size", `${data.raster.width} × ${data.raster.height} pixels, ${data.raster.band_count} band(s)`],
              ["Pixel size", data.raster.pixel_size ? `${data.raster.pixel_size.map((v) => v.toPrecision(4)).join(" × ")} ${data.raster.pixel_units ?? ""}` : muted],
              ["Geotransform", data.raster.geotransform ? <span className="font-mono text-xs">{formatGeotransform(data.raster.geotransform)}</span> : "none usable"],
              ["NoData", data.raster.nodata.map((v) => (v == null ? "none" : String(v))).join(", ")],
              ["Georeferencing in use", data.georeferencing_source === "recorded" ? `recorded by a person (version ${data.georeferencing_version})` : "the file's own"],
            ]}
          />
        </section>

        {data.transformations.length > 0 ? (
          <section aria-label="Transformations" className="space-y-4">
            <p className="text-xs font-medium text-fg-muted">Moving the coordinates</p>
            {data.transformations.map((plan) => (
              <div key={plan.purpose} className="space-y-2 rounded-md border border-line p-3" data-testid={`plan-${plan.purpose}`}>
                <p className="text-[13px] font-medium text-fg">{plan.purpose_label}</p>
                <p className="text-[11px] text-fg-subtle">
                  {plan.source_crs} → {plan.target_crs}
                </p>
                <TransformationPlanView
                  plan={plan}
                  idPrefix={`assess-${plan.purpose}`}
                  value={preview ?? plan.resolution?.operation_id ?? null}
                  onChange={plan.status === "selection_required" ? setPreview : undefined}
                />
                {plan.resolution ? (
                  <p className="text-xs text-fg-muted" data-testid={`resolution-${plan.purpose}`}>
                    {plan.resolution.chosen_by === "selection" ? "With your choice, " : "It would use "}
                    <span className="font-mono">{plan.resolution.operation_id}</span>
                    {plan.resolution.accuracy_m ? ` (documented accuracy about ${plan.resolution.accuracy_m} m)` : plan.resolution.classification === "exact" ? " (exact)" : ""}.
                  </p>
                ) : plan.blocked ? (
                  <Notice tone={plan.status === "selection_required" ? "warning" : "error"} title={plan.blocked.title}>
                    {plan.blocked.detail}
                  </Notice>
                ) : null}
              </div>
            ))}
            {preview ? (
              <p className="text-xs text-fg-subtle">
                This is a preview only; nothing was stored.{" "}
                <button type="button" className="text-accent hover:underline" onClick={() => setPreview(null)}>
                  Clear the preview
                </button>
              </p>
            ) : null}
          </section>
        ) : null}

        <details className="text-xs text-fg-muted">
          <summary className="cursor-pointer font-medium text-fg">What the classes and accuracy figures mean</summary>
          <dl className="mt-2 space-y-1.5">
            {Object.entries(data.class_explanations).map(([name, text]) => (
              <div key={name}>
                <dt className="font-medium text-fg">{name}</dt>
                <dd>{text}</dd>
              </div>
            ))}
          </dl>
          <ul className="mt-2 list-disc space-y-1 pl-4">
            {data.accuracy_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </details>

        <details className="text-xs text-fg-muted">
          <summary className="cursor-pointer font-medium text-fg">Limitations</summary>
          <ul className="mt-2 list-disc space-y-1 pl-4" aria-label="Limitations">
            {data.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
          <p className="mt-2 text-fg-subtle">
            PROJ {data.versions.proj}, pyproj {data.versions.pyproj}; network access {data.versions.network}.
          </p>
        </details>
      </div>
    </Panel>
  );
}
