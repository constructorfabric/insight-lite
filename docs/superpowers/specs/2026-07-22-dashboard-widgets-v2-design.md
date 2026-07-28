# Dashboard widgets v2 — BI-style: viz type + multi-measure

**Date:** 2026-07-22
**Status:** approved (user chose "мультиметрика + переключатель типа")

## Problem

The current dashboard widget model conflates *what data* with *how to show it*:
a panel is `{component, source:{tool, field}}`, so one measure is welded to exactly
one component. The user cannot (a) change the display type of a widget, or (b) put
several metrics on one chart. On top of that the line chart renders as a solid black
block on dashboard pages.

BI tools (DataLens/Tableau/Looker) separate the two axes: **data** (dimensions +
measures) from **visualization** (a swappable chart-type selector), and a chart can
carry **multiple measures** (and/or a split dimension → multiple series).

## Root cause of the black chart

Chart CSS (`.linechart .ax-hit{fill:transparent}`, `.linechart .lline{fill:none}`,
`.ldot`, `.ax-grid`, `.ax-x/.ax-y`) lives inline in `templates/report.j2:365-374`.
Dashboard pages (`dashboard.j2`, `dashboard_editor.j2`, preview-panel) never include
it, so the SVG hover-hit `<rect>`s and polylines fall back to the default SVG
`fill:black` and paint over the whole plot.

## Design (pragmatic BI subset — no JS libs, hand-rolled SVG)

### Widget spec v2

```
panel = { id, title, width,
          viz: "number"|"line"|"area"|"column"|"bar"|"pie"|"table",
          data: { tool, fields: ["commit_rows", "loc_rows", …] },  // 1..N
          pin? }
```

- `viz` is the user's display choice; `data.fields` is one or more measures.
- Legacy panels (`{component, source:{tool, field}}`) are **normalised on load** to
  `{viz, data:{tool, fields:[field]}}` — `kpi_tile→number`, `data_table→table`,
  `line_chart→line`. No migration of stored rows required; the resolver only sees v2.

### viz → render primitive (catalog stays the source of truth)

| viz        | primitive              | status        |
|------------|------------------------|---------------|
| number     | `kpi_tile`             | exists        |
| line       | `line_chart`           | exists (CSS fix) |
| area       | `line_chart` area_first| exists        |
| column     | `bar_chart` vertical   | **new SVG**   |
| bar        | `bar_chart` horizontal | **new SVG**   |
| pie        | `pie_chart`            | **new SVG**   |
| table      | `data_table`           | exists        |

Two new SVG renderers (`bar_chart`, `pie_chart`) registered in `view_registry` like
the existing chart fns. No scatter/tree/pivot/normalized-stacked (YAGNI).

### Data shape → allowed viz (compatibility, gated like BI)

Determined by the field's shape (from `_walk_fields`):
- **scalar** → `number` (1 field)
- **series** (trend fields: commit_rows, throughput.opened, …) → `line`, `area`;
  **multiple series fields overlay on one chart** (1..N fields, share `trend.dates`).
- **breakdown / table** (by_company, per-repo/element/person rows) → `bar`, `column`,
  `pie`, `table` (1 field; a numeric column keyed by the row label; `table` = all cols).

Multi-measure = the time-series overlay case (the explicit ask): line/area accept
several trend fields. Breakdown widgets stay single-source but gain viz switching
(table ⇄ bar ⇄ column ⇄ pie). Column/bar over a *time series* (grouped/time bars) is
intentionally out — it needs grouped-bar geometry we don't want yet.

### Resolver (`render_panel`)

- Normalise legacy → v2 first.
- Resolve `viz` → primitive + options.
- **series viz**: dig each field in `data.fields` from the trend result, concat into
  one `[{name, vals, color}]` series list (dedupe/label by field+series name), render
  once with the shared `dates`. area_first for `area`.
- **breakdown viz** (bar/column/pie): dig the field → rows; map row label→value; render
  bars/pie. `table` unchanged.
- **number**: dig the single scalar (first field), kpi_tile.
- Never raises (existing contract); tool allowlist unchanged (`_DASHBOARD_TOOLS`).

### Validation (`validate_spec`)

- Accept both legacy and v2 panels.
- v2: `viz` in the known set; `data.tool` in `_DASHBOARD_TOOLS`; `data.fields` a
  non-empty list of strings; number/pie/table require exactly one field (charts allow
  N). Keep the pin scope/period checks.

### Editor UI (BI-flavoured)

- **Viz-type selector** at the top of the picker (icon + label per type). Choosing a
  type filters which measures can be added (compatibility above).
- A **"Series / Measures" shelf**: chart types accept multiple compatible measures
  (chips, add/remove); number/table/pie accept one.
- Left measure list (grouped by category, searchable) feeds the shelf.
- Advanced tool+field entry stays (adds a raw field to the shelf).
- Live preview reflects the assembled v2 panel.

### CSS

Extract the chart rules into `shell.CHART_CSS` (superset incl. new `.barchart`,
`.piechart` classes) and inject on the dashboard view, editor, and preview. Report
keeps working; note the (small) duplication with report.j2's inline block.

## Out of scope

Full drag-and-drop shelves (X/Y/Y2/Colors), tidy per-tool datasets, secondary axis,
split-by-dimension as a first-class shelf, normalized/stacked/scatter/tree/pivot.
