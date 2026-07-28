#!/usr/bin/env python3
"""View registry — the single source of truth for the visual-component catalog.

Mirrors metrics_registry, but for the *display* side: every reusable visual
component (KPI tile, sparkline, chart, chip…) is described once here with its
purpose, parameters and a usage example. Two audiences read the same catalog:

  * in-repo dashboard building — `ref` + `example` say how to call the component
    (a Jinja macro in templates/, or a render.py chart function exposed as a
    Jinja global);
  * external artifact building via MCP — `html_contract` + `example` + the CSS
    classes let a client (Claude/Copilot) reproduce the visual outside this repo.

A drift test verifies every registered component's `ref` actually resolves, so
the catalog can't go stale. Exposed as `/views` (page) and an MCP `views_catalog`
tool. To add a component: append a view(...) below and point `ref` at its macro
or function.
"""
from __future__ import annotations

import os
import re

# The repo root, one level up from backend/: templates/, assets/ and config.yaml
# live there, not next to the modules.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# group id -> title, controls catalog layout order
GROUPS = [
    ("numbers", "Numbers & KPIs"),
    ("charts", "Charts & trends"),
    ("tables", "Tables & breakdowns"),
    ("primitives", "Primitives & chips"),
]


def view(name: str, *, kind: str, group: str, purpose: str, when_to_use: str,
         ref: str, params: list, example: str, html_contract: str = "",
         binding: dict | None = None, dashboard: bool = False) -> dict:
    """One component spec.

    ref: "tmpl:<template-file>::<macro>" for a Jinja macro, or
         "fn:<module>.<qualname>" for a Python function (Jinja global chart).

    dashboard: True if this component can stand alone as a user-dashboard panel.
    binding: how a dashboard panel maps data onto this component's params —
             at minimum {"shape": ...} describing the expected data shape."""
    return {"name": name, "kind": kind, "group": group, "purpose": purpose,
            "when_to_use": when_to_use, "ref": ref, "params": params,
            "example": example, "html_contract": html_contract,
            "binding": binding or {}, "dashboard": dashboard}


def _p(name, type, desc, required=False, values=None):
    d = {"name": name, "type": type, "desc": desc, "required": required}
    if values:
        d["values"] = values
    return d


# --- the catalog -------------------------------------------------------------
_VIEWS = [
    view("kpi_tile", kind="tile", group="numbers",
         purpose="A single headline number with optional icon, period-over-period "
                 "delta, trend sparkline and a drill-through target.",
         when_to_use="One KPI you want scannable — a count / rate / duration — "
                     "ideally with its change vs the previous period.",
         ref="tmpl:panels/01_helpers.j2::kpi_tile",
         params=[
             _p("value", "str", "PRE-FORMATTED number: '1,204' / '42%' / '3.8d' / '—'", True),
             _p("label", "str", "what the number is", True),
             _p("sub", "str", "sub-label / context line"),
             _p("icon", "enum", "category chip (omit for a delta-only tile)",
                values=["commit", "pr", "bug", "epic", "feature", "spec", "loc", "people"]),
             _p("delta", "dict", "period delta {pct, dir:'up'|'down'|'flat', prev}"),
             _p("lower_better", "bool", "flip delta colour (up is bad: defects, time-to-merge…)"),
             _p("spark", "list", "sparkline points (see sparkline)"),
             _p("spark_color", "str", "CSS colour for the sparkline"),
             _p("drill", "dict", "drill target as data-* attrs, e.g. {'drill':'issue','flag':'is_bug'}"),
             _p("tip", "str", "tooltip on the value"),
         ],
         example="{{ kpi_tile(pr.totals.bugs|num, 'bugs opened', 'issues categorised as bug', "
                 "icon='bug', delta=dl.bugs, spark=sp.bugs_pts, spark_color='var(--c-bug)', "
                 "drill={'drill':'issue','flag':'is_bug'}) }}",
         html_contract="<div class='kpi'><div class='ktop'>[icon][delta]</div>"
                       "<div class='n num'>value</div><div class='l'>label</div>"
                       "<div class='l2'>sub</div>[sparkline]</div> — needs report CSS "
                       "classes .kpi/.ktop/.n/.l/.l2/.ico/.dlt/.spark.",
         dashboard=True, binding={"shape": "scalar", "value_param": "value"}),
    view("sparkline", kind="chart", group="charts",
         purpose="A tiny inline trend line (no axes) for a metric over sub-windows.",
         when_to_use="Show direction/shape of a KPI inside a tile or table cell.",
         ref="tmpl:panels/01_helpers.j2::sparkline",
         params=[
             _p("pts", "str", "SVG polyline points, 0..100 space (see render._spark_points)", True),
             _p("color", "str", "stroke colour", True),
         ],
         example="{{ sparkline(sp.commits_pts, 'var(--c-commit)') }}",
         html_contract="<svg class='spark' viewBox='0 0 100 26'><polyline .../></svg>"),
    view("line_chart", kind="chart", group="charts",
         purpose="Multi-series line/area chart over a shared date axis.",
         when_to_use="A trend over time with one or more series (e.g. per-company).",
         ref="fn:render._line_chart_vega",
         params=[
             _p("series", "list", "[{name, color, vals:[…]}] one entry per line", True),
             _p("dates", "list", "x-axis labels aligned to vals", True),
             _p("unit", "str", "y-axis unit label"),
             _p("area_first", "bool", "fill under the first series"),
         ],
         example="{{ line_chart(series, dates, unit='commits', area_first=true) }}",
         html_contract="a .vl-panel div carrying a Vega-Lite spec, hydrated client-side.",
         dashboard=True, binding={"shape": "series", "series_param": "series"}),
    view("stacked_area", kind="chart", group="charts",
         purpose="Stacked-area chart (total + company breakdown) over a date axis.",
         when_to_use="Cumulative composition over time — e.g. contributors by company.",
         ref="fn:render._stacked_area_vega",
         params=[
             _p("rows", "list", "per-date stacked values", True),
             _p("dates", "list", "x-axis labels", True),
             _p("company_rows", "list", "series/colour definitions", True),
             _p("unit", "str", "value unit (default 'commits')"),
             _p("noun", "str", "label noun for tooltips"),
         ],
         example="{{ stacked_area(rows, dates, company_rows, unit='commits') }}",
         html_contract="a .vl-panel div carrying a Vega-Lite spec, hydrated client-side."),
    view("data_table", kind="table", group="tables",
         purpose="A schema-driven, sortable table — declare columns, pass row objects.",
         when_to_use="Any multi-column breakdown (by company, element, reviewer, repo). "
                     "Each column header sorts by the cell's raw value; cells cover the "
                     "full range of table needs, each with its own drill.",
         ref="tmpl:panels/01_helpers.j2::data_table",
         params=[
             _p("columns", "list", "column specs. kind ∈ num|loc|pctp|dur|hours|raw (value) · "
                "text (+swatch 'dot'/'edot', color_key, tags[]) · bar (width_key, content_key, "
                "+drill/tip) · heatmap (alpha_key) · link ('gh', +tags) · pair (key,key2,sep) · "
                "bool (bool_map) · html (key → |safe). Common: label, label_html?, tip?, key, "
                "fmt, unit, dash, align, sort?, cls? (extra td/th class, e.g. 'g' separator), "
                "tip_key, tags[], drill{…'@field'…}, drill_if, celltip, sort_key.", True),
             _p("rows", "list", "row objects (dicts); columns read fields by key", True),
             _p("empty", "str", "message shown when rows is empty"),
             _p("cap", "int", "show top-N rows + a reveal toggle (sorting reveals all)"),
             _p("groups", "list", "[{label, span, tip?}] → a two-row grouped header (group "
                "band over the columns) + grouped mode (sticky first column, group borders); "
                "mark each group's first column cls:'g' to carry the separator into the body"),
         ],
         example="{{ data_table([{'label':'Company','kind':'text','key':'company','swatch_key':'color'},"
                 "{'label':'Bugs','key':'bugs','drill':{'drill':'issue','flag':'is_bug','company':'@company'}},"
                 "{'label':'LOC %','kind':'bar','width_key':'loc_pct'}], pr.company_rows) }}",
         html_contract="<table class='dt'><thead>… sortable th …</thead><tbody> rows with "
                       "per-cell data-sort </tbody></table> — sorting JS in report.j2; "
                       "kinds reuse bar_cell + the .db/.hm CSS.",
         dashboard=True, binding={"shape": "table", "rows_param": "rows",
                                   "columns_param": "columns"}),
    view("cat_table", kind="table", group="tables",
         purpose="A categorical breakdown table — label · value · share-bar per row.",
         when_to_use="Show how a total splits across categories (issue categories, "
                     "work types, buckets) with each row's share as an in-cell bar.",
         ref="tmpl:panels/01_helpers.j2::cat_table",
         params=[
             _p("rows", "list", "[{label, value, pct(0-100), drill?}] one per category", True),
             _p("label_head", "str", "header for the label column (default 'Category')"),
             _p("value_head", "str", "header for the value column (default 'Count')"),
             _p("share_head", "str", "header for the share column (default 'Share')"),
             _p("share_tip", "str", "tooltip on the share header"),
             _p("mono", "bool", "render labels as <code> (default true)"),
             _p("empty", "str", "message shown when rows is empty"),
         ],
         example="{{ cat_table(rows, value_head='Issues', "
                 "share_tip='share of issues opened in the period') }}",
         html_contract="<table><tr><th>…</th></tr>{row: label · value · "
                       "bar_cell}…</table> — needs .db bar CSS; uses bar_cell.",
         # NOT dashboard-eligible (yet): cat_table's rows contract is
         # [{label, value, pct(0-100), drill?}], but tool rows (e.g.
         # contribution().by_company, contribution().categories) carry several
         # numeric fields with no reliable "this one is the value" marker —
         # an auto-mapping (first text field -> label, first numeric field ->
         # value) picks the wrong field more often than not (e.g. by_company's
         # first numeric field is `people`, not the intended `commits`) and
         # some tool rows have no per-row share at all. Rendering that would
         # silently show wrong numbers under a right-looking table. Deferred
         # until a panel spec can name which field is the value (and, where
         # needed, how to compute the share) — see dashboards.render_panel's
         # "table" shape handling, which only routes to data_table for now.
         binding={"shape": "categorical", "rows_param": "rows"}),
    view("bar_cell", kind="table", group="tables",
         purpose="A table cell with an in-cell horizontal bar (fill = a percent).",
         when_to_use="A column where each value should also read as a proportion — "
                     "LOC%, share, review volume — the bar length shows magnitude.",
         ref="tmpl:panels/01_helpers.j2::bar_cell",
         params=[
             _p("width", "number", "fill percent 0-100 (independent of the shown text)", True),
             _p("content", "str", "pre-rendered cell value", True),
             _p("drill", "dict", "data-* attrs on the cell"),
             _p("tip", "str", "tooltip on the cell"),
         ],
         example="{{ bar_cell(c.loc_pct|pct, (c.loc_pct|pct) ~ '%') }}",
         html_contract="<td class='db' style='--w:<width>%'>content</td> — .db draws "
                       "the bar from the --w custom property."),
    view("kchip", kind="primitive", group="primitives",
         purpose="Small coloured category icon-chip (bug/pr/commit/…).",
         when_to_use="Prefix a KPI or row with its category glyph, consistent colour.",
         ref="tmpl:panels/01_helpers.j2::kchip",
         params=[_p("cat", "enum", "category id", True,
                    values=["commit", "pr", "bug", "epic", "feature", "spec", "loc", "people"])],
         example="{{ kchip('bug') }}",
         html_contract="<span class='ico' style='background:var(--c-<cat>)'><svg…></span>"),
    view("deltachip", kind="primitive", group="primitives",
         purpose="Period-over-period change pill (▲/▼ N%), colour by direction.",
         when_to_use="Show change vs the previous equal period next to a number.",
         ref="tmpl:panels/01_helpers.j2::deltachip",
         params=[
             _p("d", "dict", "delta {pct, dir, prev} (from render.delta_map)", True),
             _p("lower_better", "bool", "flip colour when up is bad"),
         ],
         example="{{ deltachip(dl.bugs, lower_better=true) }}",
         html_contract="<span class='dlt up|down|flat'>▲ N%</span>"),
    view("segbar", kind="primitive", group="primitives",
         purpose="A horizontal stacked proportional bar built from segments — one "
                 "primitive for band splits, score make-up, any part-of-whole.",
         when_to_use="Show composition inline: a distribution bar, a per-row make-up, "
                     "a category split — where each segment's width is its share.",
         ref="tmpl:panels/01_helpers.j2::segbar",
         params=[
             _p("segments", "list",
                "[{value, color, label?, tip?}] — widths are value-proportional; "
                "zero-value segments are skipped in the bar", True),
             _p("height", "int", "bar height in px (default 14)"),
             _p("legend", "bool", "render an inline swatch legend below (all segments)"),
         ],
         example="{{ segbar([{'value':6,'color':'#10b981','label':'Strong'},"
                 "{'value':33,'color':'#ef4444','label':'Building'}], legend=true) }}",
         html_contract="<span class='segbar'><i style='flex:<v>;background:<color>'></i>…"
                       "</span>[<span class='segleg'>…</span>] — needs .segbar / .segleg CSS."),
]


def all_views() -> list:
    order = {gid: i for i, (gid, _) in enumerate(GROUPS)}
    return sorted(_VIEWS, key=lambda v: (order.get(v["group"], 99), v["name"]))


def dashboard_views() -> list:
    """Views usable as standalone dashboard panels (dashboard=True)."""
    return [v for v in all_views() if v.get("dashboard")]


def names() -> set:
    return {v["name"] for v in _VIEWS}


def resolve_ref(ref: str):
    """Return where a view's ref points, and whether it exists — for the drift test
    and the /views page. Kinds: 'tmpl:<file>::<macro>' or 'fn:<module>.<qual>'."""
    if ref.startswith("tmpl:"):
        spec = ref[len("tmpl:"):]
        rel, _, macro = spec.partition("::")
        path = os.path.join(ROOT, "templates", rel)
        if not os.path.exists(path):
            return {"ok": False, "where": path, "why": "template file missing"}
        body = open(path, encoding="utf-8").read()
        ok = re.search(r"{%-?\s*macro\s+" + re.escape(macro) + r"\s*\(", body) is not None
        return {"ok": ok, "where": f"{rel} :: {macro}",
                "why": "" if ok else "macro not found"}
    if ref.startswith("fn:"):
        dotted = ref[len("fn:"):]
        mod, _, qual = dotted.rpartition(".")
        try:
            obj = __import__(mod)
            for part in qual.split("."):
                obj = getattr(obj, part)
            return {"ok": callable(obj), "where": f"{mod}.py · {qual}()",
                    "why": "" if callable(obj) else "not callable"}
        except Exception as exc:                     # noqa: BLE001
            return {"ok": False, "where": dotted, "why": str(exc)}
    return {"ok": False, "where": ref, "why": "unknown ref kind"}
