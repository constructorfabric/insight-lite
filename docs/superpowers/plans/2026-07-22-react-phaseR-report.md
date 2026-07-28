# React migration — Phase R (report views) — Implementation Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Branch `feat/react-migration`.
> Design spec: `docs/superpowers/specs/2026-07-22-react-frontend-migration-design.md`.
> Builds on Phase 0 (scaffold, `render_spa_page`, `/assets/app` serving, screenshot-diff gate,
> image deploy). Phase 0 pattern proven on `/whats-new` (pixel-diff 0).

**Goal:** Split the report `/` monolith (all sections server-rendered, JS toggles by "mode")
into **per-view React routes** — `/overview`, `/trend`, `/delivery`, `/flow`, `/people`,
`/person`, `/repositories`, `/elements`, `/traffic`, `/ai-tools` — each pixel-identical under
the screenshot-diff gate. `/` → `/overview`; `mode=all` (Full report) dropped. Hybrid during
migration: unmigrated views keep working via the monolith; the sidebar routes each item to
its new route (migrated) or `/report#<mode>` (not yet).

**Pilot: `/overview`** (default landing; establishes the report-view pattern).

## Key facts (from inventory)
- Report data fragments (`/api/period|delivery|trend|flow|person`) return `{"ok":true,"html":…}`
  — HTML, not data. Phase R needs **JSON** per view (data from `build_model` slices). Charts:
  the JSON carries Vega-Lite **specs** (server builds them via `vega_spec`), React renders them.
- Filters are query params: period (`p=`/`days=`/`from&to`), `slice=` (scope level:target),
  `person=`. Already URL-synced.
- The monolith lives at `/` and `/report`; sidebar links go to `/report#<mode>`; mode is read
  from `location.hash` (not deep-linkable today). Renames: `repos→repositories`, `usage→traffic`,
  `fabric→ai-tools`, `all` dropped (see spec's redirect table).

## Shared scaffold (built with the pilot, reused by every view)
- **JSON endpoints** `GET /api/report/<view>?p=&slice=&person=&from=&to=` → the view's data +
  any chart specs. Reuse `build_model()` / the existing per-view builders; serialise to JSON
  instead of HTML. (Keep the old `render_*_fragment` HTML endpoints alive for the still-monolith
  hybrid period.)
- **Filter bar** React component: period presets (7d/30d/90d/1y/all) + custom range + scope
  `<select>` + person picker where applicable — reads/writes the SAME query params, identical
  markup/classes to today's `.ctrls`. Shared across views.
- **`useReportData(view)`** hook: reads query params, fetches `/api/report/<view>`, exposes
  `{data, loading, error}`; refetch on param change.
- **`<VegaChart spec=…/>`** React component: renders a VL spec via the vendored vega-embed
  (idempotent, ResizeObserver width guard — mirror the `hydrateVega` logic), themed already by
  the spec's config. Replaces the `.vl-panel` server-hydrate path for React pages.
- **Sidebar hybrid routing** (`shell.sidebar_html`): a set of "migrated" view keys → their new
  routes; the rest → `/report#<mode>`. Grows each phase. The active-highlight still works.

## Task R-P1: shared scaffold + `/overview` (pilot, under the gate)

**Files:** `frontend/src/` (report scaffold + Overview page + entry), `server.py`
(`/api/report/overview` JSON, `/overview` route → `render_spa_page`, sidebar routing),
`render.py`/builders (expose overview data as JSON), `shell.py` (sidebar migrated-map),
`frontend/visual/routes.mjs` (add `/overview` states), tests.

- [ ] **Step 1 — baseline FIRST.** Start current server; capture the CURRENT Overview
  (`/report#overview`, or `/` default) in its states (all-time; a period like 30d; a scope
  slice) via `visual:baseline`. This is the parity target. (Add these states to `routes.mjs`.)
- [ ] **Step 2 — study the Overview markup.** Read the `overview` `mode-section`s in
  `report.j2` + `panels/*.j2` (KPI tiles, weekly, throughput/TTM/contributors, stacked areas,
  company/category tables, etc.) — the React output must reproduce this DOM/classes exactly.
- [ ] **Step 3 — JSON endpoint.** `GET /api/report/overview?p=&slice=&person=` → the overview
  model (KPI values + deltas + sparkline points, the chart **specs** via `vega_spec`, table
  rows). Derive from `build_model`/period logic (same numbers as the fragment). Unit-test it.
- [ ] **Step 4 — scaffold + Overview page.** Build the shared scaffold (filter bar, hook,
  `<VegaChart>`) and `frontend/src/pages/Overview.tsx` rendering the overview identically
  (KPI tiles, sparklines, Vega charts via `<VegaChart>`, tables). `entries/overview.tsx` mounts
  it. SSR-safe. The filter bar drives the query params → hook refetch.
- [ ] **Step 5 — route + sidebar.** `/overview` → `render_spa_page("overview","overview","Overview")`.
  `/` still serves the monolith (hybrid). Add `/report#overview → /overview` to a client
  redirect shim (served at `/`/`/report` — reads hash, maps, preserves query) — OR defer the
  shim to the final phase and for now just make the sidebar "Overview" link point to `/overview`.
  Update `shell.sidebar_html` migrated-map so "Overview" → `/overview`, others unchanged.
- [ ] **Step 6 — gate.** Build; `visual:candidate` for the `/overview` states; `visual:diff`
  must be ~0. Iterate the React page until parity. Investigate `visual/diff/*.png` for spacing/
  font/wrapper/number-format differences and fix the markup. Paste the final diff summary.
- [ ] **Step 7** — full Python suite green; `tsc --noEmit` clean; `import server,render,spa` clean.
- [ ] **Step 8 — commit** `git add -A && git commit -m "react: report scaffold + /overview view (pixel-diff clean)"`

## Task R-P2..R-Pn: remaining views, one per task (same pattern)
`/trend`, `/delivery`, `/flow`, `/people`, `/person`, `/repositories`, `/elements`, `/traffic`,
`/ai-tools`. Each: baseline → JSON endpoint → React page (identical DOM) → route + sidebar
migrated-map entry → gate ~0 → commit. Reuse the scaffold from R-P1. Person needs the person
picker; Trend needs granularity/breakdown controls (already 2-section); Delivery/Flow have
existing fragment endpoints to convert to JSON.

## Task R-FINAL: cut over `/` + redirects + drop the monolith
- Client **hash-redirect shim** at `/` and `/report`: reads `location.hash`, maps old modes to
  new routes (spec's table: repos→repositories, usage→traffic, fabric→ai-tools, all→overview),
  preserves query params, `location.replace()`. `/` (no hash) → `/overview` (server 302 too).
- Remove the monolith `mode` machinery + `mode=all` from `report.j2`; retire the old
  `render_*_fragment` HTML endpoints once no view uses them. Sidebar map: all views → new routes.
- Full gate pass across ALL report routes; suite green; changelog entry (navigation change:
  per-view URLs, deep-linkable; Full report removed).

## Notes
- Pixel-parity per view via the screenshot-diff gate — non-negotiable, ~0 or it's not done.
- Chrome/shell stays server-rendered (`render_spa_page`), byte-identical.
- CSS: the shared base/shell/chart CSS is already extracted; page-specific CSS goes into the
  bundle — watch the cascade-order gotcha (linked bundle CSS loads AFTER inlined shell CSS;
  restate only deltas, don't re-clobber the shared `:root`/`body` — see the /whats-new pilot).
- SSR-safe React; clean JSON API (keeps a future SSR graft cheap).
- Prod cutover (image deploy + volume) is separate and user-driven; Phase R is dev/branch work.
