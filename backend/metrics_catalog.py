#!/usr/bin/env python3
"""Metrics catalog page (/metrics).

Renders EVERY metric from the registry — nothing is hardcoded here. Each metric is
declared next to the function that computes it (see metrics_registry.register_for),
so this page and the drift test always reflect the real code.

Layout: a searchable, type-filterable reference. Each metric is a compact row
(name · type · one-line meaning) that expands to its formula and exact query, so the
page scans easily instead of being a wall of code.
"""
from __future__ import annotations

import html as _h
import os

import metrics_registry as mreg

# The repo root, one level up from backend/: templates/, assets/ and config.yaml
# live there, not next to the modules.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _metric_row(m: dict) -> str:
    e = _h.escape
    body = ""
    if m.get("formula"):
        body += (f'<div class="frow"><span class="k">Formula</span>'
                 f'<code class="formula">{e(m["formula"])}</code></div>')
    if m.get("where"):
        body += (f'<div class="frow"><span class="k">Computed in</span>'
                 f'<span class="where">{e(m["where"])}</span></div>')
    if m.get("snippet"):
        body += f'<pre class="snip">{e(m["snippet"])}</pre>'
    return (
        f'<div class="m" data-name="{e(m["name"].lower())}" '
        f'data-desc="{e((m.get("desc") or "").lower())}" data-type="{e(m["type"])}">'
        f'<div class="m-row"><span class="m-name">{e(m["name"])}</span>'
        f'<span class="pill {e(m["type"])}">{e(m["type"])}</span>'
        f'<span class="m-desc">{e(m.get("desc") or "")}</span>'
        f'<span class="unit">{e(m.get("unit") or "")}</span>'
        f'<span class="m-chev">&#9656;</span></div>'
        f'<div class="m-body">{body}</div></div>')


def catalog_json() -> dict:
    """Data for the React /metrics route — the same registry render_page() draws,
    grouped in GROUPS order (empty groups dropped), plus the header counts. Each
    metric dict is passed through verbatim (name/type/desc/unit/formula/where/
    snippet), so the React page can reproduce render_page()'s markup exactly."""
    metrics = mreg.all_metrics()
    by_group: dict = {}
    for m in metrics:
        by_group.setdefault(m["group"], []).append(m)
    groups = [
        {"id": gid, "title": gtitle, "metrics": by_group.get(gid, [])}
        for gid, gtitle in mreg.GROUPS if by_group.get(gid)
    ]
    n_direct = sum(1 for m in metrics if m["type"] == "direct")
    return {"groups": groups, "total": len(metrics),
            "direct": n_direct, "computed": len(metrics) - n_direct}


