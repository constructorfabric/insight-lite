#!/usr/bin/env python3
"""Shared app-shell: the navigation model, plus one identical left sidebar rendered
from it on every page.

Every entry links straight at its own route — this used to hand out `/report#<section>`
links that a monolith's JS intercepted, and both the monolith and the interception are
gone. Manage tools are pinned to the sidebar bottom.

Used by render.py (render_spa_page, i.e. every React route) and server.py, so the
sidebar markup and CSS cannot drift between pages.
"""
from __future__ import annotations

# ── Navigation model ─────────────────────────────────────────────────────────
#
# ONE model, rendered twice: this module renders it to HTML (every page, incl. the
# legacy Jinja ones), and render_spa_page inlines it as JSON for the React sidebar
# to render with real lucide components. Two renderers over one model rather than
# two hand-kept lists, which is the failure mode the whole nav convergence with the
# big Insight exists to avoid — reproducing it inside one app would be worse.
#
# The shape mirrors insight-front's src/lib/portal/nav-model.ts: an icon rail of
# ZONES, each holding the items that belong to it. Lite has exactly one direction
# (GitHub), so the direction level that portal shows collapses away and a zone's
# items ARE the second level. A zone gets a group heading only when it has more
# than one item — a heading over a single entry is noise.
#
# `icon` is a lucide COMPONENT NAME, deliberately: it is the vocabulary
# nav-model.ts already declares (`icon: LucideIcon`), so the two products name the
# same glyph the same way. The React side imports it from lucide-react; this module
# looks it up in _ICONS, whose paths were copied out of lucide to begin with.
NAV_ZONES = (
    {"key": "overview", "label": "Overview", "icon": "LayoutGrid", "items": (
        {"key": "overview", "label": "Overview", "href": "/overview",
         "icon": "LayoutGrid"},
        {"key": "trend", "label": "Trend", "href": "/trend",
         "icon": "TrendingUp"},
    )},
    {"key": "development", "label": "Development", "icon": "GitPullRequest", "items": (
        {"key": "delivery", "label": "Delivery", "href": "/delivery",
         "icon": "Package"},
        {"key": "flow", "label": "Flow", "href": "/flow",
         "icon": "RefreshCw"},
        {"key": "repos", "label": "Repositories", "href": "/repositories",
         "icon": "Folder"},
        {"key": "elements", "label": "Elements", "href": "/elements",
         "icon": "Layers"},
        # Traffic is GitHub-specific and has no portal counterpart; it lives under
        # Development rather than at the root.
        {"key": "traffic", "label": "Traffic", "href": "/traffic",
         "icon": "Activity"},
    )},
    {"key": "person", "label": "Person", "icon": "User", "items": (
        {"key": "person", "label": "Person", "href": "/person",
         "icon": "User"},
    )},
    {"key": "people", "label": "People", "icon": "Users", "items": (
        {"key": "people", "label": "People", "href": "/people",
         "icon": "Users"},
    )},
    {"key": "aicost", "label": "AI & Cost", "icon": "DollarSign", "items": (
        {"key": "fabric", "label": "AI tools", "href": "/ai-tools",
         "icon": "Sparkles"},
    )},
    # "Report" is intentionally NOT an entry: every zone above leads into the
    # report, so a dedicated Report button would be redundant. `active` may still
    # be "report" (on the legacy monolith) — nothing is highlighted there.
    #
    # "Full report" is gone. It pointed at /report#all, and bare /report has
    # redirected to /overview since the React cutover — so the entry navigated to
    # Overview while claiming to open the one-pager. The monolith stays reachable
    # at /report/legacy for the pixel-parity baseline; it is simply no longer
    # offered, which also matches the big Insight, where no monolith exists.
    {"key": "manage", "label": "Manage", "icon": "Settings2", "items": (
        {"key": "update", "label": "Update", "href": "/update", "icon": "RefreshCw"},
        {"key": "datahealth", "label": "Data health", "href": "/data-health",
         "icon": "ShieldCheck"},
        {"key": "identity", "label": "Identity", "href": "/identity", "icon": "Contact"},
        {"key": "config", "label": "Config", "href": "/config", "icon": "Settings"},
        {"key": "semantic", "label": "Taxonomy", "href": "/semantic", "icon": "Network"},
        {"key": "setup", "label": "Setup", "href": "/setup", "icon": "SlidersHorizontal"},
        {"key": "metrics", "label": "Metrics", "href": "/metrics", "icon": "ChartColumn"},
        {"key": "mcp", "label": "MCP", "href": "/mcp-info", "icon": "Plug"},
        # `usage-insights`, not `usage`: that key was ALSO the Traffic section's, and
        # both /traffic and /usage-insights passed active="usage" — so whichever of
        # the two you opened, the sidebar highlighted both of them.
        {"key": "usage-insights", "label": "Usage insights", "href": "/usage-insights",
         "icon": "Gauge"},
        {"key": "dashboards", "label": "Dashboards", "href": "/dashboards",
         "icon": "LayoutDashboard"},
        {"key": "changelog", "label": "What's new", "href": "/whats-new", "icon": "Bell"},
    )},
)
# /calibrate is intentionally NOT in the sidebar — it's a private calibration tool
# reachable by direct URL only, so it doesn't surface to everyone.


def nav_zones() -> tuple:
    """The navigation model. A function so the React payload and the HTML renderer
    below read the same object rather than one of them holding a stale copy."""
    return NAV_ZONES

# CSS uses var() with hard fallbacks so it works on pages that don't define the
# full palette (the identity editor / portal lack --panel2).
#
# Desktop: fixed 208px sticky column beside the content.
# Mobile (<=900px): the column becomes an off-canvas drawer; a sticky top bar with
# a hamburger toggles it, dimmed by a backdrop. The .navbar / .nav-backdrop live in
# the flow but are display:none on desktop.
SHELL_CSS = """
  .navbar{display:none}
  .nav-backdrop{display:none}
  .app{display:flex;align-items:flex-start}
  .sidebar{position:sticky;top:0;flex:0 0 236px;width:236px;height:100vh;overflow-y:auto;font-size:14.5px;line-height:1.55;
    padding:20px 14px;border-right:1px solid var(--line);background:var(--bg,#f5f6f9);z-index:40;
    display:flex;flex-direction:column}
  .sidebar .brand{display:flex;align-items:center;gap:11px;font-size:15px;font-weight:800;
    line-height:1.15;margin:0 6px 18px;letter-spacing:-.02em}
  .sidebar .brand span{display:block;font-size:11px;font-weight:600;color:var(--mut);margin-top:2px}
  .sidebar .brand .logo{flex:none;width:32px;height:32px;border-radius:10px;display:grid;place-items:center;
    background:linear-gradient(135deg,var(--acc,#5b5bf0),#8b5cf6);box-shadow:0 4px 12px rgba(91,91,240,.35)}
  .sidebar .brand .logo svg{width:18px;height:18px;stroke:#fff;stroke-width:2.2;fill:none;
    stroke-linecap:round;stroke-linejoin:round}
  .navgroup{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
    color:var(--mut);padding:2px 11px 6px}
  .sidenav .navgroup{padding-top:0}
  .tabs{display:flex;flex-direction:column;gap:2px}
  .tabs .tab,.sidenav a{display:flex;align-items:center;gap:11px;text-align:left;text-decoration:none;
    border:none;background:transparent;color:var(--ink2,#475467);border-radius:10px;padding:8px 11px;
    font:600 13.5px/1.4 inherit;white-space:nowrap;cursor:pointer}
  .tabs .tab svg,.sidenav a svg{flex:none;width:18px;height:18px;stroke:currentColor;stroke-width:1.9;
    fill:none;stroke-linecap:round;stroke-linejoin:round;opacity:.72}
  .tabs .tab:hover,.sidenav a:hover{background:var(--panel2,#eaeef2);color:var(--ink)}
  .tabs .tab.active,.sidenav a.active{background:var(--panel,#fff);color:var(--acc-ink,#4a45d6);
    box-shadow:var(--sh,0 1px 2px rgba(16,24,40,.08));font-weight:700}
  .tabs .tab.active svg,.sidenav a.active svg{opacity:1;stroke:var(--acc,#5b5bf0)}
  .sidenav{margin-top:auto;padding-top:12px;border-top:1px solid var(--line);
    display:flex;flex-direction:column;gap:2px}
  .sidenav a{font-size:13px}
  .wrap{flex:1 1 auto;min-width:0}
  @media(max-width:900px){
    .app{display:block}
    .navbar{display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:60;
      background:var(--panel);border-bottom:1px solid var(--line);padding:9px 14px}
    .navburger{display:inline-flex;align-items:center;justify-content:center;flex:none;
      width:40px;height:36px;border:1px solid var(--line);border-radius:9px;
      background:var(--bg,#fff);color:var(--ink);font-size:18px;line-height:1;cursor:pointer;padding:0}
    .navburger:active{background:var(--panel2,#eaeef2)}
    .navbar-title{font-weight:800;font-size:14px;letter-spacing:-.01em;line-height:1.1}
    .navbar-title span{display:block;font-size:10px;font-weight:600;color:var(--mut)}
    .sidebar{position:fixed;top:0;left:0;height:100vh;width:250px;max-width:82vw;flex:none;
      transform:translateX(-100%);transition:transform .22s ease;z-index:80;
      border-right:1px solid var(--line);box-shadow:0 0 44px rgba(0,0,0,.24)}
    .sidebar.open{transform:none}
    .nav-backdrop{display:block;position:fixed;inset:0;background:rgba(0,0,0,.42);
      opacity:0;visibility:hidden;transition:opacity .2s;z-index:70}
    .nav-backdrop.open{opacity:1;visibility:visible}
    .wrap{width:100%;min-width:0}
  }
"""

# Shared design foundation for the Manage-section pages (Update / Identity / Config /
# Taxonomy / Setup / Metrics / What's new). Each of those pages predates the Modern
# SaaS redesign and defines its own old `:root{--acc:#0969da}` + system font. Inject
# BASE_CSS *last* in each page's <style> so these tokens + Jakarta win, while every
# page keeps its own component layout (which references the same var() names).
#
# The font is served same-origin from /assets/jakarta.woff2 (server.py) rather than
# base64-embedded, so the Manage pages stay small.
BASE_CSS = """
  @font-face{font-family:'Jakarta';font-style:normal;font-weight:400 800;
    font-display:swap;src:url(/assets/jakarta.woff2) format('woff2')}
  @font-face{font-family:'Inter';font-style:normal;font-weight:100 900;
    font-display:swap;src:url(/assets/inter.woff2) format('woff2')}
  :root{
    --bg:#f5f6f9; --panel:#ffffff; --panel2:#eef1f5; --line:#eceef2; --line2:#e2e6ec;
    --ink:#101828; --ink2:#475467; --mut:#8a93a3;
    --acc:#5b5bf0; --acc-ink:#4a45d6; --good:#0f9d58; --good-bg:#e7f6ee;
    --warn:#b7791f; --bad:#e5484d; --bad-bg:#fdeaea;
    --r:16px; --r-sm:12px; --sh:0 1px 2px rgba(16,24,40,.04),0 1px 3px rgba(16,24,40,.06);
    --sh-lift:0 10px 30px rgba(16,24,40,.10);
  }
  /* Always reserve the vertical scrollbar so navigating between short pages (no
     scrollbar) and tall ones (scrollbar) doesn't shift the layout — the sidebar
     visibly "jumped" horizontally by the scrollbar width on each route change. */
  html{overflow-y:scroll}
  /* margin:0 belongs here, not per page: /identity and /views never reset it, so
     the browser default 8px shifted the whole page (sidebar included) right+down
     on those routes. Shared, so React + legacy both get it (parity holds). */
  body{margin:0;background:var(--bg);color:var(--ink);
    font-family:'Jakarta','Inter',-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
  h1,h2,h3{letter-spacing:-.02em}
  h1{font-weight:800} h2,h3{font-weight:700}
  .num{font-variant-numeric:tabular-nums;letter-spacing:-.02em}
  a{color:var(--acc-ink)}
  /* Buttons — pill-ish, Jakarta, indigo primary. Matches the report chrome. */
  button,.btn,a.btn,input[type=submit]{border:1px solid var(--line2);background:var(--panel);
    color:var(--ink);border-radius:10px;padding:8px 14px;font:600 13.5px/1.4 inherit;cursor:pointer;
    box-shadow:var(--sh);transition:border-color .12s,background .12s,box-shadow .12s}
  button:hover,.btn:hover,a.btn:hover{border-color:var(--line2);box-shadow:var(--sh-lift)}
  button.primary,.btn.primary,a.btn.primary,input[type=submit]{background:var(--acc);
    border-color:var(--acc);color:#fff;box-shadow:0 4px 12px rgba(91,91,240,.28)}
  button.primary:hover,.btn.primary:hover{background:var(--acc-ink);border-color:var(--acc-ink)}
  button:disabled,.btn:disabled{opacity:.5;cursor:default;box-shadow:none}
  /* Form controls */
  input,select,textarea{border:1px solid var(--line2);border-radius:10px;padding:8px 11px;
    font:inherit;font-size:14px;background:var(--panel);color:var(--ink)}
  input:focus,select:focus,textarea:focus{outline:none;border-color:var(--acc);
    box-shadow:0 0 0 3px rgba(91,91,240,.14)}
  code{background:var(--panel2);padding:1px 5px;border-radius:6px;font-size:12.5px}
"""

# CSS for the client-rendered Vega-Lite charts: the .vl-panel container sizing and
# our-themed hover tooltip (#vg-tooltip-element). Inject wherever charts render —
# the report, dashboard view, editor, and the /api/dashboard/preview-panel fragment.
# All charts (report AND dashboards) render via Vega-Lite now (see vega_spec.py);
# the hand-rolled SVG renderers are gone.
CHART_CSS = """
.vl-panel{width:100%}
.vl-panel .marks{max-width:100%}
#vg-tooltip-element.vg-tooltip{font:12px Inter,-apple-system,Segoe UI,sans-serif;
  background:var(--panel);color:var(--ink);border:1px solid var(--line2);
  border-radius:8px;box-shadow:0 1px 2px rgba(16,24,40,.04),0 1px 3px rgba(16,24,40,.06);padding:6px 9px}
#vg-tooltip-element.vg-tooltip td.key{color:var(--mut)}
#vg-tooltip-element.vg-tooltip td.value{color:var(--ink)}
"""

# Vendored Vega / Vega-Lite / vega-embed bundle (same-origin, no runtime CDN —
# supply-chain rule), served by server.py's /assets/vega/*.min.js route. Load
# helper only — client hydration (finding .vl-panel containers and calling
# vegaEmbed) lives in each page's own <script> block (hydrateVega).
VEGA_SCRIPTS = ('<script src="/assets/vega/vega.min.js"></script>'
                '<script src="/assets/vega/vega-lite.min.js"></script>'
                '<script src="/assets/vega/vega-embed.min.js"></script>')

# Shared drawer toggle. Inlined once inside sidebar_html so every page (report,
# identity, portal) gets identical behaviour without touching its own JS block.
_SIDEBAR_JS = """
<script>(function(){
  var burger=document.querySelector('.navburger'),
      side=document.querySelector('.sidebar'),
      back=document.querySelector('.nav-backdrop');
  if(!burger||!side) return;
  function set(open){
    side.classList.toggle('open',open);
    if(back) back.classList.toggle('open',open);
    burger.setAttribute('aria-expanded',open?'true':'false');
  }
  burger.addEventListener('click',function(){ set(!side.classList.contains('open')); });
  if(back) back.addEventListener('click',function(){ set(false); });
  // Picking any section/mode link closes the drawer (works for both real
  // navigations and the report's preventDefault section tabs, which still bubble).
  side.addEventListener('click',function(e){ if(e.target.closest('a')) set(false); });
  document.addEventListener('keydown',function(e){ if(e.key==='Escape') set(false); });
})();</script>
"""


# Line-icons (Lucide-style, 24-grid) per nav item — one visual language everywhere.
_ICONS = {
    "LayoutGrid": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "TrendingUp": '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
    "Package": '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
    "RefreshCw": '<path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M21 21v-5h-5"/>',
    "Users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "User": '<circle cx="12" cy="7.5" r="4"/><path d="M5 21a7 7 0 0 1 14 0"/>',
    "Folder": '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/>',
    "Layers": '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
    "Activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "Sparkles": '<circle cx="12" cy="12" r="3.2"/><path d="M12 3v2M12 19v2M5 12H3M21 12h-2M6 6l1.5 1.5M16.5 16.5 18 18M18 6l-1.5 1.5M7.5 16.5 6 18"/>',
    "Contact": '<rect x="2" y="5" width="20" height="14" rx="2.5"/><circle cx="8.5" cy="12" r="2.3"/><path d="M13 10h6M13 14h4M5 16.4a3.5 3.5 0 0 1 7 0"/>',
    "Settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0a1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H3a2 2 0 1 1 0-4a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 2.9-1.2V3a2 2 0 1 1 4 0a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0 1.2 2.9H21a2 2 0 1 1 0 4a1.7 1.7 0 0 0-1.6 1Z"/>',
    "Network": '<circle cx="12" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/><path d="M18 9v1a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V9"/><path d="M12 12v3"/>',
    "SlidersHorizontal": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h13M21 18h-1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="19" cy="18" r="2"/>',
    "ChartColumn": '<path d="M3 3v18h18"/><rect x="7" y="10" width="3" height="8" rx="1"/><rect x="12" y="6" width="3" height="12" rx="1"/><rect x="17" y="13" width="3" height="5" rx="1"/>',
    "Plug": '<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
    "Bell": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a2 2 0 0 0 3.4 0"/>',
    "LayoutDashboard": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "ShieldCheck": '<path d="M12 3 5 6v5c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6z"/><path d="m9 12 2 2 4-4"/>',
}


def _icon(key: str) -> str:
    p = _ICONS.get(key, "")
    return f'<svg class="i" viewBox="0 0 24 24" aria-hidden="true">{p}</svg>' if p else ""




def sidebar_html(active: str) -> str:
    """Full sidebar markup, rendered from NAV_ZONES. `active` = which page this is,
    matched against an item's `key`.

    Every item links straight at its own route; nothing goes through `/report#<mode>`
    any more, because every view has been migrated (the monolith's own tab handler
    called preventDefault() regardless of href, so this only ever mattered when
    navigating FROM another page).

    Two markup shapes survive — `<a class="tab">` inside `<nav class="tabs">` for
    report items, `<a href … class="active">` inside `<nav class="sidenav">` for
    manage ones. Not because anything reads them back any more (the monolith did,
    and it is gone) but because SHELL_CSS styles the two selectors differently. The
    React sidebar replacing this is where that shape gets settled; unifying it here
    first would mean restyling every page for a renderer about to be replaced."""
    tabs, modes = [], []
    for zone in NAV_ZONES:
        target = modes if zone["key"] == "manage" else tabs
        # A single-item zone gets no heading: "Person" above one Person link reads
        # as a mistake rather than as structure.
        if len(zone["items"]) > 1:
            target.append('<div class="navgroup">%s</div>' % zone["label"])
        for it in zone["items"]:
            on = it["key"] == active
            if target is modes:
                target.append('<a href="%s"%s>%s%s</a>' % (
                    it["href"], ' class="active"' if on else "", _icon(it["icon"]), it["label"]))
            else:
                target.append('<a class="tab%s" href="%s">%s%s</a>' % (
                    " active" if on else "", it["href"], _icon(it["icon"]), it["label"]))
    tabs, modes = "".join(tabs), "".join(modes)
    logo = ('<span class="logo"><svg class="i" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg></span>')
    return (
        '<header class="navbar">'
        '<button class="navburger" type="button" aria-label="Toggle menu" aria-expanded="false"'
        ' aria-controls="app-sidebar">&#9776;</button>'
        '<div class="navbar-title">Constructor&nbsp;Insight<span>Contribution &amp; Usage</span></div>'
        '</header>'
        '<div class="nav-backdrop" aria-hidden="true"></div>'
        '<aside class="sidebar" id="app-sidebar">'
        '<div class="brand">' + logo + '<div>Constructor&nbsp;Insight'
        '<span>Contribution &amp; Usage</span></div></div>'
        # The group headings come from the zones now, not from here: one "Report"
        # heading over everything was the flat structure this replaces.
        '<nav class="tabs" aria-label="Report views">' + tabs + '</nav>'
        '<nav class="sidenav" aria-label="Manage tools">' + modes + '</nav>'
        '</aside>' + _SIDEBAR_JS)


def nav_model_json() -> str:
    """NAV_ZONES as JSON for the React sidebar, inlined by render_spa_page.

    Inlined rather than fetched: the sidebar is above the fold on every page, and a
    round-trip would show an empty rail first. It also keeps the model server-owned,
    which is the point — the React side renders it, it does not restate it."""
    import json
    return json.dumps(NAV_ZONES, separators=(",", ":"))
