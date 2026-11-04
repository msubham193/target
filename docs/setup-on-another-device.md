# Setting up the Geospatial PoC on another device

This guide takes the transfer package from the original computer to a second Windows computer and gets the application
running there, with the same projects, photographs and findings. Everything is local: nothing is sent off the machine,
except that **installing the software on the new device downloads packages** (section 3).

The package has three parts:

| Part | File | Contents | Protected? |
|---|---|---|---|
| Source code | `geopoc-source-<date>.zip` | The application, scripts, docs and git history. No password, no `.env`, no dependencies, no data. | No (contains no secrets) |
| Data bundle | `geopoc-data-<date>.7z` | A database backup, the uploaded originals, previews, derived files and the detector model weights. | **AES-256 encrypted**, file names hidden |
| Passphrase | `PASSPHRASE-<date>.txt` | Opens the data bundle. | Keep it **apart** from the other two files |

Each archive has a `.sha256` file next to it so you can prove it arrived intact (section 1).

## 0. What the new device needs

- **Windows 10 or 11, 64-bit.** The project is Windows-only (the locked Python packages exist for Windows only).
- PowerShell 5.1 or newer (built in).
- About **25 GB free** for the Python environment with PyTorch, the package caches, Docker's database image and your data.
  The original computer keeps all of this on drive `D:`; see section 2 if the new device has no `D:`.
- An **internet connection while installing** (section 3). After that the application runs offline.
- An NVIDIA GPU is **optional**. With one, analysis runs on the GPU (the original uses an RTX 5060 and the CUDA 12.8 build
  of PyTorch). Without one the detector falls back to the CPU (`device: auto`): the same results, only slower.
- Software to install first (all free):

| Software | Why | Check it with |
|---|---|---|
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | runs the PostgreSQL + PostGIS database | `docker --version` |
| [Git](https://git-scm.com/download/win) | the zip includes the git history | `git --version` |
| [Python 3.11](https://www.python.org/downloads/) | the API (3.11 exactly: `requires-python` is `>=3.11,<3.12`) | `py -3.11 --version` |
| [uv](https://docs.astral.sh/uv/) | installs the Python packages | `uv --version` |
| [Node.js](https://nodejs.org/) (24 or newer) then `npm install -g pnpm` | the web app | `node --version`, `pnpm --version` |
| [FFmpeg](https://www.gyan.dev/ffmpeg/builds/) (`ffmpeg` and `ffprobe` on PATH) | video analysis | `ffmpeg -version` |
| [7-Zip](https://www.7-zip.org/) | opens the encrypted data bundle | `& 'C:\Program Files\7-Zip\7z.exe'` |
| Microsoft Edge | the browser used for tests | already part of Windows |

After installing Docker Desktop, start it once and wait until it says it is running. If it asks for WSL 2, accept.

## 1. Copy and check the files

1. Copy the transfer folder to the new device (USB drive, local network, or a cloud drive you trust). Send the passphrase
   file **by a different route** (for example, hand it over, or send it in a message), not with the archives.
2. In PowerShell, in the folder that holds the archives, check that each file matches its recorded checksum:

```powershell
Get-ChildItem *.zip, *.7z | ForEach-Object {
    $expected = (Get-Content "$($_.FullName).sha256").Split(' ')[0].Trim().ToLower()
    $actual   = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
    '{0}: {1}' -f $_.Name, $(if ($expected -eq $actual) { 'OK' } else { 'MISMATCH - copy it again' })
}
```

Do not continue if a file says MISMATCH.

## 2. Folders and drive letters

The scripts expect these folders on drive `D:`:

| Folder | Holds |
|---|---|
| `D:\geopoc-data` | uploaded originals, previews, derived files and model weights (the **data root**) |
| `D:\geopoc-cache` | the Python environment, package caches, process records and logs |
| `D:\geopoc-backups` | database backups (created by `backup-db.ps1`) |

**If the new device has a `D:` drive**, create them:

```powershell
New-Item -ItemType Directory -Force D:\geopoc-data, D:\geopoc-cache, D:\geopoc-backups\db
```

**If it has no `D:` drive**, the simplest fix is to make a folder look like `D:` for the session, without editing any script:

```powershell
New-Item -ItemType Directory -Force C:\geopoc-d
subst D: C:\geopoc-d              # must be repeated after every reboot (add it to a start-up shortcut if you like)
New-Item -ItemType Directory -Force D:\geopoc-data, D:\geopoc-cache, D:\geopoc-backups\db
```

Alternatively edit the paths in `scripts\env.ps1` and in `.env` (`GEOPOC_DATA_ROOT`); a few scripts also default to `D:`
(see `Select-String -Path scripts\*.ps1,scripts\lib\*.ps1 -Pattern 'D:\\'`), which is why `subst` is easier.

## 3. Unpack the code and install

```powershell
# a folder of your choice for the code; this guide uses C:\geopoc
New-Item -ItemType Directory -Force C:\geopoc
Expand-Archive -Path .\geopoc-source-<date>.zip -DestinationPath C:\geopoc
Set-Location C:\geopoc
```

Create the settings file. The original `.env` was **not** included on purpose, because it holds the database password.

```powershell
Copy-Item .env.example .env
notepad .env
```

In `.env` set `GEOPOC_DB_PASSWORD=` to a **new** password of your own (letters and digits are enough; keep it private).
Leave `GEOPOC_DB_USER=geopoc` and `GEOPOC_DB_NAME=geopoc` **exactly as they are**: the database backup is owned by that user
and name. Leave `GEOPOC_ONLINE_ENABLED=false`. `.env` is ignored by git and must never be shared.

Now install the dependencies (this is the step that downloads from the internet; the PyTorch part is about 4 GB):

```powershell
. .\scripts\env.ps1                         # keeps caches and the Python environment on D: (run this in every new terminal)
Set-Location services\api
uv sync --group ml                          # installs the API packages and PyTorch (CUDA build; it also runs on a CPU)
Set-Location ..\..\apps\web
pnpm install                                # installs the web app packages from the lock file
Set-Location ..\..
```

If your device has no NVIDIA GPU you can use `uv sync` instead of `uv sync --group ml`, but then the detector is reported
as unavailable and photographs cannot be analysed.

> Windows Smart App Control, if it is turned on, can block native libraries (this happened on the original computer, which
> is why `psycopg`, `pyproj`, `rasterio` and `torch` are pinned to specific versions). If a package fails to load with
> "blocked" in the message, do not work around the control. Use a device without it, or ask your administrator.

## 4. Start the database and restore the data

Start only the database container. Docker Desktop must be running.

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d
docker ps --filter name=geopoc-db           # wait until the status says (healthy)
```

This creates an **empty** database called `geopoc`. Open the data bundle (7-Zip asks for the passphrase):

```powershell
$work = 'D:\geopoc-cache\incoming'
New-Item -ItemType Directory -Force $work | Out-Null
& 'C:\Program Files\7-Zip\7z.exe' x .\geopoc-data-<date>.7z "-o$work"     # prompts: Enter password
Get-ChildItem $work                                                       # expect: geopoc-data\ and geopoc-backups\
```

(Alternatively right-click the `.7z` file, choose 7-Zip, Extract to, and type the passphrase.) Put the files in place:

```powershell
# uploaded files, previews, derived files and the model weights
robocopy "$work\geopoc-data" D:\geopoc-data /E /COPY:DAT /R:1 /W:1
# the database backup (keep it; it is also your first backup on this device)
Copy-Item "$work\geopoc-backups\db\*" D:\geopoc-backups\db\
```

(`robocopy` exit codes 0 to 7 mean success.) Check the backup file is intact, then restore it **beside** the empty database
and swap them (this is the procedure of runbook section 8, using the database container's own tools so PostgreSQL does not
need to be installed on Windows):

```powershell
$dump = Get-ChildItem D:\geopoc-backups\db\*.dump | Sort-Object LastWriteTime | Select-Object -Last 1
$expected = (Get-Content "$($dump.FullName).sha256").Split(' ')[0].Trim().ToLower()
if ((Get-FileHash $dump.FullName -Algorithm SHA256).Hash.ToLower() -ne $expected) { throw 'backup file does not match its checksum' }

docker cp $dump.FullName geopoc-db:/tmp/restore.dump
docker exec geopoc-db psql -U geopoc -d postgres -c 'create database geopoc_restored'
docker exec geopoc-db pg_restore -U geopoc -d geopoc_restored --exit-on-error /tmp/restore.dump
docker exec geopoc-db psql -U geopoc -d geopoc_restored -Atc 'select version_num from alembic_version'   # expect 0009
docker exec geopoc-db psql -U geopoc -d geopoc_restored -Atc 'select count(*) from asset'                # expect 12 (as on the original)

# swap: keep the empty one under another name instead of deleting it
docker exec geopoc-db psql -U geopoc -d postgres -c 'alter database geopoc rename to geopoc_empty'
docker exec geopoc-db psql -U geopoc -d postgres -c 'alter database geopoc_restored rename to geopoc'
docker exec geopoc-db rm -f /tmp/restore.dump
```

If the rename says the database "is being accessed by other users", wait a few seconds and run it again (nothing else should
be connected yet). If `pg_restore` reports an error, stop; do not rename anything, and keep the files. Nothing has been
changed on the original device by any of this.

## 5. Start the application

```powershell
. .\scripts\env.ps1
.\scripts\dev.ps1
```

`dev.ps1` checks Docker and the database, starts the API (port 8000), the background worker and the web app (port 5173), and
waits until each is healthy. The detector needs two to three minutes after a cold start (`warming-up` then `ready`). It does
**not** change the database. Then open **<http://127.0.0.1:5173>**.

Check it:

```powershell
.\scripts\smoke.ps1 -Mode working            # read-only (GET only) health and consistency checks
```

Expected: the API reports `status ok`, database `geopoc`, schema revision `0009`, the worker running, and the detector
manifest found. In the browser, choose a profile (for example "Reviewer"). The project "UI review - harbour photographs"
should list its photographs and findings. Profiles are only names recorded in the history; there is no password.

Confirm the model weights arrived: open <http://127.0.0.1:8000/health/models>; the model should be `available`. If it says the
weights are missing, check that `D:\geopoc-data\weights\fasterrcnn_resnet50_fpn_v2\coco_v1\` holds a `.pth` file of
175,221,657 bytes. (As a last resort `uv run python -m geopoc.devtools fetch-model fasterrcnn_resnet50_fpn_v2@coco_v1`
downloads it again and verifies the checksum from `models/manifest.yaml`.)

### Daily use

| To do this | Run |
|---|---|
| Start everything (after a reboot add `-StartDocker`) | `.\scripts\dev.ps1` |
| Stop the API, worker and web app (database keeps running) | `.\scripts\stop.ps1` |
| Health check | `.\scripts\smoke.ps1 -Mode working` |
| Back up the database | `.\scripts\backup-db.ps1 -Verify` (needs PostgreSQL client tools, or set `GEOPOC_PG_BIN`; see runbook) |
| Everything else (recovery, restore, disk space) | [runbook.md](runbook.md) |
| Manual acceptance tests | [acceptance-checklist.md](acceptance-checklist.md) |

Run `.\scripts\env.ps1` as `. .\scripts\env.ps1` (with the leading dot) in every new PowerShell window before other scripts.

## 6. If something goes wrong

| Symptom | What to do |
|---|---|
| `The Docker engine is not running` | Open Docker Desktop, wait until it says it is running, run `.\scripts\dev.ps1` again. |
| `docker compose` complains about `GEOPOC_DB_PASSWORD` | `.env` is missing or the password line is empty (section 3). |
| Database authentication fails after a restart | The password in `.env` must be the one used when the container was first created. If you changed it afterwards, put the old one back. |
| Port 5433, 8000 or 5173 already in use | Something else uses it. Stop that program; do not change the ports without also changing `.env`. |
| The dev script says the database is behind the code | The database is not at revision 0009. Do not migrate blindly: read runbook section 9 first. |
| Photographs show but images are blank | The data folder copy is incomplete. Compare file counts of `D:\geopoc-data\originals` with the original device (44 MB, about a dozen files). |
| Detector `probe-failed` or `worker-not-running` | `.\scripts\stop.ps1 -Only worker`, then `.\scripts\dev.ps1` (runbook section 5). |
| `pg_restore` errors | Keep everything as it is; the data bundle and the original device are unchanged. Report the exact message. |

## 7. Security notes

- The services listen on `127.0.0.1` only. Do not change them to `0.0.0.0` and do not expose the ports.
- Local profiles are **not** authentication. Anyone who can use the computer can use the application and see the data.
- The data bundle contains project photographs and locations. After the new device works, delete the encrypted bundle and the
  extracted `D:\geopoc-cache\incoming` folder from places you do not control (USB sticks, shared drives), and keep the
  passphrase file out of cloud storage.
- The database password lives only in `.env` on each device. The two devices have independent passwords.
- Keep Earth Engine disabled (`GEOPOC_EARTH_ENGINE_ENABLED` unset). It is the one feature that would contact a Google service.
- The new device holds a **copy**. Changes made there do not flow back to the original (and vice versa). Choose which one is
  the working copy, and take a verified backup before moving data again.
