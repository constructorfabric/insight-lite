#!/usr/bin/env python3
"""Data health (Manage page) — the trust surface ("gaps to review before sharing")
plus the headline stats about the collected dataset. Read-only, built from the same
report model as the dashboard so every number matches. Moved off the report body so
the dashboard stays about the work, and data-quality lives with the Manage tools.

Scope note — what does NOT belong here. This page is about the COLLECTED DATASET:
every tile is a property of the last collection, computed by build_model(), and every
one of them names the person who can fix it and where ("Resolve in Identity →"). A
panel that failed to BUILD for one request is a different animal: it is per-request
and per-viewer, it is already gone by the time anyone loads /data-health, it has no
fix action for the report's reader, and nothing here could learn about it without a
process-wide mutable error registry in a threaded server — state this module exists
precisely to avoid, since health_json() is pure over the cached model. Runtime
degradation is reported where it can be acted on: the server log (server.log_degraded,
with traceback) and, for the reader, a one-line note in the panel's place. Putting it
in the trust surface would inflate "gaps to review before sharing" — the one number
here that has to stay believable — with things that are neither gaps in the data nor
reviewable.

One runtime failure does get a machine-readable surface, and it is instructive about
where the line is: a report model that stops rebuilding (server.stale_model_state) is
process-wide, sticky until someone fixes it, and silently freezes every number on
every page — so it is reported by /health/data, which answers 503 and exists for
exactly the claim it falsifies. Still not HERE, because it is a property of the
serving process, not of the collected dataset this page describes."""
from __future__ import annotations

import html as _h


def _num(v) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return _h.escape(str(v if v is not None else "—"))


def _count(v) -> int:
    return len(v) if isinstance(v, (list, tuple, dict, set)) else int(v or 0)


def _numraw(v) -> str:
    """Like _num() but WITHOUT HTML-escaping — for health_json (React auto-escapes
    when it renders; escaping here would double-escape)."""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v if v is not None else "—")


def health_json(model: dict) -> dict:
    """Data for the React /data-health route: the trust-surface tiles + headline
    stats + risk line, as RAW (unescaped) values. Kept deliberately PARALLEL to
    render_page()'s assembly rather than shared, so render_page — the pixel-gate
    baseline — stays byte-for-byte unchanged. The React page renders these verbatim
    (and escapes on output, hence the raw values)."""
    dq = model.get("data_quality", {}) or {}
    rs = model.get("repo_summary", {}) or {}
    meta = model.get("meta", {}) or {}
    totals = model.get("totals") or (model.get("all_block", {}) or {}).get("totals", {}) or {}
    org = meta.get("org") or "—"
    generated = (model.get("generated") or meta.get("generated_at") or "")[:16].replace("T", " ")
    window_start = (meta.get("window_start") or "")[:10]

    ta, tt, tpct = dq.get("traffic_with_access", 0), dq.get("traffic_total", 0), dq.get("traffic_pct", 0)
    rc = dq.get("risk_count", 0)
    tiles = []

    def tile(cls, val, label, fix, href, done):
        tiles.append({"cls": cls, "val": str(val), "label": label,
                      "fix": fix, "href": href, "done": done})

    if dq.get("identity_unresolved"):
        tile("bad", dq["identity_unresolved"], "unresolved human identities",
             "Resolve in Identity →", "/identity", False)
    else:
        tile("ok", 0, "unresolved human identities", "✓ all resolved", None, True)
    if dq.get("unclassified_repos"):
        tile("warn", dq["unclassified_repos"], "unclassified repos",
             "Classify in Config →", "/config", False)
    else:
        tile("ok", 0, "unclassified repos", "✓ all classified", None, True)
    if ta == tt:
        tile("ok", f"{ta} / {tt}", f"repos with traffic access ({tpct}%)",
             "✓ full access", None, True)
    else:
        tile("warn", f"{ta} / {tt}", f"repos with traffic access ({tpct}%)",
             "Needs push access on GitHub, then re-collect", None, False)
    if dq.get("api_rate_limited"):
        tile("warn", "⚠", "GitHub API rate-limited during collection",
             "Re-collect after the reset window", None, False)
    tile(("ok" if not rc else "warn"), rc, "trust gaps to review before sharing",
         ("✓ nothing to review" if not rc else "fix the flagged tiles above"), None, not rc)

    members = _count(model.get("members_contrib"))
    external = _count(model.get("external_contributors"))
    resolved = dq.get("identity_resolved", 0)
    unresolved = dq.get("identity_unresolved", 0)
    stats = [
        {"label": "Organisation", "value": str(org), "sub": "primary GitHub org"},
        {"label": "Collected", "value": (generated or "—"), "sub": "last collection (UTC)"},
        {"label": "History since", "value": (window_start or "—"), "sub": "earliest data in the window"},
        {"label": "Repos analysed", "value": _numraw(rs.get("distinct", rs.get("total", 0))),
         "sub": f"{_numraw(rs.get('primary', 0))} primary · {_numraw(rs.get('legacy_only', 0))} legacy-only"},
        {"label": "Repo types", "value": f"{_numraw(rs.get('platform', 0))} / {_numraw(rs.get('app', 0))}",
         "sub": "platform / app"},
        {"label": "Contributors", "value": _numraw(totals.get("people", 0)), "sub": "with activity in the window"},
        {"label": "Members / external", "value": f"{_numraw(members)} / {_numraw(external)}",
         "sub": "team vs outside contributors"},
        {"label": "Identities resolved", "value": f"{_numraw(resolved)} / {_numraw(resolved + unresolved)}",
         "sub": "manual · verified · bridged"},
        {"label": "Commits", "value": _numraw(totals.get("commits", 0)), "sub": "non-merge, bots excluded"},
        {"label": "Meaningful LOC", "value": _numraw(totals.get("meaningful_additions", 0)),
         "sub": "hand-relevant additions"},
        {"label": "Pull requests", "value": _numraw(totals.get("prs", 0)),
         "sub": f"{_numraw(totals.get('prs_merged', 0))} merged"},
        {"label": "Spec edits", "value": _numraw(totals.get("specs", 0)), "sub": "commits touching spec docs"},
    ]
    risk_line = ("Nothing flagged — the dataset looks safe to share."
                 if not rc else
                 f"{rc} gap{'s' if rc != 1 else ''} to review before sharing — see the tiles below.")
    return {"riskLine": risk_line, "tiles": tiles, "stats": stats}


def render_page(model: dict, active: str = "datahealth") -> str:
    import shell
    dq = model.get("data_quality", {}) or {}
    rs = model.get("repo_summary", {}) or {}
    meta = model.get("meta", {}) or {}
    totals = model.get("totals") or (model.get("all_block", {}) or {}).get("totals", {}) or {}
    ident = meta.get("identity", {}) or {}
    org = meta.get("org") or "—"
    generated = (model.get("generated") or meta.get("generated_at") or "")[:16].replace("T", " ")
    window_start = (meta.get("window_start") or "")[:10]

    # ---- trust surface: one tile per gap, fixable ones link to where you fix them ----
    ta, tt, tpct = dq.get("traffic_with_access", 0), dq.get("traffic_total", 0), dq.get("traffic_pct", 0)
    rc = dq.get("risk_count", 0)
    tiles = []
    if dq.get("identity_unresolved"):
        tiles.append(("bad", dq["identity_unresolved"], "unresolved human identities",
                      "Resolve in Identity →", "/identity", False))
    else:
        tiles.append(("ok", 0, "unresolved human identities", "✓ all resolved", None, True))
    if dq.get("unclassified_repos"):
        tiles.append(("warn", dq["unclassified_repos"], "unclassified repos",
                      "Classify in Config →", "/config", False))
    else:
        tiles.append(("ok", 0, "unclassified repos", "✓ all classified", None, True))
    if ta == tt:
        tiles.append(("ok", f"{ta} / {tt}", f"repos with traffic access ({tpct}%)",
                      "✓ full access", None, True))
    else:
        tiles.append(("warn", f"{ta} / {tt}", f"repos with traffic access ({tpct}%)",
                      "Needs push access on GitHub, then re-collect", None, False))
    if dq.get("api_rate_limited"):
        tiles.append(("warn", "⚠", "GitHub API rate-limited during collection",
                      "Re-collect after the reset window", None, False))
    tiles.append((("ok" if not rc else "warn"), rc, "trust gaps to review before sharing",
                  ("✓ nothing to review" if not rc else "fix the flagged tiles above"),
                  None, not rc))

    def _tile(cls, val, label, fix, href, done):
        fixcls = "qfix done" if done else ("qfix" if href else "qfix muted")
        inner = (f'<div class="qv">{_h.escape(str(val))}</div>'
                 f'<div class="ql">{_h.escape(label)}</div>'
                 f'<div class="{fixcls}">{_h.escape(fix)}</div>')
        if href:
            return f'<a class="qitem {cls}" href="{href}">{inner}</a>'
        return f'<div class="qitem {cls}">{inner}</div>'
    health = "".join(_tile(*t) for t in tiles)

    # ---- headline dataset stats (match the dashboard) ----
    members = _count(model.get("members_contrib"))
    external = _count(model.get("external_contributors"))
    resolved = dq.get("identity_resolved", 0)
    unresolved = dq.get("identity_unresolved", 0)
    stats = [
        ("Organisation", _h.escape(str(org)), "primary GitHub org"),
        ("Collected", _h.escape(generated or "—"), "last collection (UTC)"),
        ("History since", _h.escape(window_start or "—"), "earliest data in the window"),
        ("Repos analysed", _num(rs.get("distinct", rs.get("total", 0))),
         f"{_num(rs.get('primary', 0))} primary · {_num(rs.get('legacy_only', 0))} legacy-only"),
        ("Repo types", f"{_num(rs.get('platform', 0))} / {_num(rs.get('app', 0))}",
         "platform / app"),
        ("Contributors", _num(totals.get("people", 0)), "with activity in the window"),
        ("Members / external", f"{_num(members)} / {_num(external)}", "team vs outside contributors"),
        ("Identities resolved", f"{_num(resolved)} / {_num(resolved + unresolved)}",
         "manual · verified · bridged"),
        ("Commits", _num(totals.get("commits", 0)), "non-merge, bots excluded"),
        ("Meaningful LOC", _num(totals.get("meaningful_additions", 0)), "hand-relevant additions"),
        ("Pull requests", _num(totals.get("prs", 0)), f"{_num(totals.get('prs_merged', 0))} merged"),
        ("Spec edits", _num(totals.get("specs", 0)), "commits touching spec docs"),
    ]
    stat_html = "".join(
        f'<div class="stat"><div class="sv num">{v}</div><div class="sl">{_h.escape(label)}</div>'
        f'<div class="sub">{_h.escape(sub)}</div></div>'
        for label, v, sub in stats)

    risk_line = ("Nothing flagged — the dataset looks safe to share."
                 if not rc else
                 f"{rc} gap{'s' if rc != 1 else ''} to review before sharing — see the tiles below.")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Data health — Constructor Insight</title>
<style>
{shell.BASE_CSS}
{shell.SHELL_CSS}
  *{{box-sizing:border-box}} body{{margin:0}}
  .wrap{{padding:22px 34px 80px;max-width:1100px}}
  h1{{font-size:24px;margin:0 0 4px}}
  .sub{{color:var(--mut);font-size:13px;margin:0}}
  .lead{{color:var(--ink2);font-size:14px;margin:6px 0 22px}}
  .sec-h{{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;
    color:var(--ink2);margin:26px 0 12px}}
  .quality{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}}
  .qitem{{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--line2);
    border-radius:12px;padding:14px 16px;box-shadow:var(--sh);transition:box-shadow .15s,transform .15s}}
  .qitem.ok{{border-left-color:var(--good)}} .qitem.warn{{border-left-color:var(--warn)}}
  .qitem.bad{{border-left-color:var(--bad)}}
  .qitem .qv{{font-size:22px;font-weight:800;letter-spacing:-.02em}}
  .qitem .ql{{font-size:12.5px;color:var(--ink2);margin-top:3px}}
  a.qitem{{text-decoration:none;color:inherit;display:block}}
  a.qitem:hover{{box-shadow:var(--sh-lift);transform:translateY(-2px)}}
  .qitem .qfix{{margin-top:8px;font-size:12.5px;font-weight:700;color:var(--acc-ink)}}
  .qitem .qfix.done{{color:var(--good);font-weight:700}} .qitem .qfix.muted{{color:var(--mut);font-weight:500}}
  .statgrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}
  .stat{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;box-shadow:var(--sh)}}
  .stat .sv{{font-size:21px;font-weight:800;letter-spacing:-.02em;overflow-wrap:anywhere}}
  .stat .sl{{font-size:12.5px;color:var(--ink);font-weight:600;margin-top:3px}}
  .stat .sub{{font-size:11.5px;color:var(--mut);margin-top:2px}}
</style></head>
<body><div class="app">{shell.sidebar_html(active)}<main class="wrap">
<h1>Data health</h1>
<p class="sub">Review before sharing — the trust surface and the shape of the collected data.</p>
<p class="lead">{_h.escape(risk_line)}</p>
<div class="sec-h">Trust surface</div>
<div class="quality">{health}</div>
<div class="sec-h">Data at a glance</div>
<div class="statgrid">{stat_html}</div>
</main></div></body></html>"""
