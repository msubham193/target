import { useEffect, useId, useMemo, useRef, useState } from "react";

import { type CoordinateQuery, formatLatLon, parseCoordinateText } from "../lib/coordinates";
import { type PlaceMatch, readMapboxToken, redactSecrets, searchPlaces } from "../lib/mapbox";
import { IconSearch } from "./icons";
import { Button, inputClass, Loading, Notice, PlannedBadge } from "./ui";

/** Suggestions start after this many characters, and a short pause in typing, so Mapbox is not asked for every keystroke. */
const MIN_CHARACTERS = 3;
const PAUSE_MS = 350;

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "done"; matches: PlaceMatch[]; query: string };

/**
 * Finds a place by name with Mapbox's geocoding service and hands back the chosen position. It is online: the words typed
 * are sent to Mapbox. Suggestions appear as you type; the arrow keys, Enter and Escape work. Without a Mapbox token it says
 * so and stays off.
 */
export function PlaceSearch({
  onPick,
  compact = false,
  coordinates = false,
  landmarks = false,
  near = null,
}: {
  onPick: (match: PlaceMatch) => void;
  /** Also search landmarks and points of interest (Mapbox Search Box), not only addresses and places. */
  landmarks?: boolean;
  /** Prefer matches near this position. */
  near?: { lon: number; lat: number } | null;
  /** Leave out the help line (for use over a map). */
  compact?: boolean;
  /**
   * Also accept a typed position ("20.2961, 85.8245", "20°17'46"N 85°49'28"E"). A position is read on this computer and
   * never sent to Mapbox, so it works without a token; only place names are looked up online.
   */
  coordinates?: boolean;
}) {
  const token = useMemo(() => readMapboxToken(), []);
  const listId = useId();
  const [text, setText] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const request = useRef<AbortController | null>(null);
  // After a result is chosen the box shows its name; that change must not start another search.
  const skipNext = useRef(false);
  const tokenValue = token.ok ? token.token : null;
  const position: CoordinateQuery = coordinates ? parseCoordinateText(text) : { kind: "text" };

  const run = async (query: string) => {
    if (!tokenValue) {
      setState({
        kind: "error",
        message: "Searching by place name needs a Mapbox token. Type coordinates instead, for example 20.2961, 85.8245.",
      });
      return;
    }
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setState({ kind: "loading" });
    try {
      const matches = await searchPlaces(query, tokenValue, controller.signal, { landmarks, near });
      if (controller.signal.aborted) return;
      setState({ kind: "done", matches, query });
      setOpen(true);
      setActive(-1);
    } catch (error) {
      if (controller.signal.aborted) return;
      setState({ kind: "error", message: redactSecrets(error instanceof Error ? error.message : "The place search failed.") });
    }
  };

  // Suggest while typing, after a short pause.
  useEffect(() => {
    if (skipNext.current) {
      skipNext.current = false;
      return;
    }
    // A typed position is never sent anywhere: it is offered as the only suggestion.
    if (coordinates && parseCoordinateText(text).kind !== "text") {
      request.current?.abort();
      setState({ kind: "idle" });
      setOpen(true);
      setActive(0);
      return;
    }
    if (!tokenValue) {
      setState({ kind: "idle" });
      return;
    }
    const query = text.trim();
    if (query.length < MIN_CHARACTERS) {
      request.current?.abort();
      setState({ kind: "idle" });
      setOpen(false);
      return;
    }
    const timer = window.setTimeout(() => void run(query), PAUSE_MS);
    return () => window.clearTimeout(timer);
    // `run` only reads the token, which does not change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, tokenValue, coordinates]);

  useEffect(() => () => request.current?.abort(), []);

  if (!tokenValue && !coordinates) {
    return (
      <div className="flex items-center gap-2">
        <input id="place-search" className={inputClass} disabled placeholder="Unavailable: no Mapbox token" />
        <PlannedBadge label="Needs a Mapbox token" />
      </div>
    );
  }

  const typedPosition: PlaceMatch | null =
    position.kind === "coordinates"
      ? { id: "typed-coordinates", label: formatLatLon(position.lat, position.lon), lat: position.lat, lon: position.lon }
      : null;
  const matches = typedPosition ? [typedPosition] : state.kind === "done" ? state.matches : [];
  const showList = open && matches.length > 0 && (typedPosition !== null || state.kind === "done");

  const choose = (match: PlaceMatch) => {
    skipNext.current = true;
    setText(match.label);
    setOpen(false);
    setActive(-1);
    onPick(match);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" && matches.length > 0) {
      event.preventDefault();
      setOpen(true);
      setActive((index) => (index + 1) % matches.length);
    } else if (event.key === "ArrowUp" && matches.length > 0) {
      event.preventDefault();
      setOpen(true);
      setActive((index) => (index <= 0 ? matches.length - 1 : index - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const picked = typedPosition ?? (showList ? matches[active] : undefined);
      if (picked) choose(picked);
      else if (text.trim() && position.kind === "text") void run(text.trim());
    } else if (event.key === "Escape") {
      setOpen(false);
      setActive(-1);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <div className="relative min-w-0 flex-1">
          <IconSearch size={15} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-fg-subtle" />
          <input
            id="place-search"
            role="combobox"
            aria-expanded={showList}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={showList && active >= 0 ? `${listId}-${active}` : undefined}
            autoComplete="off"
            className={`${inputClass} pl-8`}
            value={text}
            aria-label={coordinates ? "Place name or coordinates" : undefined}
            placeholder={
              coordinates ? "Place name or latitude, longitude (e.g. 20.2961, 85.8245)" : "Start typing a place, e.g. Bhubaneswar"
            }
            onChange={(event) => setText(event.target.value)}
            onFocus={() => matches.length > 0 && setOpen(true)}
            onKeyDown={onKeyDown}
          />
          {showList ? (
            <ul
              id={listId}
              role="listbox"
              aria-label="Place search results"
              className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-line bg-raised shadow-[0_12px_32px_rgb(0_0_0/0.45)]"
            >
              {matches.map((match, index) => (
                <li
                  key={match.id}
                  id={`${listId}-${index}`}
                  role="option"
                  aria-selected={index === active}
                  className={`cursor-pointer border-b border-line px-3 py-2 text-[13px] last:border-b-0 ${
                    index === active ? "bg-accent-soft" : "hover:bg-overlay"
                  }`}
                  // mousedown, not click: it fires before the box loses focus, so the list is still there to be picked from
                  onMouseDown={(event) => {
                    event.preventDefault();
                    choose(match);
                  }}
                  onMouseEnter={() => setActive(index)}
                >
                  <span className="text-fg">{match.id === "typed-coordinates" ? `Go to ${match.label}` : match.label}</span>
                  <span className="block text-xs text-fg-subtle">
                    {match.id === "typed-coordinates"
                      ? "The position you typed (read on this computer, not looked up)"
                      : `${match.lat.toFixed(5)}, ${match.lon.toFixed(5)}`}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <Button
          variant="secondary"
          onClick={() => {
            if (typedPosition) choose(typedPosition);
            else if (text.trim() && position.kind === "text") void run(text.trim());
          }}
          disabled={!text.trim() || state.kind === "loading" || position.kind === "invalid"}
        >
          {coordinates ? "Go" : "Search places"}
        </Button>
      </div>
      {position.kind === "invalid" ? (
        <Notice tone="warning" title="This position cannot be used">
          <p>{position.message}</p>
        </Notice>
      ) : null}
      {coordinates && !tokenValue && position.kind === "text" && text.trim().length >= MIN_CHARACTERS ? (
        <p className="text-xs text-fg-subtle">
          Searching by place name needs a Mapbox token. Coordinates work without one.
        </p>
      ) : null}
      {compact ? null : (
        <p className="text-xs text-fg-subtle">Suggestions appear as you type (at least {MIN_CHARACTERS} letters). Use the arrow keys and Enter, or click one.</p>
      )}
      {state.kind === "loading" ? <Loading label="Searching places…" /> : null}
      {state.kind === "error" ? (
        <Notice tone="error" title="The place search did not work">
          <p data-testid="place-search-error">{state.message}</p>
        </Notice>
      ) : null}
      {state.kind === "done" && state.matches.length === 0 ? (
        <Notice tone="warning" title={`No place found for “${state.query}”`}>
          Try a different spelling, or add the city or country.
        </Notice>
      ) : null}
    </div>
  );
}
