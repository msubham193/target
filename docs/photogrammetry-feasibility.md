# Photogrammetry (3D from photographs): what a feasibility study would need

Status (2026-10-29): **not implemented and not started.** Nothing in the application reconstructs 3D from photographs. This is a
list of what such a capability would require, written so a later feasibility study can be planned and approved. It states no
result, because none was measured. Everything below marked "to be checked" is an open question, not a finding.

The [3D terrain viewer](3d-terrain-viewer.md) is separate: it draws elevation rasters that already exist. Photogrammetry could
*produce* such a raster (a digital surface model) which the viewer could then draw, but only after the study below and its own
approvals.

## 1. What photogrammetry needs from the photographs

| Requirement | Why | What the application has today |
|---|---|---|
| **Overlap**: typically 60-80 % between consecutive photographs along a path and 30-60 % between neighbouring paths (aerial); for ground photographs, many views of each surface point from different positions | points must appear in several images so their position can be triangulated | no check: nothing measures overlap between a project's photographs |
| **Baseline / parallax**: the camera must move between shots (rotating on the spot gives no depth) | depth comes from the change of view | camera positions exist only where a person confirmed them, as one point per photo |
| **Static, textured, well-lit subject**; limited blur and motion | matching needs repeatable features | not assessed: no blur, texture or overlap analysis exists |
| **Enough photographs**: tens to thousands for a useful result | coverage and redundancy | unknown for any project |
| **Consistent camera**: same lens/focal length, or each recorded | the camera model is estimated per camera | EXIF is read as metadata on upload; what it holds has not been checked for this purpose |

## 2. Camera calibration

* **Intrinsics**: focal length, principal point, lens distortion. Software can estimate them from the photographs (self-calibration)
  but accuracy depends on the geometry above; EXIF gives a nominal focal length (and often no sensor size).
* **Extrinsics** (where each camera was and where it pointed): estimated by the software from the images (structure from motion).
  The application stores **no camera orientation** at all.
* To be checked: whether the photographs this project actually has (phone, camera, drone) carry usable EXIF and whether lens
  distortion is significant.

## 3. Georeferencing (putting the model on the Earth)

A reconstruction is first in an arbitrary local frame and scale. To place and scale it:

* **EXIF GPS** gives a camera position typically good to metres to tens of metres, and often not at all. In this application an
  EXIF position is an **unverified candidate**; only a person-confirmed position is a location, and it is a camera position of
  one photograph, with no accuracy stated. That is weaker than what a survey-quality model needs.
* **Ground control points (GCPs)** with surveyed coordinates, or **RTK/PPK** camera positions, are what make centimetre-to-decimetre
  absolute accuracy possible. The application has no GCP concept, no vertical datum handling and no accuracy model.
* The CRS, the vertical datum and the geoid are choices that must be recorded (the viewer already shows "datum not stated" when
  the file does not say).
* A model scaled only from EXIF positions or a guessed scale must be labelled as such, and no measurement from it should be offered
  as survey-grade.

## 4. Hardware and software constraints on this machine

Observed facts are in [gpu-feasibility.md](gpu-feasibility.md): an NVIDIA RTX 5060 with 8 GB of video memory (about 1.5 GB used by
the desktop), a CUDA 12.8 PyTorch build in use, the drive layout (code on C:, caches and data on D:), and Windows Smart App Control
having blocked native packages before. For photogrammetry specifically (to be checked, not measured):

* **Dense reconstruction is memory- and GPU-hungry**: system RAM and VRAM limit the number and size of photographs per run;
  large sets need downscaling or splitting. 8 GB of VRAM is likely to constrain dense matching.
* **Disk**: sparse and dense clouds, depth maps and meshes are many times the size of the photographs.
* **Run time**: hours for hundreds of photographs is plausible; the job system would need long-running, cancellable, resumable
  jobs with progress, and a way to keep the GPU free for the detector.
* **Software candidates** (none installed, none approved): COLMAP, OpenMVS, Meshroom (AliceVision), OpenDroneMap. Each differs in
  licence, Windows/CUDA support, native dependencies (which Smart App Control may block) and output formats. Licences and
  redistribution terms must be checked before any is adopted, and installing any is a new dependency needing approval.
* **Offline operation**: the chosen tool must work without any network or cloud service (the application is local-first).

## 5. Outputs and how they would meet the viewer

* Sparse point cloud (camera poses + tie points), dense point cloud, textured mesh, orthomosaic, and a **DSM/DEM as GeoTIFF**.
* Only the GeoTIFF DSM fits the current viewer unchanged (a georeferenced single-band height raster with a stated unit and CRS).
  Point clouds and meshes would need a new viewer path and new storage (a schema decision, with approval).
* Every derived product must be stored as a derived asset with lineage back to the photographs, the software and its version, its
  parameters, and an accuracy statement, as rasters made by the application's other tools already are.

## 6. Risks to state up front

* A reconstruction can look convincing and be wrong (scale drift, doming, moving objects, repeated texture). It needs **independent
  check points** and reprojection-error reporting, not just a good-looking picture.
* An unreferenced or weakly referenced model must never be presented as a located, measured surface.
* Photographs are the person's data: the study must not upload them anywhere.

## 7. A bounded feasibility study (proposal, needs approval)

1. **Choose a dataset** the project already owns, or a small public sample (a download needs approval): 30-100 overlapping photographs
   of one scene, with some surveyed or otherwise known distances or points.
2. **Check suitability** without reconstruction: overlap estimate, EXIF presence, blur, camera consistency.
3. **Pick one open-source tool** on licence and Windows/offline grounds; install it **in a scratch environment** (approval needed);
   do not touch the application's environment.
4. **Reconstruct once**, offline, on a subset first. Record time, peak RAM/VRAM, disk, and failures.
5. **Evaluate**: reprojection error, scale and position error against the known distances/check points, visual artefacts.
6. **Decide**: go (and what accuracy can be claimed), go with limits, or stop. Only then design storage, jobs and UI.

Acceptance for "feasible": a reconstruction completes within the machine's limits and its error against independent check points
is stated and acceptable for the stated use. Nothing is claimed before that.
