# Frontend → React migration — design

**Date:** 2026-07-22
**Status:** approved in direction (pending spec review)

## Goal

Move the entire frontend from server-rendered Jinja + inline vanilla JS to **React**,
to unblock building richer interactive UI (a proper dashboard constructor, an AI
copilot, future features) — without the maintenance ceiling of hand-written vanilla JS
in 2000-line templates.

## Non-negotiable constraint

**The site must look pixel-identical after the migration.** This is a *refactor of the
rendering layer, not a redesign.* Every page, in every state (filters, modals,
light/dark), must render the same as today. This constraint governs every decision
below and is enforced by a screenshot-diff gate (see "Pixel-parity").

**One approved, intentional deviation — the navigation model.** Today the whole report
is a single `/` page: every section is server-rendered up front and client JS toggles
`hidden` by "mode" (overview/trend/…/all); the active tab isn't even in the URL. That
monolith is the "past" we're explicitly breaking from. We split it into **per-view
routes** (see Route inventory), so navigating between views becomes a page load (brief
content flash, chrome shell instant) instead of an instant client toggle, and the old
`mode=all` "Full report" page is **removed** (per-view pages + per-page print replace it).
This changes *navigation behaviour and removes one screen* — a conscious, approved trade;
the **per-screen content stays pixel-identical** and remains under the diff gate.

## Chosen architecture (and the alternatives we rejected)

**Python-only, client-rendered MPA with a server-rendered shell.**
- The app stays **multi-page** (server routes and serves each page — NOT an SPA, no
  client router). This matches today's model and keeps pixel-parity simple.
- Python (`server.py`) stays the only runtime: it serves the **shell** (sidebar/header
  chrome — the existing Jinja + CSS, unchanged), a `<div id="root">` per route, the
  built JS bundle for that route, and **JSON APIs** for data.
- **React renders the page's content area on the client**, fetching from JSON APIs.
- **Vite** builds the React app; output is **baked same-origin** into the deploy
  artifact (served from `/assets/…`, no runtime CDN — same supply-chain rule as Vega).
- **No Node runtime in production.** Build is a CI/local step; prod is the one Python
  container as today.

**Rejected — Node SSR (Next/Remix/Astro):** gives pixel-perfect first paint + SEO, but
neither matters for an internal, authed, no-SEO tool, and it adds a second runtime to
develop/maintain (hydration bug class, extra service hop, continuous Node process). The
payoff features (builder, copilot) don't need SSR.

**Reversibility (why Python-only is not a one-way door):** SSR is a delivery strategy,
not a capability gate. If SSR is ever genuinely needed, the *same React components* graft
into Next/Remix as a server layer — not a rewrite. To keep that cheap we impose two
disciplines from day one:
1. **SSR-safe React** — no `window`/`document` access at module top level; all data via
   fetch; no client-only globals during render.
2. **Clean JSON-API contract** — the API is the boundary both a client-render and a
   future SSR model consume identically.

## Pixel-parity strategy (how we guarantee "no visual change")

Three rules, mandatory:
1. **CSS ported verbatim.** All look lives in `shell.BASE_CSS`, `shell.SHELL_CSS`, the
   `<style>` blocks in `templates/report.j2` + per-manage-page Python-embedded CSS, and
   the design tokens (`:root` vars). These move into real `.css` files **unchanged** and
   are imported by the React app. No new styling, no CSS-in-JS reinterpretation.
2. **Identical DOM + classes.** Each React component emits the **same markup with the
   same `class=`** as the current Jinja output, so the same CSS produces the same result.
   Components are authored *from* the current template markup, class-for-class.
3. **Screenshot-diff gate.** Before migrating a route we capture **baseline** screenshots
   of the current server-rendered page in every state; after, we diff the React output
   against them. A route is not "done" until the settled-state diff is ~0. This is what
   *proves* parity rather than eyeballing it. (Chrome shell stays server-rendered, so the
   persistent chrome is byte-identical; only the content area is React.)

## The screenshot-diff harness (Phase 0 deliverable)

- Playwright-driven: script visits each route + state, captures PNGs.
- Two modes: **baseline** (against current `main`/server-rendered) and **candidate**
  (against the React build), then a pixel-diff with a small tolerance, failing on
  regions that differ beyond threshold.
- Runs locally and in CI as the migration gate. States enumerated per route (tabs,
  key filters, modals, empty/loaded, light/dark if applicable).

## JSON-API layer (the hidden half of the work)

Many pages are server-rendered HTML today, not JSON. Each migrated route needs a JSON
endpoint returning its data (the model that Jinja currently consumes). Some already
exist and are reused: `/api/period`, `/api/trend`, `/api/delivery`, `/api/flow`,
`/api/person`, `/api/dashboard*`, `/api/chat`, `/api/usage*`. New ones are added per
route as it's migrated (e.g. an overview/summary JSON, config JSON, identity JSON…).
The existing `build_model()` and page builders are the source; we expose their data as
JSON instead of (or alongside) HTML during transition.

## Backward-compatible redirects (bookmarks / open tabs must not break)

Today the report is one page served at both `/` and `/report`, and **the active view
lives in the URL hash** (`/report#trend`, `/report#delivery`, …), read client-side from
`location.hash` (default `overview`); filters are query params (`p`, `slice`, `person`,
`tdim`, `tgran`). People have `/report#…` links open/bookmarked, so those must keep working.

**Critical constraint:** the browser never sends the `#fragment` to the server, so a
server-side 302 from `/report#trend` is impossible. **Hash → route redirects must be
client-side.** Plan:
- Keep a tiny **legacy shim** served at `/` and `/report`: it reads `location.hash`, maps
  the old mode to the new route, **preserves the query string**, and `location.replace()`s
  to it. No hash → `/overview`.
- Server-side 302 (path-level) where the server *can* see it (e.g. bare `/report` with no
  hash → `/overview`), as belt-and-braces alongside the shim.

**Old mode → new route (note the renames):**

| old (`/report#…`) | new route      |
|-------------------|----------------|
| `overview`        | `/overview`    |
| `trend`           | `/trend`       |
| `delivery`        | `/delivery`    |
| `flow`            | `/flow`        |
| `people`          | `/people`      |
| `person`          | `/person`      |
| `repos`           | `/repositories`|
| `elements`        | `/elements`    |
| `usage`           | `/traffic`     |
| `fabric`          | `/ai-tools`    |
| `all` (dropped)   | `/overview`    |
| (no hash) / `/`   | `/overview`    |

New route names follow the sidebar **labels** (Repositories/Traffic/AI tools), not the
cryptic internal keys (`repos`/`usage`/`fabric`) — clearer deep-links; the table above is
the compatibility bridge. In-content hash links (`href="#flow"`, `href="#person"`) are
rewritten to real routes during the port. Manage routes (`/config`, `/identity`, …) keep
their paths — no redirect needed.

## Coexistence during migration (hybrid)

The end state is "all content React," but we get there **route-by-route**. Mid-flight the
site is hybrid: the server decides per route whether to render the old Jinja page or the
shell+React-root. The sidebar/shell is shared and unchanged, so navigation between a
migrated and a not-yet-migrated route is seamless. No user-visible "half-migrated" state
because each route is only cut over once its screenshot diff passes.

## Route inventory (grouping; each migrated under its own diff gate)

- **Report views — split from the `/` monolith into per-view top-level routes:**
  `/overview`, `/trend`, `/delivery`, `/flow`, `/people`, `/person`, `/repositories`,
  `/elements`, `/traffic`, `/ai-tools`; `/` redirects to `/overview`. Each route renders
  only its own sections and loads only its own data. The shared filters (period / scope /
  person / trend gran+dim) live in **query params** (already the case today: `p`, `slice`,
  `person`, `tdim`, `tgran`), so they survive cross-page navigation and are deep-linkable.
  The old `mode=all` "Full report" is dropped. (Biggest, most CSS, highest parity risk —
  migrated last, one route per phase.)
- **Dashboards** — `/dashboards` (list), `/dashboard/<id>` (view), `/dashboard/<id>/edit`
  (the builder — the one place React *improves* things: react-grid-layout etc., still
  visually identical for now).
- **Manage** — `/update`, `/data` (data health), `/identity`, `/config`, `/taxonomy`,
  `/setup`, `/metrics`, `/mcp-info`, `/usage-insights`, `/whats-new`, `/chat-log`,
  `/views`, `/calibrate`, `/exports`.

## Execution phases

- **Phase 0 — harness + pilot.** Vite build + baked-asset serving + React-mount bridge
  into a server shell + the screenshot-diff harness. Migrate ONE simple route (e.g.
  `/whats-new` or `/data`) to prove: same CSS + same DOM + diff = 0. Establishes the
  pattern and the gate.
- **Phase 1 — dashboard builder.** `/dashboard/<id>/edit` (+ view/list) — highest value;
  React genuinely improves it while staying visually identical under the gate.
- **Phase 2..N — remaining manage routes**, one per phase, each under the diff gate.
- **Phase final — the report views**, split from `/` into per-view routes (`/overview`,
  `/trend`, …), one route per phase (largest surface, done last when the pattern is
  proven and the harness is trusted). `/` → `/overview`; drop `mode=all`.

## Build & deploy integration

- Add `frontend/` (Vite + React + TS). `npm run build` → static assets into the path
  Python serves (`assets/app/`), **baked into the image** (no runtime CDN).
- **Ship a locally-built, ready image (not file-by-file rsync + server build).**
  Multi-stage Dockerfile: a `node` stage builds the frontend, the Python stage copies the
  built assets in. Deploy = build the image locally → `docker save | ssh 'docker load'` →
  `docker compose up -d` (no registry needed for a single box; a registry is the later
  option if incremental layer transfer is wanted). Keep the DB backup-before-deploy step.
- **Stateless image + persistent state volume (critical).** Today compose bind-mounts the
  whole repo (`- .:/work`) so code+data live on the host — incompatible with a baked image
  (the host dir would shadow the baked code/assets). Restructure: **code + built assets come
  from the image; ALL runtime state lives on a persistent volume.** Centralise state under a
  configurable `DATA_DIR` (report.db + wal/shm, generated report.html/data.json, caches,
  clones, history/backups, exports, server-owned people.yaml) and mount ONE persistent
  volume there; `.env` is a separate secret bind-mount; `.dockerignore` excludes all state
  so nothing stateful is baked. This is what makes image-swap deploys safe (data survives).
- Dev: Vite dev server proxying `/api` to Python; a compose override keeps local dev on
  `build:` + `- .:/work` so nothing changes locally.
- **Scheduled data refresh (cron) — verified, keeps working with no cron change.** The prod
  cron lives at `/etc/cron.d/insight-report`:
  `30 3 * * * root cd /home/alexey/insight-report && docker compose exec -T report python reportctl.py all`
  (daily full refresh) and `0 */4 * * * … reportctl.py snapshot-status` (board snapshot). It
  runs **inside the running `report` container** via `docker compose exec`, so once state is
  under a `DATA_DIR` volume mounted in that container, the cron writes to the volume and the
  web reads from it — **a named volume is sufficient; no host bind-mount and no cron edit
  needed.** Prod-cutover invariants the deploy must preserve: (1) the compose **service name
  stays `report`**; (2) the compose file stays at **`/home/alexey/insight-report`** (the
  cron `cd`s there) and is the new image-based one; (3) `DATA_DIR` is set in the container
  and the volume is mounted. `reportctl.py all`/`snapshot-status` must keep existing. A
  deploy firing at the same second as a cron `exec` could fail that one run (idempotent —
  next run recovers). Not triggered by the local Phase-0 P0-T5.

## Testing

- Python API tests (unittest) for every new JSON endpoint.
- React component/unit tests (Vitest + Testing Library) for logic-bearing components.
- The **screenshot-diff harness is the parity gate** — the primary defense of the
  "no visual change" constraint, run per route in CI.

## Risks & mitigations

- **Silent visual drift** → screenshot-diff gate per route/state; chrome stays server-rendered.
- **Scope size (20+ routes, HTML→JSON)** → strictly incremental, hybrid coexistence, each
  route shippable on its own; report `/` (the bulk) last.
- **Build/toolchain creep** → keep it minimal (Vite + React + TS), assets baked, one
  container in prod.
- **Reversibility loss** → SSR-safe discipline + clean JSON API keep a future SSR graft cheap.

## Out of scope

Any visual/UX change (this pass is parity-only); Node SSR; a client-side router / true
SPA; redesign of the dashboard builder UX beyond what react-grid-layout gives for free
(a later, separate, *visible* improvement once we're on React).
