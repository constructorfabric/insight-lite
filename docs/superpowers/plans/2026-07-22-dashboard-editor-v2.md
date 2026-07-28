# Dashboard editor v2 — measure-first widget creation

> Implemented via superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Replace the assemble-internals Add-panel form (component + tool + free-text field) with a **measure-first** flow: a searchable modal picker of human-labelled measures grouped by metric category, an auto-selected "Show as" type, prefilled title, live preview, plus an advanced manual tool+field fallback.

**Why:** The current form makes users type field paths like `totals.commits` from memory and think in internals. Measures come from the catalogs (metrics_catalog categories + tool introspection), so the picker is discoverable and auto-grows.

**Decisions (from design):** picker in a **modal**; keep the **"Show as"** toggle; include an **advanced** manual tool+field fallback; group by metric **category** (metrics_registry.GROUPS), type (number/chart/table) as a per-row icon; search filters across categories.

**Tech:** stdlib http.server, Jinja2, inline vanilla JS. Tests: unittest. Branch `feat/dashboard-editor-v2`.

---

## File structure
- **Modify `dashboards.py`** — add `measures()` (introspect dashboard-safe aggregate tools → labelled, categorised measure list).
- **Modify `server.py`** — `GET /api/dashboard/measures`.
- **Modify `templates/dashboard_editor.j2`** — replace the inline Add-panel form with a "+ Add widget" button opening a modal (category-grouped searchable measure list + Show-as + title + width + advanced tool/field) + live preview; keep list/drag/width/remove/save.
- **Modify `changelog.py`** — note the improved creation flow.
- **Test:** `tests/test_dashboards.py`.

Follow existing conventions (require_auth, `_resolve_viewer`, send_json; template registered in `render._env()`; `render_panel` unchanged).

---

## Task V2-T1: `dashboards.measures()`

**Files:** Modify `dashboards.py`; Test: `tests/test_dashboards.py`.

- [ ] **Step 1 — failing test.** Append:
```python
class MeasuresTest(unittest.TestCase):
    def test_measures_grouped_and_labelled(self):
        ms = dashboards.measures()
        self.assertTrue(ms)
        # each measure: label, category, shape, source {tool, field}, component
        m0 = ms[0]
        for k in ("label", "category", "shape", "source", "component"):
            self.assertIn(k, m0)
        self.assertIn(m0["source"]["tool"], dashboards._DASHBOARD_TOOLS)
        # a commits measure exists, categorised (via metrics_catalog) and scalar
        commits = [m for m in ms if m["source"] == {"tool": "contribution", "field": "totals.commits"}]
        self.assertTrue(commits, "expected a contribution/totals.commits measure")
        self.assertEqual(commits[0]["shape"], "scalar")
        self.assertTrue(commits[0]["category"])
```

- [ ] **Step 2 — run** `.venv/bin/python -m unittest tests.test_dashboards.MeasuresTest -v` → FAIL.

- [ ] **Step 3 — implement** `dashboards.measures()`:
  - Iterate a curated aggregate subset of `_DASHBOARD_TOOLS` that returns a walkable dict at default args: `("contribution", "delivery", "trend", "flow")`. (person/list_items need params → excluded; they're the advanced path.)
  - Call each once with default args (all-time / whole org) via the existing `_call_source({"tool":t}, "", "all")`; cache the result within the call.
  - Walk each result one level (and into `totals` for contribution/delivery):
    - `int`/`float` leaf → shape `"scalar"`, component from the scalar view (`kpi_tile`), field = dotted path.
    - `list` of dicts → shape `"table"`, component `data_table`, field = key.
    - a trend-style series structure → shape `"series"`, component `line_chart`, field = key. (Inspect trend's real keys; map the commit/loc rows.)
  - For each measure derive `label` + `category`: look up the leaf field name in `metrics_catalog` metrics (match on metric `name`); if found use its title + `group` (→ category via `metrics_registry.GROUPS` title); else humanise the key and set category to a per-tool default label (e.g. contribution→"Volume & people", delivery→"Delivery — PRs & issues", trend→"Trend & comparison", flow→"Flow & CI health") or "Other".
  - Return a list of `{label, category, shape, component, source:{tool, field}}`, sorted by category then label. Skip fields that are `None`/empty or clearly non-displayable (nested dicts that aren't lists/series).
  - Guard everything so a tool error just yields no measures from that tool (never raise).

- [ ] **Step 4 — run** the test → PASS; full suite `.venv/bin/python -m unittest discover -s tests -q 2>&1 | tail -3` → OK. Also eyeball: `.venv/bin/python -c "import dashboards,json; print(json.dumps(dashboards.measures()[:8], indent=1))"` — sanity-check labels/categories look human.

- [ ] **Step 5 — commit** `git add dashboards.py tests/test_dashboards.py && git commit -m "dashboards: measures() — labelled, categorised measure list from the catalogs"`

---

## Task V2-T2: `GET /api/dashboard/measures`

**Files:** Modify `server.py`; Test: `tests/test_dashboards.py` (reuse the ThreadingHTTPServer harness).

- [ ] **Step 1 — failing test.** Add an HTTP test (reuse `DashboardEditorEndpointTest`'s harness): `GET /api/dashboard/measures` with `X-Forwarded-Preferred-Username: tester` → 200 JSON `{"ok":true,"measures":[...]}`, measures non-empty, each has `label`/`category`/`shape`/`source`.

- [ ] **Step 2 — run** → FAIL (404).

- [ ] **Step 3 — implement** in `server.py` `do_GET`, an exact branch (before the generic `/api/dashboard` GET catch-all, and add it to that branch's exclusions like `/catalog`/`/panel`):
```python
        elif path == "/api/dashboard/measures":
            import dashboards
            self.send_json({"ok": True, "measures": dashboards.measures()})
```

- [ ] **Step 4 — run** the test → PASS; full suite OK; `.venv/bin/python -c "import server"` clean.

- [ ] **Step 5 — commit** `git add server.py tests/test_dashboards.py && git commit -m "dashboards: /api/dashboard/measures endpoint"`

---

## Task V2-T3: editor rework — modal measure picker

**Files:** Modify `templates/dashboard_editor.j2` (and `render.py` only if the render fn needs new context — it likely doesn't; measures are fetched client-side).

- [ ] **Step 1** — Replace the inline Add-panel form with:
  - A **"+ Add widget"** button. Clicking opens a **modal** (in-flow overlay div — NOT position:fixed; use the `.dov/.dbox`-style pattern or a simple overlay that contributes layout height) containing:
    - a **search box** (filters the measure list live),
    - the **measure list grouped by `category`** (headers = category titles), each row: a small type icon (number/chart/table), the measure `label`, and a muted `tool·field` hint; clicking a row selects it.
    - **"Show as"** segmented toggle — options limited to components compatible with the selected measure's `shape` (today: scalar→kpi_tile, table→data_table, series→line_chart); auto-set from the measure, editable.
    - **Title** (prefilled from the measure label, editable), **Width** (default 2).
    - an **Advanced** disclosure: manual **tool** `<select>` (from `/api/dashboard/catalog` tools) + **field** text input + component select — for a field not in the measure list. When advanced is used, it builds the panel from those raw values.
    - a **live preview** area in the modal (POST the being-built panel to `/api/dashboard/preview-panel`).
    - **Add** (appends the panel to the in-memory `panels`, closes the modal, re-renders the list) and **Cancel**.
  - Fetch `/api/dashboard/measures` on modal open (cache after first load); fetch `/api/dashboard/catalog` for the advanced tool/component selects.
  - The built panel object stays exactly `{id, component, source:{tool, field}, title, width}` — unchanged contract for `render_panel`/`validate_spec`/save.
  - Keep the existing panel list (drag reorder, width, remove) and Save flow untouched.
  - Escaping: measure labels/fields into the list via `.textContent`; preview via innerHTML (server HTML) as before.

- [ ] **Step 2** — Verify: `.venv/bin/python -c "import server, render"` clean; full suite OK. LIVE (owner via header): create a dashboard, GET `/dashboard/<id>/edit`, confirm the page has the "+ Add widget" button and (via curl grep) the modal markup + the measures/catalog fetch calls are present. (Interactive modal/drag is browser-only; endpoints are tested.) Paste the grep. Clean up the test row.

- [ ] **Step 3 — commit** `git add templates/dashboard_editor.j2 render.py && git commit -m "dashboards: measure-first modal widget picker (categories, search, show-as, advanced fallback)"`

---

## Task V2-T4: changelog + suite

- [ ] **Step 1** — In `changelog.py`, prepend to the top `2026-07-22` block:
```python
{"type": "improvement", "title": "Easier dashboard widgets",
 "detail": "Adding a widget is now pick-what-to-show: a searchable list of metrics grouped "
           "by category, instead of typing internal field names. The display type is chosen "
           "for you (with a toggle), the title is prefilled, and you see a live preview. An "
           "advanced option still lets you pick a raw tool and field."},
```
- [ ] **Step 2** — Full suite `.venv/bin/python -m unittest discover -s tests -q 2>&1 | tail -3` → OK.
- [ ] **Step 3 — commit** `git add changelog.py && git commit -m "dashboards: changelog for measure-first editor"`

---

## Notes
- `measures()` field→metric→category mapping is the alignment point — verify against real `contribution/delivery/trend/flow` output and `metrics_catalog`; humanise unmapped keys, never invent.
- Keep the panel object contract stable so nothing downstream (validator/resolver/save/view) changes.
- No deploy inside the plan; merge + deploy handled after the final review.
