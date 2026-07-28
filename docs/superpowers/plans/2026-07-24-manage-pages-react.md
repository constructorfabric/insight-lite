# Manage Pages → React Migration Plan

> **For agentic workers:** applies the proven report-migration pattern (JSON endpoint +
> `entries/<x>.tsx` + `pages/<X>.tsx` + Vite input + `render_spa_page` + sidebar). Steps
> use checkbox (`- [ ]`) tracking. Executed INLINE this session (org spend cap → no subagents).

**Goal:** Migrate the entire Manage section from server-Jinja + inline vanilla JS to React,
finishing the "whole frontend → React" initiative.

**Two modes (user decision 2026-07-24):**
- **Display pages = pixel-identical** — same Playwright screenshot-diff gate as the report
  (baseline = current server render via `?legacy=1`, candidate = React route, 0-diff).
- **Interactive editors = allowed to IMPROVE UX** — React rewrite with freedom to change
  validation/state/interaction. NO auto pixel-gate; verified by behavioural + manual
  screenshot review. Keep the server Jinja editor reachable at `?legacy=1` as fallback.

**Architecture:** unchanged from the report migration — Python-only client-render MPA,
server shell + `<div id=root>` + Vite bundle + JSON APIs. `render.render_spa_page(entry,
active, title, ...)`; new Vite entries in `frontend/vite.config.ts`; server routes gain a
`?legacy=1` branch that keeps the existing render fn.

**Tech Stack:** React 19 + Vite + TS (`frontend/`), Python stdlib http.server, SQLite.

---

## Page inventory

| Route | Render fn (server) | Lines | Mode | Notes |
|---|---|---|---|---|
| `/metrics` | `metrics_catalog.render_page` | 88 | **display** | catalog; search/filter/expand JS |
| `/views` | `views_catalog.render_page` | 93 | **display** | catalog; similar JS |
| `/data-health` | `datahealth.render_page(model)` | — | **display** | trust surface + dataset stats |
| `/mcp-info` | `server.mcp_page()` | @993 | **display** | static-ish info page |
| `/usage-insights` | `server.usage_page()` | @532 | **display** | analytics (may include charts → `vega=`) |
| `/dashboards` | inline (`store.list_dashboards`) | @3522 | **display** | dashboard list + links |
| `/config` | `configstore.render_page` | 424 | **editor** | config editor form |
| `/identity` | `directory.render_page` | 277 | **editor** | roster editor + concurrency token |
| `/semantic`(+`/advanced`) | `semantic_editor.render_wizard_page` / `render_page` | 417 | **editor** | taxonomy wizard + advanced |
| `/setup` | `server.setup_html()` | — | **editor** | first-run wizard (token, collect) |
| `/update` | `server.portal_html()` | @329 | **editor** | collect portal + job polling |
| `/calibrate` | `calibrate.render_page(user)` | 193 | **editor** | calibration form |
| `/dashboard/<id>/edit` | dashboard editor | — | **editor** | E1 preview island already scoped (WS2-T5) |
| `/chat-log` | `server.chat_log_page()` | @789 | display (unlinked) | URL-only; do last / optional |

## Workstream A — Display pages (pixel-gate)

Each page, repeat the report recipe:
1. JSON endpoint `/api/manage/<x>.json` → `render.<x>_json()` returning the page's data.
2. `frontend/src/pages/<X>.tsx` reproducing the server DOM verbatim (classes/markup).
3. `frontend/src/entries/<x>.tsx` mounting it into `#root`.
4. Add the entry to `frontend/vite.config.ts` `rollupOptions.input`.
5. Port inline-JS behaviours (search filter, expand/collapse, jump-nav) — either inside
   the React component or as a shared `shell.*_JS` document listener (mirror DRILL_JS/SORT_JS).
6. Server route: `if legacy → <existing render_page>` else `render_spa_page("<x>", "<mode>", title)`.
7. Add a gate route + state to `frontend/visual/routes.mjs`; capture baseline (`?legacy=1`)
   + candidate back-to-back; drive `diff.mjs` to 0 (document any sub-pixel threshold).
8. `npm run build`; run the gate; commit per page.

Order (simplest → hardest): metrics → views → mcp-info → dashboards → data-health → usage-insights.

## Workstream B — Interactive editors (React rewrite, UX freedom)

No pixel-gate. Per editor:
1. JSON read + write endpoints (`GET /api/manage/<x>.json`, `POST /api/manage/<x>` …) —
   reuse existing POST handlers where present; add read endpoints where the data was
   inlined into HTML.
2. React page with real form state/validation. Keep concurrency tokens where they exist
   (identity). Preserve all existing POST semantics (server stays source of truth).
3. Server route gains `?legacy=1` → old Jinja editor (fallback, not removed).
4. Behavioural verification via the browser preview (fill/submit/poll) + a manual
   screenshot for the record. Add HTTP tests for the endpoints.
5. Changelog entry; commit per editor.

Order: config → calibrate → identity → update → semantic → setup → dashboard editor.

## Cross-cutting

- Changelog "What's new" entry per user-facing change (global rule).
- Every commit message English; stage to a file for `git commit -F <path>` (Cyrillic hook).
- Keep `?legacy=1` fallbacks for ALL pages this pass (don't delete Jinja yet).
- Sidebar `shell.MODES` unchanged (routes stay the same); only the handler branches.
- Full `pytest` suite green + `npm run build` before each commit.
