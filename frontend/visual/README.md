# Screenshot-diff harness (pixel-parity gate)

> The baseline is no longer "the pre-migration server-rendered page" — that layer is
> gone. It is the last accepted render of the app against itself, recaptured whenever
> a change is deliberately visual. It was recaptured on 2026-07-31 for the move off
> Vega-Lite: the per-company trends lost a duplicate legend, so every chart page got
> ~20px shorter and the old baseline could never match again.

This is the acceptance gate for the React migration (see
`docs/superpowers/plans/2026-07-22-react-phase0.md` and the "Pixel-parity
strategy" section of the design spec). A route is not "done" migrating
until its screenshot diff against the pre-migration server-rendered page is
~0.

Playwright-driven. Dev-only tooling — lives in `frontend/visual/`, is never
baked into the Docker image, and screenshots are **not committed** (see
`.gitignore`).

## Files

- `routes.mjs` — the route/state manifest: `{ id, path, viewport?, theme?, setup? }`.
  Add one entry per route/state (tab, filter, modal, ...) as more routes migrate.
- `capture.mjs` — Playwright script that visits each route in the manifest and
  saves a full-page screenshot per entry. Used for both baseline and candidate
  captures (same code, different `--base`/`--out`).
- `diff.mjs` — pixelmatch-based comparison of `baseline/<id>.png` vs
  `candidate/<id>.png`, writes a highlighted diff to `diff/<id>.png`, and
  fails (non-zero exit) if any shot exceeds the mismatch threshold.

`baseline/`, `candidate/`, and `diff/` are generated artifacts — gitignored,
not committed.

## Gate workflow (for each route being migrated)

1. **Before migrating**: run the app as it exists on `main` (the current
   server-rendered version):
   ```
   .venv/bin/python reportctl.py serve --port 8080
   ```
   Then, from `frontend/`:
   ```
   npm run visual:baseline
   ```
   This captures every route/state in `routes.mjs` from the running server
   into `visual/baseline/`.

2. **After migrating**: build the React bundle and run the same app (now
   serving the React version of the route) on the same port:
   ```
   npm run build
   .venv/bin/python reportctl.py serve --port 8080
   npm run visual:candidate
   ```
   This captures the same routes/states into `visual/candidate/`.

3. **Gate**:
   ```
   npm run visual:diff
   ```
   Must pass (exit 0, ~0% mismatch per shot) before the route migration is
   considered done. If it fails, inspect `visual/diff/<id>.png` (differing
   pixels are highlighted) and iterate the component until it matches.

## Sanity check (harness self-test)

Running `visual:baseline` and `visual:candidate` against the **same**
unchanged running server should produce ~0 diff — this proves the
capture+diff pipeline itself is deterministic (no flaky timing, fonts,
animations, etc. causing false positives). Do this once whenever the harness
itself changes.

## Scripts (`frontend/package.json`)

- `npm run visual:baseline` — capture into `visual/baseline/`. Base URL via
  `--base <url>` or `BASE_URL` env (default `http://127.0.0.1:8080`).
- `npm run visual:candidate` — capture into `visual/candidate/`. Same base
  URL options.
- `npm run visual:diff` — compare baseline vs candidate, fail on regressions.

## Determinism

`capture.mjs` fixes the viewport, forces `prefers-reduced-motion: reduce`,
disables CSS animations/transitions/caret blinking, and waits for network
idle + web fonts ready + in-flight fetches to drain before screenshotting.
This keeps diffs meaningful — a failure means a real visual difference, not
timing noise. If a future route needs a specific extra wait (e.g. a chart
finishing render — look for `.vl-panel [data-done]` or `svg.marks`), add it
via that route's `setup(page)` in `routes.mjs` rather than editing the
generic wait helper.
