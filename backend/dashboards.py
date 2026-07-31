"""Dashboard spec: validation + (later) server-side panel rendering.

A dashboard spec is {title:str, panels:[panel]}. A panel binds a views_catalog
component to a tooldefs data source; period/scope are supplied at render time (a panel
may `pin` its own). No raw SQL — sources are catalog tools only.
"""
from __future__ import annotations

import re

from markupsafe import escape as _esc

import metrics_registry
import render
import tooldefs
import view_registry

_SCOPE_RE = re.compile(r"^(org|element|repo|project):.+$")
_PERIOD_RE = re.compile(r"^(7d|30d|90d|365d|all)$")

# Dashboard-safe data tools only — no raw SQL (sql_query is excluded), so a shared
# dashboard can never be used as a durable SQL browser.
_DASHBOARD_TOOLS = {"contribution", "delivery", "trend", "flow", "person", "list_items"}


def _dashboard_components() -> dict:
    """id -> view spec, for views flagged dashboard-eligible."""
    return {v["name"]: v for v in view_registry.dashboard_views()}


def dashboard_catalog() -> dict:
    """The palette for the editor: dashboard-eligible components (name/kind/purpose)
    and the dashboard-safe data tools. Both come straight from the catalogs, so a new
    component/tool appears automatically."""
    comps = [{"name": v["name"], "kind": v["kind"], "purpose": v.get("purpose", "")}
             for v in view_registry.dashboard_views()]
    return {"components": comps, "tools": sorted(_DASHBOARD_TOOLS)}


# ---- measures() ---------------------------------------------------------------

# Fallback category (a GROUPS title) when a field's leaf name doesn't match a
# registered metric — keeps every measure catalog-derived (title text lives in
# metrics_registry.GROUPS), just picked by tool instead of by metric group.
_TOOL_DEFAULT_CATEGORY = {
    "contribution": "Volume & people",
    "delivery": "Delivery — PRs & issues",
    "trend": "Trend & comparison",
    "flow": "Flow & CI health",
}

# Tokens that read better upper-cased than title-cased when humanising a field
# name (e.g. "ci_pass_rate" -> "CI pass rate", not "Ci pass rate").
_LABEL_ACRONYMS = {"pr": "PR", "prs": "PRs", "ci": "CI", "cr": "CR",
                   "ttm": "TTM", "ttfr": "TTFR", "loc": "LOC", "cfd": "CFD"}

# A raw tool field doesn't always share its leaf name with the registry metric
# that describes it (e.g. flow()'s `reopen_rate` is registered as
# `flow_reopen_rate`) — bridge those few so real, catalogued metrics aren't
# curated out just because the tool's own key differs from the metric name.
_FIELD_METRIC_ALIAS = {
    "bounce_rate": "flow_bounce_rate",
    "reopen_rate": "flow_reopen_rate",
    "rereq_rate": "flow_rereview_rate",
    "cr_rate": "flow_changes_requested_rate",
    "by_company": "company_rows",
}


def _humanize(key: str) -> str:
    """snake_case field/metric name -> human label: 'by_company' -> 'By company',
    'ci_pass_rate' -> 'CI pass rate'. First word capitalised, rest lower-case,
    except recognised acronyms."""
    words = (key or "").split("_")
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if lw in _LABEL_ACRONYMS:
            out.append(_LABEL_ACRONYMS[lw])
        elif i == 0:
            out.append(w.capitalize())
        else:
            out.append(lw)
    return " ".join(p for p in out if p) or (key or "")


# Tools that return a walkable dict at default args (all-time / whole org).
# person/list_items need params, so they aren't introspected — they stay on the
# editor's advanced path with manual field entry.
_INTROSPECTABLE_TOOLS = ("contribution", "delivery", "trend", "flow")


def _walk_fields(tool: str) -> list[tuple]:
    """Every displayable leaf `tool` exposes at default args, as
    [(field, shape, component)] — the uncurated superset both measures() and
    tool_fields() build on. Walks the top level plus one level inside `totals`
    for the aggregate tools, and the per-dim series of trend(). Never raises: a
    tool that errors or returns a non-dict yields []."""
    try:
        result = _call_source({"tool": tool}, "", "all")
    except Exception:                                       # noqa: BLE001
        return []
    if not isinstance(result, dict):
        return []

    fields: list[tuple] = []
    if tool == "trend":
        if not isinstance(result.get("dates"), list):
            return []
        for key, value in result.items():
            if key == "dates":
                continue
            if (isinstance(value, list) and value
                    and all(isinstance(r, dict) for r in value)
                    and any(isinstance(r.get("vals"), list) for r in value)):
                fields.append((key, "series", "line_chart"))
        return fields

    scopes = [("", result)]
    totals = result.get("totals")
    if isinstance(totals, dict):
        scopes.append(("totals.", totals))
    for prefix, obj in scopes:
        for key, value in obj.items():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                fields.append((f"{prefix}{key}", "scalar", "kpi_tile"))
            elif (isinstance(value, list) and value
                  and all(isinstance(r, dict) for r in value)):
                fields.append((f"{prefix}{key}", "table", "data_table"))
    return fields


def _walk_all() -> dict:
    """{tool: [(field, shape, component)]} — one walk per introspectable tool."""
    return {t: _walk_fields(t) for t in _INTROSPECTABLE_TOOLS}


# Leaf-name patterns for the internal sample-size / helper counters that back a
# rate but aren't user-facing measures themselves (n_items, n_prs, pr_ttfr_n,
# bounced_n, min_items, …). These are the ONLY scalars curated out of the picker;
# everything else is surfaced (labelled from the registry when matched, else
# humanised), so we don't silently drop legitimate metrics like prs_total.
_INTERNAL_LEAVES = {"min_items", "max_items"}


def _is_internal_counter(leaf: str) -> bool:
    leaf = leaf or ""
    return leaf.endswith("_n") or leaf.startswith("n_") or leaf in _INTERNAL_LEAVES


def _measures_from(walked: dict) -> list[dict]:
    """The labelled measure list from pre-walked field tuples:
    [{label, category, shape, component, source:{tool, field}}]. Curation is a
    *denylist*, not an allowlist: a scalar is dropped only when its leaf name is
    an internal sample-size/helper counter (see _is_internal_counter — n_items,
    pr_ttfr_n, bounced_n, …); every other scalar is surfaced (so real metrics like
    prs_total / issues_total aren't lost just because their field name differs
    from the registry). Labels/categories come from the registry when the leaf
    matches (by name or via the small alias map), else a humanised field name /
    per-tool default category. Tables/series are always kept. Never raises: a
    registry lookup failure just degrades labelling (nothing gets dropped)."""
    try:
        metrics_by_name = {m["name"]: m for m in metrics_registry.all_metrics()}
        group_titles = dict(metrics_registry.GROUPS)
    except Exception:                                       # noqa: BLE001
        metrics_by_name, group_titles = {}, {}

    def _match(leaf, shape):
        m = metrics_by_name.get(_FIELD_METRIC_ALIAS.get(leaf, leaf))
        # A table/series measure is a breakdown, not a single figure — only trust
        # a name match for those if the metric's own unit says "breakdown" too
        # (rollup/%). Otherwise a scalar metric can share a leaf name with an
        # unrelated collection (e.g. flow's per-person `people` table vs. the
        # registered `people` head-count metric) and get its label/category.
        if m and shape != "scalar" and m.get("unit") not in ("rollup", "%"):
            m = None
        return m

    def _label_category(tool, field, shape):
        leaf = (field or "").rsplit(".", 1)[-1]
        m = _match(leaf, shape)
        if m:
            return _humanize(leaf), group_titles.get(m["group"], m["group"])
        return _humanize(leaf), _TOOL_DEFAULT_CATEGORY.get(tool, "Other")

    out = []
    for tool in _INTROSPECTABLE_TOOLS:
        for field, shape, component in walked.get(tool, []):
            leaf = (field or "").rsplit(".", 1)[-1]
            if shape == "scalar" and _is_internal_counter(leaf):
                continue  # internal sample-size counter — not a user-facing measure
            label, category = _label_category(tool, field, shape)
            out.append({"label": label, "category": category, "shape": shape,
                         "component": component,
                         "source": {"tool": tool, "field": field}})
    out.sort(key=lambda m: (m["category"], m["label"]))
    return out


def _fields_from(walked: dict) -> dict:
    """The uncurated per-tool field list for the editor's advanced picker:
    {tool: [{field, label, shape, component}]}. Includes the internal-counter
    fields measures() curates out, so advanced can still reach them. Tools that
    aren't introspectable at default args (person, list_items) don't appear —
    they map to manual entry in the editor."""
    out = {}
    for tool in _INTROSPECTABLE_TOOLS:
        items = []
        for field, shape, component in walked.get(tool, []):
            items.append({"field": field,
                          "label": _humanize((field or "").rsplit(".", 1)[-1]),
                          "shape": shape, "component": component})
        items.sort(key=lambda x: (x["label"], x["field"]))
        out[tool] = items
    return out


def measures() -> list[dict]:
    """Discoverable, labelled, categorised measure list (see _measures_from).
    Derived entirely from the catalogs — one walk per introspectable tool."""
    return _measures_from(_walk_all())


def tool_fields() -> dict:
    """Uncurated per-tool field list for advanced entry (see _fields_from)."""
    return _fields_from(_walk_all())


def measures_payload() -> dict:
    """{measures, tool_fields} from a single walk per tool — what the editor's
    /api/dashboard/measures endpoint returns, so opening the picker walks each
    tool once, not twice."""
    walked = _walk_all()
    return {"measures": _measures_from(walked), "tool_fields": _fields_from(walked)}


# ---- widget spec v2 -----------------------------------------------------------

# viz -> (primitive component name, render options for that primitive). Chart
# vizzes (line/area/column/bar/pie) no longer resolve to a server-rendered
# primitive — they're built into a Vega-Lite spec by vega_spec.build_spec and
# emitted as a vl-panel container (see _render_panel) — so only number/table
# stay here.
_VIZ_PRIMITIVE = {
    "number": ("kpi_tile", {}),
    "table":  ("data_table", {}),
}
# Chart vizzes rendered client-side via Vega-Lite (vega_spec.build_spec).
_CHART_VIZ = ("line", "area", "column", "bar", "pie")
# binding shape -> the viz types compatible with it. Scalars can be a single
# Number OR combined into a categorical chart (each measure = one bar/slice — the
# BI "Measure Values" pattern); series overlay on a line/area; a breakdown table
# fills a bar/pie/table on its own.
_SHAPE_VIZ = {"scalar": ["number", "column", "bar", "pie", "table"],
              "series": ["line", "area"],
              "table": ["bar", "column", "pie", "table"]}
# viz types that accept more than one measure. Only Number is single-measure;
# every chart type can hold several (scalars as bars/slices, series as lines).
_MULTI_FIELD_VIZ = {"line", "area", "column", "bar", "pie", "table"}
# legacy component name -> the v2 viz it maps to.
_LEGACY_COMPONENT_VIZ = {"kpi_tile": "number", "data_table": "table",
                         "line_chart": "line", "stacked_area": "area"}


def viz_options(shape: str) -> list:
    """The viz types selectable for a measure of this binding shape."""
    return list(_SHAPE_VIZ.get(shape, []))


def _normalize_panel(panel: dict) -> dict:
    """Return a v2 panel {id,title,width,viz,data:{tool,fields},pin?}. A v2 panel
    (has both `viz` and a `data` dict) passes through unchanged; a legacy
    {component,source:{tool,field}} panel is mapped onto the v2 shape. Never
    raises: a non-dict input yields {}."""
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


def _auto_bars(rows) -> list:
    """Normalise a table-shaped source value into [{label, value}] for the
    bar/column/pie charts (see chart_panel_data): first string column is the label, first non-bool
    numeric column is the value. Rows missing a numeric value are skipped.
    Never raises: bad input yields []."""
    rows = rows if isinstance(rows, list) else []
    if not rows or not isinstance(rows[0], dict):
        return []
    label_key = next((k for k, v in rows[0].items() if isinstance(v, str)), None)
    value_key = next((k for k, v in rows[0].items()
                      if isinstance(v, (int, float)) and not isinstance(v, bool)), None)
    if not value_key:
        return []
    out = []
    for r in rows:
        v = r.get(value_key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append({"label": str(r.get(label_key, "—")), "value": v})
    return out


def chart_panel_data(viz, result, fields, title):
    """What a chart panel needs, as data — the successor to vega_spec.build_spec.

    line / area  → {"kind", "dates", "series":[{name,key,color,vals}]}, the same
                   envelope the report charts use (render.chart_data), so one client
                   component reads both.
    bar / column / pie
                 → {"kind", "rows":[{label,value,color}]}, already ordered the way
                   the chart draws them: `bar` is horizontal and sorted descending,
                   which the Vega spec did with sort="-x" and which has to happen
                   here now that nothing downstream sorts.

    None when there is nothing to draw — the caller turns that into the panel's
    "no data" message, exactly as a null spec did. Never raises."""
    try:
        import render
        if viz in ("line", "area"):
            dates = result.get("dates") if isinstance(result, dict) else None
            if not isinstance(dates, list) or not dates:
                return None
            series = []
            for field in fields:
                value = _dig(result, field) if field else result
                for s in _auto_series(value, title):
                    if isinstance(s.get("vals"), list):
                        series.append({"name": s.get("name"), "vals": s["vals"]})
            data = render.chart_data(series, dates, area_first=(viz == "area"))
            return {**data, "kind": viz} if data else None

        rows = _bars_for(result, fields)
        if not rows:
            return None
        if viz == "bar":                    # horizontal bars read top-down, biggest first
            rows = sorted(rows, key=lambda r: -(r.get("value") or 0))
        return {"kind": viz,
                "rows": [{"label": r["label"], "value": r["value"],
                          "color": render._element_color(r["label"])} for r in rows]}
    except Exception:                       # noqa: BLE001 — a panel is never fatal
        return None


def _bars_for(result, fields) -> list:
    """Rows [{label, value}] for a categorical chart (bar/column/pie), two modes:
    - breakdown: if any field digs to a list of dicts, that breakdown fills the
      chart (label per row) via _auto_bars — first such field wins.
    - measure values (BI 'Measure Values'): otherwise every field that digs to a
      number becomes one bar/slice, labelled by its humanised field leaf.
    Never raises: bad fields just don't contribute."""
    for f in fields:
        v = _dig(result, f) if f else result
        if isinstance(v, list):
            return _auto_bars(v)
    out = []
    for f in fields:
        v = _dig(result, f)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append({"label": _humanize((f or "").rsplit(".", 1)[-1]), "value": v})
    return out


def validate_spec(spec) -> tuple[bool, str | None]:
    """(ok, error). Rejects missing title, unknown component/tool/viz, malformed
    scope/period/pin, and viz/field-count mismatches. Cheap structural check —
    no data access. Panels are normalised to v2 first; legacy panels whose
    ORIGINAL `component`/`source.tool` was invalid still get an error mentioning
    "component"/"tool" respectively, so legacy validation messages don't change."""
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
        # A panel is v2 exactly when _normalize_panel treats it as v2 (has `viz`
        # + a `data` dict) — decide "legacy" the same way, so a v2 panel that
        # carries a stray `source`/`component` key (e.g. an editor round-trip that
        # didn't strip old keys) isn't wrongly rejected as an unknown component.
        is_v2 = "viz" in p and isinstance(p.get("data"), dict)
        if not is_v2 and p.get("component") not in comps:
            return False, f"{where}: unknown component {p.get('component')!r}"
        norm = _normalize_panel(p)
        viz = norm.get("viz")
        if viz not in _VIZ_PRIMITIVE and viz not in _CHART_VIZ:
            return False, f"{where}: unknown viz {viz!r}"
        data = norm.get("data") or {}
        tool = data.get("tool")
        if tool not in _DASHBOARD_TOOLS:
            return False, f"{where}: unknown tool {tool!r}"
        # pin checks before the fields check: a legacy panel without a
        # `source.field` normalises to `fields=[]`, and pre-v2 validate_spec
        # never required a field at all — keep pin errors surfacing ahead of
        # the (new) fields check so those legacy panels still fail on the pin.
        pin = p.get("pin") or {}
        if "scope" in pin and pin["scope"] and not _SCOPE_RE.match(pin["scope"]):
            return False, f"{where}: bad pin.scope (org|element|repo|project:<target>)"
        if "period" in pin and pin["period"] and not _PERIOD_RE.match(pin["period"]):
            return False, f"{where}: bad pin.period"
        fields = data.get("fields")
        if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
            return False, f"{where}: data.fields must be a list of field names"
        # A legacy panel whose source had no `field` normalises to fields=[]; pre-v2
        # validate_spec never required a field (render just shows n/a), so keep those
        # re-savable. A v2-native panel must name at least one field.
        if not fields and is_v2:
            return False, f"{where}: data.fields must not be empty"
        if fields and viz not in _MULTI_FIELD_VIZ and len(fields) != 1:
            return False, f"{where}: viz {viz!r} takes exactly one field"
    return True, None


# ---- panel resolver ----------------------------------------------------------

_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}


def _dig(obj, path):
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
        since = ""
    return since, now.strftime("%Y-%m-%d")


def _call_source(source, scope, period):
    import inspect
    fn = tooldefs.DISPATCH[source["tool"]]
    since, until = _since_until(period)
    kwargs = dict(source.get("params") or {})
    accepted = set(inspect.signature(fn).parameters)
    for k, v in (("since", since), ("until", until), ("scope", scope or "")):
        if k in accepted:
            kwargs.setdefault(k, v)
    return fn(**{k: v for k, v in kwargs.items() if k in accepted})


def _auto_columns(rows) -> list:
    if not rows:
        return []
    cols = []
    for k, v in rows[0].items():
        num = isinstance(v, (int, float))
        cols.append({"label": k, "key": k, "kind": "num" if num else "text",
                     "align": "num" if num else None})
    return cols


def _auto_series(value, title) -> list:
    """Normalise a dug-out value into [{name, vals}] for the line_chart adapter.
    Handles the shapes tooldefs.trend() actually returns: a list of per-dim rows
    ({key/company/name, vals: [...]}, e.g. commit_rows/loc_rows), a dict of named
    arrays (e.g. throughput = {opened: [...], merged: [...], ttm: [...]}), or a
    flat list of numbers (e.g. contributors) treated as a single series."""
    if isinstance(value, dict):
        return [{"name": k, "vals": v} for k, v in value.items()
                if isinstance(v, list)]
    if isinstance(value, list) and value:
        if isinstance(value[0], dict):
            out = []
            for r in value:
                vals = r.get("vals")
                if isinstance(vals, list):
                    out.append({"name": r.get("key") or r.get("name")
                                 or r.get("company") or "series", "vals": vals})
            return out
        if isinstance(value[0], (int, float)):
            return [{"name": title, "vals": value}]
    return []


def _render_component(view, kwargs) -> str:
    """Render a component via its catalog `ref`. `tmpl:...` refs are Jinja macros
    from the panels template (rendered via render.render_panel_macro, by keyword).
    `fn:module.func` refs (the chart functions — line_chart, stacked_area) are Jinja
    *globals*, not macros exported by the panels template, so render_panel_macro
    can't import them (`{% from 'panels' import line_chart %}` raises UndefinedError:
    "does not export the requested name") — call the function directly instead."""
    ref = view.get("ref") or ""
    if ref.startswith("fn:"):
        _, _, path = ref.partition(":")
        mod_name, func_name = path.rsplit(".", 1)
        if mod_name != "render":
            raise ValueError(f"unsupported ref module: {mod_name}")
        fn = getattr(render, func_name)
        return str(fn(**kwargs))
    return render.render_panel_macro(view["name"], kwargs)


def render_panel(panel, scope, period) -> str:
    """Render one panel to HTML. Applies the panel's pin over scope/period, calls the
    source tool, binds the result to the component by its kind. Never raises — a bad
    panel/source/field degrades to a small 'n/a'/error tile, for ANY input shape
    (including a non-dict panel)."""
    if not isinstance(panel, dict):
        return "<div class='dp-err'>invalid panel</div>"
    try:
        return _render_panel(panel, scope, period)
    except Exception as exc:                       # noqa: BLE001
        title = (panel.get("title") or panel.get("id") or "panel"
                 if isinstance(panel, dict) else "panel")
        return f"<div class='dp-err'>{_esc(title)}: {_esc(type(exc).__name__)}</div>"


def resolve_panel_data(panel, scope, period) -> dict:
    """Resolve one panel to DATA (no HTML) — the JSON boundary the React
    <PanelRenderer> consumes. Returns {viz, title, pin, data} where `data` is the
    resolved shape for the panel's viz:

      number → {"value": <number|null>}       — the dug scalar, or None when the
                                                 field is missing / non-numeric
                                                 (the HTML path shows "n/a" for None).
      table  → {"columns": [...], "rows": [...]}  — the same columns/rows the HTML
                                                 table renders (breakdown field, or
                                                 the measure-values fallback).
      chart  → what to draw FROM               — from chart_panel_data: `kind` plus
        (line/area/                              a time series or labelled rows; the
         column/bar/pie)                         client composes it (PanelChart).

    On any resolve failure — unknown viz, tool not allowed, source exception, a
    missing field, or empty/no data — `data` is {"error": "<message>"} instead,
    mirroring the dp-err messages the HTML path emits (minus the HTML wrapper).
    Never raises for these expected failures. `_render_panel` calls this and only
    formats HTML on top, so both paths share one resolver."""
    panel = _normalize_panel(panel)
    viz = panel.get("viz")
    pin = panel.get("pin") or {}
    if viz not in _VIZ_PRIMITIVE and viz not in _CHART_VIZ:
        return {"viz": viz, "title": panel.get("title"), "pin": pin,
                "data": {"error": f"unknown viz {viz}"}}
    scope = pin.get("scope", scope)
    period = pin.get("period", period)
    # Title fallback matches _render_panel: the primitive component name for
    # number/table (kpi_tile/data_table), else the viz name for charts.
    prim = _VIZ_PRIMITIVE[viz][0] if viz in _VIZ_PRIMITIVE else None
    title = panel.get("title") or (prim if prim else viz)
    data = panel.get("data") or {}
    tool = data.get("tool")
    fields = data.get("fields") or []
    meta = {"viz": viz, "title": title, "pin": pin}
    if tool not in _DASHBOARD_TOOLS:
        return {**meta, "data": {"error": f"{title}: tool not allowed"}}
    try:
        result = _call_source({"tool": tool, "params": data.get("params")}, scope, period)
    except Exception as exc:                       # noqa: BLE001
        return {**meta, "data": {"error": f"{title}: {type(exc).__name__}"}}

    if viz == "number":
        if not fields:
            return {**meta, "data": {"error": f"{title}: no field"}}
        val = _dig(result, fields[0])
        value = val if isinstance(val, (int, float)) else None
        return {**meta, "data": {"value": value}}

    if viz in _CHART_VIZ:
        # DATA, not a chart spec. A panel's `viz` already says which picture to draw,
        # so the payload only has to carry what to draw it FROM — the client composes
        # it (frontend/src/widgets/registry). Two shapes, matching the two the retired
        # vega_spec.build_spec had: a time series for line/area, labelled rows for the
        # categorical three.
        data = chart_panel_data(viz, result, fields, title)
        if not data:
            return {**meta, "data": {"error": f"{title}: no data"}}
        return {**meta, "data": data}

    if viz == "table":
        first = _dig(result, fields[0]) if fields and fields[0] else result
        if isinstance(first, list):        # a breakdown field → its own table
            return {**meta, "data": {"columns": _auto_columns(first), "rows": first}}
        # otherwise a measure-values table: one row per scalar field
        rows = [{"measure": _humanize((f or "").rsplit(".", 1)[-1]), "value": v}
                for f in fields for v in [_dig(result, f)]
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not rows:
            return {**meta, "data": {"error": f"{title}: no data"}}
        cols = [{"label": "Measure", "key": "measure", "kind": "text"},
                {"label": "Value", "key": "value", "kind": "num", "align": "num"}]
        return {**meta, "data": {"columns": cols, "rows": rows}}

    return {**meta, "data": {"error": f"{title}: unsupported viz {viz}"}}


def _render_panel(panel, scope, period) -> str:
    resolved = resolve_panel_data(panel, scope, period)
    viz = resolved["viz"]
    title = resolved["title"]
    data = resolved["data"]
    comps = _dashboard_components()
    # The component lookup is an HTML-only concern (the data boundary is
    # component-agnostic). Keep it here, in the original order — before the error
    # passthrough — so a primitive viz with a missing catalog component still
    # degrades to "unknown component" exactly as before.
    view = None
    if viz in _VIZ_PRIMITIVE:
        prim, _opts = _VIZ_PRIMITIVE[viz]
        view = comps.get(prim)
        if not view:
            return "<div class='dp-err'>unknown component</div>"
    # A chart's resolved data IS its Vega-Lite spec (never carries an "error" key);
    # every failure path sets data={"error": …}.
    if isinstance(data, dict) and "error" in data and viz not in _CHART_VIZ:
        return f"<div class='dp-err'>{_esc(data['error'])}</div>"

    if viz == "number":
        value = data.get("value")
        shown = f"{value:,}" if isinstance(value, (int, float)) else "n/a"
        return render.render_panel_macro("kpi_tile", {"value": shown, "label": title})

    if viz in _CHART_VIZ:
        import json as _json
        if "error" in data:
            return f"<div class='dp-err'>{_esc(data['error'])}</div>"
        payload = _json.dumps(data).replace("</", "<\\/")
        return ('<div class="vl-panel">'
                '<script type="application/json" class="vl-spec">%s</script></div>' % payload)

    if viz == "table":
        return _render_component(view, {"columns": data["columns"], "rows": data["rows"]})

    return f"<div class='dp-err'>{_esc(title)}: unsupported viz {_esc(viz)}</div>"
