#!/usr/bin/env python3
"""/views — human-browsable catalog of the reusable visual components.

Renders view_registry (the same data the MCP `views_catalog` tool returns) as
cards: purpose, when-to-use, params, a usage example and the HTML contract. Cards
are generated from the registry so the page can't drift from the components.
"""
from __future__ import annotations

import html as _h

import view_registry as vr


def _param_rows(params: list) -> str:
    out = []
    for p in params:
        req = ' <span class="req">required</span>' if p.get("required") else ""
        vals = (" · " + ", ".join(p["values"])) if p.get("values") else ""
        out.append(f'<tr><td><code>{_h.escape(p["name"])}</code></td>'
                   f'<td class="ty">{_h.escape(p["type"])}{_h.escape(vals)}</td>'
                   f'<td>{_h.escape(p["desc"])}{req}</td></tr>')
    return "".join(out)


def _card(v: dict) -> str:
    where = _h.escape(vr.resolve_ref(v["ref"])["where"])
    contract = (f'<div class="vlbl">HTML contract</div>'
                f'<pre class="vex">{_h.escape(v["html_contract"])}</pre>'
                if v.get("html_contract") else "")
    return (
        '<div class="vc">'
        f'<div class="vc-h"><span class="vname">{_h.escape(v["name"])}</span>'
        f'<span class="kind">{_h.escape(v["kind"])}</span></div>'
        f'<p class="vp">{_h.escape(v["purpose"])}</p>'
        f'<p class="vw"><b>When:</b> {_h.escape(v["when_to_use"])}</p>'
        f'<table class="vparams"><tr><th>param</th><th>type</th><th>meaning</th></tr>'
        f'{_param_rows(v["params"])}</table>'
        f'<div class="vlbl">Example</div><pre class="vex">{_h.escape(v["example"])}</pre>'
        f'{contract}<div class="vsrc">component: <code>{where}</code></div></div>')


_PAGE_CSS = """
main.wrap{padding:20px 34px 90px}
h1{font-size:24px;font-weight:800;letter-spacing:-.02em;margin:0 0 4px}
.sub{color:var(--mut,#656d76);font-size:13.5px;margin:0 0 14px;max-width:82ch}
.vg{font-size:16px;font-weight:800;letter-spacing:-.02em;margin:22px 0 10px}
.vg .gc{color:var(--mut,#656d76);font-size:12px;font-weight:700}
.vgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.vc{border:1px solid var(--line);border-radius:var(--r-sm,11px);background:var(--panel);padding:14px 16px}
.vc-h{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.vname{font-family:ui-monospace,Menlo,monospace;font-weight:700;font-size:14px}
.kind{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;
  padding:2px 8px;border-radius:999px;background:var(--acc-soft,#edecfe);color:var(--acc)}
.vp{font-size:13px;margin:0 0 6px}
.vw{font-size:12.5px;color:var(--mut,#656d76);margin:0 0 8px}
.vparams{width:100%;border-collapse:collapse;font-size:12px;margin:0 0 8px}
.vparams th{text-align:left;color:var(--mut,#656d76);font-weight:700;border-bottom:1px solid var(--line);padding:3px 6px}
.vparams td{padding:3px 6px;vertical-align:top;border-bottom:1px solid var(--line)}
.vparams .ty{color:var(--mut,#656d76);font-family:ui-monospace,Menlo,monospace}
.req{color:var(--c-bug,#ef4444);font-size:10px;font-weight:700}
.vlbl{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--mut,#656d76);margin:6px 0 3px}
.vex{background:var(--code-bg,#0d1117);color:var(--code-fg,#c9d1d9);border-radius:8px;padding:9px 11px;
  overflow:auto;font-family:ui-monospace,Menlo,monospace;font-size:11.5px;line-height:1.5;white-space:pre-wrap;margin:0}
.vsrc{font-size:11px;color:var(--mut,#656d76);margin-top:8px}
"""


def catalog_json() -> dict:
    """Data for the React /views route — view_registry grouped in GROUPS order
    (empty groups dropped), each view carrying its resolved source `where` (what
    _card() resolves via vr.resolve_ref). The React page reproduces render_page()'s
    card markup from this verbatim."""
    by_group: dict = {}
    for v in vr.all_views():
        by_group.setdefault(v["group"], []).append(v)
    groups = []
    for gid, gtitle in vr.GROUPS:
        items = by_group.get(gid, [])
        if not items:
            continue
        cards = [{**v, "where": vr.resolve_ref(v["ref"])["where"]} for v in items]
        groups.append({"id": gid, "title": gtitle, "views": cards})
    return {"groups": groups}


def render_page(active: str = "views") -> str:
    import shell
    by_group: dict = {}
    for v in vr.all_views():
        by_group.setdefault(v["group"], []).append(v)
    sections = []
    for gid, gtitle in vr.GROUPS:
        items = by_group.get(gid, [])
        if not items:
            continue
        cards = "".join(_card(v) for v in items)
        sections.append(f'<h2 class="vg">{_h.escape(gtitle)} <span class="gc">{len(items)}</span></h2>'
                        f'<div class="vgrid">{cards}</div>')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>View catalog — Constructor Insight</title><style>'
        + shell.SHELL_CSS + shell.BASE_CSS + _PAGE_CSS
        + '</style></head><body><div class="app">'
        + shell.sidebar_html(active)
        + '<main class="wrap"><h1>View catalog</h1>'
        '<p class="sub">Reusable visual components for building dashboards and artifacts — '
        'the same data the MCP <code>views_catalog</code> tool returns. Pick a display method, '
        'copy the example, or reproduce it from the HTML contract.</p>'
        + "".join(sections) + '</main></div></body></html>')
