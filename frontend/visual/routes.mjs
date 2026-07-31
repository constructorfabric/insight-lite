// Route/state manifest for the screenshot-diff (pixel-parity) harness.
//
// Each entry describes one screenshot to capture: a route, optionally a
// viewport/theme override, and an optional async `setup(page)` step to reach
// a particular UI state (open a tab, apply a filter, open a modal, ...)
// before the screenshot is taken. Add one entry per route/state as more
// routes migrate to React — `id` must be unique and is used as the output
// filename (`<id>.png`).
//
// setup(page) runs AFTER the page has loaded and settled (network idle +
// fonts ready) but BEFORE the screenshot. Keep it side-effect-free beyond
// reaching the desired visual state (no data mutation).

/** @typedef {{
 *   id: string,
 *   path: string,
 *   viewport?: { width: number, height: number },
 *   theme?: 'light' | 'dark',
 *   setup?: (page: import('playwright').Page) => Promise<void>,
 * }} RouteSpec */

const DEFAULT_VIEWPORT = { width: 1440, height: 900 };

// Higher pixel-diff tolerance for chart-dense states. Vega measures text on a
// canvas for legend/axis layout; in an isolated single-view page vs the
// monolith's shared-cache page that measurement lands ~1px differently, lighting
// up every chart-stroke/legend/axis edge in the diff while the content, layout
// and data are identical (verified by eye — see docs). 0.1% is unrealistically
// strict for full-page canvas charts; these states use 1.5% + a visual spot-check.
const CHART_THRESHOLD = 1.5;

// Three states of the Overview view — default all-time, a 30-day period, and a repo
// slice — captured as a baseline and re-captured as a candidate, paired up by `id`
// (see capture.mjs's `--base`/`--out` and diff.mjs).
//
// This harness was built for the React migration (see
// docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P1), where the
// baseline was the Jinja monolith at `/report/legacy` and the candidate the new
// `/overview`. That migration is finished and the monolith has been removed, so the
// comparison is no longer React-against-Jinja: the baseline is now the last known
// good `/overview`, and the harness guards against React regressing against itself.
// OVERVIEW_ROUTE stays overridable for a one-off comparison against another route.
const OVERVIEW_ROUTE = process.env.OVERVIEW_ROUTE || "/overview";
// A real element slice from the local report.db (see repo inventory) — any
// `level:target` scope works, this one just has non-trivial data locally.
const OVERVIEW_SLICE = "element:Insight";

// Trend (Task R-P2): baseline captures the CURRENT monolith at `/report#trend`
// (mode read from the hash — see templates/report.j2's hashMode); the
// candidate run points the SAME states at `/trend` instead (no hash needed —
// it's its own page). `TREND_ROUTE=/trend` (env var) switches to the
// candidate; defaults to the monolith's `/report` (+ `#trend` hash) for the
// baseline. Query params (?p=/?slice=/?tgran=/?tdim=) work on BOTH — the
// monolith already reads them via initFromURL, and the React route via
// useReportData/useReportQuery — so the same query string reproduces the
// same state on either front-end.
const TREND_ROUTE = process.env.TREND_ROUTE || "/report/legacy";
const TREND_HASH = TREND_ROUTE.startsWith("/report") ? "#trend" : "";

// Delivery (Task R-P3): baseline captures the CURRENT monolith at
// `/report#delivery` (mode read from the hash); the candidate run points the
// SAME states at `/delivery` instead (no hash needed — it's its own page).
// `DELIVERY_ROUTE=/delivery` (env var) switches to the candidate; defaults to
// the monolith's `/report` (+ `#delivery` hash) for the baseline. Same query-
// param contract as Overview/Trend (?p=/?slice=) works on both front-ends.
// Delivery has NO Vega charts (issue/PR/CI KPI tiles, a plain mix table, the
// hand-rolled flow-pipe) — no chart-dense threshold or vega-settle wait needed.
const DELIVERY_ROUTE = process.env.DELIVERY_ROUTE || "/report/legacy";
const DELIVERY_HASH = DELIVERY_ROUTE.startsWith("/report") ? "#delivery" : "";

// Flow (Task R-P4): baseline captures the CURRENT monolith at `/report#flow`
// (mode read from the hash); the candidate run points the SAME states at
// `/flow` instead (no hash needed — it's its own page). `FLOW_ROUTE=/flow`
// (env var) switches to the candidate; defaults to the monolith's `/report`
// (+ `#flow` hash) for the baseline. Same query-param contract as
// Overview/Trend/Delivery (?p=/?slice=) works on both front-ends. Flow has
// ONE Vega chart (the CFD stacked-area, board movement section) — and unlike
// Trend/Overview, the monolith's Flow panel is ALWAYS live-fetched (even the
// bare default state — see templates/report.j2's initFromURL()/refreshFlow(),
// no build-time fast path), so every Flow state is chart-dense/timing-
// sensitive the same way: threshold + the vega-settle wait apply uniformly.
const FLOW_ROUTE = process.env.FLOW_ROUTE || "/report/legacy";
const FLOW_HASH = FLOW_ROUTE.startsWith("/report") ? "#flow" : "";

// People (Task R-P5): baseline captures the CURRENT monolith at
// `/report#people` (mode read from the hash); the candidate run points the
// SAME states at `/people` instead (no hash needed — it's its own page).
// `PEOPLE_ROUTE=/people` (env var) switches to the candidate; defaults to
// the monolith's `/report` (+ `#people` hash) for the baseline. Same query-
// param contract as Overview/Trend/Delivery/Flow (?p=/?slice=) works on both
// front-ends. People has NO Vega charts (a %-by-category grid, a code-review
// table, and the big per-person grouped table) — no chart-dense threshold or
// vega-settle wait needed, same as Delivery.
const PEOPLE_ROUTE = process.env.PEOPLE_ROUTE || "/report/legacy";
const PEOPLE_HASH = PEOPLE_ROUTE.startsWith("/report") ? "#people" : "";

// Person (Task R-P6): baseline captures the CURRENT monolith at
// `/report#person` (mode read from the hash); the candidate run points the
// SAME states at `/person` instead (no hash needed — it's its own page).
// `PERSON_ROUTE=/person` (env var) switches to the candidate; defaults to the
// monolith's `/report` (+ `#person` hash) for the baseline.
//
// Two states: the default (no person selected → the "pick a person" hint), and
// a specific person deep-linked via `?person=<login>`. BOTH front-ends honour
// `?person=`: the monolith's initFromURL() calls window.showPerson(person)
// (switches to the person tab + drives the picker), and the React route reads
// it via useReportQuery. So the same query string reproduces the same selected
// state on either side — no fiddly picker automation needed. `ainetx` is the
// biggest local contributor (safe, data-rich). Person has an SVG gauge but NO
// Vega charts, so no chart-dense threshold / vega-settle wait; the dashboard is
// async-fetched though (both sides), so the selected state waits for it to paint.
const PERSON_ROUTE = process.env.PERSON_ROUTE || "/report/legacy";
const PERSON_HASH = PERSON_ROUTE.startsWith("/report") ? "#person" : "";
const PERSON_LOGIN = "ainetx";

// Repositories (Task R-P7): baseline captures the CURRENT monolith at
// `/report#repos` (mode read from the hash); the candidate run points the SAME
// states at `/repositories` instead (NOTE the route rename repos→repositories —
// migration spec's redirect table — no hash needed, it's its own page).
// `REPOSITORIES_ROUTE=/repositories` (env var) switches to the candidate;
// defaults to the monolith's `/report` (+ `#repos` hash) for the baseline. Same
// query-param contract as the other views (?p=/?slice=) works on both
// front-ends. Repositories has NO Vega charts (an all-time repo-coverage
// summary + inventory table inside a closed <details>, and the CSS-typebar
// "where effort goes" split panel) — no chart-dense threshold or vega-settle
// wait needed, same as Delivery/People.
const REPOSITORIES_ROUTE = process.env.REPOSITORIES_ROUTE || "/report/legacy";
const REPOSITORIES_HASH = REPOSITORIES_ROUTE.startsWith("/report") ? "#repos" : "";

// Elements (Task R-P8): baseline captures the CURRENT monolith at
// `/report#elements` (mode read from the hash); the candidate run points the
// SAME states at `/elements` instead (no hash needed — it's its own page).
// `ELEMENTS_ROUTE=/elements` (env var) switches to the candidate; defaults to
// the monolith's `/report` (+ `#elements` hash) for the baseline. Same query-
// param contract as the other views (?p=/?slice=) works on both front-ends.
// Elements has NO Vega charts (a single per-element rollup table) — no
// chart-dense threshold or vega-settle wait needed, same as Delivery/People/
// Repositories.
const ELEMENTS_ROUTE = process.env.ELEMENTS_ROUTE || "/report/legacy";
const ELEMENTS_HASH = ELEMENTS_ROUTE.startsWith("/report") ? "#elements" : "";

// Traffic (Task R-P9): baseline captures the CURRENT monolith at `/report#usage`
// (mode read from the hash); the candidate run points the SAME states at
// `/traffic` instead (NOTE the route rename usage→traffic — migration spec's
// redirect table — no hash needed, it's its own page). `TRAFFIC_ROUTE=/traffic`
// (env var) switches to the candidate; defaults to the monolith's `/report`
// (+ `#usage` hash) for the baseline. Same query-param contract as the other
// views (?p=/?slice=) works on both front-ends. Traffic has NO Vega charts (the
// two-scenarios bar lists + the CSS clone/view traffic panel + external-
// contributor chips) — no chart-dense threshold or vega-settle wait needed, same
// as Delivery/People/Repositories/Elements.
const TRAFFIC_ROUTE = process.env.TRAFFIC_ROUTE || "/report/legacy";
const TRAFFIC_HASH = TRAFFIC_ROUTE.startsWith("/report") ? "#usage" : "";

// AI tools (Task R-P10, the LAST report view): baseline captures the CURRENT
// monolith at `/report#fabric` (mode read from the hash); the candidate run
// points the SAME states at `/ai-tools` instead (NOTE the route rename
// fabric→ai-tools — migration spec's redirect table — no hash needed, it's its
// own page). `AITOOLS_ROUTE=/ai-tools` (env var) switches to the candidate;
// defaults to the monolith's `/report` (+ `#fabric` hash) for the baseline.
// Same query-param contract as the other views (?p=/?slice=) works on both
// front-ends. AI tools has NO Vega charts (the AI-usage panel is a hand-rolled
// `.split` bar + a plain table; provenance/gears/tracker tables + the
// fabric-usage rollup + the per-bot table are all plain `.dt` tables) — no
// chart-dense threshold or vega-settle wait needed, same as Traffic/Elements.
const AITOOLS_ROUTE = process.env.AITOOLS_ROUTE || "/report/legacy";
const AITOOLS_HASH = AITOOLS_ROUTE.startsWith("/report") ? "#fabric" : "";

// The person dashboard lands after an async fetch (/api/person on the monolith,
// /api/report/person on the React route) — wait for the header card to paint
// before screenshotting, on top of capture.mjs's generic settle.
async function waitForPersonDashboard(page) {
  await page
    .waitForFunction(() => document.querySelector("#person-view .phead"), { timeout: 5000 })
    .catch(() => {});
}

// Vega charts (vega-embed) render asynchronously after the JSON payload
// lands — wait for every `.vl-panel` on the page to have painted an <svg>
// before screenshotting, on top of capture.mjs's generic network-idle/fetch-
// drain settle. Trend has five charts per state, more chart-heavy than
// Overview, so this extra guard keeps the shot deterministic.
async function waitForCharts(page) {
  await page
    .waitForFunction(
      () => {
        // Every chart is Recharts now, drawn into .ch (components/ui/chart), and a
        // dashboard panel mounts one into the .vl-panel container the server-rendered
        // preview leaves behind.
        const drawn = (sel) => Array.from(document.querySelectorAll(sel))
          .every((c) => c.querySelector(".recharts-surface"));
        return drawn(".ch") && drawn(".vl-panel");
      },
      { timeout: 5000 },
    )
    .catch(() => {});
}

/** Open every collapsible section, then wait for the charts inside them.
 *
 *  Flow keeps its board-movement views behind a closed <details>, so a plain
 *  screenshot of /flow never contained the CFD chart at all — which is how the
 *  Recharts swap for it came back as a 0.0000% diff on three shots while proving
 *  nothing about the chart. A route that hides content behind a disclosure needs
 *  a state where it is open, or the gate only guards what was already visible. */
async function openSectionsAndWait(page) {
  await page.evaluate(() => {
    document.querySelectorAll("details.flow-sec").forEach((d) => { d.open = true; });
  });
  await waitForCharts(page);
  await page.waitForTimeout(300);           // let the areas finish laying out
}

// Dashboard (Phase 2, widget-system): the pixel-gate for the dashboards-on-React
// swap. `frontend/visual/seed_dashboards.py` seeds a SHARED dashboard
// `dash_gate_allviz` (owner "gate") with one panel per viz (number/table/line/
// area/column/bar/pie, all pinned period=all → deterministic). The `/dashboard/<id>`
// route serves a shared dashboard to any viewer, so no auth header is needed.
// Baseline = the CURRENT server (Jinja + `.vl-panel`) render, reached via
// `?legacy=1` (which the WS2-T4 swap keeps as the monolith fallback — mirrors the
// report's `/report/legacy`); candidate = the React `<PanelRenderer>` render at the
// bare URL (env `DASHBOARD_ROUTE=/dashboard/dash_gate_allviz`). Chart-dense → the
// 1.5% chart tolerance + the vega-settle wait, like trend/flow.
const DASHBOARD_ROUTE = process.env.DASHBOARD_ROUTE || "/dashboard/dash_gate_allviz?legacy=1";

// Manage pages (Manage migration, 2026-07-24): the display pages hold pixel-identical,
// same as the report. Baseline = the CURRENT server Jinja render via `?legacy=1` (the
// route keeps it as a fallback, mirroring `/report/legacy`); candidate = the React route
// at the bare path (env `METRICS_ROUTE=/metrics`, etc). React pages fetch their JSON
// after mount, like /whats-new — capture.mjs's network-idle/fetch settle covers it, so
// no special setup. No Vega on these, so the strict default 0.1% threshold applies.
const METRICS_ROUTE = process.env.METRICS_ROUTE || "/metrics?legacy=1";
const VIEWS_ROUTE = process.env.VIEWS_ROUTE || "/views?legacy=1";
const MCP_ROUTE = process.env.MCP_ROUTE || "/mcp-info?legacy=1";
const DASHBOARDS_ROUTE = process.env.DASHBOARDS_ROUTE || "/dashboards?legacy=1";
const DATAHEALTH_ROUTE = process.env.DATAHEALTH_ROUTE || "/data-health?legacy=1";
const USAGE_ROUTE = process.env.USAGE_ROUTE || "/usage-insights?legacy=1";
const CALIBRATE_ROUTE = process.env.CALIBRATE_ROUTE || "/calibrate?legacy=1";
const IDENTITY_ROUTE = process.env.IDENTITY_ROUTE || "/identity?legacy=1";
const CONFIG_ROUTE = process.env.CONFIG_ROUTE || "/config?legacy=1";
const UPDATE_ROUTE = process.env.UPDATE_ROUTE || "/update?legacy=1";
const SETUP_ROUTE = process.env.SETUP_ROUTE || "/setup?legacy=1";
const SEMANTIC_ROUTE = process.env.SEMANTIC_ROUTE || "/semantic?legacy=1";
const SEMANTIC_ADV_ROUTE = process.env.SEMANTIC_ADV_ROUTE || "/semantic/advanced?legacy=1";
// The dashboard editor is owner-only. `frontend/visual/seed_dashboards.py` seeds an
// owner-resolvable dashboard `dash_gate_editor` (owner `demo-dev`, a person seeded in
// the local report.db), and this route sends the oauth2-proxy username header so the
// owner check passes on BOTH the ?legacy=1 baseline and the React candidate.
const DASHBOARD_EDITOR_ROUTE = process.env.DASHBOARD_EDITOR_ROUTE || "/dashboard/dash_gate_editor/edit?legacy=1";
const CHATLOG_ROUTE = process.env.CHATLOG_ROUTE || "/chat-log?legacy=1";

/** @type {RouteSpec[]} */
export const routes = [
  {
    id: "whats-new",
    path: "/whats-new",
    viewport: DEFAULT_VIEWPORT,
    // No setup needed: the changelog page is static content, no tabs/filters/modals.
  },
  {
    id: "overview",
    path: OVERVIEW_ROUTE,
    viewport: DEFAULT_VIEWPORT,
    // Default state: all-time, whole org — no query params needed (both the
    // monolith and the React route default to this).
  },
  {
    id: "overview-30d",
    path: `${OVERVIEW_ROUTE}?p=30d`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "overview-slice",
    path: `${OVERVIEW_ROUTE}?slice=${encodeURIComponent(OVERVIEW_SLICE)}`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "trend",
    path: `${TREND_ROUTE}${TREND_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    // Default state: all-time, whole org, auto granularity, company breakdown.
    setup: waitForCharts,
    threshold: CHART_THRESHOLD,
  },
  {
    id: "trend-worktype",
    path: `${TREND_ROUTE}?tdim=work_type${TREND_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    setup: waitForCharts,
    threshold: CHART_THRESHOLD,
  },
  {
    id: "trend-month",
    path: `${TREND_ROUTE}?tgran=month${TREND_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    setup: waitForCharts,
    threshold: CHART_THRESHOLD,
  },
  {
    id: "trend-30d-slice",
    path: `${TREND_ROUTE}?p=30d&slice=${encodeURIComponent(OVERVIEW_SLICE)}${TREND_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    setup: waitForCharts,
    threshold: CHART_THRESHOLD,
  },
  {
    id: "delivery",
    path: `${DELIVERY_ROUTE}${DELIVERY_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    // Default state: all-time, whole org.
  },
  {
    id: "delivery-30d",
    path: `${DELIVERY_ROUTE}?p=30d${DELIVERY_HASH}`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "delivery-slice",
    path: `${DELIVERY_ROUTE}?slice=${encodeURIComponent(OVERVIEW_SLICE)}${DELIVERY_HASH}`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "flow",
    path: `${FLOW_ROUTE}${FLOW_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    // Default state: all-time, whole org.
    setup: waitForCharts,
    threshold: CHART_THRESHOLD,
  },
  {
    id: "flow-30d",
    path: `${FLOW_ROUTE}?p=30d${FLOW_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    setup: waitForCharts,
    threshold: CHART_THRESHOLD,
  },
  {
    id: "flow-slice",
    path: `${FLOW_ROUTE}?slice=${encodeURIComponent(OVERVIEW_SLICE)}${FLOW_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    setup: waitForCharts,
    threshold: CHART_THRESHOLD,
  },
  {
    // The same page with every section expanded — the only state that contains the
    // cumulative-flow chart, the dwell tables and the rewind list.
    id: "flow-open",
    path: `${FLOW_ROUTE}${FLOW_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    setup: openSectionsAndWait,
    threshold: CHART_THRESHOLD,
  },
  {
    id: "people",
    path: `${PEOPLE_ROUTE}${PEOPLE_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    // Default state: all-time, whole org.
  },
  {
    id: "people-30d",
    path: `${PEOPLE_ROUTE}?p=30d${PEOPLE_HASH}`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "people-slice",
    path: `${PEOPLE_ROUTE}?slice=${encodeURIComponent(OVERVIEW_SLICE)}${PEOPLE_HASH}`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "person",
    path: `${PERSON_ROUTE}${PERSON_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    // Default state: no person selected → the "pick a person" hint.
  },
  {
    id: "person-selected",
    path: `${PERSON_ROUTE}?person=${PERSON_LOGIN}${PERSON_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    setup: waitForPersonDashboard,
  },
  {
    id: "repositories",
    path: `${REPOSITORIES_ROUTE}${REPOSITORIES_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    // Default state: all-time, whole org.
  },
  {
    id: "repositories-30d",
    path: `${REPOSITORIES_ROUTE}?p=30d${REPOSITORIES_HASH}`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "repositories-slice",
    path: `${REPOSITORIES_ROUTE}?slice=${encodeURIComponent(OVERVIEW_SLICE)}${REPOSITORIES_HASH}`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "elements",
    path: `${ELEMENTS_ROUTE}${ELEMENTS_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    // Default state: all-time, whole org.
  },
  {
    id: "elements-30d",
    path: `${ELEMENTS_ROUTE}?p=30d${ELEMENTS_HASH}`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "traffic",
    path: `${TRAFFIC_ROUTE}${TRAFFIC_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    // Default state: all-time, whole org.
  },
  {
    id: "traffic-30d",
    path: `${TRAFFIC_ROUTE}?p=30d${TRAFFIC_HASH}`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "traffic-slice",
    path: `${TRAFFIC_ROUTE}?slice=${encodeURIComponent(OVERVIEW_SLICE)}${TRAFFIC_HASH}`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "ai-tools",
    path: `${AITOOLS_ROUTE}${AITOOLS_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    // Default state: all-time, whole org.
  },
  {
    id: "ai-tools-30d",
    path: `${AITOOLS_ROUTE}?p=30d${AITOOLS_HASH}`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "ai-tools-slice",
    path: `${AITOOLS_ROUTE}?slice=${encodeURIComponent(OVERVIEW_SLICE)}${AITOOLS_HASH}`,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "elements-slice",
    path: `${ELEMENTS_ROUTE}?slice=${encodeURIComponent(OVERVIEW_SLICE)}${ELEMENTS_HASH}`,
    viewport: DEFAULT_VIEWPORT,
    // Sub-pixel text-wrap noise on the SHARED period/slice legend, verified
    // pixel-identical by eye (crop compared) and deterministic. The legend text,
    // box (1136px) and position are byte-identical to the monolith; but a slice
    // filter makes this the ONLY report page short enough to have no vertical
    // scrollbar, so its content width is 1440px (vs ~1425px with a scrollbar on
    // every other view). At exactly 1440px the legend's dense last line wraps on
    // a knife-edge, and the browser's text shaping lands ~2px differently between
    // the monolith's server-rendered legend and React's, lighting up ~1600px of
    // otherwise-identical glyph edges (0.13%). It is chrome, not element data:
    // the `elements`/`elements-30d` states (full table, strict 0.1%) still guard
    // all element CONTENT, so this looser bound doesn't reduce coverage. 0.2%
    // stays far tighter than the chart states' 1.5% and well below any real
    // regression (a changed table value is <0.02%). Always spot-check the diff PNG.
    threshold: 0.2,
  },
  {
    id: "dashboard",
    path: DASHBOARD_ROUTE,
    viewport: DEFAULT_VIEWPORT,
    setup: waitForCharts,
    threshold: CHART_THRESHOLD,
  },
  {
    id: "metrics",
    path: METRICS_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "views",
    path: VIEWS_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "mcp",
    path: MCP_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "dashboards-list",
    path: DASHBOARDS_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "data-health",
    path: DATAHEALTH_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "usage-insights",
    path: USAGE_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "calibrate",
    path: CALIBRATE_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "identity",
    path: IDENTITY_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "config",
    path: CONFIG_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "update",
    path: UPDATE_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "setup",
    path: SETUP_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "semantic",
    path: SEMANTIC_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "semantic-advanced",
    path: SEMANTIC_ADV_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
  {
    id: "dashboard-editor",
    path: DASHBOARD_EDITOR_ROUTE,
    viewport: DEFAULT_VIEWPORT,
    headers: { "X-Forwarded-Preferred-Username": "demo-dev" },
    // Panels render live previews (async fetch → a mounted chart) — wait for every
    // .vl-panel to paint an <svg>, like the dashboard view gate.
    setup: waitForCharts,
    threshold: CHART_THRESHOLD,
  },
  {
    id: "chatlog",
    path: CHATLOG_ROUTE,
    viewport: DEFAULT_VIEWPORT,
  },
];
