# Acceptance checklist

State when this was written (2026-10-30): the working database `geopoc` is at **revision 0009** (G4 done) and the latest
application is running on 8000 (API) and 5173 (web) (G5 done). The read-only post-deployment checks passed (smoke 14/14,
26 browser checks). **No manual acceptance step below has been run on the working project yet**, and the working project
holds no annotations.

- Part A is the manual, browser-based acceptance test, step by step, with every step that stores data marked.
- Part B records the annotation report/export checks that are already covered by automated tests and by the rehearsal copy.

## How to run Part A safely

Every step is marked with one of these tags:

| Tag | Meaning |
|---|---|
| **[READ-ONLY]** | Nothing is stored. Opening the report or an export adds one line to the audit log (by design). |
| **[WRITES: kept]** | Stores data that stays. The application has no hard delete for it (see the cleanup guidance). |
| **[WRITES: append-only]** | Stores a version in a history table that can never be edited or removed. A later "delete" or "clear" adds another version. |
| **[WRITES: temporary]** | Stores data, but only for the duration of a view or job, or it is undone by the next step. |

Before the first write:

1. **Use a dedicated test project**, not "UI review — harbour photographs": New Project & Location, name it
   `ACCEPTANCE TEST <date>` **[WRITES: kept]**. Everything below that writes then lands in that project, and the project can be
   archived afterwards.
2. **Use synthetic or already-approved files only.** No real or sensitive imagery, and nothing fabricated and presented as real.
   Name the files so they are recognisable (for example `acc_photo_1.jpg`, `acc_dem.tif`).
3. Take a verified backup first, as a rollback point: `.\scripts\backup-db.ps1 -Verify`. Remember that restoring it later would
   also discard anything real that was saved after it was taken, so prefer archiving the test project (see cleanup).
4. Write down the counts before you start (`project`, `asset`, `observation`, `photo_location`, `map_annotation`, `audit_event`)
   so you can account for every change afterwards. The stop condition for the whole test: if something unexpected is stored, stop
   and report; do not try to fix it by editing the database.

## Part A: manual acceptance (in the browser, http://127.0.0.1:5173)

Choose an existing local profile (profiles are attribution, not login). Tick each step only when the expected result is seen.

### 1. Photo upload and GPS confirmation

| # | Step | Expected | Data |
|---|---|---|---|
| 1.1 | Upload & Ingestion, tab **Ground photograph**; add 2 synthetic JPEGs, one with GPS metadata and one without | Each shows **Stored**; a preview appears after the automatic preview job; the original file is unchanged (the SHA-256 on the asset page matches the file) | **[WRITES: kept]** asset, original file, preview job |
| 1.2 | Add the same file again | Shown as **Duplicate — linked**, no second original stored | **[WRITES: kept]** a link record |
| 1.3 | Try a file that is not an image (for example a renamed text file) | Shown as **Not accepted** with a reason; nothing stored as an asset | audit line only |
| 1.4 | Ground Image Analysis, open the photo with GPS, panel **Photo location** | Shows an **unconfirmed candidate**; its position is NOT drawn on the map and is NOT counted as a location | **[READ-ONLY]** |
| 1.5 | Press **Confirm this position** | The panel shows **Confirmed location**, source "EXIF GPS position, confirmed by a person", and your profile name | **[WRITES: append-only]** photo location version |
| 1.6 | On the photo without GPS, type a latitude and longitude, tick **I have checked this position**, press **Save location** | **Confirmed location**, source "Coordinates typed in". Out-of-range values, swapped values and 0, 0 are refused with a reason | **[WRITES: append-only]** |
| 1.7 | (Optional) Clear one confirmed location | State returns to "No location"; the history shows the confirm and the clear | **[WRITES: append-only]** |

### 2. Photo workspace and comparison

| # | Step | Expected | Data |
|---|---|---|---|
| 2.1 | Photo Workspace | Every project photograph is listed with its location state | **[READ-ONLY]** |
| 2.2 | Tick two photographs, open the **Compare** tab | Stored facts only, with "What this comparison does not say". With two confirmed positions, the distance between the recorded camera positions is shown with its unit and "about"; with one or none, no distance is shown | **[READ-ONLY]** |
| 2.3 | Open the **Map** tab | Confirmed photographs are markers; unconfirmed candidates are not drawn | **[READ-ONLY]** |

### 3. Findings report and exports

Run these after steps 1, 4 and 5, so the report has something in it. Each report or export generation is recorded in the audit
log; that is the only thing they store.

| # | Step | Expected | Data |
|---|---|---|---|
| 3.1 | Reports, **Open findings report** | Sections 1 to 9. Section 6 lists findings (reviewed apart from unreviewed); section 7 lists photographs with confirmed positions only; **section 9 "Map annotations"** lists active annotations, then deleted ones marked **"Deleted: not an active mark"**, each with ID, shape, note, author, version and times, and a version history table | **[READ-ONLY]** (1 audit line) |
| 3.2 | Check the summary counts against what you did | Annotations: active and deleted match step 4. Photos: confirmed location matches step 1. Annotations are never counted among findings | **[READ-ONLY]** |
| 3.3 | **Download CSV** | One row per observation and 26 columns; **no annotation or photograph-location rows** | **[READ-ONLY]** (1 audit line) |
| 3.4 | **Download JSON** | Has `observations`, `photo_locations` and an `annotations` member with active **and** deleted items and every version | **[READ-ONLY]** (1 audit line) |
| 3.5 | **Download GeoJSON** | Active annotations are features with `feature_type: "user_annotation"`; **deleted annotations are not features** and are counted in `annotations.deleted_not_exported`; located photographs are separate features | **[READ-ONLY]** (1 audit line) |
| 3.6 | Print the report to a PDF printer | Section 9 prints, deleted annotations visibly marked; the page states that no PDF is created by the application | **[READ-ONLY]** |
| 3.7 | Open the report of a project with no annotations | "No map annotations are stored for this project." and counts of 0 | **[READ-ONLY]** |

### 4. Annotation create, edit, delete and history

Annotations are the most permanent data in this test. Every change is a new row that can never be edited or removed; keep the
number of annotations small (one of each kind is enough) and put them only in the test project.

| # | Step | Expected | Data |
|---|---|---|---|
| 4.1 | Map & Analysis, panel **Annotations**: **Add point**, click the map, enter a note, **Save annotation** | Listed as a point, version 1, with your profile as author; drawn in magenta with the legend "Your annotations (not findings)" | **[WRITES: append-only]** |
| 4.2 | **Add line** (2 clicks), note, save | Length shown as "Length (approximate)" with a unit | **[WRITES: append-only]** |
| 4.3 | **Add polygon** (3 or more clicks), note, save | Area and outline length shown as "approximate" | **[WRITES: append-only]** |
| 4.4 | Try to save a polygon with fewer than 3 positions, or a note over 2000 characters | The save button stays disabled or the refusal says why; nothing is stored | none |
| 4.5 | **Edit** the line: change the note, **Save changes** | "Saved as a new version"; the list shows version 2 | **[WRITES: append-only]** |
| 4.6 | **Show history** of the line | Version 1 and version 2 are both listed with author, time and the changed note | **[READ-ONLY]** |
| 4.7 | **Delete** the polygon and confirm **Delete annotation** | It leaves the list and the map; the panel reads "2 active · 1 deleted" | **[WRITES: append-only]** a "deleted" version |
| 4.8 | Tick **List deleted annotations** and open the polygon's history | The deleted polygon appears, marked deleted; its history shows created then deleted | **[READ-ONLY]** |
| 4.9 | Reload the page | The same 2 active and 1 deleted | **[READ-ONLY]** |

### 5. Video-frame analysis and cancellation

Analysis uses the installed detector on the GPU and takes time. Use a short synthetic clip (a few seconds). Nothing starts until
you press the button.

| # | Step | Expected | Data |
|---|---|---|---|
| 5.1 | Upload & Ingestion, add a short synthetic video | **Stored**; Video Analysis lists it with its duration | **[WRITES: kept]** asset and video details |
| 5.2 | Video Analysis: open the video, read the sampling form but do not submit | "This video has not been analysed yet"; a plan of how many frames would be analysed and the stated limit | **[READ-ONLY]** |
| 5.3 | Set 1 frame every 1 s, at most 10 frames, **Analyse these frames** | **Latest analysis job** shows progress, then **Needs review** or **Completed**; sampled frames, a timeline and boxes appear | **[WRITES: kept]** job, run, observations (findings, proposals only), frame images |
| 5.4 | Click a frame on the timeline | The frame image with its boxes; findings show frame number and time | **[READ-ONLY]** |
| 5.5 | Review one finding (verify or reject) | The status changes and a revision with your profile name is recorded | **[WRITES: append-only]** observation revision |
| 5.6 | Reload; then open the analysis again | The same frames and findings; nothing runs again (still exactly one job) | **[READ-ONLY]** |
| 5.7 | **Cancellation:** on a second video, or the same one with a different policy, press **Analyse**, and press **Cancel** in the job row while it runs | Status becomes cancelled; the job says whether any outputs exist; **no findings are saved from a cancelled job** (results are written in one step at the end) | **[WRITES: temporary]** a cancelled job record stays; no findings |

### 6. 3D terrain upload and rendering

Use a **synthetic** elevation GeoTIFF (a small float32 grid, declared metres, with a CRS; a hole of no-data is a good test).
Colour imagery must never be offered as terrain.

| # | Step | Expected | Data |
|---|---|---|---|
| 6.1 | Upload & Ingestion, tab **Satellite / aerial raster (GeoTIFF)**: add the elevation file | **Stored**; georeferencing shown | **[WRITES: kept]** asset, raster details |
| 6.2 | 3D View | The list marks the new file **Elevation** and the imagery rasters **Not elevation** or **Cannot be drawn**, each with its reasons. No reason line is missing and the browser console shows no errors | **[READ-ONLY]** |
| 6.3 | Select the elevation raster | The surface is drawn; the panel states grid size, decimation, no-data cells, elevation unit and source, and what zero height means (or that the datum is not stated) | **[READ-ONLY]** |
| 6.4 | Drag to orbit, scroll the wheel to zoom, shift-drag or right-drag to pan; also try the arrow keys with the view focused and the **Reset view** button; hover the surface | The view moves and resets; the readout shows the stored elevation with its unit | **[READ-ONLY]** |
| 6.5 | Change vertical exaggeration | The view states the exaggeration; true scale (×1) is the default | **[WRITES: temporary]** for this view only |
| 6.6 | A raster that only possibly holds elevation: state the band and unit, tick the confirmation | Drawn, credited to you, "used for this view only. It is not saved" | **[WRITES: temporary]** nothing stored |
| 6.7 | Select an imagery raster | "Cannot be shown in 3D" with the reasons; no terrain is generated | **[READ-ONLY]** |

### 7. Persistence after reload and restart

| # | Step | Expected | Data |
|---|---|---|---|
| 7.1 | Hard-reload every page used above | The same photographs, locations, annotations (2 active, 1 deleted), findings, video results and rasters | **[READ-ONLY]** |
| 7.2 | `.\scripts\stop.ps1`, then `.\scripts\dev.ps1` (the database container is left running) | `dev.ps1` says the schema is current (revision 0009), applies no migration, and reports the API, web and detector healthy | none |
| 7.3 | `.\scripts\smoke.ps1 -Mode working` | All checks pass (GET only) | **[READ-ONLY]** |
| 7.4 | Repeat 7.1 | Everything is still there; nothing re-ran; the video job count is unchanged | **[READ-ONLY]** |
| 7.5 | Optional full restart: stop the services, restart Docker Desktop, then `.\scripts\dev.ps1 -StartDocker` | Same result as 7.4 | none |
| 7.6 | Account for every change | Compare the counts to the ones you wrote down. The only differences are the items you created, plus audit lines | **[READ-ONLY]** |

At every step, also check that the browser console shows no errors and that no request leaves 127.0.0.1 (the header badge says
"Local only"; online access is off).

## Cleanup guidance (respects append-only history)

The application is built so that stored evidence cannot be quietly removed. That shapes what "cleanup" means:

| Kind of data | What you can do | What you cannot do |
|---|---|---|
| **Annotations** | **Delete** each in the panel (step 4.7). It disappears from the map and the active list but its history, and the "deleted" version, stay forever and appear in the report and JSON export marked deleted | Edit or remove a version; make a deleted annotation vanish from the report's history. A "restore" does not exist (it could be added without a schema change) |
| **Photo locations** | **Clear** a confirmed location (step 1.7). The photograph returns to "No location" and the clear is added to its history | Remove a history row |
| **Uploaded files, assets, findings, video results, jobs** | Leave them; the project's **Archive project** button hides the project from the active list and **Restore project** brings it back | Delete an asset or an original (originals are never modified or removed by the application) |
| **A whole project** | **Archive** the test project (Project overview). Its data stays in the database and the report | Delete it |
| **Audit log** | Nothing: it is append-only | Edit or remove a line |

Therefore:

- Keep all test data in the dedicated `ACCEPTANCE TEST` project, so the real project is never touched, then archive it.
- Do not use `scripts\reset-working-data.ps1` for cleanup. It is not routine: it erases the working database content and moves
  the stored files to quarantine, needs a typed confirmation and a verified backup, and would also remove real work.
- If something wrong was stored in the real project, do not edit the database. Add a correcting annotation, a "deleted"
  version, or a "cleared" location, and note it. Restoring a backup is the last resort and discards everything saved since.
- The test annotations remain visible in that project's report, marked deleted. That is correct behaviour, not a leftover.

## Part B: annotations in the report and exports, already covered

Status of each line: **Rehearsed** = verified on the restored copy `geopoc_rehearsal`; **Automated** = covered by a test that
runs on every check. The working database has been migrated and checked read-only (empty state); the populated cases above are
what Part A re-checks.

### Report (Reports, Findings report)

| # | Check | How | Status |
|---|---|---|---|
| R1 | Section "9. Map annotations" exists, after Conflicts; sections 1 to 8 keep their numbers | Page test; rehearsal spec | Automated, Rehearsed |
| R2 | Summary shows "Annotations: active" and "Annotations: deleted", apart from Findings and Photographs | Page test; rehearsal spec | Automated, Rehearsed |
| R3 | Each annotation shows ID, kind, shape (latitude, longitude), note, version, who drew it and when, who last changed it and when | Page test; backend test | Automated |
| R4 | Active and deleted annotations are under separate headings; a deleted one says "Deleted: not an active mark" in words, shows who deleted it and when, and has no measurement | Page test; rehearsal spec | Automated, Rehearsed |
| R5 | Version history table: created / changed (note, shape) / deleted, with reason, author and time | Page test | Automated |
| R6 | No annotation text appears in "6. Findings"; finding counts are unchanged by annotations | Page test; backend test; rehearsal spec | Automated, Rehearsed |
| R7 | A project with no annotations says "No map annotations are stored for this project." | Page test; backend test; working project (read-only) | Automated, Verified on working DB |
| R8 | A database without the annotation table says annotations cannot be listed and that this does not mean there are none; counts show "Unavailable", never 0 | Page test; backend test | Automated |
| R9 | Hostile note, reason and author text is shown as text; no script or image is created | Page test; backend test | Automated |
| R10 | Generating the report writes only an audit line (counts, never note text); the annotation table is not touched | Backend test | Automated |

### JSON export (`observations.json`)

| # | Check | How | Status |
|---|---|---|---|
| J1 | `annotations` member lists active and deleted annotations, each with `status`, geometry, kind, note, author, version and timestamps | Backend test; rehearsal spec | Automated, Rehearsed |
| J2 | Every version of every annotation is present (a deleted polygon has 2: created, deleted) | Backend test; rehearsal spec | Automated, Rehearsed |
| J3 | `observations`, `photo_locations`, `conflicts` and `runs` are identical with and without annotations | Backend test | Automated |
| J4 | The `status` filter applies to observations only | Backend test | Automated |

### GeoJSON export (`observations.geojson`)

| # | Check | How | Status |
|---|---|---|---|
| G1 | Active annotations are separate features with `feature_type: "user_annotation"`, `status: "active"`, the annotation ID, kind, note, version, author and timestamps | Backend test; rehearsal spec | Automated, Rehearsed |
| G2 | Deleted annotations are **not** features; `annotations.deleted_not_exported` counts them | Backend test; rehearsal spec | Automated, Rehearsed |
| G3 | Observation and photograph features and the `excluded`/`photographs` members are unchanged | Backend test | Automated |

### CSV export (`observations.csv`)

| # | Check | How | Status |
|---|---|---|---|
| C1 | Byte-identical with and without annotations in the project (and with annotations in another project) | Backend test | Automated |
| C2 | Same 26 columns; one row per observation; no annotation ID or note anywhere | Rehearsal spec | Rehearsed |
| C3 | Byte-identical to the pre-annotation code's output on the same data, with the annotation table present and with it forced unavailable (3 filter variants, 26 data rows) | Comparison against the G1 source snapshot, 2026-10-30 | Verified |

### All three

| # | Check | How | Status |
|---|---|---|---|
| X1 | Another project's annotations never appear (including its deleted ones); an unknown project is 404 | Backend test | Automated |
| X2 | A local profile is required (401 without one, nothing leaked, no audit line) | Backend test | Automated |
| X3 | History stays append-only: reading changes no row of `map_annotation` | Backend test | Automated |
| X4 | No request leaves the machine while opening the report or the exports | Rehearsal spec; working app browser check | Rehearsed, Verified on working DB |
| X5 | Report and exports list the same annotations with the same statuses | Backend test; rehearsal spec | Automated, Rehearsed |

### 3D page

| # | Check | How | Status |
|---|---|---|---|
| T1 | Repeated reason, disclosure or warning text is shown in full and raises no React duplicate-key warning | Page test (3 cases); working app browser check | Automated, Verified on working DB |
