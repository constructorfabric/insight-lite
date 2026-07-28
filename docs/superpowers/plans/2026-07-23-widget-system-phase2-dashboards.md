# Widget System — Phase 2–3 (Dashboards on React) Implementation Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development (per-task subagent + gate +
> commit), same as Phase 1. Design spec: `docs/superpowers/specs/2026-07-23-unified-widget-
> system-design.md`. Phase-1 plan (DONE): `2026-07-23-widget-system-phase1-catalog.md`.
> Branch `feat/react-migration`.

**Goal:** Render dashboards with the SAME React widget catalog as the report (one
`<PanelRenderer>` mapping `viz → widget`), replacing the server Jinja-macro + `.vl-panel`
path. Vega becomes the `chart` widget. Editor uses a React preview island (E1). Then bridge:
expose generic report sections as widget specs + register bespoke report widgets.

**Architecture:** New `frontend/src/widgets/registry.tsx` (`viz → component`) + `<PanelRenderer
spec data/>`. New resolved-data JSON endpoints (panels return `{viz,title,pin,data}` where
`data` is scalar / `{columns,rows}` / a Vega-Lite spec object — `vega_spec.build_spec` stays
server-side). Dashboard view page `/dashboard/<id>` renders via `render_spa_page` + a `dashboard`
React entry that fetches the spec + per-panel data and draws `<PanelRenderer>`. Editor
(`dashboard_editor.j2`, vanilla JS) keeps its chrome; only its preview swaps to a React island.

**Gate protocol:** dashboards were never gated. WS2-T1 establishes the baseline (seed +
gate-as-owner) BEFORE any render change; every render swap is captured candidate-vs-that-baseline.
Report states stay green throughout. `pytest -q`, `tsc --noEmit`, `docker build` green each task.

---

## WS2-T1: seed multi-viz dashboards + gate baseline (PREREQUISITE — do first)

**Files:** `frontend/visual/seed_dashboards.py` (or a fixture script), `frontend/visual/routes.mjs`
(+ dashboard states + a viewer-auth hook), possibly `frontend/visual/capture.mjs` (send an
`X-Forwarded-Email` header if the view page gates by owner).

- [ ] **Step 1 — verify the view-page auth gate.** Read `server.py` `/dashboard/<id>` (~3620) and
  `render.render_dashboard_page`: does it restrict by `_oauth_user()` vs `owner_login`/`visibility`?
  Determine whether a `shared`-visibility dashboard is viewable with NO auth header. Record the answer.
- [ ] **Step 2 — seed script.** A Python script that opens `history/report.db` via `store` and
  `store.create_dashboard(conn, owner_login="gate", title=..., spec=..., visibility="shared")` for a
  handful of dashboards whose panels cover EVERY viz: `number`, `table`, `line`, `area`, `column`,
  `bar`, `pie` (each panel a real `{viz, data:{tool,fields,params}, title}` using an allowed tool
  from `_DASHBOARD_TOOLS`). Idempotent (delete-by-title then recreate). Committed so the gate is
  reproducible. Run it against the local DB.
- [ ] **Step 3 — routes.mjs dashboard states.** Add `dashboard-<viz>` (or one multi-panel
  `dashboard-all`) capture states pointing at `/dashboard/<seeded-id>`. If Step 1 found the page
  gates by owner, add the viewer header in capture.mjs (e.g. `extraHTTPHeaders:{ 'X-Forwarded-Email':
  'gate@local' }`) and seed with that owner; else `shared` + no header.
- [ ] **Step 4 — capture the BASELINE** of the seeded dashboards against the CURRENT server
  (Jinja+Vega render) into `visual/baseline`. This is the parity target for the swap. Eyeball each.
- [ ] **Step 5 — commit** `git commit -m "gate: seed multi-viz dashboards + dashboard baseline states"`

## WS2-T2: resolved-data JSON endpoint for panels

**Files:** `server.py` (new `/api/dashboard/panel.json` GET + `/api/dashboard/preview-panel.json`
POST, or a `?format=json` flag on the existing ones), `dashboards.py` (a `resolve_panel_data(panel,
scope, period)` returning the per-viz data shape), test `tests/test_dashboard_json.py`.

- [ ] **Step 1 — `resolve_panel_data`.** Refactor `_render_panel` so the data resolution
  (`_call_source` + per-viz shaping) is separable from HTML emission. Return
  `{viz, title, pin, data}`: scalar for `number`; `{columns, rows}` for `table`/bar-family
  breakdowns; a **Vega-Lite spec object** (from `vega_spec.build_spec`, unchanged) for `_CHART_VIZ`.
- [ ] **Step 2 — JSON endpoints** returning that. Keep the existing HTML endpoints during the swap.
- [ ] **Step 3 — unit test** the JSON shapes per viz (against `history/report.db` + a seeded spec).
- [ ] **Step 4 — verify** `pytest -q` green; `import server, dashboards` clean.
- [ ] **Step 5 — commit** `git commit -m "dashboards: resolved-data JSON panel endpoints (build_spec stays server-side)"`

## WS2-T3: widget registry + `<PanelRenderer>` + dashboard React entry

**Files:** `frontend/src/widgets/registry.tsx`, `frontend/src/widgets/PanelRenderer.tsx`,
`frontend/src/pages/Dashboard.tsx`, `frontend/src/entries/dashboard.tsx`, `frontend/vite.config.ts`.

- [ ] **Step 1 — registry** `{ number: KpiTile, table: DataTable, line|area|column|bar|pie:
  VegaChart, …report-native types… }`. Map each `viz` to a widget + an adapter that turns the
  resolved `data` into that widget's props (scalar→KpiTile value; `{columns,rows}`→DataTable; Vega
  spec→`<VegaChart spec=…>`).
- [ ] **Step 2 — `<PanelRenderer spec data />`** looks up the registry, renders the widget, wraps
  in the dashboard panel chrome (title, pin) — byte-identical to the current `.dp`/panel wrapper.
- [ ] **Step 3 — `Dashboard.tsx`** page: reads the dashboard spec (embedded or via `/api/dashboard/
  <id>`) + fetches each panel's resolved data (WS2-T2 JSON) + lays out `<PanelRenderer>` per panel,
  reproducing `render_dashboard_page`'s grid/markup exactly. `entries/dashboard.tsx` mounts it;
  vite input entry added. Ship `CHART_CSS` + vega fonts (report_chrome path).
- [ ] **Step 4 — verify** tsc + build; (no route swap yet — not gated here). Commit
  `git commit -m "widgets: registry + PanelRenderer + Dashboard React entry"`

## WS2-T4: swap the dashboard VIEW page to React (gate)

**Files:** `server.py` `/dashboard/<id>` route → `render.render_spa_page("dashboard", "dashboards",
title, report_chrome=True)` (embedding the spec + panel data JSON), `render.py`.

- [ ] **Step 1 — swap** the view route to serve the React shell + dashboard entry (keep
  `?legacy=1` → old Jinja render as a fallback, mirroring the report `/report/legacy` pattern).
- [ ] **Step 2 — GATE** the seeded dashboard states (candidate = React `/dashboard/<id>`) vs the
  WS2-T1 baseline. Iterate `Dashboard.tsx`/`PanelRenderer` until pixel-clean (charts use the 1.5%
  chart tolerance; number/table strict). Investigate diffs.
- [ ] **Step 3 — verify** pytest + tsc + build green.
- [ ] **Step 4 — commit** `git commit -m "dashboards: view page renders via React PanelRenderer (gate clean)"`

## WS2-T5: editor preview island (E1) (gate)

**Files:** `templates/dashboard_editor.j2` (replace the `innerHTML` preview injection with a React
mount point + fetch of `/api/dashboard/preview-panel.json`), a small `frontend/src/entries/
dashboard-preview.tsx` mounting `<PanelRenderer>`, `frontend/vite.config.ts`.

- [ ] **Step 1 — island.** Keep the editor's vanilla JS (drag/width/measure-picker). Replace the
  preview render: on change, POST the panel spec to `/preview-panel.json`, mount `<PanelRenderer>`
  into the preview node (React island). Keep the preview container markup/classes identical.
- [ ] **Step 2 — GATE** the editor preview if capturable (add an editor state to routes.mjs, or
  verify manually via the browser + a screenshot vs the old preview). Editor chrome unchanged.
- [ ] **Step 3 — verify** editor HTTP tests (`tests/test_*editor*`) still green; tsc + build.
- [ ] **Step 4 — commit** `git commit -m "dashboards: editor preview island via PanelRenderer (E1)"`

## WS2-T6: retire the server panel-HTML/.vl-panel path + cleanup + full gate

- [ ] **Step 1 — retire** the dashboard server-HTML path (`_render_panel`'s HTML emission,
  `render_panel_macro` for dashboards, the `.vl-panel` script injection) now nothing uses it —
  keep only the `?legacy=1` fallback if we want it, else remove. Keep `vega_spec.build_spec`.
- [ ] **Step 2 — FULL gate** (all report + dashboard states) + `pytest -q` + `tsc` + `docker build`.
- [ ] **Step 3 — changelog** entry (user-facing: dashboards now share the report's chart/table
  rendering — consistent look, hover tooltips; note any behaviour change).
- [ ] **Step 4 — commit** `git commit -m "dashboards: retire server panel-HTML path; Vega is a widget"`

---

## Phase 3 — Report ↔ dashboard bridge

## WS3-T1: expose generic report sections as widget specs
- [ ] For the genuinely generic report pieces (KPI grid → `number` tiles, a plain table → `table`,
  a trend chart → `line`/`area`, a split bar → a `splitbar` widget), define widget specs + ensure the
  registry renders them identically to the report. A user can add them to a custom dashboard.
- [ ] Test that a seeded dashboard using these renders; gate that dashboard state. Commit.

## WS3-T2: register bespoke report widget types
- [ ] Register `MarkerTable`, `FlowPipe`, `PersonScore`, `Scorecard`, `HeatStrip`, `GroupedTable`-
  as-Person-weekly, etc. in the registry as placeable widget types (data-shape documented) so a
  dashboard CAN place them (report still composes in code). Gate a dashboard placing one. Commit.
- [ ] Changelog + final full gate + suite + docker build.

## Notes / guardrails
- The dashboard swap is the riskiest step — WS2-T1's baseline MUST exist before WS2-T4.
- Keep `?legacy=1` (Jinja dashboard render) as a fallback through Phase 2, retire only in WS2-T6.
- `vega_spec.build_spec` stays server-side; the JSON carries the spec object → `<VegaChart>`.
- `data-drill`/`data-sort` on dashboards is NEW behaviour — decide in/out; if in, add BEFORE the
  WS2-T1 baseline capture so the swap diff is honest (default: leave out for parity, add in Phase 3).
- Prereqs are captured in the spec; do WS2-T1..T2 before the React renderer tasks.
