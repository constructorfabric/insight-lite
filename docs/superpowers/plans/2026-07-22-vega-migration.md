# Dashboard charts → Vega-Lite migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.
> Steps use `- [ ]`. Branch `feat/vega-migration`.

**Goal:** Replace the hand-rolled dashboard **chart** renderers (line/area/column/bar/pie)
with client-rendered **Vega-Lite**, themed to our design tokens, so new chart types and
future dimension-slicing come nearly free — while KPI tiles and tables stay server HTML.

**Prototype outcome (already validated):** Vega-Lite fully themes to our tokens (palette,
axes, grid, legend, fonts, donut) and looks on-brand — often cleaner than the hand-rolled
SVG. Bundle ≈250 KB gzip. No CSP in the app, so Vega's default (Function-based) eval is fine.

## Design decisions
- **Scope: dashboards only.** The report's own charts (`report.j2` uses `line_chart` /
  `stacked_area`) keep the hand-rolled SVG — untouched. `_line_chart_svg` /
  `_stacked_area_svg` STAY. Only the dashboard-only `_bar_chart_svg` / `_pie_chart_svg`
  (added this week) are retired.
- **Client-side render** via `vega-embed`, bundle vendored **same-origin** (no runtime CDN —
  supply-chain rule). Server-side `vl-convert` for PDF is a later, separate add-on.
- **Which vizzes become Vega:** `line, area, column, bar, pie` → a VL spec rendered client-
  side. `number` → `kpi_tile` HTML and `table` → `data_table` HTML stay exactly as today.
- **Render contract preserved:** `render_panel(...)` still returns an HTML string. For a chart
  viz it returns a container `<div class="vl-panel"><script type="application/json"
  class="vl-spec">{spec}</script></div>`; a shared `hydrateVega(root)` on the view + editor
  pages finds these and calls `vegaEmbed`. Panels are already fetched-then-innerHTML'd, so
  hydration runs right after injection. (innerHTML doesn't execute the JSON script — it's
  inert data, parsed by hydrateVega.)
- **Theming = our design:** one shared VL `config` built from `shell.BASE_CSS` tokens, plus a
  colour scale seeded from `render._element_color` so companies/elements keep stable colours
  across charts (matching the hand-rolled behaviour).
- **Security:** the spec JSON is embedded in a `<script type="application/json">` block with
  `<` escaped (`</` → `<\/`) — same convention as the editor's `spec_json`. Data values reach
  the SVG through Vega (which sets text via the DOM, not innerHTML), so no XSS via labels. Tool
  allowlist unchanged.

## File structure
- `assets/vega/{vega,vega-lite,vega-embed}.min.js` — vendored bundle (committed).
- `server.py` — serve `/assets/vega/<name>.min.js` (mirror the font route).
- `shell.py` — `VEGA_SCRIPTS` (the three `<script src>` tags) + tooltip/vl CSS in `CHART_CSS`.
- `vega_spec.py` (new) — `vega_config()`, `color_scale(labels)`, `build_spec(viz, result, fields, title)`.
- `dashboards.py` — `_render_panel` chart branches emit the `vl-panel` container via
  `vega_spec.build_spec`; number/table unchanged; retire `_VIZ_PRIMITIVE` chart→component rows.
- `templates/dashboard.j2`, `templates/dashboard_editor.j2` — load `VEGA_SCRIPTS`; add
  `hydrateVega`; call it after panel/preview injection.
- `render.py`, `view_registry.py` — remove `_bar_chart_svg`/`_pie_chart_svg` + their views.
- `changelog.py`, `tests/test_dashboards.py`, `tests/test_vega_spec.py` (new).

---

## Task M-T1: vendor the Vega bundle + serve it + load helper

**Files:** add `assets/vega/*.min.js`; Modify `server.py`, `shell.py`,
`templates/dashboard.j2`, `templates/dashboard_editor.j2`; Test: `tests/test_dashboards.py`.

- [ ] **Step 1 — assets.** The three minified files already exist at `scratch_vega/vega.min.js`,
  `scratch_vega/vega-lite.min.js`, `scratch_vega/vega-embed.min.js` (vendored downloads). Move
  them to `assets/vega/`. (If missing, download: `curl -sSL -o assets/vega/vega.min.js
  https://cdn.jsdelivr.net/npm/vega@5/build/vega.min.js` and the `vega-lite@5` / `vega-embed@6`
  equivalents.) `git add assets/vega/*.min.js` (committed, same-origin).
- [ ] **Step 2 — serve.** In `server.py`, next to the `/assets/*.woff2` branch
  (~line 2051), add a branch for `path.startswith("/assets/vega/") and path.endswith(".min.js")`:
  resolve `ROOT / "assets" / "vega" / <basename>`, reject path traversal (basename only, must be
  one of the three known names), 404 if absent, else send with
  `Content-Type: application/javascript` + `Cache-Control: public, max-age=31536000, immutable`.
- [ ] **Step 3 — load helper.** In `shell.py`, add
  `VEGA_SCRIPTS = ('<script src="/assets/vega/vega.min.js"></script>'
  '<script src="/assets/vega/vega-lite.min.js"></script>'
  '<script src="/assets/vega/vega-embed.min.js"></script>')`. Include `{{ vega_scripts|safe }}`
  in `dashboard.j2` and `dashboard_editor.j2` (pass `vega_scripts=shell.VEGA_SCRIPTS` from the
  two render fns in `render.py`), just before the page's own `<script>`.
- [ ] **Step 4 — failing test first, then pass.** Add an HTTP test (reuse the endpoint harness):
  `GET /assets/vega/vega-lite.min.js` → 200 and `application/javascript`; a bogus name
  `GET /assets/vega/evil.min.js` → 404. Confirm the two dashboard pages contain
  `/assets/vega/vega-embed.min.js` (render + assertIn). Run the file → PASS; full suite OK.
- [ ] **Step 5 — commit** `git add -A && git commit -m "dashboards: vendor + serve Vega-Lite bundle same-origin"`

---

## Task M-T2: theme + spec builder (`vega_spec.py`)

**Files:** Create `vega_spec.py`; Test: create `tests/test_vega_spec.py`.

- [ ] **Step 1 — failing tests.** Create `tests/test_vega_spec.py`:
```python
import unittest
import vega_spec


class VegaSpecTest(unittest.TestCase):
    def test_config_uses_our_palette(self):
        cfg = vega_spec.vega_config()
        self.assertIn("#5b5bf0", cfg["range"]["category"])      # --acc leads the palette
        self.assertIsNone(cfg["view"]["stroke"])                # no frame box
    def test_color_scale_stable_by_label(self):
        import render
        sc = vega_spec.color_scale(["Acme", "Beta"])
        self.assertEqual(sc["domain"], ["Acme", "Beta"])
        self.assertEqual(sc["range"], [render._element_color("Acme"), render._element_color("Beta")])
    def test_build_column_spec_is_measure_values(self):
        result = {"totals": {"bugs": 3, "prs": 7}}
        spec = vega_spec.build_spec("column", result, ["totals.bugs", "totals.prs"], "Volume")
        self.assertEqual(spec["mark"]["type"] if isinstance(spec["mark"], dict) else spec["mark"], "bar")
        self.assertEqual(len(spec["data"]["values"]), 2)
        self.assertIn("config", spec)
    def test_build_line_spec_multi_series(self):
        result = {"dates": ["Q1", "Q2"],
                  "commit_rows": [{"key": "A", "vals": [1, 2]}, {"key": "B", "vals": [3, 4]}]}
        spec = vega_spec.build_spec("line", result, ["commit_rows"], "Commits")
        self.assertEqual(len(spec["data"]["values"]), 4)        # 2 series x 2 dates, long form
        self.assertEqual(spec["encoding"]["color"]["field"], "series")
    def test_build_pie_spec(self):
        result = {"by_company": [{"company": "A", "commits": 5}, {"company": "B", "commits": 3}]}
        spec = vega_spec.build_spec("pie", result, ["by_company"], "By company")
        self.assertEqual(spec["mark"]["type"], "arc")
    def test_unknown_or_empty_returns_none(self):
        self.assertIsNone(vega_spec.build_spec("column", {"totals": {}}, ["totals.x"], "t"))
```

- [ ] **Step 2 — run** `.venv/bin/python -m unittest tests.test_vega_spec -v` → FAIL.

- [ ] **Step 3 — implement `vega_spec.py`.** Import `render` (for `_element_color`,
  `_ELEM_PALETTE`) and `dashboards` (for `_dig`, `_auto_series`, `_auto_bars`, `_humanize`, `_bars_for`).
  Avoid a circular import: `dashboards` must NOT import `vega_spec` at module top — `_render_panel`
  imports it lazily (inside the function) OR `vega_spec` imports the small helpers it needs by
  reaching into `dashboards` lazily too. Prefer: `vega_spec` does `import dashboards` at top
  (dashboards doesn't import vega_spec at top → no cycle).
  - `_TOKENS` = the hex values from `shell.BASE_CSS` (panel #ffffff, line #eceef2, line2
    #e2e6ec, ink #101828, ink2 #475467, mut #8a93a3, acc #5b5bf0) — copy them as literals with a
    comment pointing at `shell.BASE_CSS` (single source is the CSS; these mirror it for canvas).
  - `vega_config()` → the shared config dict (background None; font "Inter, …"; `view.stroke`
    None; `axis` labelColor/titleColor=mut, gridColor=line, domainColor/tickColor=line2, small
    fonts; `legend` orient bottom, circle symbols, labelColor=ink2; `range.category` =
    `render._ELEM_PALETTE`; `mark.color`=acc; `line.strokeWidth`=1.8 + filled points;
    `bar.cornerRadiusEnd`=3; `arc.stroke`=panel).
  - `color_scale(labels)` → `{"domain": list(labels), "range": [render._element_color(l) for l in labels]}`.
  - `build_spec(viz, result, fields, title)`:
    - **line/area**: long-form `[{date, series, value}]` from each field via `_auto_series` +
      `result["dates"]`; mark `line` (area → `{"type":"area","line":true,"opacity":0.5}` or
      `area` with `line` overlay); encoding x=date(ordinal, **sorted by date order** — pass the
      dates as the sort domain so the axis keeps chronological order), y=value(quant),
      color=series(nominal) with `scale=color_scale(series names)`. Return None if no data/dates.
    - **column/bar/pie**: rows via `dashboards._bars_for(result, fields)` → `[{label, value}]`.
      None if empty. column → mark bar, x=label(nominal, sort None), y=value; bar → mark bar,
      y=label(nominal, sort "-x"), x=value; pie → mark `{"type":"arc","innerRadius":40}`,
      theta=value, color=label. All get `color.scale = color_scale(labels)` for stability.
    - Every spec: `{"$schema": VL5, "config": vega_config(), "width":"container","height":220,
      "autosize":{"type":"fit","contains":"padding"}, ...}` (width container is fine here — the
      cell has a real width at hydrate time, unlike the throwaway prototype; if flaky, the
      hydrate JS passes an explicit width).
  - Never raise: any bad input → return None (the caller renders a small "no data" tile).

- [ ] **Step 4 — run** the tests → PASS; `.venv/bin/python -c "import vega_spec, dashboards"` clean
  (no import cycle); full suite OK.

- [ ] **Step 5 — commit** `git add vega_spec.py tests/test_vega_spec.py && git commit -m "dashboards: Vega-Lite spec builder + our-theme config (stable colours)"`

---

## Task M-T3: rewire the resolver to emit Vega panels

**Files:** Modify `dashboards.py`; Test: `tests/test_dashboards.py`.

- [ ] **Step 1 — update the chart tests.** The chart-viz tests in `tests/test_dashboards.py`
  currently assert SVG (`<svg`, `class="bar"`, `piechart`, `barchart`). Rewrite them to assert
  the new contract: a chart panel renders a `vl-panel` container whose embedded JSON spec has the
  right `mark`/`data`. Helper:
```python
    def _spec_of(self, html):
        import json, re
        m = re.search(r'class="vl-spec"[^>]*>(.*?)</script>', html, re.S)
        self.assertTrue(m, f"no vl-spec in: {html[:200]}")
        return json.loads(m.group(1).replace("<\\/", "</"))
```
  Then e.g. `test_render_multi_series_line` → assert `"vl-panel" in html` and
  `self._spec_of(html)["encoding"]["color"]["field"] == "series"`; multi-scalar column →
  `mark` resolves to `"bar"` and `len(spec["data"]["values"]) == 3`; breakdown pie → arc.
  Keep the number (kpi_tile) and table (data_table) tests asserting server HTML unchanged.
  Keep `test_error_tile_escapes_title`, `test_sql_query_tool_rejected_at_render_time`,
  legacy-normalisation, and validate tests as-is (still pass).

- [ ] **Step 2 — run** the updated tests → FAIL (still emitting SVG).

- [ ] **Step 3 — implement.** In `dashboards.py`:
  - `_render_panel`: keep number→kpi_tile and table→data_table branches exactly. Replace the
    `line/area` and `bar/column/pie` branches with a single chart branch:
```python
        if viz in ("line", "area", "column", "bar", "pie"):
            import vega_spec, json as _json
            spec = vega_spec.build_spec(viz, result, fields, title)
            if not spec:
                return f"<div class='dp-err'>{_esc(title)}: no data</div>"
            payload = _json.dumps(spec).replace("</", "<\\/")
            return ('<div class="vl-panel">'
                    '<script type="application/json" class="vl-spec">%s</script></div>' % payload)
```
  - Drop the now-unused `_VIZ_PRIMITIVE` chart rows? Keep the dict for number/table mapping, or
    inline those two. Simplest: keep `_VIZ_PRIMITIVE` only for `number`/`table` lookups used
    above, remove the line/area/column/bar/pie entries and the `view =` lookups they needed.
    `_SHAPE_VIZ` / `_MULTI_FIELD_VIZ` / validate_spec are UNCHANGED (viz names identical).
  - `render_panel` stays never-raising; the container is escaped.

- [ ] **Step 4 — run** the updated chart tests → PASS; full suite OK. Spot-check:
  `.venv/bin/python -c "import dashboards,re,json; h=dashboards.render_panel({'id':'p','viz':'pie','title':'T','data':{'tool':'contribution','fields':['by_company']}},'','all'); print('vl-panel' in h); s=json.loads(re.search(r'vl-spec\"[^>]*>(.*?)</script>',h,16).group(1).replace('<\\\\/','</')); print(s['mark'])"`

- [ ] **Step 5 — commit** `git add dashboards.py tests/test_dashboards.py && git commit -m "dashboards: chart panels emit Vega-Lite specs (tiles/tables stay server HTML)"`

---

## Task M-T4: client hydration on view + editor

**Files:** Modify `templates/dashboard.j2`, `templates/dashboard_editor.j2`, `shell.py`
(tooltip/vl CSS). Verify in-browser.

- [ ] **Step 1 — hydrate helper.** Add a shared JS function to BOTH pages (identical body):
```js
function hydrateVega(root){
  (root || document).querySelectorAll('.vl-panel').forEach(function(el){
    if (el.dataset.done) return;
    var s = el.querySelector('script.vl-spec');
    if (!s || !window.vegaEmbed) return;
    var spec;
    try { spec = JSON.parse(s.textContent); } catch(e){ return; }
    el.dataset.done = "1";
    vegaEmbed(el, spec, {actions:false, renderer:'svg', tooltip:true})
      .catch(function(){ el.innerHTML = '<div class="dp-err">chart failed</div>'; });
  });
}
```
- [ ] **Step 2 — call it.** In `dashboard.j2`'s `load()`, after `body.innerHTML=h;` call
  `hydrateVega(body);`. In `dashboard_editor.j2`'s preview success (after
  `wpPreview.innerHTML = html;`) call `hydrateVega(wpPreview);`. Both pages must include
  `{{ vega_scripts|safe }}` (done in M-T1). Guard: hydrate only after `vegaEmbed` exists (the
  script tags are synchronous in `<head>`/before our script, so it's loaded — but the `if
  (!window.vegaEmbed) return;` keeps it safe).
- [ ] **Step 3 — CSS.** In `shell.CHART_CSS`, add: `.vl-panel{width:100%}` and vega-tooltip
  theming (`#vg-tooltip-element.vg-tooltip{font:12px Inter,sans-serif;background:var(--panel);
  color:var(--ink);border:1px solid var(--line2);border-radius:8px;box-shadow:var(--sh)}`), and
  remove the now-dead `.barchart`/`.piechart` rules (keep `.linechart`/`.areachart` — report uses
  them).
- [ ] **Step 4 — verify in-browser.** Start the app locally (launch config "Report web portal
  (reportctl serve)" on :8080 — local serve has no SSO). Open a dashboard with one chart of each
  viz; confirm via screenshot that charts render in our theme, tooltips styled, no console
  errors (read_console_messages). If the editor is reachable locally, add a chart and confirm the
  live preview renders a Vega chart. Paste a screenshot.
- [ ] **Step 5 — commit** `git add templates/dashboard.j2 templates/dashboard_editor.j2 shell.py && git commit -m "dashboards: hydrate Vega panels on view + editor preview"`

---

## Task M-T5: retire dead SVG renderers + changelog + suite

**Files:** Modify `render.py`, `view_registry.py`, `changelog.py`; Test: `tests/test_dashboards.py`.

- [ ] **Step 1.** Remove `_bar_chart_svg` and `_pie_chart_svg` from `render.py` and their
  `env.globals` registrations; remove the `bar_chart` / `pie_chart` `view(...)` entries from
  `view_registry.py`. KEEP `_line_chart_svg` / `_stacked_area_svg` and their `line_chart` /
  `stacked_area` views (the report uses them). Remove the `NewChartRenderersTest` /
  `WidgetFixesTest` bar/pie SVG assertions that referenced the deleted fns (keep the pie/unit
  fixes' intent only if still relevant — they're gone with the renderer). Grep to confirm nothing
  else references `_bar_chart_svg`/`_pie_chart_svg`/`bar_chart`/`pie_chart` views.
- [ ] **Step 2 — changelog.** Prepend to the top `2026-07-22` block:
```python
{"type": "improvement", "title": "Sharper, interactive dashboard charts",
 "detail": "Dashboard charts are now drawn with a proper charting engine (Vega-Lite), themed "
           "to Insight's look — cleaner axes, real legends and hover tooltips — while numbers "
           "and tables are unchanged. Groundwork for richer chart types."},
```
- [ ] **Step 3 — full suite** `.venv/bin/python -m unittest discover -s tests -q 2>&1 | tail -3` → OK.
  `.venv/bin/python -c "import server, render, dashboards, vega_spec, view_registry"` clean.
- [ ] **Step 4 — commit** `git add -A && git commit -m "dashboards: retire hand-rolled bar/pie SVG; changelog for Vega charts"`

---

## Notes
- Do NOT touch `report.j2` or `_line_chart_svg`/`_stacked_area_svg` — the report keeps its
  server-SVG charts.
- Keep `render_panel` never-raising; `build_spec` returns None on bad input → small dp-err tile.
- Bundle is served same-origin from `/assets/vega/…`; never reference a CDN at runtime.
- Escape the embedded spec (`</` → `<\/`); labels are safe inside Vega's DOM text.
- No deploy inside the plan; merge + deploy after the final whole-slice review (confirm first).
