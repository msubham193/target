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

## Online 3D view

The 3D Terrain screen has an "Online 3D (Mapbox)" tab that uses the same token and style with terrain and raised buildings,
tilted. See docs/3d-terrain-viewer.md, section 0. Like the basemap, it sends the view to Mapbox.

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

## Satellite Images: find a location, and where an uploaded image is

**Find a location** (`/projects/:id/satellite?view=location`, also the screen when a project has no images) is the Mapbox
satellite map with one search box (`components/SatelliteLocationView.tsx`, `PlaceSearch` with `coordinates` and `landmarks`):

- **Coordinates** are read on this computer by `lib/coordinates.ts` and never sent to Mapbox: decimal degrees
  (`20.2961, 85.8245`), hemisphere letters before or after (`20.2961 N 85.8245 E`, `N 20.2961 E 85.8245`, any order when both
  carry a letter) and degrees-minutes-seconds (`20°17'46"N 85°49'28"E`). Plain numbers are latitude first; a reversed pair is
  refused with a "did you mean" hint, never swapped. They work without a Mapbox token (on the plain offline map).
- **Place names** go to Mapbox: the Search Box API (landmarks and points of interest) and the geocoding v6 API (addresses and
  places) in parallel, biased with `proximity` towards the area being viewed, merged landmarks first and de-duplicated. Natural
  features (mountains) are poorly covered by both; use coordinates for them.
- The chosen position is kept in the address (`lat`, `lon`, `label`) so a view can be reloaded or shared. Clicking the map pins
  a point and shows its position. Nothing is saved to the project.
- The project's **georeferenced images** (not elevation files) are drawn in place over the satellite map from their tiles, with
  a switch to hide them; `?image=<asset id>` frames one.

In the **image viewer**, a georeferenced image shows the centre of its footprint and **Show on satellite map**. Clicking a point
asks `GET /api/v1/assets/{id}/pixel?col=&row=` for its longitude/latitude, so the server's datum-transformation policy applies:
where the conversion is not exact enough, its note is shown instead of a position. An image without georeferencing offers no
positions.
## Photos on the satellite map: where a photo was taken, and which way its objects are

**Find a location** (Satellite Images) also shows the project's photos:

- **Where each photo was taken.** Each photo with a *confirmed* location appears as a green marker, and the photos are listed
  on the left. An unconfirmed GPS position from a file is never drawn; confirm it in Photo Analysis first. Opening a photo
  (from the marker, the list, Photo Analysis → *Show on the satellite map*, or the Map page) zooms to it and shows the
  photo with its detection boxes.
- **What the AI found and what it sees.** Under the photo: the latest analysis counted by class ("AI analysis: 54
  objects — car 23, person 19, …"; rejected findings left out), and **What the AI sees**: a short description of the scene
  and what is going on, written by the local Qwen vision model (`qwen2.5vl` through Ollama, on this computer only) from
  the preview picture and the counts (`POST /api/v1/assets/{id}/scene-description`). It is written once per photo per
  session when the panel opens (12–70 s), checked before it is shown (numbers must be the detector's counts; no
  targeting, threat or intent language; no positions), labelled as an unreviewed draft, never stored, and audited without
  its text. The hover popup over a photo marker shows the photo with its boxes, the counts, and the description once it
  has been written.
- **Which way the camera faced.** If the photo file records a compass direction (EXIF `GPSImgDirection`), it is used and said
  so, including whether it is from true or magnetic north. It is not checked: a phone compass can be off by tens of degrees.
  Many files, including GPS Map Camera photos, record no direction. Then the person sets it: *Set on the map*, then click the
  satellite map at something visible in the middle of the photo. Sliders fine-tune the direction, the width of view (from
  the lens's 35 mm equivalent focal length when the file gives it, otherwise 65°, typical of a phone) and the line length.
  These settings are kept in this browser only.
- **Which way each object is.** Each object from the latest analysis is drawn as a coloured line from the camera, at the angle
  given by where its box sits across the picture. **A photo gives a direction, not a distance: the object is somewhere along
  its line.** Lens distortion and camera tilt are ignored, so directions are approximate. Objects a reviewer rejected are
  not drawn, and whole classes can be hidden.
- **Where an object is.** An object is put on the map only when a person marks it: *Mark*, then click the spot on the
  satellite map. The panel shows the marked position and its distance from the camera. A mark stays in this browser
  until *Save as a map note* turns it into a map annotation (point) in the project, with a note naming the object and the
  photo. The annotation is the person's mark; it does not change the finding, which stays a position in the picture.

- **Every object at once, from matched ground spots.** *Place all objects on the map* → *Match spots*. The panel widens so
  the photo is large. Click a spot on the flat ground in the photo (the end of a lane marking, a kerb corner, the edge of
  the median), then click exactly the same spot on the satellite map; repeat. With 4 or more pairs the app fits the
  photo-to-ground mapping (a plane-to-plane homography) and places **every** detected object at the middle of its box's
  bottom edge, where it touches the ground. Objects are coloured dots; hollow dots are outside the matched area
  (extrapolated) and less reliable; objects whose bottom edge is at or above the horizon cannot be placed.
  - **Accuracy.** Each pair shows how far the fit is from it. From 5 pairs, every spot is predicted from the others
    (leave-one-out) and the panel says "about ±X m where you matched spots". A large figure, or one pair far off, means a
    mismatched spot: remove it. Use 6–8 spots spread from near to far and left to right.
  - **What it assumes.** The ground between the spots is flat (a road, a yard). Things not on that ground (signs, people on a
    balcony, a vehicle on the flyover deck when the spots are on the road below) are placed wrongly. Lens distortion is
    ignored. Positions are relative to Mapbox's imagery, which may itself be shifted a few metres from true ground.
  - **Keeping the result.** The matched spots are kept in this browser with the photo's other settings. *Download positions
    (GeoJSON)* gives a file with each object's class, number, score, review status, position, method and an extrapolated
    flag. *Save positions as map notes* (after a confirmation that gives the count) adds one point annotation per object to
    the project, with a note saying the position is an estimate and how it was found; extrapolated objects are left out
    unless ticked. A person's own *Mark* on an object takes precedence over the estimate.

The direction is read from the stored original by `GET /api/v1/assets/{id}/camera-view` (checksum checked first). It is read
each time and never stored. The matching is computed in the browser; no backend or database change was needed.
