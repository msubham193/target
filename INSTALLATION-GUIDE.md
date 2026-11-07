# INT and TGT Development – Installation and User Guide

This guide installs the application on one Windows computer and gets you to a working first project.
Plan about **1 hour** for the first installation, mostly download time.

The application runs **on your own computer**. The web page you use is served from this computer
(`http://127.0.0.1:5173`). Nothing is reachable from other computers on your network.

---

## 1. What you received

| Item | What it is |
|---|---|
| `INT-TGT-Development-<date>.zip` | The application (this guide is inside it too) |
| `samples\` | One photograph and one short video to try the application with |
| `INT-TGT-demo-walkthrough.mp4` | A recorded tour of every screen |
| `CHECKSUMS.sha256.txt` | Fingerprints to confirm the files arrived intact (optional, section 3) |

No passwords or access keys are included. Setup creates a new database password for your computer, and you can add
your own map key (section 8.1).

## 2. What the computer needs

| | Minimum | Recommended |
|---|---|---|
| Operating system | Windows 10 or 11, 64-bit | Windows 11 |
| Memory (RAM) | 8 GB | 16 GB or more (needed for the AI draft summary) |
| Free disk space | 25 GB | 40 GB or more, preferably on a second disk `D:` |
| Graphics card | none (analysis runs on the processor, more slowly) | NVIDIA graphics card (analysis is much faster) |
| Internet | needed **during installation** | also for the optional online satellite map |
| Rights | an account that can install programs (administrator) | |

> **Windows Smart App Control.** If it is switched on, Windows may block some of the application's components. If setup
> reports a component as "blocked", contact your IT administrator. Do not try to work around it.

## 3. Step 1 – Install the required programs

Install these free programs **once**. The quickest way is to open **PowerShell** (Start menu → type *PowerShell*) and run:

```powershell
winget install -e --id Docker.DockerDesktop
winget install -e --id astral-sh.uv
winget install -e --id OpenJS.NodeJS.LTS
winget install -e --id Gyan.FFmpeg
winget install -e --id Ollama.Ollama        # optional: only for the AI draft summary
```

Then **close PowerShell, open a new one** and run:

```powershell
npm install -g pnpm
```

If `winget` is not available, download each program from its website instead:

| Program | Why it is needed | Download |
|---|---|---|
| Docker Desktop | runs the database | <https://www.docker.com/products/docker-desktop/> |
| uv | installs the analysis engine (Python) | <https://docs.astral.sh/uv/getting-started/installation/> |
| Node.js (LTS) | runs the web interface | <https://nodejs.org/> |
| pnpm | installs the web interface's packages | `npm install -g pnpm` after Node.js |
| FFmpeg | reads video files | <https://www.gyan.dev/ffmpeg/builds/> (add its `bin` folder to PATH) |
| Ollama *(optional)* | runs the local AI that writes draft report summaries | <https://ollama.com/download> |

**Start Docker Desktop once** after installing it. Accept its terms and, if asked, let it install or update **WSL 2**
and restart the computer. Wait until Docker Desktop says it is **running**. Signing in to a Docker account is not needed.

*Optional check that the files arrived intact.* In PowerShell, in the folder you received:

```powershell
Get-FileHash .\INT-TGT-Development-*.zip -Algorithm SHA256
```

Compare the result with the line for the zip file in `CHECKSUMS.sha256.txt`. If they differ, ask for the file again.

## 4. Step 2 – Unpack the application

1. Right-click the zip file → **Extract All…** and extract it to a short folder **without spaces**, for example
   `C:\INT-TGT`.
2. Open that folder. You should see `SETUP.cmd`, `START.cmd`, `STOP.cmd` and this guide.

## 5. Step 3 – Run the setup (once)

Double-click **`SETUP.cmd`**. A window opens and works through these steps. Every step says **OK** when it is done.

1. **Checks the programs** from Step 1. If one is missing, it shows the command to install it and stops. Install it,
   close the window, and double-click `SETUP.cmd` again.
2. **Prepares storage on drive `D:`.** If the computer has no second disk `D:`, setup uses the folder
   `C:\INT-TGT-storage` and makes it appear as `D:`. `START.cmd` does this again after every restart.
3. **Creates the settings file** (`.env`) with a new random database password. Keep this file private.
4. **Asks for a Mapbox public token (optional).** Press **Enter** to skip. Section 8.1 explains what it adds.
5. **Installs the analysis engine and the web interface.** This is the long step, often 10–30 minutes, because about
   4 GB is downloaded.
6. **Downloads the detection model** and checks its fingerprint.
7. **Starts the database** and creates its tables.
8. **Offers to download the AI summary model** (about 6 GB, only if Ollama is installed). Type `y` and Enter, or just
   Enter to skip.

At the end the window shows **SETUP COMPLETE**. If it shows **SETUP STOPPED**, read the message, fix the problem
(section 10 helps) and run `SETUP.cmd` again. Finished steps are skipped.

## 6. Step 4 – Start the application

1. Make sure **Docker Desktop** is running. `START.cmd` also tries to start it.
2. Double-click **`START.cmd`**.
3. Three extra windows open: **API**, **worker** and **web**. **Leave them open** while you use the application;
   you can minimise them.
4. When everything is ready, a summary table shows **healthy**, and your browser opens **<http://127.0.0.1:5173>**.

The first start after a restart takes a few minutes. The detector shows *warming-up* for about 2–3 minutes before it
is ready.

> The start window may show a yellow line: *"The database is at revision 0009 but the code expects 0010."* This is
> **expected**. Version 0009 is the tested version for this release. The one feature that would need 0010 is switched
> off, so do not run any migration.

**To stop:** double-click **`STOP.cmd`**. Your projects and files are kept.

## 7. Step 5 – Your first project (about 10 minutes)

1. **Sign in.** Click **Fill in the demo details**, then **Sign in**.
   *This sign-in is a placeholder for demonstrations and does not protect anything. See section 9.*
2. **Choose a profile.** Type your name and click **Create profile and continue**. Every review and change is recorded
   under this name.
3. **Create a project.** Click **New Project**, give it a name (you can search for a place to set its area) and click
   **Create project**.
4. **Add the sample files.** Open **Add Files**:
   - On the **Photographs** tab, click **Choose files** and pick the photograph from the `samples` folder.
   - On the **Video** tab, pick the video from the `samples` folder.

   Each file is checked and a preview is made in the background.
5. **Analyse the photograph.** Open **Photo Analysis**, choose the photograph, open **Run another analysis** (or
   **Analyse this photograph**), choose the model and click **Run analysis**. The marked objects appear when the
   job finishes: under a minute with an NVIDIA card, a few minutes without one.
6. **Review.** Click a box or a row to see it. Use **Box**, **Point** or **Outline** to draw your own marks with a label.
7. **Confirm where the photo was taken.** On the **Photo location** tab, confirm the position. The photo then appears on
   the **Map** and in **3D Terrain**.
8. **Analyse the video.** Open **Videos**, choose the video and click **Analyse these frames**.
9. **Review the findings** on the **Findings** screen. **Make a report** on the **Reports** screen: open the findings
   report, print it, or download CSV, GeoJSON or JSON.

The file `INT-TGT-demo-walkthrough.mp4` shows each of these screens.

### What each screen is for

| Screen | What you can do |
|---|---|
| Project Dashboard | See all projects and recent activity |
| New Project | Name a project and, optionally, set its area by searching a place |
| Add Files | Upload photographs, GeoTIFF satellite or aerial images, and videos. Originals are kept unchanged |
| Map | See located photographs and images on a map. Switch between the offline plain map and the online satellite map, measure distances and areas, pin points, add notes |
| Photos | Work through photographs in order: inspect → confirm location → review findings → map → compare |
| Photo Analysis | Run the detector, review each finding, draw your own marks |
| Satellite Images | View GeoTIFF satellite or aerial images. For a georeferenced image, see its centre and click any point to read its latitude and longitude. **Find a location**: type a place name or coordinates (for example `20.2961, 85.8245` or `20°17'46"N 85°49'28"E`) to see that spot on the online satellite map, with your georeferenced images drawn in place. Photos with a confirmed location show as green markers; open one to see its objects and a line from the camera towards each one. Match 4 or more ground spots between the photo and the map to place every object on the satellite map (with an accuracy figure), then download the positions as GeoJSON or save them as map notes |
| Videos | Analyse sampled frames of a video like photographs |
| 3D Terrain | See the location in 3D (online, needs a Mapbox token), or draw your own elevation files in 3D (offline) |
| Findings | Every finding with its evidence and review status. Filter and review here |
| Reports | The findings report (printable), the AI draft summary, and CSV, GeoJSON and JSON exports |
| Settings | Models (including uploading your own, see 8.4), coordinate systems, data sources, network use, diagnostics |

**Important:** detections are **model predictions**. The scores are not probabilities, and every finding needs a person
to review it. The application describes what was detected; it does not tell you what anything means or what to do.

## 8. Optional features

### 8.1 Online satellite map, 3D view and place search (Mapbox)

Without this the application works fully offline with a plain map. To add satellite imagery, the online 3D terrain view
and place-name search:

1. Create a free account at <https://www.mapbox.com/> and copy your **default public token**. It starts with `pk.`.
   Never use a secret token, which starts with `sk.`.
2. Create the file `apps\web\.env.local` in the application folder with this one line:
   ```
   VITE_MAPBOX_ACCESS_TOKEN=pk.your-token-here
   ```
   Or run `SETUP.cmd` again, which asks for it if the file does not exist yet.
3. Double-click **STOP.cmd**, then **START.cmd**.

When it is on, the map area you view and the place names you type are sent to Mapbox. Mapbox's free allowance is
generous for normal use. See their pricing page for details.

### 8.2 AI draft summary in the findings report (local)

The report can include a short, plain-language draft summary written by a language model that runs **on this
computer** (Qwen 2.5 VL through Ollama). Nothing is sent off the computer, and the draft is never saved.

1. Install Ollama (Step 1) and run in PowerShell: `ollama pull qwen2.5vl` (about 6 GB).
2. Open the file `.env` in the application folder with Notepad. If there is a line `GEOPOC_LLM_ENABLED=false`, change
   it to `GEOPOC_LLM_ENABLED=true`.
3. Restart with **STOP.cmd** then **START.cmd**.

Writing a summary is quicker with an NVIDIA card. Without one it can take several minutes. 16 GB of memory is
recommended.

### 8.3 Online photo detection (Roboflow) – off by default

An optional online detector is available, but it **uploads each analysed photograph to Roboflow**. Turn it on only if
your organisation allows that. It needs a Roboflow account; contact your supplier for the settings.

### 8.4 Use your own detection model

You can add an object-detection model that you trained yourself. It runs **on this computer**, and nothing is sent
anywhere.

1. Open **Settings → Models**. Under **Add your own model**, choose the model file. Three kinds are accepted:

   | File | What it is | Class names |
   |---|---|---|
   | `.onnx` | A YOLO model (v5, v8, v11, v10 or 26) exported to ONNX: `yolo export model=best.pt format=onnx` | Read from the file |
   | `.pt` | A YOLO model as trained with Ultralytics (`best.pt`) | Read from the file |
   | `.pth` | A Faster R-CNN model trained with torchvision (`torch.save(model.state_dict(), "model.pth")`) | Type them, one per line, in training order, without the background class |

2. Give it a short **Name** (for example `vehicle-detector`) and a **Version**. Optionally set the **Minimum score**:
   findings scoring under it are not kept.
3. Click **Add model**. The application opens the model and runs it once on a blank picture to check it works. This
   can take a minute. A model that cannot be opened is refused, and nothing is kept.
4. Open a photograph in **Photo Analysis** (or a video in **Videos**), choose your model under **Model**, and run the
   analysis. The findings appear like those of the built-in model, and need the same review.

**ONNX is the recommended format.** A `.pt` file can run hidden code when it is opened, so the application only accepts
one after you tick **I trust the source of this file**. Only upload `.pt` files from people you trust; otherwise export
them to ONNX first.

To remove a model, click **Remove** next to it in **Settings → Models**. Findings it already produced are kept.

Uploaded models are stored in `D:\geopoc-data\models\custom` (or `C:\INT-TGT-storage\geopoc-data\models\custom`).

## 9. Security and privacy

- **Everything runs locally.** The application listens only on `127.0.0.1`, so other computers cannot reach it. Do not
  change this, and do not expose its ports (5173, 8000, 5433).
- **The demo sign-in protects nothing.** Anyone who can use this computer can use the application and see its data.
  Use Windows accounts and disk encryption (BitLocker) to protect the computer. A real login is part of the server
  version.
- **What goes online:** only the optional Mapbox map, 3D view and place search (8.1) and the optional Roboflow detector
  (8.3). Installation downloads software once.
- **Your files** are stored unchanged in `D:\geopoc-data` (or `C:\INT-TGT-storage\geopoc-data`). Back up this folder.
- **The `.env` file holds the database password.** Do not share it or send it by email.

## 10. Troubleshooting

| What you see | What to do |
|---|---|
| `MISSING` next to a program in setup | Install it (Step 1), **close the window**, run `SETUP.cmd` again |
| `'winget' is not recognized` | Use the download links in Step 1 instead |
| `The Docker engine is not running` | Open Docker Desktop, wait until it says *running*, run `START.cmd` again |
| Docker Desktop asks for WSL 2 or shows a WSL error | Accept its update, restart Windows, open Docker Desktop again |
| `Drive letter D: is used by a CD/DVD, USB or network drive` | Free the letter `D:` in *Disk Management* (or remove the USB drive), then run setup again |
| Setup stops while installing packages | Check the internet connection and run `SETUP.cmd` again; it continues where it stopped |
| A message containing **blocked** | Windows Smart App Control or antivirus blocked a component: contact your IT administrator |
| The browser says the page cannot be reached | Wait a minute after `START.cmd`. Check the **web** window is open, then refresh |
| Port 5173, 8000 or 5433 is already in use | Another program uses it. Close that program and run `START.cmd` again |
| Detector shows *warming-up* | Normal for 2–3 minutes after starting |
| Analysis stays *queued* | The **worker** window was closed. Run `STOP.cmd`, then `START.cmd` |
| No satellite map, the 3D view says it needs a token | Add a Mapbox token (8.1) |
| Draft summary says the model is not available | Install Ollama and pull the model (8.2), check `GEOPOC_LLM_ENABLED=true`, restart |
| Database login fails after changing `.env` | Put back the original `GEOPOC_DB_PASSWORD`. The database keeps the password it was created with |
| After a Windows restart, the files on `D:` are missing | Start with `START.cmd`. It re-maps `C:\INT-TGT-storage` to `D:` |

A health check, from PowerShell in the application folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\smoke.ps1 -Mode working
```

*Expected result:* every line says **PASS**. If the optional online detector (8.3) is switched on, the check
**"online access is off"** fails; that is expected in that case.

When you report a problem, send a screenshot of the window with the message. **Never send the `.env` file.**

## 11. Uninstall

1. Double-click `STOP.cmd`.
2. In PowerShell: `docker rm -f geopoc-db` then `docker volume rm geopoc_geopoc-pgdata`. **This deletes the database.**
3. Delete the application folder, `D:\geopoc-data`, `D:\geopoc-cache` and `D:\geopoc-backups`
   (or `C:\INT-TGT-storage`).
4. Uninstall the programs from Step 1 if nothing else uses them.
