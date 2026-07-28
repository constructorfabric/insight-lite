# User Dashboards — Manual Editor (Slice 2) Implementation Plan

> **For agentic workers:** implemented via superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A hybrid manual constructor so a signed-in user can build/edit a dashboard by hand — add panels via a catalog-driven form, reorder by HTML5 drag, set width/title/visibility, see a live preview, and save — all writing the same spec the foundation renders.

**Architecture:** Pure additions on top of slice 1. New read endpoints feed the editor (`/api/dashboard/catalog` for dropdowns; `POST /api/dashboard/preview-panel` renders a panel from an unsaved spec). A `/dashboards` list page and a `/dashboard/<id>/edit` editor page (owner-only) are new server-rendered pages with inline vanilla JS; Save calls the existing CRUD API. No new libraries, no build step.

**Tech Stack:** stdlib http.server (server.py), Jinja2 templates (render.py + templates/), inline vanilla JS. Tests: stdlib unittest.

**Depends on (all merged on this branch):** `dashboards.validate_spec`, `dashboards.render_panel`, `view_registry.dashboard_views`, `dashboards._DASHBOARD_TOOLS`, `store.create/get/list/update/delete_dashboard`, CRUD API + `/dashboard/<id>` page.

**Design:** `docs/superpowers/specs/2026-07-22-user-dashboards-design.md` (slice 2 = "manual constructor over the shared spec").

---

## File structure
- **Modify `dashboards.py`** — expose `dashboard_catalog()` (components + safe tools, for the editor dropdowns) so the list is catalog-driven in one place.
- **Modify `server.py`** — routes: `GET /api/dashboard/catalog`, `POST /api/dashboard/preview-panel`, `GET /dashboards`, `GET /dashboard/<id>/edit`.
- **Modify `render.py`** — `render_dashboards_list(rows, login)` and `render_dashboard_editor(dashboard)`; register the two templates.
- **Create `templates/dashboards_list.j2`** and **`templates/dashboard_editor.j2`**.
- **Modify `shell.py`** — add a "Dashboards" sidebar link.
- **Modify `changelog.py`** — user-facing entry (feature is now hand-usable).
- **Test:** `tests/test_dashboards.py` (extend).

Follow slice-1 conventions: do_GET gates on `require_auth`; do_POST also `reject_cross_origin`; identity via `self._resolve_viewer(conn)`; owner-only for edit/preview of a private board; `self.send_json`/`self.send_bytes`; templates registered in `render._env()`'s DictLoader via module-level `_load_tmpl` constants.

---

## Task 1: `dashboards.dashboard_catalog()` (catalog-driven editor data)

**Files:** Modify `dashboards.py`; Test: `tests/test_dashboards.py`.

- [ ] **Step 1: Failing test** — append:
```python
class CatalogEndpointDataTest(unittest.TestCase):
    def test_dashboard_catalog_lists_components_and_safe_tools(self):
        cat = dashboards.dashboard_catalog()
        names = {c["name"] for c in cat["components"]}
        self.assertIn("kpi_tile", names)
        self.assertIn("data_table", names)
        self.assertIn("contribution", cat["tools"])
        self.assertNotIn("sql_query", cat["tools"])   # not dashboard-safe
        for c in cat["components"]:
            self.assertIn("kind", c)
```

- [ ] **Step 2: Run** `.venv/bin/python -m unittest tests.test_dashboards.CatalogEndpointDataTest -v` → FAIL (AttributeError).

- [ ] **Step 3: Implement** in `dashboards.py`:
```python
def dashboard_catalog() -> dict:
    """The palette for the editor: dashboard-eligible components (name/kind/purpose)
    and the dashboard-safe data tools. Both come straight from the catalogs, so a new
    component/tool appears automatically."""
    comps = [{"name": v["name"], "kind": v["kind"], "purpose": v.get("purpose", "")}
             for v in view_registry.dashboard_views()]
    return {"components": comps, "tools": sorted(_DASHBOARD_TOOLS)}
```

- [ ] **Step 4: Run** the test → PASS; full suite `.venv/bin/python -m unittest discover -s tests -q 2>&1 | tail -3` → OK.

- [ ] **Step 5: Commit** `git add dashboards.py tests/test_dashboards.py && git commit -m "dashboards: dashboard_catalog() for the editor palette"`

---

## Task 2: catalog + preview-panel endpoints

**Files:** Modify `server.py`; Test: `tests/test_dashboards.py` (HTTP test mirroring `tests/test_usage.py`'s server-spin pattern).

- [ ] **Step 1: Failing test** — append a test that spins the handler on a ThreadingHTTPServer (copy the setup from `tests/test_usage.py`'s server test — bind 127.0.0.1:0, isolated `REPORT_DB` temp) and asserts:
  - `GET /api/dashboard/catalog` (with header `X-Forwarded-Preferred-Username: tester`) → 200 JSON with `components` and `tools`.
  - `POST /api/dashboard/preview-panel` with body `{"panel":{"id":"p","component":"kpi_tile","source":{"tool":"contribution","field":"totals.commits"}},"period":"all"}` and the same header → 200, body contains `class="kpi"` (a rendered tile) — against the empty temp DB it renders a valid tile (0/n-a), no `dp-err`.
  (Read `tests/test_usage.py` for the exact ThreadingHTTPServer harness; reuse it verbatim, only changing the requests/asserts.)

- [ ] **Step 2: Run** → FAIL (routes 404).

- [ ] **Step 3: Implement** in `server.py` `do_GET`, add:
```python
        elif path == "/api/dashboard/catalog":
            import dashboards
            self.send_json({"ok": True, **dashboards.dashboard_catalog()})
```
In `server.py` `do_POST` (after `reject_cross_origin`), add:
```python
        if path == "/api/dashboard/preview-panel":
            import dashboards
            payload = self._read_json_body()
            if payload is None:
                return
            panel = payload.get("panel") or {}
            scope = (payload.get("scope") or "")[:120]
            period = (payload.get("period") or "all")[:16]
            html = dashboards.render_panel(panel, scope=scope, period=period)
            self.send_bytes(html.encode(), "text/html; charset=utf-8")
            return
```
(`render_panel` never raises and does not touch the DB destructively; preview needs no ownership check — it renders a transient panel the caller supplied, using only read-only catalog tools. It does require auth, already enforced by `require_auth` at the top of do_POST.)

- [ ] **Step 4: Run** the test → PASS; full suite → OK.

- [ ] **Step 5: Commit** `git add server.py tests/test_dashboards.py && git commit -m "dashboards: catalog + preview-panel endpoints"`

---

## Task 3: `/dashboards` list page

**Files:** Create `templates/dashboards_list.j2`; Modify `render.py` (`render_dashboards_list` + register template), `server.py` (`GET /dashboards`).

- [ ] **Step 1** — Create `templates/dashboards_list.j2`: a page (reuse `shell.SHELL_CSS`/`sidebar_html("dashboards")` like other manage pages) showing a "New dashboard" button (POSTs a blank spec to create, then redirects to its editor) and a table of `rows` (title, owner, visibility, updated) each linking to `/dashboard/<id>` (view) and `/dashboard/<id>/edit` (edit, shown only when `r.owner_login == login`). Inline JS for the New button:
```javascript
document.getElementById('new-dash').addEventListener('click', function(){
  fetch('/api/dashboard', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({spec:{title:'Untitled dashboard', panels:[]}})})
    .then(r=>r.json()).then(function(s){ if(s.ok) location.href='/dashboard/'+s.id+'/edit'; });
});
```
Exact page shell/CSS: mirror `templates/dashboard.j2` + the manage-page pattern in `server.py`'s `usage_page()`; keep it minimal.

- [ ] **Step 2** — In `render.py`: add `DASHBOARDS_LIST = _load_tmpl("dashboards_list")`, register `"dashboards_list"` in `_env()` loader, and:
```python
def render_dashboards_list(rows: list, login) -> str:
    return _env().get_template("dashboards_list").render(rows=rows, login=login)
```

- [ ] **Step 3** — In `server.py` `do_GET`:
```python
        elif path == "/dashboards":
            import store, render
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                rows = store.list_dashboards(conn, login)
                page = render.render_dashboards_list(rows, login)
            finally:
                conn.close()
            self.send_bytes(page.encode(), "text/html; charset=utf-8")
```

- [ ] **Step 4** — Verify import + suite OK; live: `GET /dashboards` with header renders the page and the New button flow returns a new id. Commit `git add templates/dashboards_list.j2 render.py server.py && git commit -m "dashboards: /dashboards list page + New flow"`

---

## Task 4: `/dashboard/<id>/edit` hybrid editor

**Files:** Create `templates/dashboard_editor.j2`; Modify `render.py` (`render_dashboard_editor` + register), `server.py` (`GET /dashboard/<id>/edit`, owner-only).

- [ ] **Step 1** — In `server.py` `do_GET`, add BEFORE the `/dashboard/<id>` view route (so `/edit` is matched first) — actually the view route uses `path.startswith("/dashboard/")`, so add the edit branch first:
```python
        elif path.startswith("/dashboard/") and path.endswith("/edit"):
            import store, render
            did = path[len("/dashboard/"):-len("/edit")]
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                d = store.get_dashboard(conn, did)
                if not d or d["owner_login"] != login:      # edit is owner-only
                    self.send_error(HTTPStatus.NOT_FOUND); return
                page = render.render_dashboard_editor(d)
            finally:
                conn.close()
            self.send_bytes(page.encode(), "text/html; charset=utf-8")
```

- [ ] **Step 2** — Create `templates/dashboard_editor.j2`. Server-side it only needs `id`, `title`, `visibility`, and the initial `spec` (as JSON for the JS). All component/tool options are fetched client-side from `/api/dashboard/catalog` (catalog-driven — no server-side hardcoding). The page has:
  - a title input + visibility select (private/shared),
  - an "Add panel" form: `component` select (filled from catalog), `tool` select (filled from catalog), `field` text input (placeholder e.g. `totals.commits`), `title` text, `width` number (1-6), an "Add" button that appends `{id: 'p'+counter, component, source:{tool, field}, title, width}` to an in-memory `panels` array and re-renders the list,
  - a panel list where each item is `draggable="true"` (HTML5 drag reorder updates the array order), with a width input, a remove button, and a live-preview `<div>` filled by POSTing the panel to `/api/dashboard/preview-panel`,
  - a Save button → `POST /api/dashboard/<id>` with `{spec:{title, panels}, visibility}`; show a saved/failed indicator.
  Provide the full inline JS (drag handlers via `dragstart`/`dragover`/`drop` on the list; a `renderList()` that rebuilds panel rows and triggers per-panel preview; `save()`). Reuse the `.dp-err`/grid styling from `templates/dashboard.j2`.
  (This is the largest file; keep the JS in one `<script>`. The implementer should verify the field/param names the resolver reads: a panel = `{id, component, source:{tool, field, params?}, title, width, pin?}` — match `dashboards.render_panel`/`validate_spec`.)

- [ ] **Step 3** — In `render.py`: `DASHBOARD_EDITOR = _load_tmpl("dashboard_editor")`, register `"dashboard_editor"`, and:
```python
def render_dashboard_editor(dashboard: dict) -> str:
    import json as _json
    spec = dashboard["spec"]
    return _env().get_template("dashboard_editor").render(
        id=dashboard["id"], title=spec.get("title", "Untitled dashboard"),
        visibility=dashboard.get("visibility", "private"),
        spec_json=_json.dumps(spec))
```

- [ ] **Step 4** — Live verify (start server, header auth): create a dashboard via New, open `/dashboard/<id>/edit`, add a `kpi_tile`+`contribution`/`totals.commits` panel (preview shows a number), add a `data_table`+`contribution`/`by_company`, drag to reorder, Save, then open `/dashboard/<id>` and confirm the saved panels render. Screenshot. Delete the test row. Commit `git add templates/dashboard_editor.j2 render.py server.py && git commit -m "dashboards: hybrid drag/form editor at /dashboard/<id>/edit"`

---

## Task 5: sidebar entry + changelog

**Files:** Modify `shell.py` (sidebar link), `changelog.py`.

- [ ] **Step 1** — In `shell.py`, add a "Dashboards" link to the sidebar (find `sidebar_html` and the existing nav-item pattern; add an item pointing to `/dashboards`, active-key `"dashboards"`). Match the existing item markup exactly.

- [ ] **Step 2** — In `changelog.py`, under the top (`2026-07-22`) block, prepend:
```python
{"type": "feature", "title": "Build your own dashboards",
 "detail": "A new Dashboards area lets you assemble your own dashboard from the report's "
           "building blocks: add panels (a KPI, a table, a chart) bound to any metric or "
           "tool, arrange them by drag, and share a link. Panels re-slice live by period "
           "and scope like the main report. Create one from the sidebar → Dashboards → New."},
```

- [ ] **Step 3** — Verify `.venv/bin/python -c "import changelog, shell; changelog.render_page()"` and full suite OK. Live: sidebar shows Dashboards, links to `/dashboards`. Commit `git add shell.py changelog.py && git commit -m "dashboards: sidebar entry + changelog"`

---

## Task 6: end-to-end editor flow test

**Files:** Test `tests/test_dashboards.py`.

- [ ] **Step 1** — Add a hermetic HTTP test (reuse the ThreadingHTTPServer harness from Task 2) that: POSTs create (blank), POSTs update with a 2-panel spec, GETs `/api/dashboard/preview-panel` for one panel (200, rendered), GETs `/dashboard/<id>/edit` as owner (200, contains the editor), GETs `/dashboard/<id>/edit` as a DIFFERENT user (404). Assert each.

- [ ] **Step 2** — Run the class + full suite → OK. Report "Ran N … OK".

- [ ] **Step 3** — Commit `git add tests/test_dashboards.py && git commit -m "dashboards: editor flow HTTP tests"`

---

## Notes for the implementer
- Everything is additive on the slice-1 foundation; do not change slice-1 behavior.
- The editor is catalog-driven: component/tool dropdowns come from `/api/dashboard/catalog`, never hardcoded — adding a `dashboard=True` component makes it appear in the editor with no editor change.
- Owner-only for `/dashboard/<id>/edit` and Save (update); preview needs auth but not ownership (it renders a transient posted panel with read-only tools).
- Pure-JS editor interactions are verified live in the browser (no JS unit harness in this repo); server endpoints/pages are unit-tested.
- Do NOT deploy — this stays on the branch until you (the human) decide to merge/deploy.
