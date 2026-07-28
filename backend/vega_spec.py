"""Vega-Lite spec builder for dashboard charts (line/area/column/bar/pie).

Pure functions: build_spec() never raises — bad/empty input returns None and
the caller (dashboards._render_panel) falls back to a small "no data" tile.

No circular import: dashboards does NOT import this module at module top, so
`import dashboards` here is safe (verify with `python -c "import vega_spec"`).
"""
import json

from markupsafe import Markup

import dashboards
import render

# Hex values mirrored from shell.BASE_CSS :root — that CSS is the single
# source of truth; these are copied here so Vega (canvas/SVG, no CSS vars)
# can be themed identically.
_TOKENS = {
    "panel": "#ffffff",
    "line": "#eceef2",
    "line2": "#e2e6ec",
    "ink": "#101828",
    "ink2": "#475467",
    "mut": "#8a93a3",
    "acc": "#5b5bf0",
}
_FONT = "Inter, -apple-system, Segoe UI, Roboto, sans-serif"

_VL_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"


def vega_config() -> dict:
    """Shared Vega-Lite `config` block themed to our design (not Vega defaults)."""
    t = _TOKENS
    return {
        "background": None,
        "font": _FONT,
        "padding": 6,
        "view": {"stroke": None},
        "axis": {
            "labelColor": t["mut"], "titleColor": t["mut"],
            "labelFont": _FONT, "titleFont": _FONT,
            "labelFontSize": 10, "titleFontSize": 10, "titleFontWeight": 600,
            "gridColor": t["line"], "gridWidth": 0.5,
            "domainColor": t["line2"], "tickColor": t["line2"],
            "title": None,
        },
        "legend": {
            "orient": "bottom", "symbolType": "circle",
            "labelColor": t["ink2"], "titleColor": t["mut"], "title": None,
            "labelFont": _FONT, "labelFontSize": 11,
        },
        "range": {"category": render._ELEM_PALETTE},
        "mark": {"color": t["acc"], "tooltip": True},
        # data-point markers a touch larger than the old r≈3px dots, on lines AND areas.
        "point": {"filled": True, "size": 45},
        "line": {"strokeWidth": 1.6, "point": True},
        "bar": {"cornerRadiusEnd": 3},
        "arc": {"stroke": t["panel"], "strokeWidth": 1},
    }


def color_scale(labels) -> dict:
    """Stable per-label colours (matches the hand-rolled render._element_color hash),
    so a company/element keeps the same colour across charts."""
    labels = list(labels)
    return {"domain": labels, "range": [render._element_color(l) for l in labels]}


def _envelope(**kwargs) -> dict:
    spec = {
        "$schema": _VL_SCHEMA,
        "config": vega_config(),
        "width": "container",
        "height": 220,
        "autosize": {"type": "fit", "contains": "padding"},
    }
    spec.update(kwargs)
    return spec


def _line_or_area_spec(viz, result, fields, title):
    if not isinstance(result, dict):
        return None
    dates = result.get("dates")
    if not isinstance(dates, list) or not dates:
        return None
    rows = []
    series_names = []
    for field in fields:
        value = dashboards._dig(result, field) if field else result
        for s in dashboards._auto_series(value, title):
            name = s.get("name")
            vals = s.get("vals")
            if not isinstance(vals, list):
                continue
            if name not in series_names:
                series_names.append(name)
            for d, v in zip(dates, vals):
                rows.append({"date": d, "series": name, "value": v})
    if not rows:
        return None

    mark = "line" if viz == "line" else {"type": "area", "line": True, "opacity": 0.5}
    return _envelope(
        data={"values": rows},
        mark=mark,
        encoding={
            "x": {"field": "date", "type": "ordinal", "sort": dates},
            "y": {"field": "value", "type": "quantitative"},
            "color": {"field": "series", "type": "nominal",
                      "scale": color_scale(series_names)},
        },
    )


def _categorical_spec(viz, result, fields, title):
    rows = dashboards._bars_for(result, fields)
    if not rows:
        return None
    labels = [r["label"] for r in rows]
    scale = color_scale(labels)
    data = {"values": rows}

    if viz == "column":
        return _envelope(
            data=data,
            mark="bar",
            encoding={
                "x": {"field": "label", "type": "nominal", "sort": None,
                      "axis": {"labelAngle": 0}},
                "y": {"field": "value", "type": "quantitative"},
                "color": {"field": "label", "type": "nominal", "scale": scale,
                          "legend": None},
            },
        )
    if viz == "bar":
        return _envelope(
            data=data,
            mark="bar",
            encoding={
                "y": {"field": "label", "type": "nominal", "sort": "-x"},
                "x": {"field": "value", "type": "quantitative"},
                "color": {"field": "label", "type": "nominal", "scale": scale,
                          "legend": None},
            },
        )
    # pie
    return _envelope(
        data=data,
        mark={"type": "arc", "innerRadius": 40},
        encoding={
            "theta": {"field": "value", "type": "quantitative"},
            "color": {"field": "label", "type": "nominal", "scale": scale},
        },
    )


def panel_html(spec):
    """Wrap a Vega-Lite spec dict as the `<div class="vl-panel">` the report/dashboard
    templates hydrate client-side (see hydrateVega). Falsy spec -> Markup("") so the
    caller's "no data" hint still shows. Never raises."""
    if not spec:
        return Markup("")
    try:
        payload = json.dumps(spec).replace("</", "<\\/")
    except Exception:                       # noqa: BLE001
        return Markup("")
    return Markup(
        '<div class="vl-panel"><script type="application/json" class="vl-spec">%s</script></div>'
        % payload
    )


def _yfmt(unit):
    """d3 number format for a value axis/tooltip: hours as a trimmed float, else
    SI-compact (2.6M, 12k) — matches the readability the hand-rolled charts had.
    (Report charts carry counts/LOC/hours, never rates, so ~s is safe here.)"""
    return ".2~f" if unit == "hours" else "~s"


def _hover_layer(cat_field, cat_values, dates, fmt, with_total=False):
    """A nearest-x shared-hover layer — the old ax-hit behaviour: hovering anywhere
    in an x-column (not just on a point) shows a faint vertical marker and ONE
    combined tooltip listing every series/company value at that x. Pivots the long
    data to one row per x (a column per category) so the tooltip can list them all.
    `cat_field` is 'series' (line) or 'company' (stacked area); `cat_values` are the
    category names, in order. `with_total` (stacked areas) prepends the stack sum,
    matching the old '<total> total' tooltip header."""
    transform = [{"pivot": cat_field, "value": "value", "groupby": ["x"]}]
    tooltip = [{"field": "x", "type": "nominal", "title": "Period"}]
    if with_total and cat_values:
        # sum the pivoted category columns → the stack total (null-safe per column).
        expr = "+".join("(datum[%s]||0)" % json.dumps(c) for c in cat_values)
        transform.append({"calculate": expr, "as": "Total"})
        tooltip.append({"field": "Total", "type": "quantitative", "title": "Total", "format": fmt})
    tooltip += [{"field": c, "type": "quantitative", "title": c, "format": fmt}
                for c in cat_values]
    return {
        "transform": transform,
        "mark": {"type": "rule", "color": _TOKENS["ink"]},
        "encoding": {
            "x": {"field": "x", "type": "ordinal", "sort": list(dates)},
            "opacity": {"condition": {"param": "hover", "value": 0.18, "empty": False},
                        "value": 0},
            "tooltip": tooltip,
        },
        "params": [{"name": "hover",
                    "select": {"type": "point", "fields": ["x"], "nearest": True,
                               "on": "pointerover", "clear": "pointerout"}}],
    }


def line_spec(series, dates, unit="", area_first=False):
    """REPORT-driven line/area spec built from explicit `series`=[{name, vals, color?}]
    and `dates` labels (the report's line_chart Jinja global passes these directly,
    unlike build_spec's tool-driven result/fields shape). None on empty/bad input,
    never raises."""
    try:
        dates = list(dates or [])
        series = [s for s in (series or []) if s.get("vals")]
        if not dates or not series:
            return None
        values = [
            {"x": dates[i], "series": s["name"], "value": v}
            for s in series
            for i, v in enumerate(s["vals"])
            if i < len(dates) and v is not None
        ]
        if not values:
            return None
        names = [s["name"] for s in series]
        colors = [s.get("color") or render._element_color(s["name"]) for s in series]
        # area_first = the old "fill faintly under the line" look: faint fill (.12),
        # solid line on top (matches the retired .lfill{fill-opacity:.12} + .lline).
        # tooltip:False on the base mark — the shared hover layer owns the tooltip.
        mark = ({"type": "area", "fillOpacity": 0.12, "line": True, "point": True,
                 "tooltip": False}
                if area_first else {"type": "line", "tooltip": False})
        fmt = _yfmt(unit)
        base = {
            "mark": mark,
            "encoding": {
                "x": {"field": "x", "type": "ordinal", "sort": list(dates), "title": None},
                "y": {"field": "value", "type": "quantitative", "title": None,
                      "axis": {"format": fmt}},
                "color": {"field": "series", "type": "nominal", "title": None,
                          "scale": {"domain": names, "range": colors}},
            },
        }
        return _envelope(
            data={"values": values},
            layer=[base, _hover_layer("series", names, dates, fmt)],
        )
    except Exception:                       # noqa: BLE001
        return None


def stacked_area_spec(rows, dates, company_rows, unit="commits"):
    """REPORT-driven stacked-area spec built from explicit `rows`=[{company|key|name, vals}]
    and `dates` labels, with `company_rows` supplying per-company colour (same
    company->colour map the report always used, so colours stay consistent). None
    on empty/bad input, never raises."""
    try:
        dates = list(dates or [])
        rows = [r for r in (rows or []) if any(r.get("vals") or [])]
        if not dates or not rows:
            return None
        cmap = {c["company"] if isinstance(c, dict) else c.company:
                (c["color"] if isinstance(c, dict) else c.color) for c in (company_rows or [])}
        companies = []
        values = []
        for r in rows:
            co = r.get("company") or r.get("key") or r.get("name")
            if co not in companies:
                companies.append(co)
            for i, v in enumerate(r.get("vals") or []):
                if i < len(dates) and v is not None:
                    values.append({"x": dates[i], "company": co, "value": v})
        if not values:
            return None
        colors = [cmap.get(co) or render._element_color(co) for co in companies]
        fmt = _yfmt(unit)
        # match the old stacked bands: .82 fill + thin panel-coloured separators.
        # tooltip:False on the band — the shared hover layer owns the tooltip.
        base = {
            "mark": {"type": "area", "fillOpacity": 0.82, "tooltip": False,
                     "stroke": _TOKENS["panel"], "strokeWidth": 0.4},
            "encoding": {
                "x": {"field": "x", "type": "ordinal", "sort": list(dates), "title": None},
                "y": {"field": "value", "type": "quantitative", "stack": True, "title": None,
                      "axis": {"format": fmt}},
                "color": {"field": "company", "type": "nominal", "title": None,
                          "scale": {"domain": companies, "range": colors}},
            },
        }
        return _envelope(
            data={"values": values},
            layer=[base, _hover_layer("company", companies, dates, fmt, with_total=True)],
        )
    except Exception:                       # noqa: BLE001
        return None


def build_spec(viz, result, fields, title):
    """Build a Vega-Lite spec dict for one chart panel, or None on empty/bad input.
    Never raises."""
    try:
        fields = fields or []
        if viz in ("line", "area"):
            return _line_or_area_spec(viz, result, fields, title)
        if viz in ("column", "bar", "pie"):
            return _categorical_spec(viz, result, fields, title)
        return None
    except Exception:                       # noqa: BLE001
        return None
