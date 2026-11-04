import { defineConfig } from "@playwright/test";

// UI review walkthrough for the UI/UX foundation phase. Not part of the test suite: it saves screenshots and a
// browser log for a person to look at. Like the end-to-end tests it starts its own stack, on a scratch database
// (geopoc_review) and data subfolder (_review), so the working database is never touched. Unlike them it uses
// the real model manifest, so ground analysis runs the installed detector on this computer.
//   pnpm exec playwright test --config playwright.ui-review.config.ts
const apiPort = 8011;
const webPort = 5175;

const baseEnv = Object.fromEntries(
  Object.entries(process.env).filter((entry): entry is [string, string] => entry[1] !== undefined),
);

export default defineConfig({
  testDir: "./review",
  testMatch: process.env.GEOPOC_REVIEW_SPEC ?? "ui-foundation.spec.ts",
  workers: 1,
  reporter: "list",
  timeout: 600_000,
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    channel: "msedge",
    viewport: { width: 1440, height: 900 },
  },
  webServer: [
    {
      command:
        "uv run --no-sync python -m geopoc.devtools reset-db && " +
        "(start /b uv run --no-sync python -m geopoc.jobs.worker) && " +
        "uv run --no-sync python -m geopoc",
      cwd: "../../services/api",
      env: {
        ...baseEnv,
        GEOPOC_DB_NAME: "geopoc_review",
        GEOPOC_DATA_SUBDIR: "_review",
        GEOPOC_API_PORT: String(apiPort),
        // The scratch data folder has no model weights; read them from where they are installed.
        GEOPOC_WEIGHTS_BASE: process.env.GEOPOC_WEIGHTS_BASE ?? "D:\\geopoc-data",
      },
      url: `http://127.0.0.1:${apiPort}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "pnpm exec vite",
      env: { ...baseEnv, GEOPOC_API_TARGET: `http://127.0.0.1:${apiPort}`, GEOPOC_WEB_PORT: String(webPort) },
      url: `http://127.0.0.1:${webPort}/login`,
      reuseExistingServer: false,
      timeout: 90_000,
    },
  ],
});
