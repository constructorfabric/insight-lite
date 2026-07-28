#!/usr/bin/env node
// Screenshot capture step of the pixel-parity harness.
//
// Usage:
//   node visual/capture.mjs --base <url> --out <dir>
//
// For every route in routes.mjs: navigate to `base + path`, wait for the
// page to settle deterministically (network idle, fonts ready, no pending
// fetches, animations disabled), run the optional `setup(page)` step, then
// take a full-page screenshot into `<out>/<id>.png`.
//
// This script is used for BOTH the "baseline" capture (against the current
// server-rendered site) and the "candidate" capture (against the React
// build) — same code, different `--base`/`--out`. That symmetry is what
// makes the diff meaningful.

import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { routes } from "./routes.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const args = {
    base: process.env.BASE_URL || "http://127.0.0.1:8080",
    out: null,
    // Optional route filter (comma-separated ids) — capture just the route(s)
    // you're iterating on instead of the whole suite. Also settable via
    // ROUTE_ID env. Empty → all routes.
    only: process.env.ROUTE_ID || null,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--base") args.base = argv[++i];
    else if (a === "--out") args.out = argv[++i];
    else if (a === "--only") args.only = argv[++i];
    else if (a.startsWith("--base=")) args.base = a.slice("--base=".length);
    else if (a.startsWith("--out=")) args.out = a.slice("--out=".length);
    else if (a.startsWith("--only=")) args.only = a.slice("--only=".length);
  }
  if (!args.out) {
    throw new Error("--out <dir> is required (e.g. --out visual/baseline)");
  }
  return args;
}

// Deterministic-settle helper: wait for network idle, web fonts to be
// ready, and any in-flight `fetch`/XHR calls to drain. Reusable for
// chart-bearing pages later (e.g. also poll for `.vl-panel [data-done]` or
// `svg.marks` once such routes exist — add route-specific waits via
// `setup()` rather than editing this generic helper).
async function waitForSettled(page) {
  await page.waitForLoadState("networkidle");
  await page.evaluate(async () => {
    if (document.fonts && document.fonts.ready) {
      await document.fonts.ready;
    }
  });
  // Drain any pending fetch()/XHR calls that started after networkidle
  // fired but before we could observe them (best-effort; short budget).
  await page.evaluate(() => {
    return new Promise((resolve) => {
      const pending = window.__pendingFetches;
      if (!pending || pending.size === 0) return resolve();
      const start = Date.now();
      const check = () => {
        if (pending.size === 0 || Date.now() - start > 2000) return resolve();
        setTimeout(check, 50);
      };
      check();
    });
  });
  // Short fixed settle for any rAF-driven layout/paint work.
  await page.waitForTimeout(150);
}

async function installDeterminism(page) {
  // Disable animations/transitions/caret blinking so timing never causes a
  // false-positive diff. Respect prefers-reduced-motion too.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    // Track in-flight fetches so waitForSettled can drain them.
    window.__pendingFetches = new Set();
    const origFetch = window.fetch;
    if (origFetch) {
      window.fetch = function (...args) {
        const token = Symbol("fetch");
        window.__pendingFetches.add(token);
        return origFetch.apply(this, args).finally(() => {
          window.__pendingFetches.delete(token);
        });
      };
    }
    const style = document.createElement("style");
    style.textContent = `
      *, *::before, *::after {
        animation-duration: 0s !important;
        animation-delay: 0s !important;
        transition-duration: 0s !important;
        transition-delay: 0s !important;
        caret-color: transparent !important;
        scroll-behavior: auto !important;
      }
    `;
    document.addEventListener("DOMContentLoaded", () => {
      document.head.appendChild(style);
    });
    // In case DOMContentLoaded already fired by the time this runs.
    if (document.head) document.head.appendChild(style);
  });
}

async function main() {
  const { base, out, only } = parseArgs(process.argv.slice(2));
  const outDir = path.isAbsolute(out) ? out : path.join(process.cwd(), out);
  await mkdir(outDir, { recursive: true });

  const onlyIds = only ? new Set(only.split(",").map((s) => s.trim())) : null;
  const selected = onlyIds ? routes.filter((r) => onlyIds.has(r.id)) : routes;

  const browser = await chromium.launch();
  try {
    for (const route of selected) {
      const viewport = route.viewport || { width: 1440, height: 900 };
      const context = await browser.newContext({
        viewport,
        colorScheme: route.theme || "light",
        reducedMotion: "reduce",
        // Per-route auth headers — the owner-only /dashboard/<id>/edit gate sends
        // the oauth2-proxy username header so its owner check passes (see routes.mjs).
        ...(route.headers ? { extraHTTPHeaders: route.headers } : {}),
      });
      const page = await context.newPage();
      await installDeterminism(page);

      const url = new URL(route.path, base).toString();
      await page.goto(url, { waitUntil: "load" });
      await waitForSettled(page);

      if (typeof route.setup === "function") {
        await route.setup(page);
        await waitForSettled(page);
      }

      // Reset scroll before the shot. A route whose URL carries a hash that
      // matches an in-page id (e.g. `/report#trend` → `<h2 id="trend">`)
      // triggers the browser's native anchor-scroll on load; combined with a
      // `position: sticky` sidebar (see shell.SHELL_CSS's `.sidebar`), a
      // full-page screenshot then freezes the sidebar at that scrolled
      // offset while the rest of the (non-sticky) page renders from the top
      // — a real Playwright/CDP compositing artifact, not a rendering
      // difference between the two front-ends. Scrolling back to (0,0)
      // first makes the shot represent the same "loaded fresh, viewed from
      // the top" state on every route, hash or not.
      await page.evaluate(() => window.scrollTo(0, 0));

      const outFile = path.join(outDir, `${route.id}.png`);
      await page.screenshot({ path: outFile, fullPage: true, animations: "disabled" });
      console.log(`captured ${route.id} -> ${path.relative(process.cwd(), outFile)}`);

      await context.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
