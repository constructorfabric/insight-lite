# Report charts → Vega-Lite migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.
> Steps use `- [ ]`. Branch `feat/report-vega`.

**Goal:** Move the REPORT's charts (the `line_chart` and `stacked_area` Jinja globals — ~8 call
sites across `report.j2`, `panels/02_overview.j2`, `panels/03_delivery.j2`) from hand-rolled
server SVG to client-rendered Vega-Lite, matching the dashboards. One chart engine everywhere;
retire the hand-rolled SVG. `sparkline` (decorative inline) stays.

**Context / why now:** The email report (no-JS) is deprecated (user confirmed), so client-side
Vega is fine for the report too. This unifies on Vega-Lite (added for dashboards) and deletes the
last hand-rolled chart code.

**Architecture:** Reimplement the two Jinja globals to return a `<div class="vl-panel">` +
embedded spec (SAME arguments, so all call sites are untouched). Load `shell.VEGA_SCRIPTS` on the
report page; hydrate on load and after every fragment swap via the idempotent `hydrateVega`.

**Tech:** Python stdlib http.server, Jinja2, vendored Vega-Lite (already at `/assets/vega/`),
`vega_spec.py` (extend it). Tests: unittest.

## Chart call-site inventory (do NOT edit call sites — the globals' output changes under them)
- `line_chart(series, dates, unit='', area_first=False)`: report.j2:785 (contrib trend);
  overview 251 (weekly, 1 series), 290 (throughput opened/merged, 2 series), 294 (Median TTM,
  hours, area), 298 (Contributors, area).
- `stacked_area(rows, dates, company_rows, unit='commits', noun=None)`: delivery 73 (items CFD);
  overview 284 (commits by company), 287 (LOC by company).

## Portal fragment-swap points (report.j2 `<script>` ~line 1082) — hydrate after each
- Initial load: server-rendered vl-panels already in the DOM → hydrate on DOMContentLoaded.
- `_applyMap(m)` (~1116): `/api/period` swaps `[data-period-panel]` innerHTML (overview charts).
- Trend fetch (~1209), Delivery fetch (~1257), Flow fetch (~1269), Person fetch (~1286).
- Because `hydrateVega` is idempotent (`el.dataset.done` guard), call `hydrateVega(document)`
  after each swap — re-scanning is cheap and safe.

---

## Task R-T1: report spec builders in `vega_spec.py`

**Files:** Modify `vega_spec.py`; Test: `tests/test_vega_spec.py`.

- [ ] **Step 1 — failing tests.** Append to `tests/test_vega_spec.py`:
```python
class ReportSpecTest(unittest.TestCase):
    def test_line_spec_multi_series_with_colors(self):
        spec = vega_spec.line_spec(
            [{"name": "Opened", "vals": [1, 2], "color": "#2f80ed"},
             {"name": "Merged", "vals": [3, 4], "color": "#10b981"}], ["Q1", "Q2"])
        self.assertEqual(len(spec["data"]["values"]), 4)
        self.assertEqual(spec["encoding"]["color"]["scale"]["range"], ["#2f80ed", "#10b981"])
        self.assertIn(spec["mark"] if isinstance(spec["mark"], str) else spec["mark"]["type"],
                      ("line",))
    def test_line_spec_area_first_is_area(self):
        spec = vega_spec.line_spec([{"name": "TTM", "vals": [1, 2]}], ["a", "b"], area_first=True)
        self.assertEqual(spec["mark"]["type"] if isinstance(spec["mark"], dict) else spec["mark"], "area")
    def test_line_spec_x_sorted_by_dates(self):
        spec = vega_spec.line_spec([{"name": "s", "vals": [1, 2, 3]}], ["Q3 25", "Q4 25", "Q1 26"])
        self.assertEqual(spec["encoding"]["x"]["sort"], ["Q3 25", "Q4 25", "Q1 26"])
    def test_stacked_area_spec(self):
        spec = vega_spec.stacked_area_spec(
            [{"company": "A", "vals": [1, 2]}, {"company": "B", "vals": [3, 4]}],
            ["Q1", "Q2"], [{"company": "A", "color": "#111"}, {"company": "B", "color": "#222"}])
        self.assertEqual(spec["mark"]["type"] if isinstance(spec["mark"], dict) else spec["mark"], "area")
        self.assertEqual(len(spec["data"]["values"]), 4)
        self.assertEqual(spec["encoding"]["color"]["scale"]["range"], ["#111", "#222"])
        self.assertTrue(spec["encoding"]["y"].get("stack"))
    def test_specs_empty_return_none(self):
        self.assertIsNone(vega_spec.line_spec([], ["a"]))
        self.assertIsNone(vega_spec.stacked_area_spec([], ["a"], []))
    def test_panel_html_wraps_and_escapes(self):
        html = vega_spec.panel_html({"mark": "line", "x": "</script>"})
        self.assertIn('class="vl-panel"', html)
        self.assertIn('class="vl-spec"', html)
        self.assertNotIn("</script></script>", html)   # spec's </ is escaped
```

- [ ] **Step 2 — run** `.venv/bin/python -m unittest tests.test_vega_spec -v` → FAIL.

- [ ] **Step 3 — implement** in `vega_spec.py`:
  - `panel_html(spec)` → `markupsafe.Markup('<div class="vl-panel"><script type="application/json" class="vl-spec">%s</script></div>' % json.dumps(spec).replace("</", "<\\/"))`. Return `Markup("")` if spec is falsy.
  - `line_spec(series, dates, unit="", area_first=False)`:
    - `series` = `[{name, vals, color?}]`, `dates` = labels. Filter series with a non-empty `vals`;
      if none or no dates → return None.
    - Long-form `values` = `[{"x": dates[i], "series": s["name"], "value": v}
      for s in series for i, v in enumerate(s["vals"]) if i < len(dates) and v is not None]`.
    - mark: `{"type": "area", "opacity": 0.55, "line": True}` if `area_first` else `"line"`.
    - encoding: `x` = `{"field":"x","type":"ordinal","sort": list(dates), "title": None}`;
      `y` = `{"field":"value","type":"quantitative","title": None}`;
      `color` = `{"field":"series","type":"nominal","title":None,
        "scale": {"domain":[s["name"] for s in series],
                  "range":[s.get("color") or render._element_color(s["name"]) for s in series]}}`.
    - Envelope = the shared one (see below).
  - `stacked_area_spec(rows, dates, company_rows, unit="commits")`:
    - `rows` = `[{company, vals}]` (company key may be `company`/`key`/`name`); company_rows =
      `[{company,color}]` or objects with `.company/.color` (mirror `_stacked_area_svg`'s cmap:
      `cmap = {c["company"] if isinstance(c, dict) else c.company: c["color"] if isinstance(c, dict) else c.color for c in company_rows}`).
    - Filter rows with any nonzero vals; None if empty/no dates.
    - Long-form `values` = `[{"x": dates[i], "company": co, "value": v}]` (co from the row's
      company/key/name).
    - mark `"area"`; encoding `x` ordinal sort dates; `y` = `{"field":"value","type":"quantitative","stack":True,"title":None}`;
      `color` = `{"field":"company","type":"nominal","title":None,
        "scale":{"domain":[...companies...], "range":[cmap.get(co) or render._element_color(co) for co in companies]}}`.
  - Shared envelope helper `_envelope(spec)` (or inline): merge into
    `{"$schema": VL5, "config": vega_config(), "width":"container", "height":220,
      "autosize":{"type":"fit","contains":"padding"}, **spec}`. Reuse for build_spec too if easy
    (optional; don't break existing build_spec tests).
  - Never raise (wrap in try/except → None like build_spec).

- [ ] **Step 4 — run** ReportSpecTest → PASS; full suite OK; `import vega_spec, render, dashboards` clean.

- [ ] **Step 5 — commit** `git add vega_spec.py tests/test_vega_spec.py && git commit -m "report: Vega-Lite line + stacked-area spec builders"`

---

## Task R-T2: reimplement the `line_chart` / `stacked_area` globals → vl-panel

**Files:** Modify `render.py`; Test: `tests/` (whatever covers these globals).

- [ ] **Step 1.** In `render.py`, replace the bodies wired to the Jinja globals:
  - `env.globals["line_chart"] = _line_chart_vega` where
    `def _line_chart_vega(series, dates, unit="", area_first=False): return vega_spec.panel_html(vega_spec.line_spec(series, dates, unit, area_first))` (import vega_spec lazily inside, to avoid a cycle — vega_spec imports render).
  - `env.globals["stacked_area"] = _stacked_area_vega` where
    `def _stacked_area_vega(rows, dates, company_rows, unit="commits", noun=None): return vega_spec.panel_html(vega_spec.stacked_area_spec(rows, dates, company_rows, unit))`.
  - DELETE the old `_line_chart_svg` and `_stacked_area_svg` function bodies (no longer used).
  - Keep `sparkline` (it's a template macro, not a global here — untouched), `_element_color`, `_ELEM_PALETTE`.
  - The globals still return `Markup` (via `panel_html`), so `{{ line_chart(...) }}` /
    `{{ stacked_area(...) }}` in templates emit the container unescaped. Empty data → `Markup("")`
    (panel_html returns empty for falsy spec) so the macros' "no data" hint still shows.
- [ ] **Step 2.** Grep for any remaining reference to `_line_chart_svg`/`_stacked_area_svg`
  (should be none after deletion). Fix/adjust any test that called them directly (e.g. a
  `test_render.py`); the globals' output is now vl-panel HTML, not `<svg>`.
- [ ] **Step 3 — verify.** `.venv/bin/python -c "import render, vega_spec, dashboards"` clean;
  render a report model chart via the global and confirm vl-panel:
  `.venv/bin/python -c "import render; g=render._env().globals; print(str(g['line_chart']([{'name':'x','vals':[1,2],'color':'#5b5bf0'}], ['a','b']))[:120])"` → shows `vl-panel`.
  Full suite OK.
- [ ] **Step 4 — commit** `git add render.py tests/ && git commit -m "report: line_chart/stacked_area globals emit Vega-Lite panels; drop hand-rolled SVG"`

---

## Task R-T3: load Vega + hydrate the report page & all fragment swaps

**Files:** Modify `templates/report.j2` (+ verify fragments don't need their own script — they're
injected into the report page which owns hydrateVega). In-browser verify.

- [ ] **Step 1 — load the bundle + CSS.** In `report.j2`'s `<head>` (before `</head>` ~line 583),
  add `{{ vega_scripts|safe }}` — pass `vega_scripts=shell.VEGA_SCRIPTS` from `render.render_report`
  (and from `render_period_fragment`/`render_trend_fragment`/etc.? NO — fragments are injected
  into the already-loaded report page, so only the top-level report page needs the scripts).
  In `report.j2`'s `<style>`: remove the `.areachart`/`.linechart` rules (~365-374, now dead), and
  add the Vega rules — reuse `{{ chart_css|safe }}` if `shell.CHART_CSS` is already passed, else add
  `.vl-panel{width:100%}` + the `#vg-tooltip-element.vg-tooltip{...}` block from `shell.CHART_CSS`.
  (Simplest: pass `chart_css=shell.CHART_CSS` to `render_report` and inject `{{ chart_css|safe }}`,
  then drop the inline areachart/linechart rules.)
- [ ] **Step 2 — hydrateVega + calls.** Add the idempotent `hydrateVega(root)` function (identical
  to the dashboards' one) to report.j2's main `<script>` (~1082). Then call `hydrateVega(document)`:
  - once on initial load (end of the main script, after the panel wiring runs);
  - inside `_applyMap` (after the `Object.keys(m).forEach(... innerHTML ...)` loop, alongside `dimZeros()`);
  - in the `.then` of the trend fetch (~1209), delivery fetch (~1257), flow fetch (~1269), and
    person fetch (~1286, after `box.innerHTML = s.html`).
  Since `hydrateVega` guards on `el.dataset.done`, calling `hydrateVega(document)` broadly is safe
  and cheap. Guard the whole thing with `if (window.vegaEmbed)` inside hydrateVega (already there).
- [ ] **Step 3 — verify in-browser (controller does this; agent verifies structurally).** Agent:
  `.venv/bin/python -c "import render, shell"` clean; full suite OK; render the report page and grep
  that it contains `/assets/vega/vega-embed.min.js`, `function hydrateVega`, at least 5
  `hydrateVega(document)` call sites, `vl-panel` (from a chart), and NO `.linechart .ax-hit`
  (old CSS gone). Paste greps.
- [ ] **Step 4 — commit** `git add templates/report.j2 render.py && git commit -m "report: load Vega + hydrate charts on load and every fragment swap"`

---

## Task R-T4: cleanup + changelog + suite + (controller) in-browser check

- [ ] **Step 1.** Grep the whole repo (excl `.venv/`) for `_line_chart_svg`, `_stacked_area_svg`,
  `.linechart`, `.areachart` — confirm only intended remnants (none in py; report.j2 CSS removed).
  If `shell.CHART_CSS` still lists `.linechart`/`.areachart` selectors AND nothing else uses them
  (dashboards use vl-panel, report now too), trim them there too — but KEEP `.vl-panel` + tooltip.
- [ ] **Step 2 — changelog.** Prepend to the top `2026-07-22` block:
```python
{"type": "improvement", "title": "Report charts use the same engine as dashboards",
 "detail": "The report's trend, throughput and contributor charts now render with Vega-Lite too "
           "— consistent look, real legends and hover tooltips across the whole product."},
```
- [ ] **Step 3 — suite** `.venv/bin/python -m unittest discover -s tests -q 2>&1 | tail -3` → OK;
  `.venv/bin/python -c "import server, render, dashboards, vega_spec, view_registry, shell; import changelog; changelog.render_page(); print('ok')"`.
- [ ] **Step 4 — commit** `git add -A && git commit -m "report: cleanup dead chart CSS + changelog for Vega charts"`

---

## Notes
- All 8 call sites are UNCHANGED — only the two globals' implementations change.
- Never break the "no data → hint" behaviour (panel_html("") for empty specs).
- Escape embedded spec JSON (`</`→`<\/`). Vega renders data as DOM text (safe).
- `sparkline` stays hand-rolled (decorative inline, no Vega).
- Report is browser-only now (email deprecated) — client hydration is fine; the old
  "mail clients strip <script>" caveat no longer constrains us.
- Controller verifies the live report at `/` locally (reportctl serve, no SSO) — screenshot the
  overview + trend charts rendering in our theme with no console errors before merge.
- No deploy in the plan; merge + deploy after final review (confirm first).
