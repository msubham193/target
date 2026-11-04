import { defineConfig } from "@playwright/test";

// Manual UI review aid, not part of the test suite. It drives the application that is ALREADY running
// (start it with scripts\dev.ps1) and saves screenshots for a person to look at:
//   pnpm exec playwright test --config playwright.review.config.ts
export default defineConfig({
  testDir: "./review",
  workers: 1,
  reporter: "list",
  timeout: 300_000,
  use: {
    baseURL: process.env.GEOPOC_REVIEW_URL ?? "http://127.0.0.1:5173",
    channel: "msedge",
    viewport: { width: 1366, height: 800 },
  },
});
