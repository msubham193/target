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
  },
});
