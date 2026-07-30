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
    stats + risk line, as RAW (unescaped) values — the React page renders them
    verbatim and escapes on output."""
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


