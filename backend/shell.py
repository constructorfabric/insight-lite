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

from html import escape as _e

# ── Navigation model ─────────────────────────────────────────────────────────
#
# ONE model, rendered twice: this module renders it to HTML, and render_spa_page
# inlines it as JSON for the React sidebar (frontend/src/components/Sidebar.tsx) to
# render with real lucide components. Two renderers over one model rather than two
# hand-kept lists, which is the failure mode the whole nav convergence with the big
# Insight exists to avoid — reproducing it inside one app would be worse. The server
# render is not a fallback: it is what makes the sidebar correct before any JS runs,
# so mounting React over it moves nothing on the page.
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
    # Two zones, as in the portal. Each gets a pane of its own parts — see the `items`
    # below; a zone whose pane held one row would just duplicate the rail icon lit
    # beside it, which is what these lists exist to avoid.
    #
    # What lite does NOT copy is the portal's Person pane, which is a person PICKER over
    # an org tree (manager, division, department). Lite has none of that — the person
    # table carries name, company, is_member and activity metrics, and GitHub hands out
    # no employment hierarchy — so a picker here would be a flat list of every login in
    # a sidebar, a worse version of the combo box already on the page.
    #
    # Identity is not in People either, though the portal's People zone holds an
    # "Employees" entry. That one is a VIEW of the roster; the portal keeps "Identities"
    # — managing them — under Manage, and lite's /identity is that: it edits companies
    # and aliases and writes override rows. So it stays in Manage.
    # Pane labels are deliberately terse — one or two words. The pane is ~108px of text
    # at 236px of sidebar, and "Where the work goes" was being cut mid-word. The page
    # heading carries the fuller phrase; this is its short form, not a second name for a
    # different thing.
    #
    # Each of these two is one route broken into VIEWS by `?view=`, so the pane holds
    # real addresses rather than in-page anchors — a pane entry you can send to a
    # colleague, and one kind of thing in one list. The page renders the requested view
    # only; the header (subject picker, profile, KPI tiles) is context and stays on all
    # of them. Item keys are `<zone>-<view>` because that is what the route passes as
    # `active` once it has read the query.
    {"key": "person", "label": "Person", "icon": "User", "items": (
        # The profile card and the KPI tiles live HERE, not above every view. They were
        # page-wide context until it turned out they are just content: on "Lasting
        # impact" the same seven tiles were repeated above numbers that had nothing to
        # do with them. What stays on every view is the subject bar — who this page is
        # about — and that is in the page, not in this pane.
        {"key": "person-overview", "label": "Overview",
         "href": "/person?view=overview", "icon": "LayoutGrid"},
        {"key": "person-activity", "label": "Activity",
         "href": "/person?view=activity", "icon": "Activity"},
        {"key": "person-work", "label": "Composition",
         "href": "/person?view=work", "icon": "Layers"},
        {"key": "person-impact", "label": "Impact",
         "href": "/person?view=impact", "icon": "TrendingUp"},
        {"key": "person-score", "label": "Score",
         "href": "/person?view=score", "icon": "Gauge"},
    )},
    {"key": "people", "label": "People", "icon": "Users", "items": (
        {"key": "people-roster", "label": "Roster",
         "href": "/people?view=roster", "icon": "Users"},
        {"key": "people-categories", "label": "Categories",
         "href": "/people?view=categories", "icon": "ChartColumn"},
        {"key": "people-reviews", "label": "Reviews",
         "href": "/people?view=reviews", "icon": "GitPullRequest"},
    )},
    # The portal's counterpart zone is "AI & Cost" (credits burn-down, spend by tool,
    # pricing). Lite has no source of spend at all — /api/report/ai-tools carries
    # aiUsage, studio/gears provenance, trackers and bots, and nothing priced — so the
    # zone is named for the half that exists. A dollar sign over usage-only data is
    # exactly the "menu entry for something we do not have" Denis objected to. If a
    # spend source ever lands here, this becomes the portal's zone verbatim.
    {"key": "ai", "label": "AI usage", "icon": "Sparkles", "items": (
        {"key": "fabric", "label": "AI tools", "href": "/ai-tools",
         "icon": "Sparkles"},
    )},
    # "Full report" used to sit here. It pointed at /report#all, and bare /report had
    # redirected to /overview since the React cutover — so the entry navigated to
    # Overview while claiming to open a one-pager. The monolith it named is now gone
    # entirely, which also matches the big Insight, where no such page exists.
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
  .sidebar .brand{font-size:15px;font-weight:800;
    line-height:1.15;margin:0 6px 18px;letter-spacing:-.02em}
  .sidebar .brand span{display:block;font-size:11px;font-weight:600;color:var(--mut);margin-top:2px}
  /* Rail + pane, the shape the big Insight's portal shell has. 56 + 180 = the 236px
     the single column already occupied, so the content area loses nothing.
     The rail's entries are plain links, not zone-pinning state as in the portal:
     lite navigates per page (server-routed MPA), so a zone link points at its first
     item and the pane always shows whichever zone the CURRENT page belongs to. Same
     look, no client state, and the Python and React renderers emit the same markup —
     which is what keeps React mounting over it from moving anything. */
  .sb-cols{display:flex;flex:1 1 auto;min-height:0;gap:8px}
  /* The rail keeps a fixed 44px SLOT in the flow and its hover expansion is pure
     DECORATION: the widened panel and the labels are painted by a pseudo-element and
     an absolutely-positioned span, both pointer-events:none, while the clickable
     anchors stay 44px wide.
     That distinction is the whole design. The first version widened the anchors
     themselves, so the open rail (186px) covered the pane (which starts at 44+8) —
     and moving the mouse rightwards to reach a pane item kept it inside the rail, so
     the pane became unreachable without leaving the sidebar entirely. Expanding over
     the very thing you are reaching for is a trap, not an affordance. Now the cursor
     only ever meets the 44px column: move right and the rail collapses behind you,
     and you land on the pane. */
  .sb-rail{position:relative;flex:0 0 44px;align-self:stretch}
  .sb-rail:hover,.sb-rail:focus-within{z-index:95}
  /* The open rail is NOT a card over the page. It has no shadow, no border and no
     surface of its own: it is filled with the sidebar's own background and it is 186px
     inside a 208px content box, so it never leaves the sidebar. What you see is the
     sidebar showing its zone labels — the pane's rows are simply covered while it does.
     An earlier version gave it a border, a radius and `0 8px 24px` of shadow, and that
     is what made it read as a rectangle levitating over the interface.
     It is also why this fill does not FADE: a fade would cross-dissolve the rail's
     labels with the pane's, and two sets of text ghosting through each other is exactly
     the cheap look. It steps in, opaque, at the moment the labels start arriving, and
     steps back out once they have gone.
       open:  .18s before anything happens — long enough that crossing the rail on the
              way to the pane does not trigger it — then .13s for the labels.
       close: immediate, .1s for the labels.
     Nothing animates width. The first version stretched this surface and all seven row
     highlights from 44px to 186px at once, with the labels fading in over the top while
     it was still moving; growing to size is the tell that something is not real.
     The width is the sidebar's CONTENT width (236 minus 14px of padding either side),
     because the pane runs to that edge: any narrower and its rows show past the fill as
     a sliver of half-covered menu down the side of the open rail. */
  .sb-rail::before{content:"";position:absolute;inset:0 auto 0 0;width:208px;
    pointer-events:none;background:var(--bg,#f5f6f9);
    opacity:0;transition:opacity 0s .1s}
  .sb-rail:hover::before,.sb-rail:focus-within::before{opacity:1;transition:opacity 0s .18s}
  .sb-rail-inner{display:flex;flex-direction:column;gap:3px;width:44px;height:100%}
  .sb-pane{display:flex;flex-direction:column;gap:2px;flex:1 1 auto;min-width:0;
    padding-left:8px;border-left:1px solid var(--line)}
  /* Manage sits at the bottom of the rail, the way it sat at the bottom of the
     single column — it is tooling, not a view of the data. */
  .sb-rail .rz-bottom{margin-top:auto}
  .rz{position:relative;display:flex;align-items:center;justify-content:center;
    width:44px;height:40px;flex:none;color:var(--ink2,#475467);
    text-decoration:none;cursor:pointer}
  /* The hover/active BACKGROUND is a pseudo-element, not the anchor's own, because
     the two have to be different sizes: the anchor stays 44px so the pointer never
     strays over the pane, while the highlight has to grow with the open rail and wrap
     the label too. Drawn 44px wide, widened to the panel's width on rail hover.
     Painted first (::before precedes the children) and pointer-events:none, so it
     neither covers the glyph nor becomes a hit target. */
  /* The width is a STEP, not an animation, and it is timed to land inside the fade:
     on open it snaps at .18s, the moment the surface starts appearing; on close it snaps
     back at .1s, once the surface has gone. Either way the change happens while this
     element is invisible or the panel behind it is, so nothing is seen to stretch. */
  .rz::before{content:"";position:absolute;inset:0 auto 0 0;width:44px;border-radius:11px;
    pointer-events:none;transition:background .13s ease,box-shadow .13s ease,width 0s .1s}
  .sb-rail:hover .rz::before,.sb-rail:focus-within .rz::before{width:196px;
    transition:background .13s ease,box-shadow .13s ease,width 0s .18s}
  .rz svg{position:relative;flex:none;width:19px;height:19px;stroke:currentColor;
    stroke-width:1.9;fill:none;stroke-linecap:round;stroke-linejoin:round;opacity:.72}
  .rz:hover{color:var(--ink)}
  .rz:hover::before{background:var(--panel2,#eaeef2)}
  .rz.active{color:var(--acc-ink,#4a45d6)}
  .rz.active::before{background:var(--panel,#fff);
    box-shadow:var(--sh,0 1px 2px rgba(16,24,40,.08))}
  .rz.active svg{opacity:1;stroke:var(--acc,#5b5bf0)}
  /* Always in the DOM so a screen reader gets it; clipped out of sight when the rail
     is closed, and never a pointer target. */
  /* The labels are the only thing that actually moves: a short slide in from under the
     icon column, so they read as coming OUT of the rail rather than switching on. */
  .rz .rz-l{position:absolute;left:44px;top:50%;
    font:600 13px/1.4 inherit;white-space:nowrap;pointer-events:none;
    opacity:0;transform:translate(-6px,-50%);
    transition:opacity .1s ease,transform .1s ease}
  .sb-rail:hover .rz-l,.sb-rail:focus-within .rz-l{opacity:1;transform:translateY(-50%);
    transition:opacity .13s ease .18s,transform .13s cubic-bezier(.2,.7,.3,1) .18s}
  /* The labels are an appearance, not information. Without motion they are simply there
     or not — the same affordance, none of the movement. */
  @media(prefers-reduced-motion:reduce){
    .rz .rz-l,.sb-rail:hover .rz-l,.sb-rail:focus-within .rz-l{
      transform:translateY(-50%);transition-duration:0s}
  }
  /* Suppressed after you pick a zone, until the pointer leaves the rail.
     A rail click navigates, so the destination loads with the cursor still parked on
     the rail; left to plain :hover it would open again over the pane you came to see.
     The first attempt gated expansion on pointer MOVEMENT instead, which made it
     flicker: collapsed on load, then popped open at the first twitch of the mouse.
     Leaving and returning re-enables it. Two classes beat the one-class rules above,
     which is what makes this an override rather than a fight. */
  .sb-rail.rail-dismissed:hover::before{opacity:0;transition-delay:0s}
  .sb-rail.rail-dismissed:hover .rz::before{width:44px;transition:width 0s}
  .sb-rail.rail-dismissed:hover .rz-l{opacity:0;transform:translate(-6px,-50%);
    transition-delay:0s}
  .navgroup{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
    color:var(--mut);padding:2px 11px 6px}
  .sb-pane .tab{display:flex;align-items:center;gap:10px;text-align:left;text-decoration:none;
    border:none;background:transparent;color:var(--ink2,#475467);border-radius:10px;padding:8px 10px;
    font:600 13px/1.4 inherit;cursor:pointer;min-width:0}
  /* The label is its own span so it can ellipsis: as a bare text node it was a flex
     child that overflow:hidden simply CUT, mid-glyph. Labels are short by policy (see
     NAV_ZONES) — this is what happens when one is not. */
  .sb-pane .tab>span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .sb-pane .tab svg{flex:none;width:17px;height:17px;stroke:currentColor;stroke-width:1.9;
    fill:none;stroke-linecap:round;stroke-linejoin:round;opacity:.72}
  .sb-pane .tab:hover{background:var(--panel2,#eaeef2);color:var(--ink)}
  .sb-pane .tab.active{background:var(--panel,#fff);color:var(--acc-ink,#4a45d6);
    box-shadow:var(--sh,0 1px 2px rgba(16,24,40,.08));font-weight:700}
  .sb-pane .tab.active svg{opacity:1;stroke:var(--acc,#5b5bf0)}
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
    "Gauge": '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
    "GitPullRequest": '<circle cx="18" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><line x1="6" x2="6" y1="9" y2="21"/>',
    "Settings2": '<path d="M14 17H5"/><path d="M19 7h-9"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
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




# The two GLOBAL filters. Every report page reads them, so every report link should
# arrive with them still applied.
CARRY_GLOBAL = ("p", "from", "to", "slice")
# A page SUBJECT, and the one zone where it means anything. `?person=` on /elements
# would be a param nothing reads, kept alive in everyone's URL bar; inside the Person
# zone it is the whole point — switching to Activity must not forget whose activity.
CARRY_SUBJECT = {"person": "person"}
# Every key navigation can carry, DERIVED from the two above rather than restated:
# server.py reads exactly these off the request, and the nav model advertises them so
# the client knows what it may merge. Three hand-kept copies of one list is three
# chances for a param to be read but never carried, or carried but never read.
CARRY_KEYS = CARRY_GLOBAL + tuple(CARRY_SUBJECT)


def zone_carry(zone_key: str, carry: dict | None) -> dict | None:
    """The subset of the report query (see server.py's _report_carry) that links into
    this zone should keep.

    Manage takes none of it: it is settings, and `?p=30d` means nothing to /config —
    it would only survive in someone's bookmark.

    One rule, consulted by BOTH renderers (sidebar_html / nav_model_json here,
    components/Sidebar.tsx on the client), because the two disagreeing about which
    links keep your filters is exactly the bug this exists to prevent."""
    if not carry or zone_key == "manage":
        return None
    return {k: v for k, v in carry.items()
            if k in CARRY_GLOBAL or CARRY_SUBJECT.get(k) == zone_key}


def _carry_href(href: str, carry: dict | None) -> str:
    """Merge `carry` into `href`, keeping whatever the href already specifies.

    A view switch must not lose the page's subject: /person?view=work has to keep the
    `?person=` that says whose page it is, and the same goes for the period and scope
    on every report link. The server merges the values from the REQUEST, which is what
    makes the sidebar work with no bundle at all; the React sidebar merges from the
    LIVE query on top of that, because a person picked after page load never reached
    the server (see components/Sidebar.tsx)."""
    if not carry:
        return href
    path, _, qs = href.partition("?")
    # `k not in have` mirrors Sidebar.tsx's `!params.has(k)`: the href wins where it
    # says something itself. No nav href declares a carry key today (only `view=`), so
    # the two agree either way — but the moment one does, without this the server would
    # emit the param twice and the client once, which is precisely the divergence
    # between the two renderers this module exists to prevent.
    from urllib.parse import parse_qs
    have = set(parse_qs(qs))
    keep = {k: v for k, v in carry.items() if v and k not in have}
    if not keep:
        return href
    from urllib.parse import quote
    extra = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in sorted(keep.items()))
    return f"{path}?{qs}&{extra}" if qs else f"{path}?{extra}"


def sidebar_html(active: str, carry: dict | None = None) -> str:
    """Full sidebar markup, rendered from NAV_ZONES. `active` = which page this is,
    matched against an item's `key`.

    Every item links straight at its own route; nothing goes through `/report#<mode>`
    any more, because every view has been migrated (the monolith's own tab handler
    called preventDefault() regardless of href, so this only ever mattered when
    navigating FROM another page).

    The shape is an icon RAIL of zones plus a PANE of the active zone's items — what
    the big Insight's portal shell looks like. Two differences from it, both because
    lite is a server-routed MPA rather than an SPA: a rail entry is a plain link to
    its zone's first item instead of pinning zone state, and the pane always shows
    whichever zone the current page belongs to. No client state, so this and
    components/Sidebar.tsx emit identical markup — which is what lets React mount
    over this without anything moving.

    A zone with one item still gets a pane holding that one item, deliberately: the
    alternative (no pane) would change the sidebar's width depending on which page you
    are on, and a layout that resizes as you navigate is worse than a pane with one
    row in it. The group HEADING is what a single item does not earn."""
    # Escaped here, not in report_caption: the caption is `runs.org` from the DB, and
    # an org that arrived through a config file or CONSTRUCTOR_ORG never passed the
    # regex /api/setup/save applies. The React twin gets it as JSON and escapes on
    # output, so this is the only path where raw markup could land on every page.
    caption = _e(report_caption())
    zones = list(NAV_ZONES)
    # Which zone the current page belongs to. Falls back to the first: `active` is ""
    # or unknown on a page outside the nav (e.g. /calibrate), and a rail with nothing
    # lit beside an empty pane reads as broken rather than as "not in the menu".
    current = next((z for z in zones
                    if any(i["key"] == active for i in z["items"])), zones[0])

    rail = []
    for z in zones:
        on = z is current
        bottom = " rz-bottom" if z["key"] == "manage" else ""
        rail.append(
            '<a class="rz%s%s" href="%s" aria-label="%s">%s<span class="rz-l">%s</span></a>' % (
                " active" if on else "", bottom,
                _carry_href(z["items"][0]["href"], zone_carry(z["key"], carry)),
                z["label"], _icon(z["icon"]), z["label"]))

    pane = []
    if len(current["items"]) > 1:
        pane.append('<div class="navgroup">%s</div>' % current["label"])
    for it in current["items"]:
        pane.append('<a class="tab%s" href="%s">%s<span>%s</span></a>' % (
            " active" if it["key"] == active else "",
            _carry_href(it["href"], zone_carry(current["key"], carry)),
            _icon(it["icon"]), it["label"]))

    return (
        '<header class="navbar">'
        '<button class="navburger" type="button" aria-label="Toggle menu" aria-expanded="false"'
        ' aria-controls="app-sidebar">&#9776;</button>'
        '<div class="navbar-title">Constructor&nbsp;Insight'
        '<span>' + (caption or "Contribution &amp; Usage") + '</span></div>'
        '</header>'
        '<div class="nav-backdrop" aria-hidden="true"></div>'
        '<aside class="sidebar" id="app-sidebar">'
        '<div class="brand"><div>Constructor&nbsp;Insight'
        '<span>' + (caption or "Contribution &amp; Usage") + '</span></div></div>'
        '<div class="sb-cols">'
        '<nav class="sb-rail" aria-label="Sections">'
        '<div class="sb-rail-inner">' + "".join(rail) + '</div></nav>'
        '<nav class="sb-pane" aria-label="' + current["label"] + '">' + "".join(pane) + '</nav>'
        '</div>'
        '</aside>' + _SIDEBAR_JS)


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def report_caption() -> str:
    """"<org> · <when the data was built>", for the sidebar's brand block.

    This replaces a line every page used to print above its own heading:
    "Org constructorfabric · all-time history (since 2008-01-01) · generated
    2026-07-29 03:33 UTC" — ten pages rendering the same sentence, and the longest
    part of it was a lie. `since 2008-01-01` is not when the data starts; it is the
    sentinel the all-time window uses for "no lower bound". Showing a placeholder in
    the shape of a fact is worse than showing nothing, and the period control already
    says "All-time" in one word.

    What is left is what a reader actually needs from chrome: whose org this is, and
    how old the numbers are. Degrades to the plain tagline if the store cannot be read
    (a fresh install has no run yet), because chrome must never be the thing that
    breaks a page."""
    try:
        import store
        conn = store.connect()
        try:
            row = conn.execute(
                "SELECT org, generated_at FROM runs ORDER BY date DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        if not row or not row["generated_at"]:
            return ""
        g = str(row["generated_at"])
        when = f"{int(g[8:10])} {_MONTHS[int(g[5:7]) - 1]} {g[11:16]} UTC"
        return f"{row['org']} · {when}" if row["org"] else when
    except Exception:                      # noqa: BLE001 — chrome, never fatal
        return ""


def nav_model_json(active: str = "", carry: dict | None = None) -> str:
    """The navigation model plus which entry is current, as JSON for the React
    sidebar. Inlined by render_spa_page rather than fetched: the sidebar is above the
    fold on every page, and a round-trip would mean rendering it twice or showing it
    empty first. `active` travels in the payload instead of being sniffed back out of
    the server markup's `.active` class — the server already knows it."""
    import json
    zones = [{**z, "carry": sorted(zone_carry(z["key"], {k: 1 for k in CARRY_KEYS}) or {}),
              "items": [{**i, "href": _carry_href(i["href"], zone_carry(z["key"], carry))}
                        for i in z["items"]]}
             for z in NAV_ZONES]
    return json.dumps({"active": active, "zones": zones,
                       "caption": report_caption()}, separators=(",", ":"))
