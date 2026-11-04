# Runbook: start, recover, back up, restore

For the person running the application on this computer. Everything here is local; nothing is sent off the machine.
Commands are for PowerShell, run from the repository folder (`C:\Users\HP\Desktop\target`).

## 0. Which commands are safe

| Command | Changes data? | Notes |
|---|---|---|
| `.\scripts\dev.ps1` | no | Starts what is missing. Safe to run twice. Never applies migrations. |
| `.\scripts\stop.ps1` | no | Stops the API, worker and web app only. The database container keeps running. |
| `.\scripts\smoke.ps1 -Mode working` | no | Read-only (GET only). |
| `.\scripts\smoke.ps1` | scratch only | Uses database `geopoc_smoke`, folder `_smoke` and port 8020. |
| `.\scripts\backup-db.ps1 -Verify` | no | Writes a new file under `D:\geopoc-backups\db`; creates and drops a temporary database. |
| `.\scripts\migrate.ps1` | no (dry run) | `-Apply` changes the database and needs a verified backup. |
| `.\scripts\reset-working-data.ps1` | **only with `-Execute`** | Dangerous. See section 9. |

## 1. Normal start

```powershell
.\scripts\dev.ps1
```

It checks the Docker engine, starts the database container, **reports** whether the database schema matches the code,
starts only the services that are not already running (API on 8000, worker, web app on 5173), then waits until each is
healthy and prints a summary. Open <http://127.0.0.1:5173>.

- Running it again is harmless: a running service is never started a second time.
- `-SkipWeb` starts only the API and worker. `-NoWait` returns immediately.
- The detector (the AI model on the GPU) is the last thing to become ready, about 2-3 minutes after a cold start.
  The summary shows `warming-up` until then. Do not run an analysis before it says `ready`.

## 2. After a reboot or sign-in

Docker Desktop does not start by itself on this machine, so nothing works until it does.

```powershell
.\scripts\dev.ps1 -StartDocker
```

This starts Docker Desktop (up to 4 minutes), then does everything in section 1. The database container restarts by
itself once Docker is up. Your data is stored in a Docker volume and survives reboots.

If it stops with "The Docker engine is not running": open Docker Desktop yourself, wait until it says it is running, run
`.\scripts\dev.ps1` again.

## 3. Checking that it is healthy

```powershell
.\scripts\smoke.ps1 -Mode working
```

Read-only. It checks the health document, that the database is reachable, that online access is off, that the key routes
exist, that errors are readable, and that photographs have coordinates only when a person confirmed them. If the API is an
older version that does not report which database it uses, the output says so (the checks are still read-only).

By hand: <http://127.0.0.1:8000/health> (status `ok`, `worker.running` true) and <http://127.0.0.1:8000/health/models>
(model `available`). In the application: Settings, "Preferences and diagnostics".

## 4. Stopping and restarting one service

```powershell
.\scripts\stop.ps1 -DryRun          # lists what would be stopped
.\scripts\stop.ps1 -Only worker     # stops only the worker (api, worker or web)
.\scripts\dev.ps1                   # starts whatever is missing
```

Restart only the worker when the detector shows `probe-failed` or analysis jobs are stuck in the queue. The worker
recovers jobs that were running when it stopped (they are retried or failed, never lost).

`stop.ps1` never stops Docker or the database. If it reports more than one API or worker, stop that service with `-Only`
and start again.

## 5. Detector readiness

`GET /health/models`, or the "Detector" line of `dev.ps1`:

| State | Meaning | Action |
|---|---|---|
| `ready` | The model can run on the GPU. | none |
| `warming-up` | The worker is checking PyTorch and the GPU. | wait 2-3 minutes |
| `probe-failed` | The check crashed (seen once after a cold start). | `.\scripts\stop.ps1 -Only worker`, then `.\scripts\dev.ps1` |
| `worker-not-running` | No worker. Jobs wait in the queue. | `.\scripts\dev.ps1` |
| `unavailable` | The weights are missing or do not match. | See the reason shown; `fetch-model` is the only command that downloads anything. |

## 6. Disk-space problems

`C:` was once completely full, which stops Docker and the database. Docker's disk image lives on `C:`
(`C:\Users\HP\AppData\Local\Docker\wsl\disk\docker_data.vhdx`); uploads, previews and caches live on `D:`.

1. Look first, delete nothing: `Get-PSDrive C, D`.
2. If `C:` is under about 5 GB free, the database may fail to write. Free space on `C:` before using the application:
   downloads, archives and caches that you recognise.
3. Never delete anything under `D:\geopoc-data` (originals), `D:\geopoc-backups` or `D:\geopoc-quarantine` to make room
   without reading section 8 and 9 first.
4. The health document shows free space for the data folder (`storage.free_bytes`). Uploads are refused when less than 1 GB remains.

## 7. Backups

```powershell
.\scripts\backup-db.ps1 -Verify
```

Writes `D:\geopoc-backups\db\<database>-<date>-<time>-<4 hex>.dump`, a `.sha256` file and a `.manifest.json`. It never
overwrites a file, and if the dump fails it leaves no file behind. `-Verify` restores the dump into a temporary database
(`geopoc_verify_<timestamp>`), compares every table with the source, and drops the temporary database. The manifest says
`verified: true` only if every table matched. (`worker_heartbeat` is not compared: a live worker rewrites it every few seconds.)

- Take a verified backup before every migration and every reset. Both refuse without one that is under 24 hours old and still
  matches its recorded SHA-256.
- The password is read from `.env`; it never appears on a command line or in output.
- Other folders: `-OutputDir`. Another database (for example `geopoc_test`): `-Database`.
- **A backup holds the database only.** The uploaded files are under `D:\geopoc-data\originals`; copy that folder too if
  you need a complete copy. The source code is backed up separately (`D:\geopoc-backups\source`).

## 8. Restoring a backup

Never restore over the working database in place. Restore beside it, check, then swap.

```powershell
. .\scripts\lib\common.ps1; . .\scripts\lib\backup.ps1
$s = Get-GeopocSettings
$dump = 'D:\geopoc-backups\db\<the .dump file>'
Get-Content "$dump.manifest.json"                     # check database, date, verified: true
(Get-FileHash $dump -Algorithm SHA256).Hash           # must equal the sha256 in the manifest

# 1. restore into a NEW database
Invoke-Psql -Settings $s -Database postgres -Sql 'create database "geopoc_restored"'
$env:PGPASSWORD = $s.GetDbPassword()
& (Find-PostgresTool pg_restore) -h $s.DbHost -p $s.DbPort -U $s.DbUser -d geopoc_restored --no-password --exit-on-error $dump
Remove-Item Env:\PGPASSWORD

# 2. inspect it (counts and revision)
Invoke-Psql -Settings $s -Database geopoc_restored -Sql 'select version_num from alembic_version'
```

To make the restored copy the working database: stop the application (`.\scripts\stop.ps1`), then rename both databases
(`alter database geopoc rename to geopoc_before_restore`, then `alter database geopoc_restored rename to geopoc`) and
start again. Keep `geopoc_before_restore` until you are sure. The uploaded files must match the restored database: if a
reset moved them, move the folders back from `D:\geopoc-quarantine\<database>-<timestamp>\` (section 9).

## 9. Migrations

Start-up never migrates. When `dev.ps1` says the database is behind the code:

```powershell
.\scripts\backup-db.ps1 -Verify
.\scripts\migrate.ps1                  # dry run: shows the revisions
.\scripts\migrate.ps1 -Apply           # refuses unless a verified backup under 24 hours old exists
```

If a migration fails it is not rolled back automatically: inspect the database before doing anything else, and keep the
verified backup. Scratch databases (`geopoc_*`) do not need a backup.

## 10. Resetting data (dangerous; not routine)

`.\scripts\reset-working-data.ps1` empties every application table and moves the stored files aside. It exists to start a
demonstration from an empty installation. **It is a dry run unless `-Execute` is given:**

```powershell
.\scripts\reset-working-data.ps1                 # prints the database, row counts, and the files that would be moved
```

To erase the **working** database all of these must be true, and the dry run shows which are not:

1. `-Execute -AuthorizeWorkingData` are both given (`-Force` has no effect on the working database);
2. `-Confirmation` is the exact phrase the dry run prints (it names the database and the data folder);
3. a verified backup (section 7) under 24 hours old exists;
4. the API, worker and web app are stopped;
5. the database name matches the application configuration.

Nothing is permanently deleted: `originals`, `previews` and `tmp` are **moved** to
`D:\geopoc-quarantine\<database>-<timestamp>\`, with a `RESET-RECORD.json`. Model weights and every other folder stay.
If emptying the database fails, the folders are moved back. To undo a completed reset: restore the verified backup
(section 8) and move the folders back from the quarantine.

Scratch databases need `-Database geopoc_x -DataRoot <data root>\_<name>` (a direct child of the working data root, for example `D:\geopoc-data\_e2e`), and `-Force` is allowed for them.

## 11. Smoke tests

- `.\scripts\smoke.ps1 -Mode working`: read-only check of the running app (section 3).
- `.\scripts\smoke.ps1`: scratch mode. It recreates `geopoc_smoke`, starts its own API on port 8020 with stub models,
  uploads synthetic photographs, confirms and places them, checks the exports and refusals, then stops that API. It
  refuses to start if the database, folder or port could be the working ones, and the client refuses to write unless the API
  itself reports a scratch database.

## 12. What this runbook does not protect against yet

- **The audit log is not tamper-protected in the database.** The application connects as the database administrator, so a
  trigger alone would not stop a deliberate bypass. A separate least-privilege application role and a protection migration
  are planned (Phase 0, item 5) and need a decision before they are applied. Until then, backups are the protection.
- **No authentication.** Anyone who can reach 127.0.0.1 can use the application. Do not expose it.
- **No automatic backups and no automatic start-up.** Both are manual.

## 13. Quick troubleshooting

| Symptom | Likely cause | Do this |
|---|---|---|
| Page does not load | web app not running | `.\scripts\dev.ps1` |
| Errors "API is not reachable" | API not running | `.\scripts\dev.ps1` |
| `dev.ps1`: Docker engine not running | after a reboot | `.\scripts\dev.ps1 -StartDocker` |
| Analysis never finishes | worker or detector not ready | section 5 |
| `dev.ps1` says database is behind | code is newer than the database | section 9 |
| `dev.ps1` says database is newer | wrong branch checked out | check `git branch`; do not migrate |
| Tests cannot start: DLL blocked | Smart App Control blocks Schemathesis | use `.\scripts\pytest.ps1`; the contract test is skipped with a reason |
