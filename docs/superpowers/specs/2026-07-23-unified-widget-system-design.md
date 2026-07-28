# Unified Widget System — Design

**Status:** approved (approach A+, single initiative), 2026-07-23
**Branch:** `feat/react-migration` (continues from the React report migration)
**Relates to:** [React frontend migration spec](2026-07-22-react-frontend-migration-design.md)

## Goal

One React component/widget system that powers **both** the report views **and** the
dashboard constructor (and its Vega charts). Maximise reuse: every table, KPI tile,
bar-row, split-bar, chart, etc. is a single shared component. Kill the current split
where the report renders via hand-written React while dashboards render via
server-side Jinja macros + Vega specs. Deliver a widget catalog that a future
custom-dashboard renderer composes.

Chosen approach **A+** (of A / B / A+): build the shared catalog + one React renderer
and move dashboards onto it now; keep the report views as hand-written React pages but
rebuilt from the catalog (so pixel-parity stays cheap to hold); opportunistically
expose the *generic* report sections as widget specs so they are droppable on custom
dashboards, and register the *bespoke* report widgets as widget types. Full "report =
a saved dashboard" (variant B) is explicitly out of scope but not foreclosed — each
widgetised section is a step toward it.

## Current state (two render worlds)

- **Report views** (`frontend/src/pages/*.tsx`): hand-written React pages, client-
  rendered from per-view JSON APIs (`/api/report/<view>` → `render.<view>_json`).
  Charts via `<VegaChart>`; tables via `<DataTable>`; plus many bespoke/duplicated
  bits (`GhLink` copied in 3 files; bar-rows, split-bars, mini-stats repeated across
  People/Person/Traffic/Overview/AiTools; dev-score gauge/chain/board; provenance
  tables; flow pipe; grouped tables).
- **Dashboards** (`dashboards.py` + `render.py`): a declarative widget spec
  `{viz, data:{tool,fields,params}, pin, title}`. `_render_panel` resolves it:
  - `_VIZ_PRIMITIVE = {number→kpi_tile, table→data_table}` → server Jinja macros
    (`render.render_panel_macro`) → HTML;
  - `_CHART_VIZ = (line, area, column, bar, pie)` → `vega_spec.build_spec` → a
    `.vl-panel` `<script class="vl-spec">` client-hydrated by the vendored Vega.
  - Data from `_DASHBOARD_TOOLS = {contribution, delivery, trend, flow, person,
    list_items}` via `_call_source(tool, scope, period)`.
  - Shape→viz rules: `_SHAPE_VIZ`, `_MULTI_FIELD_VIZ`; legacy alias map
    `_LEGACY_COMPONENT_VIZ`. Editor at `/dashboard/<id>/edit`.

So: two composition mechanisms, two renderers, and Vega is a special case rather than
"just another widget".

## Target architecture

Three concepts, shared everywhere:

1. **Widget registry** — `viz → React component`. One source of truth mapping a viz
   name (the existing vocabulary: `number`, `table`, `line`, `area`, `column`, `bar`,
   `pie`, plus new report-native types `barlist`, `splitbar`, `ministats`, `statrow`,
   `heatstrip`, `markertable`, `groupedtable`, `score`, `flowpipe`) to the component
   that draws it. Lives in `frontend/src/widgets/registry.ts`.

2. **Widget spec** — the existing dashboard spec `{viz, data, pin, title}`, kept and
   lightly extended (new viz names + per-widget options). The report does **not** have
   to express itself as specs; specs remain the dashboard authoring format.

3. **`<PanelRenderer spec data />`** — a React component that reads the registry and
   renders the widget for a spec against already-resolved data. One renderer for the
   dashboard page and editor preview; the report composes components directly (and can
   use `<PanelRenderer>` where a section is genuinely a widget).

Vega charts become the `chart` family of widgets → `<VegaChart>`. Dashboards render
**entirely in React** via `<PanelRenderer>` — the server stops emitting panel HTML /
`.vl-panel` scripts for dashboards; it serves the shell + a dashboard spec + resolved
panel data as JSON, and React draws it. `vega_spec.build_spec` stays (it builds the
Vega-Lite spec object) but its output is consumed by `<VegaChart>`, not injected as
server HTML.

### Component catalog (`frontend/src/widgets/`)

Primitives (data-agnostic, take resolved data as props):
- `KpiTile`, `DataTable`, `VegaChart`, `SegBar` — already exist; move under `widgets/`.
- `GhLink` — dedup the 3 copies (Flow/People/Traffic).
- `BarRow` / `BarList` — the `.row > .nm + .bb>.bar>i + .vv` horizontal bar-with-value
  row (+ optional drill, tip, "+N more" tail). Replaces the copies in People
  (categories), Person (top-repos / by-element / work-type), Traffic (contributors).
- `SplitBar` + `Legend` — the `.split2`/`.leg2` (and `.cmix-bar` + legend) proportion
  bar. Replaces Person `Split2Card`, Overview `WorkType`, AiTools AI-usage split,
  Repositories `TypeBar`, Delivery mix.
- `MiniStats` — the `.mini > .m > .mv/.ml` stat-tile row (AiTools / People-reviews /
  Traffic).
- `StatRow`, `HeatStrip`, `Chips`.

Composite / bespoke widget types (registered so a dashboard can place them; the report
composes them in code):
- `MarkerTable` (AiTools provenance/gears/tracker), `GroupedTable` (Person weekly,
  Flow dwell/by-person), `FlowPipe` (delivery/flow pipeline).
- **Dev-score is two different widgets, not one shared trio** (review correction):
  Person renders the personal `ScoreGauge` + `ScoreChain` + per-member board
  (`Person.tsx`); Overview renders a *separate* team **scorecard table** (top devs +
  by-company — `Overview.tsx` `Score`). They share the concept, not the markup — extract
  as two components (a `ScoreChain`/gauge set for Person, a scorecard for Overview),
  don't force one abstraction.
- Bar-rows also appear in **Overview** (not only People/Person/Traffic) — include it in
  the `BarRow`/`BarList` adoption list.

### Data flow

Widgets never fetch. They receive resolved data as props. Two source styles feed them,
unchanged: the report's per-view JSON APIs and the dashboard `tools`. Both yield plain
data → the same components. A future common "data source" interface (tool-like for
both) is noted but **out of scope** for this initiative.

### Data / JSON boundary for dashboards (net-new — flagged by review)

Today the dashboard endpoints return **rendered HTML**, not data:
`/api/dashboard/panel` (GET, `server.py`) and `/api/dashboard/preview-panel` (POST) both
run `_call_source` and immediately turn the result into HTML / a `.vl-panel` Vega
`<script>`. There is no resolved-data JSON boundary yet — Phase 2 must add it. Decisions
this initiative commits to:
- Add JSON variants of those endpoints that return `{viz, title, pin, data}` where
  `data` is the *resolved* shape per viz: a scalar for `number`; `{columns, rows}` for
  `table`/bar-family breakdowns; and — importantly — a **Vega-Lite spec object** for the
  `chart` vizzes. I.e. `vega_spec.build_spec` **stays server-side**; it emits the spec
  object into the JSON, and `<VegaChart spec=…>` consumes it (matches VegaChart's
  existing prop; nothing ported to JS). This keeps theming/spec logic in one place.
- Report charts keep using `render.py`'s `line_spec`/`stacked_area_spec` inside the
  `*_json` builders; only dashboards call `build_spec`. There is **no single
  `build_spec` entry point** across both — the convergence is at the `<VegaChart>` prop
  (a Vega-Lite spec object), not at one builder function.
- The React dashboard path must ship `shell.CHART_CSS` + the vega fonts, or charts fall
  back to `fill:black` (the reason the current preview endpoint injects `CHART_CSS`).

### Interactions & theming (unified)

- Drill-down and click-to-sort (currently shared delegated listeners in
  `shell.DRILL_JS` / `shell.SORT_JS`) stay a **single implementation** used by report
  and dashboards alike — injected wherever the widget catalog renders. (They already
  work by delegation on `data-drill` / `data-sort`, which the catalog emits.) Longer
  term they may become component-level behaviour; for this initiative one shared copy
  is the rule — no per-page reimplementation.
- Theming stays on the shared CSS variables already in `shell`/`report.css`.

## Testing / pixel-parity strategy

The pixel-diff gate stays the guardrail:
- **Report views:** states are unchanged; every component extraction / adoption must
  keep each report state diff-clean (≤0.1%, chart states ≤1.5%, the one documented
  `elements-slice` exception). Same harness (`frontend/visual/`).
- **Dashboards:** before swapping the dashboard render to `<PanelRenderer>`, add
  dashboard screenshot states to the harness with the CURRENT server-rendered
  dashboard as baseline; the React-rendered dashboard is the candidate; swap only when
  it matches. (Dashboards were never under the gate before — this brings them in.)
- Python suite stays green; `tsc --noEmit` clean; image builds.

## Non-goals / deferred

- Variant B (report views become widget-spec compositions / "report = saved
  dashboard"). Not now; the bridge (generic sections as specs) is the on-ramp if ever
  wanted.
- Unifying the two data-source styles behind one interface.
- New dashboard widget *features* beyond parity with today's viz vocabulary (plus the
  new report-native widget types the catalog naturally adds).
- Prod cutover / deploy (separate, user-driven, as with the report migration).

## Risks (ranked, per review)

1. **HIGH — the dashboard JSON boundary is net-new.** The endpoints return HTML today;
   Phase 2 must add resolved-data JSON endpoints + a per-viz data shape (see Data/JSON
   boundary above). Not "nearly free" as the first draft implied.
2. **HIGH — the pixel-gate baseline for dashboards does not exist yet.** `history/
   report.db` has exactly **one** dashboard, with **zero panels**, owned by a private
   user (`userA`); the capture harness sends no auth header (viewer comes from
   `X-Forwarded-*`). So there is nothing meaningful to screenshot as a baseline. **Seed
   multi-viz dashboards (number/table/line/area/column/bar/pie) and make the gate
   resolve as their owner (or use a shared board)** — this is an explicit Phase-2
   PREREQUISITE, not a step. Until done, the dashboard guardrail is theater.
3. **MED — the editor is vanilla JS in `dashboard_editor.j2`, not React** (no dashboard
   entry under `frontend/src/entries/`). Its live preview injects
   `/api/dashboard/preview-panel` HTML via `innerHTML`; drag-reorder / width / the
   measure-picker modal are all inline JS. "Move the preview to `<PanelRenderer>`"
   understates this. **Scope decision required (see open question).**
4. **MED — drill-down / click-to-sort on dashboards is NEW behaviour, not parity.**
   `dashboard.j2`/`dashboard_editor.j2` have no `data-drill`/`data-sort`; `DRILL_JS`/
   `SORT_JS` are report-only today. Adding them to dashboards is a feature; decide
   in/out BEFORE capturing the baseline so the swap diff is honest.
5. **LOW — chart theming.** Covered by shipping `CHART_CSS` + vega fonts on the React
   dashboard path (above). The view page is already shell + per-panel fetch, so
   `<PanelRenderer>` maps onto it naturally.
6. **Report pixel-parity drift** during catalog adoption — mitigated by the gate per
   step (proven across the 10 report views this session).

## Reconciliation with the parent (2026-07-22) spec

The parent React-migration spec ordered "Phase 1 = dashboard builder first". Reality
went the opposite way: the **report views** were migrated to React (this session);
**dashboards remain 100% Jinja** (no React entry, HTML-returning endpoints). This spec's
current-state reflects reality — do not assume dashboards are partly React.

## Phasing (single initiative; Phase 1 is independently shippable)

Done as one branch of work, but **Phase 1 ships on its own** — it has no dependency on
the JSON boundary or the dashboard baseline, carries only the proven report pixel-gate
risk, and delivers the "maximum components" value immediately. It must not be held
hostage to the heavier Phase 2. Phases 2–3 begin only after their prerequisites land.

- **Phase 1 — Catalog (independent).** Create `frontend/src/widgets/` and add the NEW
  components there: GhLink, BarRow/BarList, SplitBar+Legend, MiniStats, StatRow,
  HeatStrip, Chips, MarkerTable, GroupedTable, FlowPipe, and the two dev-score
  components (Person gauge/chain/board; Overview scorecard). Adopt them across the
  report views (People, Person, Traffic, Overview, AiTools, Repositories, Delivery,
  Flow), deleting the duplicated/bespoke in-page copies. Gate every view.
  - **The `components/` → `widgets/` move of the EXISTING DataTable/KpiTile/VegaChart/
    SegBar/FilterBar is optional churn** (touches every importing page; CSS side-effect
    imports carry real diff-risk). Default: **leave them in `components/` and just add
    `widgets/` for the new ones** (re-export from one index if a single import path is
    wanted). Only do the physical move as one mechanical, gated, import-rewrite commit
    if we decide the single directory is worth it.
- **Phase 2 — One renderer (dashboards on React).** PREREQUISITES FIRST: (a) **seed
  multi-viz dashboards** covering every viz + (b) make the pixel-gate **resolve as the
  owner** so those boards are screenshot-able; (c) add the **resolved-data JSON
  endpoints** (Data/JSON boundary above); (d) decide **drill/sort-on-dashboards in or
  out**. THEN: widget registry + `<PanelRenderer>`; capture dashboard baselines; move
  `/dashboard/<id>` (already shell + per-panel fetch) and the editor preview to render
  via `<PanelRenderer>` + resolved-data JSON; Vega becomes the `chart` widget. Retire
  the dashboard server-HTML/`.vl-panel` path once matched.
- **Phase 3 — Report ↔ dashboard bridge.** Expose the generic report sections (KPI
  grid, plain tables, a trend chart, a split bar) as widget specs so they are droppable
  on a custom dashboard; register the bespoke report widget types in the registry.
  Changelog entry.

## Open question (needs a decision before the Phase-2 plan)

**Editor scope.** The dashboard editor (`dashboard_editor.j2`) is entirely vanilla JS
today (drag-reorder, width, measure-picker modal, HTML-`innerHTML` preview) — there is
no React dashboard entry. Two options:
- **(E1) Preview island:** keep the Jinja editor + its JS, but replace only the preview
  render with a small React `<PanelRenderer>` island fed the resolved-data JSON. Small,
  low-risk, gets charts/tables consistent; the editor chrome stays as-is.
- **(E2) Full React editor:** migrate the whole editor to React (react-grid-layout per
  the parent spec). Big, unlocks a proper dashboard builder, higher risk/effort.
Recommendation: **E1 now**, E2 as a later, separate effort. Confirm before planning
Phase 2.

## Success criteria

- One `frontend/src/widgets/` catalog; no duplicated `GhLink`/bar-row/split-bar/
  mini-stats markup left in pages.
- Dashboards render via the same React components as the report; no server panel-HTML
  or `.vl-panel` injection for dashboards; Vega is a registered widget.
- All report + dashboard pixel-gate states pass; full Python suite green; image builds.
- A custom dashboard can place at least the generic report widgets.
