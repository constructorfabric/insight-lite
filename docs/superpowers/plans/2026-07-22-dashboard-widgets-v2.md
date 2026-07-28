# Dashboard widgets v2 — viz type + multi-measure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.
> Steps use `- [ ]`. Branch `feat/dashboard-widgets-v2`. Spec:
> `docs/superpowers/specs/2026-07-22-dashboard-widgets-v2-design.md`.

**Goal:** Decouple a dashboard widget's *display type* from its *data* — a widget is a
swappable viz type (`number/line/area/column/bar/pie/table`) over one-or-more measures —
and fix the line chart rendering as a black block on dashboard pages.

**Architecture:** Panel spec v2 = `{id,title,width,viz,data:{tool,fields:[…]},pin?}`.
Legacy `{component,source:{tool,field}}` panels normalise to v2 on load. `viz` maps to an
existing/new catalog primitive. Two new SVG renderers (`bar_chart`, `pie_chart`). Chart
CSS extracted to `shell.CHART_CSS` and injected wherever charts render outside the report.

**Tech Stack:** Python stdlib http.server, Jinja2, inline vanilla JS, hand-rolled SVG.
Tests: unittest (`tests/test_dashboards.py`, `tests/test_render.py` if present).

**Compatibility (shape → allowed viz):** scalar→[number]; series→[line,area] (1..N
fields overlaid); table→[bar,column,pie,table] (1 field). No time-bars, no grouped bars.

---

## File structure
- `render.py` — add `_bar_chart_svg`, `_pie_chart_svg`; register as globals.
- `view_registry.py` — register `bar_chart`, `pie_chart` views (dashboard=True, bindings).
- `shell.py` — add `CHART_CSS` (chart rules incl. new `.barchart`/`.piechart`).
- `templates/dashboard.j2`, `templates/dashboard_editor.j2` — inject `chart_css`.
- `render.py` — `render_dashboard_page`/`render_dashboard_editor` pass `chart_css`.
- `server.py` — preview-panel wraps its fragment with CHART_CSS (once) so preview isn't black.
- `dashboards.py` — `_VIZ_PRIMITIVE`, `_SHAPE_VIZ`, `viz_options(shape)`, `_normalize_panel`,
  v2 `validate_spec`, v2 `render_panel`; extend `measures()`/`tool_fields()` to carry `shape`
  so the editor can gate viz.
- `templates/dashboard_editor.j2` — viz-type selector + multi-measure shelf.
- `changelog.py` — entry.

Follow existing conventions: `render_panel` never raises; tool allowlist `_DASHBOARD_TOOLS`
enforced inside; escaping via markupsafe; tests use a temp `REPORT_DB` like the others.

---

## Task W3-T1: fix the black chart — shared CHART_CSS

**Files:** Modify `shell.py`, `render.py`, `templates/dashboard.j2`,
`templates/dashboard_editor.j2`, `server.py`; Test: `tests/test_dashboards.py`.

- [ ] **Step 1 — failing test.** Add to the render/编辑 area of `tests/test_dashboards.py`:
```python
class ChartCssTest(unittest.TestCase):
    def test_dashboard_view_includes_chart_css(self):
        import render
        html = render.render_dashboard_page({"id": "x", "spec": {"title": "T", "panels": []}})
        self.assertIn(".linechart .ax-hit", html)   # transparent-fill rule present
        self.assertIn("fill:transparent", html.replace(" ", ""))
    def test_editor_includes_chart_css(self):
        import render
        html = render.render_dashboard_editor(
            {"id": "x", "spec": {"title": "T", "panels": []}, "visibility": "private"})
        self.assertIn(".linechart .ax-hit", html)
```
(Adjust `render_dashboard_page` kwargs to its real signature — grep it first.)

- [ ] **Step 2 — run** `.venv/bin/python -m unittest tests.test_dashboards.ChartCssTest -v` → FAIL.

- [ ] **Step 3 — implement.**
  - In `shell.py`, add a module constant `CHART_CSS` — copy the chart rules currently in
    `templates/report.j2` (the `.areachart,.linechart{…}` block through
    `.ax-hit:hover`), and append placeholders for the new primitives:
    ```
    CHART_CSS = """
    .areachart,.linechart,.barchart,.piechart{width:100%;height:auto;display:block}
    .linechart .lline{fill:none;stroke-width:1.6;vector-effect:non-scaling-stroke}
    .linechart .lfill{fill-opacity:.12}
    .linechart .ldot{opacity:.9}
    .areachart .ax-grid,.linechart .ax-grid,.barchart .ax-grid{stroke:var(--line);stroke-width:.5}
    .areachart .ax-y,.linechart .ax-y,.barchart .ax-y{fill:var(--mut);font-size:8px;text-anchor:end}
    .areachart .ax-x,.linechart .ax-x,.barchart .ax-x{fill:var(--mut);font-size:8px}
    .areachart .ax-hit,.linechart .ax-hit,.barchart .ax-hit{fill:transparent;cursor:help}
    .areachart .ax-hit:hover,.linechart .ax-hit:hover,.barchart .ax-hit:hover{fill:var(--ink);fill-opacity:.05}
    .barchart .bar{stroke:none}
    .barchart .bar-lbl{fill:var(--mut);font-size:8px}
    .piechart .slice{stroke:var(--panel);stroke-width:1}
    .piechart .pie-lbl{fill:var(--ink);font-size:8px}
    """
    ```
  - In `render.py`, have `render_dashboard_page` and `render_dashboard_editor` pass
    `chart_css=shell.CHART_CSS` into their templates.
  - In `templates/dashboard.j2` and `templates/dashboard_editor.j2`, inside the existing
    `<style>` block (where `base_css`/`shell_css` are injected), add `{{ chart_css|safe }}`.
  - In `server.py` preview-panel handler: prepend `"<style>%s</style>" % shell.CHART_CSS`
    to the returned fragment HTML (so the modal preview isn't black either). Grep the
    handler for where it builds the response; wrap once.

- [ ] **Step 4 — run** the ChartCssTest → PASS; full suite `… | tail -3` → OK. LIVE check:
  render a dashboard with one `line_chart` panel and grep the HTML for `fill:transparent`
  and absence of a bare `<rect …/>` without a class inside `.linechart`.

- [ ] **Step 5 — commit** `git add -A && git commit -m "dashboards: inject chart CSS on dashboard pages (fix black line chart)"`

---

## Task W3-T2: new SVG renderers — bar_chart + pie_chart

**Files:** Modify `render.py`, `view_registry.py`; Test: `tests/test_dashboards.py`.

- [ ] **Step 1 — failing test.**
```python
class NewChartRenderersTest(unittest.TestCase):
    def test_bar_chart_svg(self):
        import render
        svg = str(render._bar_chart_svg(
            [{"label": "A", "value": 3}, {"label": "B", "value": 7}], unit="commits"))
        self.assertIn("<svg", svg); self.assertIn("barchart", svg)
        self.assertIn("class=\"bar\"", svg)
        self.assertEqual(str(render._bar_chart_svg([], unit="")), "")   # empty → ""
    def test_pie_chart_svg(self):
        import render
        svg = str(render._pie_chart_svg([{"label": "A", "value": 3}, {"label": "B", "value": 1}]))
        self.assertIn("<svg", svg); self.assertIn("piechart", svg)
        self.assertIn("slice", svg)
        self.assertEqual(str(render._pie_chart_svg([])), "")
    def test_bar_and_pie_registered_dashboard_views(self):
        import view_registry
        names = {v["name"] for v in view_registry.dashboard_views()}
        self.assertIn("bar_chart", names); self.assertIn("pie_chart", names)
```

- [ ] **Step 2 — run** `.venv/bin/python -m unittest tests.test_dashboards.NewChartRenderersTest -v` → FAIL.

- [ ] **Step 3 — implement** in `render.py` (mirror `_line_chart_svg`/`_stacked_area_svg`
  geometry + `from markupsafe import Markup, escape`; use `_num` for values; palette via
  `_element_color(label)` for stable colours):
```python
def _bar_chart_svg(rows, unit="", horizontal=False):
    """Categorical bar chart. rows=[{label, value, color?}]. horizontal=True → bars grow
    right (labels at left); else vertical columns (labels under). Returns SVG Markup;
    '' when no rows. No client JS."""
    from markupsafe import Markup, escape
    rows = [r for r in (rows or []) if isinstance(r.get("value"), (int, float))]
    if not rows:
        return Markup("")
    fmt = _num
    top = max((r["value"] for r in rows), default=0) or 1
    W, H = 760.0, max(90.0, 22.0 * len(rows) + 30) if horizontal else 200.0
    L, R, T, B = (150.0, 46.0, 8.0, 8.0) if horizontal else (46.0, 10.0, 12.0, 40.0)
    pw, ph = W - L - R, H - T - B
    parts = ['<svg class="barchart" viewBox="0 0 %g %g" role="img">' % (W, H)]
    col = lambda r: escape(r.get("color") or _element_color(r["label"]))
    if horizontal:
        bh = ph / len(rows)
        for i, r in enumerate(rows):
            y = T + i * bh + bh * 0.15
            w = round(pw * (r["value"] / top), 1)
            parts.append('<rect class="bar" x="%g" y="%g" width="%g" height="%g" fill="%s"><title>%s</title></rect>'
                         % (L, y, w, bh * 0.7, col(r), escape("%s  %s" % (r["label"], fmt(r["value"])))))
            parts.append('<text class="bar-lbl" x="%g" y="%g" text-anchor="end">%s</text>'
                         % (L - 6, y + bh * 0.45, escape(str(r["label"]))))
            parts.append('<text class="bar-lbl" x="%g" y="%g">%s</text>'
                         % (L + w + 3, y + bh * 0.45, escape(fmt(r["value"]))))
    else:
        bw = pw / len(rows)
        y0 = T + ph
        for i, r in enumerate(rows):
            x = L + i * bw + bw * 0.15
            h = round(ph * (r["value"] / top), 1)
            parts.append('<rect class="bar" x="%g" y="%g" width="%g" height="%g" fill="%s"><title>%s</title></rect>'
                         % (x, y0 - h, bw * 0.7, h, col(r), escape("%s  %s" % (r["label"], fmt(r["value"])))))
            parts.append('<text class="ax-x" x="%g" y="%g" text-anchor="middle">%s</text>'
                         % (x + bw * 0.35, H - 6, escape(str(r["label"])[:14])))
    parts.append("</svg>")
    return Markup("".join(parts))


def _pie_chart_svg(rows, unit=""):
    """Pie/donut chart. rows=[{label, value, color?}]. Returns SVG Markup; '' when the
    values sum to 0. Legend to the right. No client JS."""
    from markupsafe import Markup, escape
    import math
    rows = [r for r in (rows or []) if isinstance(r.get("value"), (int, float)) and r["value"] > 0]
    total = sum(r["value"] for r in rows)
    if not rows or total <= 0:
        return Markup("")
    W, H = 320.0, 200.0
    cx, cy, rad = 100.0, 100.0, 82.0
    parts = ['<svg class="piechart" viewBox="0 0 %g %g" role="img">' % (W, H)]
    ang = -math.pi / 2
    for i, r in enumerate(rows):
        frac = r["value"] / total
        a2 = ang + frac * 2 * math.pi
        large = 1 if frac > 0.5 else 0
        x1, y1 = cx + rad * math.cos(ang), cy + rad * math.sin(ang)
        x2, y2 = cx + rad * math.cos(a2), cy + rad * math.sin(a2)
        col = escape(r.get("color") or _element_color(r["label"]))
        parts.append('<path class="slice" d="M%g %g L%g %g A%g %g 0 %d 1 %g %g Z" fill="%s"><title>%s</title></path>'
                     % (cx, cy, round(x1, 1), round(y1, 1), rad, rad, large,
                        round(x2, 1), round(y2, 1), col,
                        escape("%s  %s (%.0f%%)" % (r["label"], _num(r["value"]), frac * 100))))
        ly = 18 + i * 16
        parts.append('<rect class="slice" x="210" y="%g" width="10" height="10" fill="%s"/>' % (ly, col))
        parts.append('<text class="pie-lbl" x="224" y="%g">%s</text>'
                     % (ly + 9, escape("%s (%.0f%%)" % (str(r["label"])[:16], frac * 100))))
        ang = a2
    parts.append("</svg>")
    return Markup("".join(parts))
```
  Register globals near `env.globals["line_chart"] = _line_chart_svg`:
  `env.globals["bar_chart"] = _bar_chart_svg` and `env.globals["pie_chart"] = _pie_chart_svg`.
  In `view_registry.py`, add two `view(...)` entries after `stacked_area`, kind="chart",
  group="charts", `ref="fn:render._bar_chart_svg"` / `_pie_chart_svg`, `dashboard=True`,
  `binding={"shape": "table", "rows_param": "rows"}`, params documenting `rows`
  (`[{label,value,color}]`), `unit`, and (bar) `horizontal`. Keep copy generic (no org names).

- [ ] **Step 4 — run** NewChartRenderersTest → PASS; full suite OK. Eyeball:
  `.venv/bin/python -c "import render; print(str(render._pie_chart_svg([{'label':'x','value':2},{'label':'y','value':1}]))[:200])"`.

- [ ] **Step 5 — commit** `git add render.py view_registry.py tests/test_dashboards.py && git commit -m "dashboards: bar_chart + pie_chart SVG renderers"`

---

## Task W3-T3: spec v2 — normalize, validate, resolve

**Files:** Modify `dashboards.py`; Test: `tests/test_dashboards.py`.

- [ ] **Step 1 — failing tests.**
```python
class WidgetV2Test(unittest.TestCase):
    def test_normalize_legacy_panel(self):
        p = dashboards._normalize_panel(
            {"id": "p1", "component": "kpi_tile",
             "source": {"tool": "contribution", "field": "totals.commits"}, "title": "C"})
        self.assertEqual(p["viz"], "number")
        self.assertEqual(p["data"], {"tool": "contribution", "fields": ["totals.commits"]})
    def test_normalize_line_chart_legacy(self):
        p = dashboards._normalize_panel(
            {"id": "p", "component": "line_chart", "source": {"tool": "trend", "field": "commit_rows"}})
        self.assertEqual(p["viz"], "line")
        self.assertEqual(p["data"]["fields"], ["commit_rows"])
    def test_v2_panel_passthrough(self):
        v2 = {"id": "p", "viz": "area", "data": {"tool": "trend", "fields": ["commit_rows", "loc_rows"]}}
        self.assertEqual(dashboards._normalize_panel(v2)["viz"], "area")
    def test_validate_v2_spec(self):
        ok, err = dashboards.validate_spec(
            {"title": "D", "panels": [
                {"id": "p", "viz": "line", "data": {"tool": "trend", "fields": ["commit_rows"]}}]})
        self.assertTrue(ok, err)
    def test_validate_rejects_bad_viz(self):
        ok, _ = dashboards.validate_spec(
            {"title": "D", "panels": [{"id": "p", "viz": "sankey", "data": {"tool": "trend", "fields": ["x"]}}]})
        self.assertFalse(ok)
    def test_validate_rejects_multi_field_number(self):
        ok, _ = dashboards.validate_spec(
            {"title": "D", "panels": [{"id": "p", "viz": "number",
                                        "data": {"tool": "contribution", "fields": ["a", "b"]}}]})
        self.assertFalse(ok)
    def test_validate_rejects_sql_tool_v2(self):
        ok, _ = dashboards.validate_spec(
            {"title": "D", "panels": [{"id": "p", "viz": "table", "data": {"tool": "sql_query", "fields": ["x"]}}]})
        self.assertFalse(ok)
    def test_render_multi_series_line(self):
        html = dashboards.render_panel(
            {"id": "p", "viz": "line", "title": "Trend",
             "data": {"tool": "trend", "fields": ["commit_rows", "loc_rows"]}}, "", "all")
        self.assertNotIn("dp-err", html); self.assertIn("<svg", html)
    def test_render_legacy_still_works(self):
        html = dashboards.render_panel(
            {"id": "p", "component": "kpi_tile", "title": "C",
             "source": {"tool": "contribution", "field": "totals.commits"}}, "", "all")
        self.assertNotIn("dp-err", html)
    def test_viz_options_by_shape(self):
        self.assertEqual(dashboards.viz_options("scalar"), ["number"])
        self.assertEqual(dashboards.viz_options("series"), ["line", "area"])
        self.assertIn("pie", dashboards.viz_options("table"))
```

- [ ] **Step 2 — run** `.venv/bin/python -m unittest tests.test_dashboards.WidgetV2Test -v` → FAIL.

- [ ] **Step 3 — implement** in `dashboards.py`:
```python
_VIZ_PRIMITIVE = {
    "number": ("kpi_tile", {}),
    "line":   ("line_chart", {"area_first": False}),
    "area":   ("line_chart", {"area_first": True}),
    "column": ("bar_chart", {"horizontal": False}),
    "bar":    ("bar_chart", {"horizontal": True}),
    "pie":    ("pie_chart", {}),
    "table":  ("data_table", {}),
}
_SHAPE_VIZ = {"scalar": ["number"], "series": ["line", "area"],
              "table": ["bar", "column", "pie", "table"]}
_MULTI_FIELD_VIZ = {"line", "area"}          # the rest are single-field
_LEGACY_COMPONENT_VIZ = {"kpi_tile": "number", "data_table": "table",
                         "line_chart": "line", "stacked_area": "area"}


def viz_options(shape: str) -> list:
    return list(_SHAPE_VIZ.get(shape, []))


def _normalize_panel(panel: dict) -> dict:
    """Return a v2 panel {id,title,width,viz,data:{tool,fields},pin?}. A v2 panel
    passes through; a legacy {component,source:{tool,field}} panel is mapped."""
    if not isinstance(panel, dict):
        return {}
    if "viz" in panel and isinstance(panel.get("data"), dict):
        return panel
    comp = panel.get("component")
    src = panel.get("source") or {}
    fields = [src["field"]] if src.get("field") else []
    out = {"id": panel.get("id"), "title": panel.get("title"),
           "width": panel.get("width", 2),
           "viz": _LEGACY_COMPONENT_VIZ.get(comp, "table"),
           "data": {"tool": src.get("tool"), "fields": fields}}
    if panel.get("pin"):
        out["pin"] = panel["pin"]
    return out
```
  - Rewrite `validate_spec` to normalise each panel then check: `viz in _VIZ_PRIMITIVE`;
    `data.tool in _DASHBOARD_TOOLS`; `data.fields` a non-empty `list[str]`; if
    `viz not in _MULTI_FIELD_VIZ` then `len(fields) == 1`; keep the pin scope/period regex
    checks. Keep the existing legacy tests passing (they build legacy panels — normalise
    handles them). Keep title-required and panels-is-list checks.
  - Rewrite `_render_panel` to: `panel = _normalize_panel(panel)`; guard
    `data.tool in _DASHBOARD_TOOLS` (else `dp-err … tool not allowed`); call the tool once;
    look up `prim, opts = _VIZ_PRIMITIVE[viz]`; then:
    - **number**: `_dig(result, fields[0])` → kpi_tile (as today).
    - **line/area**: for each field, `_dig`+`_auto_series`, concat; assign colours from a
      palette cycling `_ELEM_PALETTE`; render `line_chart` with `series`, `dates`,
      `area_first=opts["area_first"]`. `dp-err` if no series / no dates.
    - **bar/column/pie**: `rows = _dig(result, fields[0])` (list of dicts); build
      `[{label, value}]` via a new `_auto_bars(rows)` (first text col = label, first numeric
      col = value; skip rows without a numeric); render `bar_chart`(horizontal from opts)
      or `pie_chart`. `dp-err` if no rows.
    - **table**: `data_table` as today (`_auto_columns` + rows).
    Reuse `_render_component(view, kwargs)` with the primitive's view spec
    (`_dashboard_components()[prim]`). Keep the whole thing inside the never-raises wrapper.
  - Add `_auto_bars(rows)`:
```python
def _auto_bars(rows) -> list:
    rows = rows if isinstance(rows, list) else []
    if not rows or not isinstance(rows[0], dict):
        return []
    label_key = next((k for k, v in rows[0].items() if isinstance(v, str)), None)
    value_key = next((k for k, v in rows[0].items() if isinstance(v, (int, float)) and not isinstance(v, bool)), None)
    if not value_key:
        return []
    out = []
    for r in rows:
        v = r.get(value_key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append({"label": str(r.get(label_key, "—")), "value": v})
    return out
```

- [ ] **Step 4 — run** WidgetV2Test → PASS; **full suite** OK (the legacy panel tests must
  still pass via normalisation). If a legacy test asserted on `component`/`source` in a
  rendered panel, update it only if it checked internal spec shape, not output.

- [ ] **Step 5 — commit** `git add dashboards.py tests/test_dashboards.py && git commit -m "dashboards: widget spec v2 (viz + multi-field) with legacy normalisation"`

---

## Task W3-T4: editor — viz selector + multi-measure shelf

**Files:** Modify `dashboards.py` (measures/tool_fields carry `shape`), `server.py`
(measures payload already bundles), `templates/dashboard_editor.j2`; Test:
`tests/test_dashboards.py`.

- [ ] **Step 1 — failing test.**
```python
    def test_measures_carry_shape_for_gating(self):
        ms = dashboards.measures()
        self.assertTrue(all("shape" in m for m in ms))     # already true; assert it stays
    def test_editor_has_viz_selector(self):
        import render
        html = render.render_dashboard_editor(
            {"id": "x", "spec": {"title": "T", "panels": []}, "visibility": "private"})
        for needle in ("wp-viz", "data-viz", "wp-series", "viz_options"):
            # markup/JS hooks for the type selector + series shelf
            pass
        self.assertIn("wp-viz", html)
        self.assertIn("wp-series", html)
```
(The `measures()`/`tool_fields()` items already include `shape`/`component`; confirm and
keep. No backend shape change needed beyond ensuring `shape` is present — it is.)

- [ ] **Step 2 — run** → FAIL on the editor markup asserts.

- [ ] **Step 3 — implement** in `templates/dashboard_editor.j2` (reuse the existing modal;
  this replaces the single "Show as" `.pseg` with a richer flow — keep it vanilla JS, no libs):
  - **Viz selector** `#wp-viz`: a row of buttons, one per viz type, each with a small glyph
    and label (reuse text glyphs: number `123`, line `∿`, area `◺`, column `▮`, bar `▬`,
    pie `◔`, table `▦`). Data-attr `data-viz="line"` etc. Selecting a viz sets the active
    type; types incompatible with the current selection are disabled.
  - **Series/measures shelf** `#wp-series`: chips for the chosen measures with a remove (×).
    Clicking a measure row in the left list **adds** it to the shelf when the active viz is
    multi-field (`line`/`area`) and the measure's `shape==="series"`; for single-field viz
    it **replaces** the shelf's single chip. Left-list rows whose `shape` is incompatible
    with the active viz are dimmed/disabled.
  - Derive the compatible viz set from the FIRST chosen measure's shape (mirror
    `_SHAPE_VIZ` in a JS const `SHAPE_VIZ = {scalar:["number"], series:["line","area"],
    table:["bar","column","pie","table"]}`). When the shelf is empty, all viz shown but
    adding a measure narrows to its shape's set and auto-picks a default
    (series→line, table→table, scalar→number).
  - `currentPanel()` now emits **v2**: `{id, title, width, viz, data:{tool, fields:[…]}}`.
    All chips must share one `tool` (enforce: adding a measure from a different tool is
    rejected with a small inline note; simplest is to require same tool as the first chip).
  - Advanced tool+field still adds a raw field to the shelf (as a chip; shape unknown →
    treat as its tool's field entry; default viz table unless the field is a known series).
  - Keep search, category groups, width, title, live preview (`/api/dashboard/preview-panel`
    already accepts a `panel` — send the v2 panel; the resolver normalises/renders it).
  - Update the panel LIST row summary (bottom of the editor) to read from v2
    (`p.viz` + `p.data.tool`/`fields.join('+')`) with a legacy fallback
    (`p.component`/`p.source`).

- [ ] **Step 4 — verify:** `.venv/bin/python -c "import server, render"` clean; full suite OK.
  Render the editor and grep for `wp-viz`, `wp-series`, `SHAPE_VIZ`, and that
  `currentPanel` builds `data:{tool,fields`. Paste greps. (Interactive add/remove is
  browser-only; the resolver + endpoints are unit-tested.)

- [ ] **Step 5 — commit** `git add dashboards.py templates/dashboard_editor.j2 tests/test_dashboards.py && git commit -m "dashboards: editor viz-type selector + multi-measure shelf (v2)"`

---

## Task W3-T5: changelog + full suite

- [ ] **Step 1** — In `changelog.py`, prepend to the top `2026-07-22` block:
```python
{"type": "improvement", "title": "Choose how each widget looks — and combine metrics",
 "detail": "Dashboard widgets now separate the metric from how it's shown: pick a "
           "display type (number, line, area, column, bar, pie, table) and switch it "
           "any time. Time-series widgets can carry several metrics on one chart. Also "
           "fixes charts that rendered as a solid block on dashboards."},
```
- [ ] **Step 2** — Full suite `.venv/bin/python -m unittest discover -s tests -q 2>&1 | tail -3` → OK.
- [ ] **Step 3 — commit** `git add changelog.py && git commit -m "dashboards: changelog for widgets v2"`

---

## Notes
- Keep `render_panel` never-raising; every degrade path returns a small `dp-err` tile.
- Tool allowlist (`_DASHBOARD_TOOLS`) enforced in BOTH `validate_spec` and `_render_panel`
  after normalisation — no raw SQL, ever.
- Escape every label/value that reaches SVG/HTML (markupsafe in Python; `textContent` in JS).
- Colours: reuse `render._element_color` / `_ELEM_PALETTE`; never invent org/element names.
- Universal copy: viz labels and hints stay generic; real names come from data at runtime.
- No deploy inside the plan; merge + deploy after the final whole-slice review (confirm first).
