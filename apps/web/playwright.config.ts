import { defineConfig } from "@playwright/test";

// End-to-end tests start their own API (scratch database geopoc_e2e) and web server on separate ports,
// so they never touch the working database. The installed Microsoft Edge is used: no browser download.
const apiPort = 8010;
const webPort = 5174;

const baseEnv = Object.fromEntries(
  Object.entries(process.env).filter((entry): entry is [string, string] => entry[1] !== undefined),
);

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  reporter: "list",
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    channel: "msedge",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // Resets the scratch database, starts the background worker, then serves the API. Playwright ends the
      // whole process tree afterwards, worker included.
      command:
        "uv run --no-sync python -m geopoc.devtools reset-db && " +
        "(start /b uv run --no-sync python -m geopoc.jobs.worker) && " +
        "uv run --no-sync python -m geopoc",
      cwd: "../../services/api",
      env: {
        ...baseEnv,
        GEOPOC_DB_NAME: "geopoc_e2e",
        GEOPOC_DATA_SUBDIR: "_e2e",
        GEOPOC_API_PORT: String(apiPort),
        // Analysis in the browser tests uses the test-only stub detector, so they need no GPU or weights.
        GEOPOC_ALLOW_STUB_MODELS: "true",
        GEOPOC_MANIFEST_PATH: "tests/fixtures/manifest.stub.yaml",
        GEOPOC_JOB_POLL_INTERVAL_S: "0.2",
      },
      url: `http://127.0.0.1:${apiPort}/health`,
      reuseExistingServer: false,
      timeout: 90_000,
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
