# Mapbox Standard Satellite basemap (optional, online)

The Map & Analysis page can draw **Mapbox Standard Satellite** (`mapbox://styles/mapbox/standard-satellite`) under the project's
layers. It is the only external basemap. It is off unless a Mapbox public token is configured; the default is still the offline
map. No migration, no schema change, no backend change.

## Why a second library is in the bundle

The Standard styles need Mapbox GL JS v3 (they use style `imports`, runtime configuration and lighting; Mapbox does not describe
MapLibre as supporting them or resolving `mapbox://` URLs). The app's map is MapLibre. So `MapCanvas` builds **one** map at a
time, with either library: MapLibre for the offline basemap, Mapbox GL JS (`mapbox-gl` 3.33.0, pinned) for Mapbox. Both share the
API used for everything the app draws, so the project layers, footprints, area of interest, annotations, photographs, pin and
measurement sketch are the same code on both. Changing the basemap replaces the map and **keeps the position, zoom and bearing**.
`mapbox-gl` is a separate chunk that is loaded only in Mapbox mode.

## Configure

1. Create a Mapbox **public** token (`pk.…`) and restrict it to this address in your Mapbox account.
2. Put it in `apps/web/.env.local` (git-ignored; see `apps/web/.env.example`):
   `VITE_MAPBOX_ACCESS_TOKEN=pk.…`
3. Restart the web app (Vite reads it at start).

Vite exposes every `VITE_*` value to the browser, so a **secret** token (`sk.…`) is refused and never used. With no token, a
malformed one or a secret one, the page shows what is wrong and keeps the offline map; nothing else is affected. If Mapbox
rejects the token (HTTP 401/403) or a basemap tile cannot be loaded, the page says so and the project layers keep working.

## What it does

- A **Basemap and location** panel chooses offline or Mapbox, and takes a **latitude and longitude** (decimal degrees, optional
  N/S/E/W). The map flies there on either basemap. A latitude is never swapped with a longitude; out-of-range values are refused
  with the reason.
- Zoom, pan and rotation work (rotation is on for the Mapbox map; the offline map keeps rotation off as before). Pitch is fixed
  flat and the projection is Mercator, because the project's rasters, measurements and coordinates are Web Mercator / geodetic.
- Road labels, place names, points of interest, transit labels and pedestrian roads can be switched with the Standard style's
  `basemap` configuration (`showRoadLabels`, `showPlaceLabels`, `showPointOfInterestLabels`, `showTransitLabels`,
  `showPedestrianRoads`).
- Mapbox's attribution control and logo are left on. The panel repeats the attribution and says the picture is not live.
- Project rasters (served by this app's API, georeferenced as before), annotations and measurements are drawn **on top of** the
  basemap and are **not positioned by it**. CRS conversion, the transformation rules and OSM/ESM handling are unchanged. The 3D
  terrain viewer and NDVI Studio are separate screens and untouched.

## Imagery: what is and is not claimed

- The picture is Mapbox's own satellite imagery as Mapbox serves it: not Sentinel-2, not false colour, not sharpened or
  stretched by this app.
- It is **not live and not newly captured**. Capture dates are not shown here and vary by place.
- Detail depends on the place. Above zoom 17 the page says the picture may be enlarged beyond the resolution it was captured at.
  That is a conservative general note, not a measured limit for any place: the app cannot tell where native coverage ends.
- Whether a particular road, transit or place label appears also depends on the zoom and on what Mapbox has there.

## Limits and costs (check them yourself)

- It needs the network and sends the map's view (and so the places you look at) to Mapbox. Mapbox GL JS normally also sends
  usage events to Mapbox; that was not inspected or changed. This differs from the rest of the app, which sends nothing off the
  machine. Do not use it for locations that must not be disclosed.
- Mapbox usage is billed to the account that owns the token under Mapbox's terms. Those terms and prices were not checked here.
- Mapbox's licence for `mapbox-gl` 3.x is proprietary (not open source like MapLibre). Review it before wider use.

## Manual verification checklist

Nothing below has been done. It needs a real token and a browser; only then can a row be filled in. Do not record a location as
verified unless it was opened and looked at.

Set up: put the token in `apps/web/.env.local` (never in a command, a report or a commit), then restart the web app. Vite
normally restarts itself when it sees an `.env` file change; if the Basemap panel still says no token is configured, restart it
yourself, from PowerShell in the repository root (this stops and starts only the web app, not the API, worker or database):

```powershell
.\scripts\stop.ps1 -Only web
. .\scripts\env.ps1; . .\scripts\lib\common.ps1; . .\scripts\lib\startup.ps1
$w = Read-ServiceWindows; $w['web'] = Start-ServiceWindow -Name web; Save-ServiceWindows -WindowIds $w
```

Then open Map & Analysis, choose Mapbox Standard Satellite, and for each place enter the latitude and longitude, press "Go to
this location", and look at the map at zoom about 12, 15, 17 and 19. Also check with the browser's network tool that the
tile requests succeed, and that the token does not appear in the page or the console.

| Place | Latitude | Longitude | Natural colour | Buildings and roads | Sharpness by zoom | Road names, places, POIs | Gaps or missing tiles | Verdict |
|---|---|---|---|---|---|---|---|---|
| Bhubaneswar | 20.2961 | 85.8245 | not tested | not tested | not tested | not tested | not tested | not tested |
| New Delhi | 28.6139 | 77.2090 | not tested | not tested | not tested | not tested | not tested | not tested |
| Rural Odisha | 20.5000 | 85.5000 | not tested | not tested | not tested | not tested | not tested | not tested |
| Coastal Odisha | 19.8000 | 86.9000 | not tested | not tested | not tested | not tested | not tested | not tested |

Also check: each label switch (roads, places, POIs, transit, pedestrian) changes the map; Mapbox's attribution and logo stay
visible; rotating and zooming work; an annotation, a footprint, the area of interest and a measurement still draw and respond on
top of the satellite picture; switching to the offline map and back keeps the position, zoom and bearing; with the network
blocked, the project layers still draw. For "sufficiently detailed for the intended visualisation", judge by the zoom at which
the places you care about stop showing real detail; this app cannot tell you.

## Tests, and what they do not show

`src/lib/mapbox.test.ts` (token, coordinates, zoom note, label names) and `src/components/basemap.test.tsx` (which engine is
made, with what; one map at a time; view carried over; label switches; go-to; error reporting; the panel) run against stand-ins for
both libraries, because jsdom has no WebGL. **They do not show that any place looks right.** No location was visually verified
(see the test log in the change report): that needs a real token, the network and a browser.
