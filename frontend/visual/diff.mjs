#!/usr/bin/env node
// Diff step of the pixel-parity harness.
//
// Usage:
//   node visual/diff.mjs [--baseline <dir>] [--candidate <dir>] [--out <dir>] [--threshold <pct>]
//
// Compares visual/baseline/<id>.png vs visual/candidate/<id>.png (one pair
// per entry in routes.mjs) with pixelmatch, writes a highlighted diff PNG to
// visual/diff/<id>.png, prints a per-shot mismatched-pixel count/percentage,
// and exits non-zero if any shot exceeds the threshold (default 0.1% of
// pixels) — that's the pixel-parity gate. A baseline/candidate size mismatch
// is a hard fail (can't meaningfully diff differently-sized images).

import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { routes } from "./routes.mjs";

const DEFAULT_THRESHOLD_PCT = 0.1; // percent of total pixels

function parseArgs(argv) {
  const args = {
    baseline: "visual/baseline",
    candidate: "visual/candidate",
    out: "visual/diff",
    threshold: DEFAULT_THRESHOLD_PCT,
    // Optional route filter (comma-separated ids), matching capture.mjs's
    // --only. Also settable via ROUTE_ID env. Empty → all routes.
    only: process.env.ROUTE_ID || null,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--baseline") args.baseline = argv[++i];
    else if (a === "--candidate") args.candidate = argv[++i];
    else if (a === "--out") args.out = argv[++i];
    else if (a === "--threshold") args.threshold = Number(argv[++i]);
    else if (a === "--only") args.only = argv[++i];
    else if (a.startsWith("--baseline=")) args.baseline = a.slice(11);
    else if (a.startsWith("--candidate=")) args.candidate = a.slice(12);
    else if (a.startsWith("--out=")) args.out = a.slice(6);
    else if (a.startsWith("--threshold=")) args.threshold = Number(a.slice(12));
    else if (a.startsWith("--only=")) args.only = a.slice(7);
  }
  return args;
}

async function loadPng(filePath) {
  const buf = await readFile(filePath);
  return PNG.sync.read(buf);
}

async function main() {
  const { baseline, candidate, out, threshold, only } = parseArgs(process.argv.slice(2));
  const baselineDir = path.isAbsolute(baseline) ? baseline : path.join(process.cwd(), baseline);
  const candidateDir = path.isAbsolute(candidate) ? candidate : path.join(process.cwd(), candidate);
  const outDir = path.isAbsolute(out) ? out : path.join(process.cwd(), out);
  await mkdir(outDir, { recursive: true });

  let anyFailed = false;
  const summary = [];

  const onlyIds = only ? new Set(only.split(",").map((s) => s.trim())) : null;
  const selected = onlyIds ? routes.filter((r) => onlyIds.has(r.id)) : routes;

  for (const route of selected) {
    const baselineFile = path.join(baselineDir, `${route.id}.png`);
    const candidateFile = path.join(candidateDir, `${route.id}.png`);

    let baseImg, candImg;
    try {
      baseImg = await loadPng(baselineFile);
    } catch (err) {
      console.error(`[FAIL] ${route.id}: cannot read baseline ${baselineFile}: ${err.message}`);
      anyFailed = true;
      continue;
    }
    try {
      candImg = await loadPng(candidateFile);
    } catch (err) {
      console.error(`[FAIL] ${route.id}: cannot read candidate ${candidateFile}: ${err.message}`);
      anyFailed = true;
      continue;
    }

    if (baseImg.width !== candImg.width || baseImg.height !== candImg.height) {
      console.error(
        `[FAIL] ${route.id}: size mismatch — baseline ${baseImg.width}x${baseImg.height} ` +
          `vs candidate ${candImg.width}x${candImg.height}`
      );
      anyFailed = true;
      summary.push({ id: route.id, mismatch: null, pct: null, sizeMismatch: true });
      continue;
    }

    const { width, height } = baseImg;
    const diffImg = new PNG({ width, height });
    const mismatchedPixels = pixelmatch(baseImg.data, candImg.data, diffImg.data, width, height, {
      threshold: 0.1, // pixelmatch's own per-pixel color-delta sensitivity
    });

    const diffFile = path.join(outDir, `${route.id}.png`);
    await writeFile(diffFile, PNG.sync.write(diffImg));

    const totalPixels = width * height;
    const pct = (mismatchedPixels / totalPixels) * 100;
    // Per-route threshold override: chart-dense states (many Vega canvas text
    // edges / dense stacked bands) drift ~1px in Vega's own text metrics between
    // the monolith and an isolated page — invisible to a human but over the
    // strict default. Those states set `threshold` in routes.mjs; everything
    // else stays on the strict default. Always visually spot-check the diff PNG.
    const routeThreshold = route.threshold != null ? route.threshold : threshold;
    const failed = pct > routeThreshold;
    if (failed) anyFailed = true;

    summary.push({ id: route.id, mismatch: mismatchedPixels, pct, sizeMismatch: false });

    const status = failed ? "FAIL" : "OK";
    console.log(
      `[${status}] ${route.id}: ${mismatchedPixels} / ${totalPixels} px mismatched ` +
        `(${pct.toFixed(4)}%, threshold ${routeThreshold}%) -> ${path.relative(process.cwd(), diffFile)}`
    );
  }

  console.log("");
  console.log("Pixel-parity summary:");
  for (const s of summary) {
    if (s.sizeMismatch) {
      console.log(`  ${s.id}: SIZE MISMATCH`);
    } else {
      console.log(`  ${s.id}: ${s.mismatch} px (${s.pct.toFixed(4)}%)`);
    }
  }

  if (anyFailed) {
    console.error("\nPixel-parity gate FAILED.");
    process.exit(1);
  } else {
    console.log("\nPixel-parity gate passed.");
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
