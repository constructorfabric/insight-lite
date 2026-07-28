#!/usr/bin/env python3
"""Seed a shared multi-viz dashboard covering every dashboard viz type.

Creates one `shared` dashboard ("All viz types (gate)") owned by `gate`, with one
panel per viz in the dashboard vocabulary (number, table, line, area, column, bar,
pie). The `/dashboard/<id>` view route renders a shared dashboard for any viewer,
so the coordinator can add a route state per panel and capture a pixel baseline.

Each panel binds a real dashboard-safe tool + field that resolves against the local
`history/report.db` (verified by re-rendering every panel and asserting it is not a
`dp-err`). Panels pin `period=all` so the dashboard renders identically regardless
of the viewer-selected period, keeping the visual baseline deterministic.

Idempotent: deletes any dashboard with the same title first, then re-creates it.

Usage:
    REPORT_DB=history/report.db python frontend/visual/seed_dashboards.py
"""
from __future__ import annotations

import os
import sys

# Repo root is two levels up from frontend/visual/ — make the top-level modules
# (store, dashboards, ...) importable regardless of the cwd the script is run from.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import dashboards  # noqa: E402
import store  # noqa: E402

OWNER = "gate"
VISIBILITY = "shared"
TITLE = "All viz types (gate)"
# FIXED id (not the random one create_dashboard mints) so the pixel-gate route
# `/dashboard/<id>` is stable and reproducible across re-seeds.
DID = "dash_gate_allviz"

# One panel per viz. Field/tool choices are all real measures from
# dashboards.measures() that resolve against the local DB:
#   number -> a scalar    (contribution.totals.prs)
#   table  -> a breakdown (contribution.by_company)
#   line   -> a series    (trend.commit_rows)
#   area   -> a series    (trend.loc_rows)
#   column/bar/pie -> a breakdown table (contribution.by_company / categories)
PANELS = [
    {"id": "p_number", "title": "Total PRs", "viz": "number", "width": 1,
     "data": {"tool": "contribution", "fields": ["totals.prs"]},
     "pin": {"period": "all"}},
    {"id": "p_table", "title": "By company", "viz": "table", "width": 2,
     "data": {"tool": "contribution", "fields": ["by_company"]},
     "pin": {"period": "all"}},
    {"id": "p_line", "title": "Commits over time", "viz": "line", "width": 2,
     "data": {"tool": "trend", "fields": ["commit_rows"]},
     "pin": {"period": "all"}},
    {"id": "p_area", "title": "LOC over time", "viz": "area", "width": 2,
     "data": {"tool": "trend", "fields": ["loc_rows"]},
     "pin": {"period": "all"}},
    {"id": "p_column", "title": "By company (column)", "viz": "column", "width": 2,
     "data": {"tool": "contribution", "fields": ["by_company"]},
     "pin": {"period": "all"}},
    {"id": "p_bar", "title": "By company (bar)", "viz": "bar", "width": 2,
     "data": {"tool": "contribution", "fields": ["by_company"]},
     "pin": {"period": "all"}},
    {"id": "p_pie", "title": "Categories", "viz": "pie", "width": 2,
     "data": {"tool": "contribution", "fields": ["categories"]},
     "pin": {"period": "all"}},
]

SPEC = {"title": TITLE, "panels": PANELS}

# A SECOND dashboard for the /dashboard/<id>/edit pixel gate. The editor is
# owner-only, so — unlike the shared view dashboard above — it must be owned by a
# login the server can RESOLVE from an auth header. `demo-dev` is a person seeded in
# the local report.db (store.person_login_for('demo-dev') == 'demo-dev'), so the gate
# capture sends `X-Forwarded-Preferred-Username: demo-dev` (see routes.mjs) and the
# owner check passes. Same panels/spec → same deterministic previews.
EDITOR_OWNER = "demo-dev"
EDITOR_VISIBILITY = "private"
EDITOR_TITLE = "Editor gate"
EDITOR_DID = "dash_gate_editor"


def _panel_ok(html: str) -> bool:
    """A panel resolved to real content — not an error tile."""
    return "dp-err" not in html


def main() -> int:
    ok, err = dashboards.validate_spec(SPEC)
    if not ok:
        print(f"SPEC INVALID: {err}", file=sys.stderr)
        return 1

    conn = store.connect()

    # Idempotent: drop the fixed-id row AND any prior title-matched rows (from an
    # older random-id seed), then re-insert with the FIXED id so the gate URL is stable.
    store.delete_dashboard(conn, DID)
    for r in conn.execute("SELECT id FROM dashboard WHERE title=? AND owner_login=?",
                          (TITLE, OWNER)).fetchall():
        store.delete_dashboard(conn, r[0])
        print(f"deleted prior dashboard {r[0]}")

    import json as _json
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO dashboard (id, owner_login, title, visibility, spec, created_ts, updated_ts)"
        " VALUES (?,?,?,?,?,?,?)",
        (DID, OWNER, TITLE, VISIBILITY, _json.dumps(SPEC, ensure_ascii=False), ts, ts))
    # Second: the owner-resolvable editor-gate dashboard (see EDITOR_* above).
    store.delete_dashboard(conn, EDITOR_DID)
    for r in conn.execute("SELECT id FROM dashboard WHERE title=? AND owner_login=?",
                          (EDITOR_TITLE, EDITOR_OWNER)).fetchall():
        store.delete_dashboard(conn, r[0])
    editor_spec = {"title": EDITOR_TITLE, "panels": PANELS}
    conn.execute(
        "INSERT INTO dashboard (id, owner_login, title, visibility, spec, created_ts, updated_ts)"
        " VALUES (?,?,?,?,?,?,?)",
        (EDITOR_DID, EDITOR_OWNER, EDITOR_TITLE, EDITOR_VISIBILITY,
         _json.dumps(editor_spec, ensure_ascii=False), ts, ts))
    conn.commit()
    did = DID
    print(f"created dashboard {did} title={TITLE!r} visibility={VISIBILITY} "
          f"panels={len(PANELS)}")
    print(f"created dashboard {EDITOR_DID} title={EDITOR_TITLE!r} "
          f"owner={EDITOR_OWNER} visibility={EDITOR_VISIBILITY} panels={len(PANELS)}")

    # Prove every panel resolves against the live DB (same resolver the view route
    # uses). period=None so the panel's own pin (90d) drives the render.
    print("--- panel resolution ---")
    all_pass = True
    for p in PANELS:
        html = dashboards.render_panel(p, scope="", period=None)
        passed = _panel_ok(html)
        all_pass = all_pass and passed
        kind = ("vl-panel" if "vl-panel" in html
                else "kpi" if "class=\"kpi\"" in html
                else "table" if "<table" in html
                else "?")
        status = "PASS" if passed else "DP-ERR"
        print(f"  {p['viz']:8} {status:7} [{kind:9}] {p['id']:10} "
              f"tool={p['data']['tool']} fields={p['data']['fields']}")

    if not all_pass:
        print("ONE OR MORE PANELS FAILED TO RESOLVE", file=sys.stderr)
        return 1
    print(f"ALL {len(PANELS)} PANELS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
