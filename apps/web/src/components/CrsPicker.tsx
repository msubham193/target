import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { type Crs, searchCrs } from "../api/client";
import { IconSearch } from "./icons";
import { Button, ErrorNotice, inputClass, Loading, Notice } from "./ui";

export function describeAxes(crs: Crs): string {
  return crs.axis_order.map((axis) => `${axis.abbreviation} (${axis.direction}, ${axis.unit})`).join(", ");
}

/** Searches the local CRS registry. The user chooses; the application never assigns a CRS on its own. */
export function CrsPicker({ selected, onSelect }: { selected: Crs | null; onSelect: (crs: Crs | null) => void }) {
  const [text, setText] = useState("");
  const [submitted, setSubmitted] = useState("");
  const results = useQuery({
    queryKey: ["crs", submitted],
    queryFn: () => searchCrs(submitted),
    enabled: submitted.length > 0,
  });

  if (selected) {
    return (
      <div className="rounded-md border border-accent/40 bg-accent-soft p-3 text-[13px]">
        <p className="font-medium text-fg">
          {selected.identifier} — {selected.name}
        </p>
        <p className="mt-0.5 text-fg-muted">
          {selected.kind} · units: {selected.units.join(", ")} · authority axis order: {describeAxes(selected)}
        </p>
        {selected.area_of_use?.name ? <p className="mt-0.5 text-fg-muted">Area of use: {selected.area_of_use.name}</p> : null}
        {selected.deprecated ? <p className="mt-0.5 text-warn">This CRS is marked deprecated in the registry.</p> : null}
        <button type="button" className="mt-1.5 text-xs text-accent hover:underline" onClick={() => onSelect(null)}>
          Choose a different CRS
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <div className="relative min-w-0 flex-1">
          <IconSearch size={15} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-fg-subtle" />
          <input
            id="crs-search"
            className={`${inputClass} pl-8`}
            value={text}
            placeholder="e.g. EPSG:4326, 32643, or UTM zone 43N"
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                setSubmitted(text.trim());
              }
            }}
          />
        </div>
        <Button variant="secondary" onClick={() => setSubmitted(text.trim())}>
          Search
        </Button>
      </div>
      {results.isLoading ? <Loading label="Searching the local CRS registry…" /> : null}
      {results.isError ? <ErrorNotice error={results.error} /> : null}
      {results.data && results.data.items.length === 0 ? (
        <Notice tone="warning" title="No matching CRS in the local registry">
          A CRS must be a complete, identifiable definition. A datum name or a projection method alone (for example
          “WGS84 + LCC”) does not define one.
        </Notice>
      ) : null}
      {results.data && results.data.items.length > 0 ? (
        <ul className="max-h-56 overflow-auto rounded-md border border-line bg-raised" aria-label="CRS search results">
          {results.data.items.map((crs) => (
            <li key={crs.identifier} className="border-b border-line last:border-b-0">
              <button
                type="button"
                className="w-full px-3 py-1.5 text-left text-[13px] hover:bg-overlay"
                onClick={() => onSelect(crs)}
              >
                <span className="font-medium text-fg">{crs.identifier}</span> <span className="text-fg">— {crs.name}</span>
                <span className="text-fg-subtle">
                  {" "}
                  ({crs.kind}, {crs.units.join(", ")}
                  {crs.deprecated ? ", deprecated" : ""})
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
