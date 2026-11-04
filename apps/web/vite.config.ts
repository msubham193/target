import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Both overridable so end-to-end tests can run against a separate API and database.
const apiTarget = process.env.GEOPOC_API_TARGET ?? "http://127.0.0.1:8000";
const port = Number(process.env.GEOPOC_WEB_PORT ?? 5173);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Loopback only: the app is never exposed to the LAN.
  server: {
    host: "127.0.0.1",
    port,
    strictPort: true,
    proxy: { "/api": apiTarget, "/health": apiTarget },
  },
  preview: { host: "127.0.0.1" },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    include: ["src/**/*.test.{ts,tsx}"],
    // Each test file runs in its own jsdom worker. By default there is one per core, so on a busy machine (the backend
    // tests, a type check or the end-to-end tests running at the same time) the workers starve each other: waits that
    // take 100 ms on a quiet machine pass 1 s, and tests fail at random. Measured on this 16-thread machine with every
    // core kept busy: one worker per core failed 4 runs of 4; four workers passed 3 of 3, at the default timeouts. On a
    // quiet machine four workers cost about 2 s (10-13 s against 14-15 s). The timeouts are deliberately left at their
    // defaults: a real regression should still fail quickly.
    maxWorkers: 4,
  },
});
