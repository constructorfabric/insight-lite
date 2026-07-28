# User Dashboards — Foundation (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A catalog-bound dashboard spec that is stored in the DB, validated, and rendered server-side by reusing the existing `views_catalog` components — with CRUD API and a shareable `/dashboard/<id>` page. No editor UI and no AI authoring yet (later slices).

**Architecture:** A dashboard is a JSON spec (`{title, panels:[…]}`) persisted in a `dashboard` table. Each panel names a `views_catalog` component and a `tooldefs` data source. A resolver binds generically by the component's `kind` (tile/chart/table/primitive) — one adapter per kind, driven by a `binding` descriptor added to each catalog entry — so new catalog components are picked up automatically. Rendering reuses the report's Jinja macros as HTML fragments (the `/api/period` pattern); period/scope are live viewer controls, optionally pinned per panel.

**Tech Stack:** Python 3.12 stdlib `http.server` (server.py), SQLite (store.py), Jinja2 macros (render.py + templates/panels), plain inline JS. Tests: stdlib `unittest` under `tests/`.

**Design spec:** `docs/superpowers/specs/2026-07-22-user-dashboards-design.md`

---

## File structure

- **Create `dashboards.py`** — spec validation + the panel resolver (kind adapters). Depends on `tooldefs`, `view_registry`, `render`, `store`.
- **Modify `store.py`** — `dashboard` table in `_SCHEMA`; CRUD functions.
- **Modify `view_registry.py`** — extend `view()` with `binding` + `dashboard` kwargs; tag the initial eligible components; add `dashboard_views()` accessor.
- **Modify `render.py`** — add `render_panel_macro(macro, kwargs)` to render one `panels` macro from Python.
- **Modify `server.py`** — routes: `GET /dashboard/<id>`, `GET /api/dashboard/panel`, and CRUD `GET/POST/PUT/DELETE /api/dashboard[/<id>]`.
- **Create `templates/dashboard.j2`** — the dashboard page shell (title, global scope/period controls, panel grid).
- **Create `tests/test_dashboards.py`** — validator + resolver + store + API tests.

Conventions to follow: `store.connect()` opens the DB and runs `_SCHEMA` + migrations; server handlers gate on `require_auth()` then `reject_cross_origin()` (POST); the viewer login is resolved via `self._resolve_viewer(conn)`; JSON bodies via `self._read_json_body()`; responses via `self.send_json(...)`.

---

## Task 1: Dashboard spec validator

**Files:**
- Create: `dashboards.py`
- Test: `tests/test_dashboards.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboards.py
import unittest
import dashboards


class ValidateSpecTest(unittest.TestCase):
    def _spec(self, **over):
        s = {"title": "T", "panels": [
            {"id": "p1", "component": "kpi_tile",
             "source": {"tool": "contribution", "field": "totals.commits"}}]}
        s.update(over)
        return s

    def test_valid_spec_passes(self):
        ok, err = dashboards.validate_spec(self._spec())
        self.assertTrue(ok, err)
        self.assertIsNone(err)

    def test_title_required(self):
        ok, err = dashboards.validate_spec({"panels": []})
        self.assertFalse(ok)
        self.assertIn("title", err)

    def test_unknown_tool_rejected(self):
        ok, err = dashboards.validate_spec(self._spec(panels=[
            {"id": "p1", "component": "kpi_tile", "source": {"tool": "nope"}}]))
        self.assertFalse(ok)
        self.assertIn("tool", err)

    def test_unknown_component_rejected(self):
        ok, err = dashboards.validate_spec(self._spec(panels=[
            {"id": "p1", "component": "made_up",
             "source": {"tool": "contribution"}}]))
        self.assertFalse(ok)
        self.assertIn("component", err)

    def test_bad_pin_scope_rejected(self):
        ok, err = dashboards.validate_spec(self._spec(panels=[
            {"id": "p1", "component": "kpi_tile", "source": {"tool": "contribution"},
             "pin": {"scope": "person:bob"}}]))
        self.assertFalse(ok)
        self.assertIn("scope", err)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_dashboards -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboards'`.

- [ ] **Step 3: Write minimal implementation**

```python
# dashboards.py
"""Dashboard spec: validation + server-side panel rendering.

A dashboard spec is {title:str, panels:[panel]}. A panel binds a views_catalog
component to a tooldefs data source; period/scope are supplied at render time (a panel
may `pin` its own). No raw SQL — sources are catalog tools only.
"""
from __future__ import annotations

import re

import tooldefs
import view_registry

_SCOPE_RE = re.compile(r"^(org|element|repo|project):.+$")
_PERIOD_RE = re.compile(r"^(7d|30d|90d|365d|all)$")

_TOOL_NAMES = set(tooldefs.DISPATCH)


def _dashboard_components() -> dict:
    """id -> view spec, for views flagged dashboard-eligible (see Task 3)."""
    return {v["name"]: v for v in view_registry.dashboard_views()}


def validate_spec(spec) -> tuple[bool, str | None]:
    """(ok, error). Rejects missing title, unknown component/tool, malformed
    scope/period/pin. Cheap structural check — no data access."""
    if not isinstance(spec, dict):
        return False, "spec must be an object"
    if not (spec.get("title") or "").strip():
        return False, "title is required"
    panels = spec.get("panels")
    if not isinstance(panels, list):
        return False, "panels must be a list"
    comps = _dashboard_components()
    for i, p in enumerate(panels):
        where = f"panel[{i}]"
        if not isinstance(p, dict) or not p.get("id"):
            return False, f"{where}: id is required"
        if p.get("component") not in comps:
            return False, f"{where}: unknown component {p.get('component')!r}"
        src = p.get("source") or {}
        if src.get("tool") not in _TOOL_NAMES:
            return False, f"{where}: unknown tool {src.get('tool')!r}"
        pin = p.get("pin") or {}
        if "scope" in pin and pin["scope"] and not _SCOPE_RE.match(pin["scope"]):
            return False, f"{where}: bad pin.scope (org|element|repo|project:<target>)"
        if "period" in pin and pin["period"] and not _PERIOD_RE.match(pin["period"]):
            return False, f"{where}: bad pin.period"
    return True, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_dashboards -v`
Expected: PASS (all 5 cases). Requires Task 3's `view_registry.dashboard_views()` — implement Task 3 first if run standalone; for now stub `dashboard_views` is added in Task 3. To keep Task 1 green in isolation, temporarily add to `view_registry.py`: `def dashboard_views(): return [v for v in all_views() if v.get("dashboard")]` (Task 3 finalises it).

- [ ] **Step 5: Commit**

```bash
git add dashboards.py tests/test_dashboards.py
git commit -m "dashboards: spec validator"
```

---

## Task 2: `dashboard` table + store CRUD

**Files:**
- Modify: `store.py` (add table to `_SCHEMA`; add CRUD functions near `record_chat_message`)
- Test: `tests/test_dashboards.py` (add `StoreTest`)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_dashboards.py
import store


class DashboardStoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = store.connect()
        self.conn.execute("DELETE FROM dashboard WHERE owner_login='tester'")
        self.conn.commit()

    def tearDown(self):
        self.conn.execute("DELETE FROM dashboard WHERE owner_login='tester'")
        self.conn.commit()
        self.conn.close()

    def test_create_get_update_list_delete(self):
        did = store.create_dashboard(self.conn, "tester", "My board",
                                     {"title": "My board", "panels": []})
        self.assertTrue(did)
        row = store.get_dashboard(self.conn, did)
        self.assertEqual(row["owner_login"], "tester")
        self.assertEqual(row["spec"]["title"], "My board")
        self.assertEqual(row["visibility"], "private")

        store.update_dashboard(self.conn, did, title="Renamed",
                               spec={"title": "Renamed", "panels": []},
                               visibility="shared")
        row = store.get_dashboard(self.conn, did)
        self.assertEqual(row["title"], "Renamed")
        self.assertEqual(row["visibility"], "shared")

        mine = store.list_dashboards(self.conn, "tester")
        self.assertTrue(any(d["id"] == did for d in mine))

        store.delete_dashboard(self.conn, did)
        self.assertIsNone(store.get_dashboard(self.conn, did))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_dashboards.DashboardStoreTest -v`
Expected: FAIL — `no such table: dashboard` (or `AttributeError: create_dashboard`).

- [ ] **Step 3: Add the table to `_SCHEMA`**

In `store.py`, inside the `_SCHEMA` string, after the `chat_tool_call` block, add:

```sql
CREATE TABLE IF NOT EXISTS dashboard (
    id           TEXT    PRIMARY KEY,   -- 'dash_' + hex
    owner_login  TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    visibility   TEXT    NOT NULL DEFAULT 'private',  -- 'private' | 'shared'
    spec         TEXT    NOT NULL,      -- JSON
    created_ts   TEXT    NOT NULL,
    updated_ts   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dashboard_owner ON dashboard(owner_login);
```

- [ ] **Step 4: Add CRUD functions**

In `store.py` (near `record_chat_message`), add (`json`, `os`, `_utc_iso` already available):

```python
def create_dashboard(conn, owner_login, title, spec, visibility="private") -> str:
    did = "dash_" + os.urandom(6).hex()
    ts = _utc_iso()
    conn.execute(
        "INSERT INTO dashboard (id, owner_login, title, visibility, spec, "
        " created_ts, updated_ts) VALUES (?,?,?,?,?,?,?)",
        (did, owner_login, title or "Untitled", visibility,
         json.dumps(spec, ensure_ascii=False), ts, ts))
    conn.commit()
    return did


def get_dashboard(conn, did):
    r = conn.execute("SELECT * FROM dashboard WHERE id=?", (did,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["spec"] = json.loads(d["spec"])
    return d


def list_dashboards(conn, owner_login) -> list:
    """The viewer's own dashboards + all shared ones, newest first."""
    rows = conn.execute(
        "SELECT id, owner_login, title, visibility, updated_ts FROM dashboard "
        "WHERE owner_login=? OR visibility='shared' ORDER BY updated_ts DESC",
        (owner_login,))
    return [dict(r) for r in rows]


def update_dashboard(conn, did, title=None, spec=None, visibility=None) -> None:
    sets, params = ["updated_ts=?"], [_utc_iso()]
    if title is not None:
        sets.append("title=?"); params.append(title)
    if spec is not None:
        sets.append("spec=?"); params.append(json.dumps(spec, ensure_ascii=False))
    if visibility is not None:
        sets.append("visibility=?"); params.append(visibility)
    params.append(did)
    conn.execute(f"UPDATE dashboard SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()


def delete_dashboard(conn, did) -> None:
    conn.execute("DELETE FROM dashboard WHERE id=?", (did,))
    conn.commit()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_dashboards.DashboardStoreTest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add store.py tests/test_dashboards.py
git commit -m "dashboards: dashboard table + store CRUD"
```

---

## Task 3: Catalog binding — make components dashboard-eligible

**Files:**
- Modify: `view_registry.py` (extend `view()`, tag components, add `dashboard_views()`)
- Test: `tests/test_dashboards.py` (add `CatalogBindingTest`)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_dashboards.py
import view_registry


class CatalogBindingTest(unittest.TestCase):
    def test_dashboard_views_are_flagged_and_have_binding(self):
        dv = {v["name"]: v for v in view_registry.dashboard_views()}
        self.assertIn("kpi_tile", dv)
        self.assertIn("data_table", dv)
        self.assertIn("line_chart", dv)
        # bar_cell is a sub-part, not a standalone panel
        self.assertNotIn("bar_cell", dv)
        for v in dv.values():
            self.assertIn("shape", v["binding"])   # kind-level data contract
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_dashboards.CatalogBindingTest -v`
Expected: FAIL — `AttributeError: dashboard_views` or missing `binding`.

- [ ] **Step 3: Extend `view()` and add the accessor**

In `view_registry.py`, change the `view()` signature and return dict:

```python
def view(name: str, *, kind: str, group: str, purpose: str, when_to_use: str,
         ref: str, params: list, example: str, html_contract: str = "",
         binding: dict | None = None, dashboard: bool = False) -> dict:
    return {"name": name, "kind": kind, "group": group, "purpose": purpose,
            "when_to_use": when_to_use, "ref": ref, "params": params,
            "example": example, "html_contract": html_contract,
            "binding": binding or {}, "dashboard": dashboard}
```

Add after `all_views()`:

```python
def dashboard_views() -> list:
    """Views usable as standalone dashboard panels (dashboard=True)."""
    return [v for v in all_views() if v.get("dashboard")]
```

- [ ] **Step 4: Tag the initial eligible components**

On the `view("kpi_tile", …)` call add:
```python
    dashboard=True,
    binding={"shape": "scalar", "value_param": "value"},
```
On `view("data_table", …)` add:
```python
    dashboard=True,
    binding={"shape": "table", "rows_param": "rows", "columns_param": "columns"},
```
On `view("cat_table", …)` add:
```python
    dashboard=True,
    binding={"shape": "categorical", "rows_param": "rows"},
```
On `view("line_chart", …)` add:
```python
    dashboard=True,
    binding={"shape": "series", "series_param": "series"},
```
(Leave `bar_cell`, `sparkline`, `kchip`, `deltachip`, `segbar`, `stacked_area` without `dashboard=True` for slice 1.)

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_dashboards.CatalogBindingTest -v`
Expected: PASS.

- [ ] **Step 6: Verify the drift test / drift page still builds**

Run: `.venv/bin/python -c "import views_catalog; views_catalog.render_page()" && .venv/bin/python -m unittest discover -s tests -q 2>&1 | tail -3`
Expected: no error; suite still OK (the new `binding`/`dashboard` keys are additive).

- [ ] **Step 7: Commit**

```bash
git add view_registry.py tests/test_dashboards.py
git commit -m "dashboards: catalog binding descriptors + dashboard_views()"
```

---

## Task 4: `render.render_panel_macro()` helper

**Files:**
- Modify: `render.py` (add helper near the other fragment renderers)
- Test: `tests/test_dashboards.py` (add `RenderMacroTest`)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_dashboards.py
import render


class RenderMacroTest(unittest.TestCase):
    def test_render_kpi_tile_macro(self):
        html = render.render_panel_macro("kpi_tile", {"value": "1,204", "label": "Commits"})
        self.assertIn("1,204", html)
        self.assertIn("Commits", html)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_dashboards.RenderMacroTest -v`
Expected: FAIL — `AttributeError: render_panel_macro`.

- [ ] **Step 3: Implement the helper**

In `render.py`, add (uses the memoised `_env()` which already loads the `panels` macros):

```python
def render_panel_macro(macro: str, kwargs: dict) -> str:
    """Render ONE macro from the 'panels' template with the given kwargs. Used by the
    dashboard panel resolver so panels reuse the exact report components."""
    keys = ", ".join(kwargs)
    src = ("{% from 'panels' import " + macro + " with context %}"
           + "{{ " + macro + "(" + keys + ") }}")
    return _env().from_string(src).render(**kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_dashboards.RenderMacroTest -v`
Expected: PASS. (If `kpi_tile`'s required params differ, pass them — check `views_catalog` params for `kpi_tile`; `value` is the required one.)

- [ ] **Step 5: Commit**

```bash
git add render.py tests/test_dashboards.py
git commit -m "dashboards: render_panel_macro helper"
```

---

## Task 5: Panel resolver (kind adapters)

**Files:**
- Modify: `dashboards.py` (add `render_panel`)
- Test: `tests/test_dashboards.py` (add `ResolverTest`)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_dashboards.py
class ResolverTest(unittest.TestCase):
    def test_scalar_panel_renders_a_number(self):
        panel = {"id": "p1", "component": "kpi_tile", "title": "Commits",
                 "source": {"tool": "contribution", "field": "totals.commits"}}
        html = dashboards.render_panel(panel, scope="", period="all")
        self.assertRegex(html, r"\d")          # a rendered number
        self.assertIn("Commits", html)

    def test_unknown_shape_is_reported_not_raised(self):
        panel = {"id": "p1", "component": "kpi_tile", "title": "X",
                 "source": {"tool": "contribution", "field": "totals.NOPE"}}
        html = dashboards.render_panel(panel, scope="", period="all")
        self.assertIn("n/a", html.lower())     # missing field degrades, no crash
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_dashboards.ResolverTest -v`
Expected: FAIL — `AttributeError: render_panel`.

- [ ] **Step 3: Implement resolver + adapters**

Add to `dashboards.py`:

```python
import render

# period token -> tooldefs since/until helpers
_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}


def _dig(obj, path):
    """Dotted-path lookup into a dict/list result; None if missing."""
    cur = obj
    for part in (path or "").split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _since_until(period):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    if period in _PERIOD_DAYS:
        since = (now - timedelta(days=_PERIOD_DAYS[period])).strftime("%Y-%m-%d")
    else:
        since = ""                         # all-time
    return since, now.strftime("%Y-%m-%d")


def _call_source(source, scope, period):
    """Invoke the panel's tool with scope/period + params; return its dict result."""
    fn = tooldefs.DISPATCH[source["tool"]]
    since, until = _since_until(period)
    kwargs = dict(source.get("params") or {})
    # only pass args the tool actually accepts
    import inspect
    accepted = set(inspect.signature(fn).parameters)
    for k, v in (("since", since), ("until", until), ("scope", scope or "")):
        if k in accepted:
            kwargs.setdefault(k, v)
    return fn(**{k: v for k, v in kwargs.items() if k in accepted})


def render_panel(panel, scope, period) -> str:
    """Render one panel to HTML. Applies the panel's pin (if any) over scope/period,
    calls its source tool, and binds the result to the component by its kind. Never
    raises — a bad source degrades to a small 'n/a' tile."""
    comps = _dashboard_components()
    view = comps.get(panel.get("component"))
    if not view:
        return f"<div class='dp-err'>unknown component</div>"
    pin = panel.get("pin") or {}
    scope = pin.get("scope", scope)
    period = pin.get("period", period)
    title = panel.get("title") or view["name"]
    try:
        result = _call_source(panel["source"], scope, period)
    except Exception as exc:                       # noqa: BLE001
        return f"<div class='dp-err'>{title}: {type(exc).__name__}</div>"

    shape = (view.get("binding") or {}).get("shape")
    field = panel["source"].get("field")
    if shape == "scalar":
        val = _dig(result, field)
        shown = f"{val:,}" if isinstance(val, (int, float)) else "n/a"
        return render.render_panel_macro("kpi_tile", {"value": shown, "label": title})
    if shape in ("table", "categorical"):
        rows = _dig(result, field) if field else result
        rows = rows if isinstance(rows, list) else []
        cols = _auto_columns(rows)
        return render.render_panel_macro("data_table", {"columns": cols, "rows": rows})
    if shape == "series":
        series = _dig(result, field) if field else result.get("series")
        return render.render_panel_macro("line_chart",
                                         {"series": series if isinstance(series, list) else []})
    return f"<div class='dp-err'>{title}: unsupported shape {shape}</div>"


def _auto_columns(rows) -> list:
    """Derive data_table column specs from the keys of the first row: numbers get
    kind 'num' + right align, everything else is text."""
    if not rows:
        return []
    first = rows[0]
    cols = []
    for k, v in first.items():
        num = isinstance(v, (int, float))
        cols.append({"label": k, "key": k, "kind": "num" if num else "text",
                     "align": "num" if num else None})
    return cols
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_dashboards.ResolverTest -v`
Expected: PASS. (If `contribution()` needs a populated DB, the dev `report.db` already has data.)

- [ ] **Step 5: Commit**

```bash
git add dashboards.py tests/test_dashboards.py
git commit -m "dashboards: panel resolver with kind adapters"
```

---

## Task 6: CRUD API

**Files:**
- Modify: `server.py` (`do_GET` for `GET /api/dashboard[/<id>]`; `do_POST` for create/update/delete)
- Test: manual curl (documented) + a route smoke test in `tests/test_dashboards.py`

- [ ] **Step 1: Write the failing test (validation gate)**

```python
# append to tests/test_dashboards.py
class ApiValidationTest(unittest.TestCase):
    def test_bad_spec_is_rejected_by_validator(self):
        ok, err = dashboards.validate_spec({"panels": "not a list", "title": "x"})
        self.assertFalse(ok)
```

(The HTTP layer is thin; the security-relevant logic — validation + ownership — is unit-tested via `dashboards`/`store`. This test locks the validator contract the API relies on.)

- [ ] **Step 2: Add the GET routes**

In `server.py` `do_GET`, near the other `/api/…` branches:

```python
        elif path == "/api/dashboard" or path.startswith("/api/dashboard/"):
            import store, dashboards
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                did = path[len("/api/dashboard/"):] if path != "/api/dashboard" else ""
                if not did:
                    self.send_json({"ok": True, "dashboards": store.list_dashboards(conn, login)})
                    return
                d = store.get_dashboard(conn, did)
                if not d or (d["visibility"] != "shared" and d["owner_login"] != login):
                    self.send_json({"ok": False, "error": "not found"}, 404)
                    return
                self.send_json({"ok": True, "dashboard": d})
            finally:
                conn.close()
```

- [ ] **Step 3: Add the POST routes (create/update/delete)**

In `server.py` `do_POST` (after `reject_cross_origin`), add:

```python
        if path == "/api/dashboard" or path.startswith("/api/dashboard/"):
            import store, dashboards
            payload = self._read_json_body()
            if payload is None:
                return
            action = (payload.get("action") or "save")
            spec = payload.get("spec") or {}
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                if login is None:
                    self.send_json({"ok": False, "error": "sign-in required"}, 403)
                    return
                did = path[len("/api/dashboard/"):] if path != "/api/dashboard" else ""
                if action == "delete" and did:
                    d = store.get_dashboard(conn, did)
                    if not d or d["owner_login"] != login:
                        self.send_json({"ok": False, "error": "not found"}, 404)
                        return
                    store.delete_dashboard(conn, did)
                    self.send_json({"ok": True})
                    return
                ok, err = dashboards.validate_spec(spec)
                if not ok:
                    self.send_json({"ok": False, "error": err}, 400)
                    return
                vis = payload.get("visibility") or "private"
                if did:                              # update — owner only
                    d = store.get_dashboard(conn, did)
                    if not d or d["owner_login"] != login:
                        self.send_json({"ok": False, "error": "not found"}, 404)
                        return
                    store.update_dashboard(conn, did, title=spec.get("title"),
                                           spec=spec, visibility=vis)
                    self.send_json({"ok": True, "id": did})
                else:                                # create
                    new_id = store.create_dashboard(conn, login, spec.get("title"),
                                                     spec, visibility=vis)
                    self.send_json({"ok": True, "id": new_id})
            finally:
                conn.close()
            return
```

- [ ] **Step 4: Run the validator test + a live curl smoke**

Run: `.venv/bin/python -m unittest tests.test_dashboards.ApiValidationTest -v` → PASS.
Then start the server and curl (sign-in simulated via the proxy header):
```bash
set -a; source .env; set +a
PORTAL_HOST=127.0.0.1 REPORT_PORT=8099 .venv/bin/python server.py >/tmp/dash.log 2>&1 &
sleep 5
curl -s -X POST http://127.0.0.1:8099/api/dashboard \
  -H 'Content-Type: application/json' -H 'X-Forwarded-Preferred-Username: someuser' \
  -d '{"spec":{"title":"Test","panels":[{"id":"p1","component":"kpi_tile","source":{"tool":"contribution","field":"totals.commits"}}]}}'
```
Expected: `{"ok": true, "id": "dash_…"}`. Then `curl .../api/dashboard -H 'X-Forwarded-Preferred-Username: someuser'` lists it. Kill the server; delete the test row: `.venv/bin/python -c "import store;c=store.connect();c.execute(\"DELETE FROM dashboard WHERE title='Test'\");c.commit()"`.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_dashboards.py
git commit -m "dashboards: CRUD API (create/list/get/update/delete)"
```

---

## Task 7: Dashboard page + panel fragment endpoint

**Files:**
- Create: `templates/dashboard.j2` (page shell + global controls + panel grid)
- Modify: `server.py` (`GET /dashboard/<id>`, `GET /api/dashboard/panel`)
- Modify: `render.py` (add `render_dashboard_page(dashboard)` that fills the shell)

- [ ] **Step 1: Add the panel fragment route**

In `server.py` `do_GET`:

```python
        elif path == "/api/dashboard/panel":
            from urllib.parse import parse_qs
            import store, dashboards, json as _json
            qs = parse_qs(urlparse(self.path).query)
            did = (qs.get("id", [""])[0])
            pid = (qs.get("panel", [""])[0])
            scope = (qs.get("scope", [""])[0])
            period = (qs.get("period", ["all"])[0])
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                d = store.get_dashboard(conn, did)
                if not d or (d["visibility"] != "shared" and d["owner_login"] != login):
                    self.send_error(HTTPStatus.NOT_FOUND); return
                panel = next((p for p in d["spec"]["panels"] if p.get("id") == pid), None)
                if not panel:
                    self.send_error(HTTPStatus.NOT_FOUND); return
                html = dashboards.render_panel(panel, scope=scope, period=period)
            finally:
                conn.close()
            self.send_bytes(html.encode(), "text/html; charset=utf-8")
```

- [ ] **Step 2: Add the page route**

In `server.py` `do_GET`, add:

```python
        elif path.startswith("/dashboard/"):
            import store, render
            did = path[len("/dashboard/"):]
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                d = store.get_dashboard(conn, did)
                if not d or (d["visibility"] != "shared" and d["owner_login"] != login):
                    self.send_error(HTTPStatus.NOT_FOUND); return
                page = render.render_dashboard_page(d)
            finally:
                conn.close()
            self.send_bytes(page.encode(), "text/html; charset=utf-8")
```

- [ ] **Step 3: Create the page template**

Create `templates/dashboard.j2`. It renders the shell, a global scope/period control bar, and a panel `<div>` per spec panel with `data-panel="<id>"` and `data-width="<width>"`; a small inline script fetches `/api/dashboard/panel` for each on load and on control change.

```jinja
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title|e }} — Constructor Insight</title>
<style>
  body{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;margin:0;background:#f6f8fa;color:#1f2328}
  .wrap{padding:20px 26px}
  .grid{display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin-top:14px}
  .cell{grid-column:span var(--w,2);background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:12px;min-height:80px}
  .ctrls{display:flex;gap:8px;align-items:center}
  .ctrls select,.ctrls button{border:1px solid #d0d7de;border-radius:8px;padding:5px 10px;background:#fff;font:inherit}
  .dp-err{color:#cf222e;font-size:12px}
</style></head><body><div class="wrap">
<h1>{{ title|e }}</h1>
<div class="ctrls">
  <select id="scope"><option value="">Whole org</option>{{ scope_options|safe }}</select>
  <select id="period">
    <option value="7d">7 days</option><option value="30d">30 days</option>
    <option value="90d">90 days</option><option value="365d">1 year</option>
    <option value="all" selected>All-time</option>
  </select>
</div>
<div class="grid" id="grid">
{% for p in panels %}<div class="cell" data-panel="{{ p.id|e }}" style="--w:{{ p.get('width', 2) }}"></div>{% endfor %}
</div>
</div>
<script>
var DID = {{ id|tojson }};
function load(){
  var scope=document.getElementById('scope').value, period=document.getElementById('period').value;
  document.querySelectorAll('.cell').forEach(function(c){
    var pid=c.getAttribute('data-panel');
    fetch('/api/dashboard/panel?id='+encodeURIComponent(DID)+'&panel='+encodeURIComponent(pid)
      +'&scope='+encodeURIComponent(scope)+'&period='+encodeURIComponent(period))
      .then(function(r){return r.text();}).then(function(h){c.innerHTML=h;})
      .catch(function(){c.innerHTML='<div class="dp-err">failed</div>';});
  });
}
document.getElementById('scope').addEventListener('change', load);
document.getElementById('period').addEventListener('change', load);
load();
</script>
</body></html>
```

- [ ] **Step 4: Add `render_dashboard_page`**

In `render.py`:

```python
def render_dashboard_page(dashboard: dict) -> str:
    """Fill templates/dashboard.j2 for one dashboard row (from store.get_dashboard)."""
    import discovery, store
    spec = dashboard["spec"]
    conn = store.connect()
    try:
        targets = discovery.scope_targets(conn) if hasattr(discovery, "scope_targets") else {}
    finally:
        conn.close()
    opts = []
    for lvl in ("org", "element", "repo"):
        for t in (targets.get(lvl) or []):
            opts.append(f'<option value="{lvl}:{t}">{t}</option>')
    return _env().get_template("dashboard").render(
        id=dashboard["id"], title=spec.get("title", "Dashboard"),
        panels=spec.get("panels", []), scope_options="".join(opts))
```

Register `dashboard.j2` in the `_env()` DictLoader: add `"dashboard": _load_tmpl("dashboard")` to the loader dict and `DASHBOARD = _load_tmpl("dashboard")` near the other `_load_tmpl` module constants. (Check `discovery` for the real scope-target accessor; if none, pass `scope_options=""` for slice 1 and refine later.)

- [ ] **Step 5: Live verify**

Start the server (as in Task 6), create a dashboard, open `http://127.0.0.1:8099/dashboard/<id>` in the browser preview, confirm the KPI panel renders a number and changing period refetches. Screenshot for proof. Clean up the test row.

- [ ] **Step 6: Commit**

```bash
git add server.py render.py templates/dashboard.j2
git commit -m "dashboards: /dashboard/<id> page + panel fragment endpoint"
```

---

## Task 8: End-to-end test + suite green + changelog

**Files:**
- Test: `tests/test_dashboards.py` (add `EndToEndTest`)
- Modify: `changelog.py`

- [ ] **Step 1: Add an end-to-end test (spec → store → render)**

```python
# append to tests/test_dashboards.py
class EndToEndTest(unittest.TestCase):
    def test_create_then_render_each_panel(self):
        conn = store.connect()
        spec = {"title": "E2E", "panels": [
            {"id": "a", "component": "kpi_tile",
             "source": {"tool": "contribution", "field": "totals.commits"}},
            {"id": "b", "component": "data_table",
             "source": {"tool": "contribution", "field": "by_company"}},
        ]}
        ok, err = dashboards.validate_spec(spec)
        self.assertTrue(ok, err)
        did = store.create_dashboard(conn, "tester", "E2E", spec)
        try:
            d = store.get_dashboard(conn, did)
            for p in d["spec"]["panels"]:
                html = dashboards.render_panel(p, scope="", period="all")
                self.assertTrue(html and "dp-err" not in html, p["id"])
        finally:
            store.delete_dashboard(conn, did)
            conn.close()
```

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/python -m unittest discover -s tests -q 2>&1 | grep -E "^(Ran|OK|FAILED)"`
Expected: `OK` (187 existing + the new dashboard tests).

- [ ] **Step 3: Add a changelog entry**

In `changelog.py`, under the `2026-07-22` block, prepend:
```python
{"type": "feature", "title": "Build your own dashboards (foundation)",
 "detail": "Dashboards can now be created, stored and shared as a spec of panels — "
           "each panel is a report component bound to a metric/tool, rendered live and "
           "re-sliceable by period and scope. The visual builder and AI authoring come "
           "next; this ships the spec, storage, API and renderer."},
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_dashboards.py changelog.py
git commit -m "dashboards: end-to-end test + changelog"
```

---

## Notes for the implementer

- **Align as we go:** the exact `binding` descriptor and the `_auto_columns` mapping are the parts most likely to need refinement against real macro signatures — verify `kpi_tile`, `data_table`, and `line_chart` params in `view_registry.py`/the macros as you wire each adapter, and adjust the adapter kwargs to match.
- **Deploy** only after the suite is green, via `./deploy.sh` (it backs up `report.db` first). The `dashboard` table is created by `_SCHEMA` on connect — no separate migration needed.
- **Out of scope (later slices):** manual drag-drop editor, AI copilot authoring, more component kinds, fine-grained permissions, versioning.
