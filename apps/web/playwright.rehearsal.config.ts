import { defineConfig } from "@playwright/test";

// Deployment rehearsal (G3). Drives an application that is ALREADY running against the restored copy of the working
// database (geopoc_rehearsal), never the working app. Start it by hand on other ports (see docs/integration plan), then:
//   $env:GEOPOC_REHEARSAL_URL = "http://127.0.0.1:5175"
//   pnpm exec playwright test --config playwright.rehearsal.config.ts
// Every spec first asks the app which database it is connected to and refuses to run unless it is geopoc_rehearsal.
export default defineConfig({
  testDir: "./rehearsal",
  workers: 1,
  fullyParallel: false,
  reporter: "list",
  timeout: 300_000,
  use: {
    baseURL: process.env.GEOPOC_REHEARSAL_URL ?? "http://127.0.0.1:5175",
    channel: "msedge",
    viewport: { width: 1366, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
