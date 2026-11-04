import { vi } from "vitest";

import {
  DEFAULT_LABELS,
  ENLARGED_ABOVE_ZOOM,
  LABEL_TOGGLES,
  MAPBOX_STYLE,
  parseLatLon,
  readMapboxToken,
  redactSecrets,
  satelliteBackdropUrl,
  satelliteBannerUrl,
  zoomNote,
} from "./mapbox";

const PUBLIC = "pk.eyJ1IjoiZXhhbXBsZSIsImEiOiJmYWtlZmFrZWZha2UifQ.fakefakefakefakefake";

describe("readMapboxToken", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("uses the standard satellite style", () => {
    expect(MAPBOX_STYLE).toBe("mapbox://styles/mapbox/standard-satellite");
  });

  it("accepts a public token", () => {
    expect(readMapboxToken(PUBLIC)).toEqual({ ok: true, token: PUBLIC });
    expect(readMapboxToken(`  ${PUBLIC}  `)).toEqual({ ok: true, token: PUBLIC });
  });

  it("says plainly when none is configured, and where to put it", () => {
    // `undefined` reads the environment, so it must not see a real token from a local .env.local.
    vi.stubEnv("VITE_MAPBOX_ACCESS_TOKEN", "");
    for (const raw of [undefined, "", "   ", null, 5]) {
      const state = readMapboxToken(raw);
      expect(state.ok).toBe(false);
      if (!state.ok) {
        expect(state.reason).toBe("missing");
        expect(state.message).toContain("VITE_MAPBOX_ACCESS_TOKEN");
        expect(state.message).toContain("apps/web/.env.local");
      }
    }
  });

  it("refuses a secret token and never repeats it", () => {
    const secret = "sk.eyJ1IjoiZXhhbXBsZSIsImEiOiJmYWtlZmFrZWZha2UifQ.fakefakefakefakefake";
    const state = readMapboxToken(secret);
    expect(state.ok).toBe(false);
    if (!state.ok) {
      expect(state.reason).toBe("secret");
      expect(state.message).not.toContain(secret);
      expect(state.message).toContain("public token");
    }
  });

  it("refuses something that is not a token", () => {
    for (const raw of ["abc", "pk.short", "Bearer pk.x", "https://api.mapbox.com"]) {
      const state = readMapboxToken(raw);
      expect(state.ok).toBe(false);
      if (!state.ok) expect(state.reason).toBe("malformed");
    }
  });
});

describe("parseLatLon", () => {
  it("reads decimal degrees for the places the map is checked at", () => {
    expect(parseLatLon("20.2961", "85.8245")).toEqual({ ok: true, lat: 20.2961, lon: 85.8245 });
    expect(parseLatLon("28.6139", "77.2090")).toEqual({ ok: true, lat: 28.6139, lon: 77.209 });
  });

  it("reads signs and hemisphere letters, and never swaps the two", () => {
    expect(parseLatLon("-33.9", "18.4")).toEqual({ ok: true, lat: -33.9, lon: 18.4 });
    expect(parseLatLon("33.9 S", "70.6 W")).toEqual({ ok: true, lat: -33.9, lon: -70.6 });
    expect(parseLatLon("20.3°N", "85.8°e")).toEqual({ ok: true, lat: 20.3, lon: 85.8 });
    // 85.8 is not a latitude: it is refused, not quietly treated as a longitude.
    expect(parseLatLon("85.8245", "20.2961")).toMatchObject({ ok: false });
  });

  it("refuses what is not a coordinate, with the reason", () => {
    expect(parseLatLon("abc", "1")).toMatchObject({ ok: false, message: expect.stringContaining("Latitude must be a number") });
    expect(parseLatLon("1", "")).toMatchObject({ ok: false, message: expect.stringContaining("Longitude must be a number") });
    expect(parseLatLon("20 E", "85")).toMatchObject({ ok: false, message: expect.stringContaining("N or S") });
    expect(parseLatLon("-20 S", "85")).toMatchObject({ ok: false, message: expect.stringContaining("either a sign or a hemisphere") });
    expect(parseLatLon("20", "190")).toMatchObject({ ok: false, message: expect.stringContaining("-180 and 180") });
    expect(parseLatLon("NaN", "1")).toMatchObject({ ok: false });
    expect(parseLatLon("1e2", "1")).toMatchObject({ ok: false });
  });

  it("keeps latitude inside what the web map can show", () => {
    expect(parseLatLon("85.05", "0")).toMatchObject({ ok: true });
    expect(parseLatLon("89", "0")).toMatchObject({ ok: false, message: expect.stringContaining("85.0511") });
  });
});

describe("redactSecrets", () => {
  it("removes a token wherever it appears in a message", () => {
    const token = "pk.eyJ1IjoiZXhhbXBsZSIsImEiOiJmYWtlZmFrZWZha2UifQ.fakefakefakefakefake";
    for (const text of [
      `Forbidden (403): https://api.mapbox.com/styles/v1/mapbox/standard-satellite?access_token=${token}`,
      `bad token ${token} here`,
      `https://events.mapbox.com/x?access_token=sk.abcdefghijklmnop.qrstuv&other=1`,
    ]) {
      const clean = redactSecrets(text);
      expect(clean).not.toContain(token);
      expect(clean).not.toMatch(/(?:pk|sk)\.[A-Za-z0-9]{10}/);
      expect(clean).toContain("[token removed]");
    }
    expect(redactSecrets("Not Found")).toBe("Not Found");
  });
});

describe("zoomNote", () => {
  it("says nothing up to the zoom where detail usually holds, then says imagery may be enlarged and not live", () => {
    expect(zoomNote(null)).toBeNull();
    expect(zoomNote(ENLARGED_ABOVE_ZOOM)).toBeNull();
    const note = zoomNote(19.2)!;
    expect(note).toContain("zoom 19.2");
    expect(note).toContain("enlarged");
    expect(note).toContain("does not sharpen");
    expect(note).toContain("not live");
  });
});

describe("label groups", () => {
  it("are the five documented Standard configuration names, all on by default", () => {
    expect(LABEL_TOGGLES.map((toggle) => toggle.key)).toEqual([
      "showRoadLabels",
      "showPlaceLabels",
      "showPointOfInterestLabels",
      "showTransitLabels",
      "showPedestrianRoads",
    ]);
    expect(Object.values(DEFAULT_LABELS).every((on) => on)).toBe(true);
  });
});

describe("satelliteBackdropUrl", () => {
  it("asks Mapbox for one still picture with its logo and attribution left on", () => {
    const url = satelliteBackdropUrl(PUBLIC);
    expect(url.startsWith("https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static/")).toBe(true);
    expect(url).toContain("logo=true");
    expect(url).toContain("attribution=true");
    expect(url).toContain(`access_token=${PUBLIC}`);
  });
});

describe("satelliteBannerUrl", () => {
  it("asks for a wide still picture with the Mapbox logo and attribution left on", () => {
    const url = satelliteBannerUrl(PUBLIC);
    expect(url).toContain("/styles/v1/mapbox/satellite-v9/static/");
    expect(url).toContain("1280x400@2x");
    expect(url).toContain("logo=true");
    expect(url).toContain("attribution=true");
  });
});
