# React migration — Phase 0 (harness + pilot + image deploy) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.
> Steps use `- [ ]`. Branch `feat/react-migration`. Design spec:
> `docs/superpowers/specs/2026-07-22-react-frontend-migration-design.md`.

**Goal:** Stand up the React toolchain and prove the pattern end-to-end on ONE low-risk
route, with a screenshot-diff gate guaranteeing pixel-parity, and switch deployment to a
locally-built, ready-shipped Docker image. After Phase 0 we can migrate any route by
repeating the pilot pattern.

**Architecture (from the spec):** Python-only client-render MPA. Python serves the shell
(existing Jinja+CSS chrome, unchanged) + `<div id="root">` + the route's Vite-built
bundle + JSON APIs. React renders the content client-side. Build baked same-origin. No
Node in prod. SSR-safe React (no top-level `window`; data via fetch).

**Pilot route:** `/whats-new` (the changelog page — small, mostly static, low risk).

**Not in Phase 0:** the legacy hash-redirect shim (only needed once the report `/`
monolith is split — a later phase); any report/dashboard/manage route beyond the pilot.

---

## Task P0-T1: `frontend/` scaffold (Vite + React + TS)

**Files:** Create `frontend/` (package.json, vite.config.ts, tsconfig.json, index.html
per entry, src/…); Create `frontend/src/styles/` (verbatim CSS extraction).

- [ ] **Step 1 — scaffold** a minimal Vite + React + TypeScript project under `frontend/`.
  No CRA. `package.json` with `vite`, `react`, `react-dom`, `typescript`, `@vitejs/plugin-react`.
  Pin versions. `.nvmrc`/engines node 20.
- [ ] **Step 2 — build output contract.** Configure `vite.config.ts`:
  - `build.outDir = "../assets/app"`, `build.emptyOutDir = true`, `build.manifest = true`
    (emits `assets/app/.vite/manifest.json` mapping entry → hashed js/css).
  - **Multi-entry**: `build.rollupOptions.input = { whatsnew: "src/entries/whatsnew.tsx", … }`
    — one entry per migrated route (start with just `whatsnew`).
  - `base = "/assets/app/"` so hashed asset URLs resolve to the Python-served path.
  - Dev: `server.proxy` maps `/api` → `http://127.0.0.1:8080` for local iteration.
- [ ] **Step 3 — CSS verbatim.** Extract the existing CSS **unchanged** into
  `frontend/src/styles/`: `base.css` (from `shell.BASE_CSS`), `shell.css` (from
  `shell.SHELL_CSS`), `chart.css` (from `shell.CHART_CSS`), and page CSS as needed. Import
  them from the entry so the bundle carries identical styles. (Keep the Python `shell.*`
  strings too during transition — the shell is still server-rendered; these CSS files are
  the SAME text, single source to reconcile later. Add a note/TODO to dedupe post-migration.)
- [ ] **Step 4 — a trivial entry** `src/entries/whatsnew.tsx` that mounts `<App/>` into
  `#root` and renders "hello" (placeholder; real content in P0-T4). `npm ci && npm run build`
  → confirm `assets/app/` gets hashed `whatsnew.[hash].js` + a manifest. `npm run dev` boots.
- [ ] **Step 5 — commit** `git add frontend .gitignore assets/app/.gitkeep && git commit -m "react: frontend/ scaffold (Vite + React + TS), build → assets/app with manifest"`
  (gitignore `frontend/node_modules`; decide whether to commit `assets/app` build output —
  NO: it's baked by the Docker build stage, so gitignore `assets/app/*` except a `.gitkeep`).

---

## Task P0-T2: server mount bridge + hashed-asset serving

**Files:** Modify `server.py` (serve `/assets/app/*`, read the Vite manifest), `render.py`
or a new `spa.py` (a helper that builds the shell + root + correct script/css tags for an
entry); Test: `tests/`.

- [ ] **Step 1 — serve built assets.** In `server.py`, add a route for
  `path.startswith("/assets/app/")` serving files from `ROOT/assets/app/` with correct
  mime types (`.js`→application/javascript, `.css`→text/css) + immutable Cache-Control
  (filenames are content-hashed). Reject traversal (resolve under the dir, 404 otherwise).
- [ ] **Step 2 — manifest reader.** Add a small helper (e.g. `spa.py`
  `entry_assets(name)`) that reads `assets/app/.vite/manifest.json` and returns the hashed
  `{js, css[]}` for an entry. Memoise; tolerate a missing manifest (dev) by returning a
  dev-server script tag or empty (guarded).
- [ ] **Step 3 — mount helper.** Add `render.render_spa_page(entry, active, title)` (or in
  spa.py): returns the FULL page = the existing shell (`shell.sidebar_html(active)` +
  `shell.BASE_CSS`/`SHELL_CSS`/`CHART_CSS` in `<head>` exactly as manage pages do now) with
  `<main class="wrap"><div id="root"></div></main>` and, before `</body>`, the entry's
  `<link rel=stylesheet>` + `<script type=module src=…>` from the manifest. The chrome is
  byte-identical to today's shelled pages; only the content area is the React root.
- [ ] **Step 4 — test.** Unit test: `entry_assets("whatsnew")` returns hashed names from a
  fixture manifest; `render_spa_page` output contains the sidebar, `#root`, and the hashed
  script tag; the asset route serves a file 200 + js mime and 404s a traversal.
  Full suite green; `import server, render, spa` clean.
- [ ] **Step 5 — commit** `git add server.py render.py spa.py tests && git commit -m "react: serve hashed /assets/app + server mount bridge (shell + #root + manifest tags)"`

---

## Task P0-T3: screenshot-diff harness (the parity gate)

**Files:** Create `frontend/visual/` (or `tests/visual/`): a Playwright runner + config;
add `npm` scripts. This is tooling, not shipped in the image.

- [ ] **Step 1 — Playwright setup** in `frontend/` (devDependency). A script `visual/capture.ts`
  that, given a base URL + a route/state list, screenshots each into a target dir.
- [ ] **Step 2 — route/state manifest** `visual/routes.ts`: initially just `/whats-new`
  (single state). Structure it to grow (route, url, setup steps for filters/modals,
  viewport, theme).
- [ ] **Step 3 — baseline + candidate + diff.** Scripts:
  - `visual:baseline` — capture from the CURRENT server-rendered site (a running instance
    on `main`), save under `visual/baseline/`.
  - `visual:candidate` — capture from the React build instance, save under `visual/candidate/`.
  - `visual:diff` — pixel-diff (pixelmatch/odiff) baseline vs candidate per shot, write diffs
    to `visual/diff/`, **fail** if any shot exceeds a small threshold. Print a summary.
- [ ] **Step 4 — document** the gate in `visual/README.md`: how to capture baseline before a
  route migration and run the diff as the acceptance gate. Run it once against the current
  `/whats-new` (baseline vs itself) → expect 0 diff (sanity of the harness).
- [ ] **Step 5 — commit** `git add frontend/visual frontend/package.json && git commit -m "react: Playwright screenshot-diff harness (parity gate)"`

---

## Task P0-T4: migrate the pilot route `/whats-new` to React

**Files:** Modify `server.py` (`/whats-new` serves `render_spa_page("whatsnew", "whats-new")`;
add `GET /api/whats-new` returning the changelog as JSON); Modify `changelog.py` (expose the
data as a structure, not only HTML); Create `frontend/src/entries/whatsnew.tsx` +
`src/pages/WhatsNew.tsx`; Test: `tests/`.

- [ ] **Step 1 — read the current page.** Inspect how `/whats-new` renders today
  (`changelog.render_page()` + its markup/classes). The React output MUST reproduce that
  DOM + classes exactly.
- [ ] **Step 2 — JSON endpoint.** `GET /api/whats-new` → `{ok:true, releases:[{date, changes:[{type,title,detail}]}]}`
  from `changelog.CHANGELOG` (refactor `changelog.py` so the data is a function returning the
  list; `render_page` keeps working for the transition/fallback). Unit-test the endpoint.
- [ ] **Step 3 — React page.** `WhatsNew.tsx` fetches `/api/whats-new` and renders the
  **identical markup + classes** the Jinja page produced (pills by `type`, dates, titles,
  details). `whatsnew.tsx` mounts it into `#root`. Keep it SSR-safe (no top-level window).
- [ ] **Step 4 — cut over + gate.** Point `/whats-new` at `render_spa_page`. Build. Run the
  screenshot harness: `visual:baseline` (from `main`'s `/whats-new`) then `visual:candidate`
  (React) then `visual:diff` → **must be ~0**. Iterate the component until the diff passes.
  Paste the diff summary. Full Python suite green.
- [ ] **Step 5 — commit** `git add -A && git commit -m "react: migrate /whats-new to React (pilot) — pixel-diff clean"`

---

## Task P0-T5: image-based deploy (build locally, ship ready) + persistent state

**Files:** Modify `Dockerfile` (multi-stage), `docker-compose.yml` (image ref + volumes),
`deploy.sh` (local build + save/load + DB backup + up), `.dockerignore`, and the Python
state-path resolution (`reportctl.py`/`store.py`/`server.py` — introduce `DATA_DIR`).
Verify locally (cannot fully verify prod).

> **Why this task is bigger than "just build an image":** compose currently bind-mounts the
> WHOLE repo (`- .:/work`), so code+data live on the host and the image is barely used. An
> image-based deploy needs the opposite — **code + built assets come from the image; ONLY
> state lives on a persistent volume.** If we ship a baked image but keep `- .:/work`, the
> host dir shadows the baked code/assets. So this task restructures the volume model.

- [ ] **Step 1 — centralise state under `DATA_DIR`.** Introduce a `DATA_DIR` env
  (container default `/work/data`; local-dev default `.` for backward-compat). Route ALL
  runtime state through it: `report.db` (+ `-wal`/`-shm`), generated `report.html` /
  `data.json`, caches (`.cache`/`.repos`/`.runtime`), `clones`, `history/` (backups),
  `exports/`, and `people.yaml` (server-owned). Grep every hardcoded path in
  `reportctl.py`/`store.py`/`server.py`/`mcp_server.py`/`deploy.sh` and resolve it under
  `DATA_DIR`. `.env` stays a separate secret bind-mount. Keep local `.venv`/tests working
  (DATA_DIR defaults to repo root locally). Add/adjust tests for path resolution.
- [ ] **Step 2 — multi-stage Dockerfile (stateless image).** First stage
  `FROM node:20-slim AS frontend`: `COPY frontend/ …; RUN npm ci && npm run build` (emits
  `assets/app/`). Python stage: `COPY --from=frontend … /work/assets/app` + `COPY . .` for
  code/templates. Result: one image = Python + baked React + baked Vega, no Node at runtime,
  **no state baked in**. `.dockerignore` must exclude ALL state (report.db*, .cache, .repos,
  .runtime, clones, history, exports, data.json, report.html, people.yaml, .env, frontend/
  node_modules, assets/app if built outside) so `COPY . .` bakes only code.
- [ ] **Step 3 — compose: image + persistent volume.** Tag the built image
  `insight-report:latest`; `report` + `mcp` reference `image: insight-report:latest`
  (drop `build:`/`- .:/work` for prod). Mount **one persistent volume** at `DATA_DIR`
  (named volume or host bind, e.g. `- report-data:/work/data`) shared by `report` + `mcp`,
  plus the `.env` secret bind-mount. A `docker-compose.override.yml` keeps LOCAL dev on
  `build:` + `- .:/work` so nothing changes for local iteration. Data survives image swaps
  because it lives only in the volume, never in the image.
- [ ] **Step 4 — deploy.sh rework.** New flow:
  1. `docker build -t insight-report:latest .` **locally** (runs the frontend build stage).
  2. `docker save insight-report:latest | gzip | ssh "$HOST" 'gunzip | docker load'`.
  3. On the server: **backup report.db** (keep the existing WAL-checkpoint + copy, last 10),
     then `docker compose up -d` (recreates containers from the loaded image; volumes persist).
  4. Health check (`/` → 200) as today.
  Keep `--refresh`/`--identity`/`--pull-identity` behaviours where still relevant (identity
  is server-owned; a code deploy must NOT clobber it). Preserve the "never overwrite
  server-owned people.yaml / report.db / .env" guarantees.
- [ ] **Step 5 — verify locally.** `docker build -t insight-report:latest .` succeeds;
  compose up locally (with a `DATA_DIR` volume) serves the site incl. the React `/whats-new`;
  the baked `assets/app/*` is present in the image AND **no state files are baked** (inspect
  the image: no report.db/.env/people.yaml). Simulate an image swap: recreate the container
  from a rebuilt image and confirm the volume's report.db + people.yaml survive. (Prod deploy
  itself is exercised by the user when ready — the script is verified structurally + local.)
- [ ] **Step 6 — commit** `git add Dockerfile docker-compose.yml docker-compose.override.yml deploy.sh .dockerignore reportctl.py store.py server.py mcp_server.py tests && git commit -m "deploy: stateless multi-stage image (baked React) via docker save/load; ALL state under DATA_DIR on a persistent volume; DB backup preserved"`

---

## Notes
- Pixel-parity is enforced by the P0-T3 harness — no route is "done" until its diff is ~0.
- SSR-safe React discipline from day one (keeps a future SSR graft cheap).
- The shell/chrome stays server-rendered (Jinja + existing CSS) — instant, byte-identical.
- Don't commit build output (`assets/app/*`) — it's produced by the Docker frontend stage.
- Changelog entry: user-facing behaviour of `/whats-new` is unchanged (parity), so no
  changelog entry needed for the pilot; add one when a *visible* change ships.
- After Phase 0: repeat the pilot pattern per route; add the legacy hash-redirect shim when
  the report `/` monolith is split (later phase).
- **Cron data-refresh (prod-cutover, NOT triggered by local P0-T5) — verified:** the prod
  cron (`/etc/cron.d/insight-report`) runs `cd /home/alexey/insight-report &&
  docker compose exec -T report python reportctl.py all` (03:30 daily) + `… snapshot-status`
  (every 4h) — i.e. INSIDE the `report` container. So with state under a `DATA_DIR` volume
  mounted in that container, the cron writes to the volume — **no cron change, a named volume
  suffices.** P0-T5 Step 1 (DATA_DIR) must cover the collect/render paths — it does. Cutover
  invariants: keep the service name `report`, the compose file at `/home/alexey/insight-report`,
  and DATA_DIR mounted. Local P0-T5 uses a throwaway volume + downloaded prod DB — cron out of scope.
