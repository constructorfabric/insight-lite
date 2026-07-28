import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

// Build-output contract (see docs/superpowers/specs/2026-07-22-react-frontend-migration-design.md):
// - Vite output lands in ../assets/app, same-origin, served by server.py under /assets/app/.
// - manifest: true emits ../assets/app/.vite/manifest.json mapping each entry to its hashed
//   js/css files; server.py's spa.py reads it to build <script>/<link> tags.
// - Multi-entry: one src/entries/<name>.tsx per migrated route. Add more keys here as routes
//   are migrated — this file has no index.html input, entries are the inputs directly
//   ("backend integration" mode: https://vite.dev/guide/backend-integration.html).
export default defineConfig({
  base: "/assets/app/",
  plugins: [react()],
  build: {
    outDir: "../assets/app",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: {
        whatsnew: resolve(__dirname, "src/entries/whatsnew.tsx"),
        overview: resolve(__dirname, "src/entries/overview.tsx"),
        trend: resolve(__dirname, "src/entries/trend.tsx"),
        delivery: resolve(__dirname, "src/entries/delivery.tsx"),
        flow: resolve(__dirname, "src/entries/flow.tsx"),
        people: resolve(__dirname, "src/entries/people.tsx"),
        person: resolve(__dirname, "src/entries/person.tsx"),
        repositories: resolve(__dirname, "src/entries/repositories.tsx"),
        elements: resolve(__dirname, "src/entries/elements.tsx"),
        traffic: resolve(__dirname, "src/entries/traffic.tsx"),
        "ai-tools": resolve(__dirname, "src/entries/ai-tools.tsx"),
        dashboard: resolve(__dirname, "src/entries/dashboard.tsx"),
        metrics: resolve(__dirname, "src/entries/metrics.tsx"),
        views: resolve(__dirname, "src/entries/views.tsx"),
        mcp: resolve(__dirname, "src/entries/mcp.tsx"),
        dashboards: resolve(__dirname, "src/entries/dashboards.tsx"),
        datahealth: resolve(__dirname, "src/entries/datahealth.tsx"),
        usage: resolve(__dirname, "src/entries/usage.tsx"),
        calibrate: resolve(__dirname, "src/entries/calibrate.tsx"),
        identity: resolve(__dirname, "src/entries/identity.tsx"),
        config: resolve(__dirname, "src/entries/config.tsx"),
        update: resolve(__dirname, "src/entries/update.tsx"),
        setup: resolve(__dirname, "src/entries/setup.tsx"),
        "semantic-advanced": resolve(__dirname, "src/entries/semantic-advanced.tsx"),
        "semantic-wizard": resolve(__dirname, "src/entries/semantic-wizard.tsx"),
        "dashboard-editor": resolve(__dirname, "src/entries/dashboard-editor.tsx"),
        chatlog: resolve(__dirname, "src/entries/chatlog.tsx"),
        "report-chrome": resolve(__dirname, "src/entries/report-chrome.tsx"),
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8080",
    },
  },
});
