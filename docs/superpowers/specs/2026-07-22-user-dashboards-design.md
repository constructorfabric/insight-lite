# User-built dashboards (constructor + AI copilot) — design

**Date:** 2026-07-22
**Status:** approved (foundation slice) — pending implementation plan

## 1. Goal

Let users assemble their own dashboards from the report's existing building blocks,
via two interchangeable authoring modes over a shared, persisted spec:

- a **manual constructor** (pick components, bind data, arrange), and
- the **AI copilot** (describe it in words; the assistant emits the same spec).

Both produce/edit one `dashboard` spec; a renderer turns the spec into a live dashboard.

## 2. Why we are well-positioned

- `views_catalog` (`view_registry.py`) — a registry of visual components (KPI tiles,
  charts, chips, tables) with parameters and an `html_contract`, built to be
  reproducible outside the report.
- `metrics_catalog` (`metrics_registry.py`) — every metric with formula and unit.
- Read-only data tools (`tooldefs.py`) + the AI copilot (`chat_agent.py`) that already
  calls them and knows both catalogs.

A dashboard is therefore a **persisted spec**: a list of panels, each = a component
from `views_catalog` bound to a data source from `tooldefs`. The DB is the source of
truth (matches the self-serve product vision).

## 3. Decisions

| Decision | Choice |
|---|---|
| Authoring | Manual constructor **and** AI copilot, equal, over one shared spec |
| Data binding | **Catalog only** — metrics/tools; NO raw SQL persisted in a dashboard |
| Ownership | **Personal by default + shareable** (private → optional share) |
| Rendering | **Hybrid** — server renders panels with the existing Jinja view macros as HTML fragments; the client lays them out (the report's `/api/period` pattern) |
| Period & scope | **NOT stored in the spec.** They are live viewer controls (as in the report) applied to all panels; a panel may optionally `pin` an override |
| Extensibility | **Catalog-driven.** Components/metrics/tools are enumerated live from the catalogs; binding is generic per shape (`kind`). A new element added to a catalog appears automatically — no builder/renderer code per component |

## 4. Architecture & data flow

```
[Manual constructor]   [AI copilot]        ← slices 2 and 3 (later)
         \                 /
          → write ONE dashboard spec (JSON) via the CRUD API
                     ↓
              dashboard table (DB)
                     ↓
   GET /dashboard/<id>  →  page shell + the report's global scope/period controls
        per panel:  component (views_catalog) + data (tool from tooldefs, at the
                    viewer's current scope/period, unless the panel pins its own)
                 →  server renders via Jinja view macros → HTML fragments
                     ↓
        client arranges panels in a grid; changing scope/period refetches fragments
```

The **foundation slice (slice 1)** delivers: spec + validation + storage + CRUD API +
renderer. It is demoable by POSTing a spec and opening `/dashboard/<id>`. The two
editors are UIs on top of the same CRUD API.

## 5. Dashboard spec (the contract)

```json
{
  "title": "AI adoption — Insight",
  "panels": [
    { "id": "p1", "component": "kpi_tile", "title": "Commits",
      "source": { "tool": "contribution", "field": "totals.commits" } },

    { "id": "p2", "component": "line_chart", "title": "Commits over time",
      "source": { "tool": "trend", "params": { "dim": "company" } },
      "width": 2 },

    { "id": "p3", "component": "data_table", "title": "Last 7 days (fixed)",
      "source": { "tool": "contribution", "field": "by_company" },
      "pin": { "period": "7d", "scope": "org:your-org" } }
  ]
}
```

- `component` — a real view id from `views_catalog` (e.g. `kpi_tile`, `data_table`,
  `cat_table`, `line_chart`).
- `source` — a data tool from `tooldefs` (`tool` + optional `params`; `field` is a
  **dotted path** into the tool's JSON result, e.g. `totals.commits`).
- `width` — grid span. Layout is **ordered panels with a `width` span** (1..N of an
  N-column grid); no explicit row/col in slice 1.
- `pin` — **optional** deliberate override of the live scope/period for one panel
  (for comparison dashboards). Absent = the panel follows the viewer's global controls.
- **No dashboard-level `scope`/`period`, and no raw SQL.** Scope/period are runtime
  viewer state; bindings are catalog-only.

## 6. Persistence & ownership

Table `dashboard`:

| column | note |
|---|---|
| `id` | stable id |
| `owner_login` | resolved viewer login (server-side, as today) |
| `title` | |
| `visibility` | `'private'` (default) or `'shared'` |
| `spec` | JSON |
| `created_ts`, `updated_ts` | |

- Private dashboards are visible only to their owner.
- Sharing = set `visibility='shared'`; the `/dashboard/<id>` link is then readable by
  anyone with portal access. Only the owner edits.
- Fine-grained permissions and versioning are out of scope for slice 1.

## 7. Rendering: catalog-driven, bind by shape (no per-component code)

The builder, renderer, and AI must NOT hardcode a component list — new elements should
appear automatically from the catalogs. So binding is generic, keyed off each
component's shape, not its id:

- **Enumerate live.** The component palette is `views_catalog` filtered to
  dashboard-eligible entries; data sources are `tooldefs` tools and `metrics_catalog`
  metrics. Adding a metric or tool (a registry entry) makes it available with no
  builder change.
- **Bind by `kind`, one adapter per shape.** Each view already declares a `kind`
  (`tile | chart | table | primitive`). The renderer holds ONE generic adapter per kind
  that maps a `source` (tool result at `field`) into that kind's macro args:
  tile→scalar, chart→series, table→rows. A new component of an existing kind is
  auto-supported; only a genuinely new shape needs one new adapter (4 kinds today).
- **The mapping lives in the catalog, not the builder.** Each view entry gains a small
  machine-readable `binding` (what result shape it consumes and how to fill its
  required `params` from `source`) plus `dashboard: true|false` (a standalone panel vs a
  sub-part like `bar_cell`). A new component = one catalog entry → it appears in the
  palette, renders, and is usable by the AI, with zero builder/renderer code. This is a
  one-time enrichment of `views_catalog`; after it, growth is per-shape, not
  per-component.

`ref` (`tmpl:…::macro` or `fn:…`, already in the catalog) tells the renderer how to
invoke the component; `params` (already machine-readable) tell it which args are required.

Endpoints:
- `GET /dashboard/<id>` — full render (shell + global controls + all panels).
- `GET /api/dashboard/panel?...` — one panel fragment (live edit / re-slice refetch).
- CRUD for the spec (`POST`/`PUT`/`GET`/`DELETE /api/dashboard[/<id>]`) — both editors
  persist by writing the spec.

A **validator** rejects a spec whose component is not a dashboard-eligible
`views_catalog` entry, whose tool is not in `tooldefs`, or whose scope/period/pin is
malformed — before it is stored.

## 8. Scope boundaries (YAGNI)

**In slice 1 (foundation):**
- spec schema + validator
- `dashboard` table (personal + `shared` flag)
- CRUD API
- `GET /dashboard/<id>` server render + global scope/period controls + per-panel `pin`
- **catalog-driven rendering:** the four `kind` adapters (tile/chart/table/primitive)
  + enrich `views_catalog` entries with `binding` + `dashboard` flags. Every
  dashboard-eligible component is then available automatically — no per-component code.
  (Initial eligible set will naturally include `kpi_tile`, `data_table`, `cat_table`,
  `line_chart`, but they are not a hardcoded list.)
- basic sharing (`visibility='shared'` + link)

**Not in slice 1 (later slices):**
- manual drag-drop editor UI (slice 2)
- AI copilot authoring — extend the chat to emit/edit specs (slice 3)
- full component coverage
- fine-grained permissions, versioning, dashboard folders/tags

## 9. Testing

- Spec validator: valid spec passes; unknown component / unknown tool / malformed
  scope / malformed `pin` are rejected.
- Golden render: one render test per starter component (spec panel → expected HTML
  shape via the view macro).
- Data binding: a panel's `source` (tool + field/params) returns the expected numbers
  for a known window (grounded, matches the tool called directly).
- Ownership: owner sees/edits; `shared` is readable by others; a private dashboard is
  not readable by a different login.
- Live re-slice: changing scope/period refetches panel fragments; a pinned panel
  ignores the change.

## 10. Open questions / follow-ups

- Exact shape of the catalog `binding` descriptor (fields + how it names the
  `source→params` mapping) — to be pinned in the implementation plan; it is the one new
  concept the four `kind` adapters read.
- Slice 3 (AI copilot authoring): whether the assistant emits a full spec or
  incremental panel operations — deferred to that slice's design.
