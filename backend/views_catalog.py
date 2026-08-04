#!/usr/bin/env python3
"""/views — the data behind the human-browsable catalog of reusable components.

Serves view_registry (the same data the MCP `views_catalog` tool returns): purpose,
when-to-use, params, a usage example and the HTML contract, grouped for display. The
cards themselves are built by the React page (frontend/src/pages/Views.tsx) from this
payload, so the page cannot drift from the registry.

This module used to render those cards itself, in HTML, with its own copy of the page
CSS; that went with the React migration.
"""
from __future__ import annotations

import view_registry as vr


def catalog_json() -> dict:
    """Data for the React /views route — view_registry grouped in GROUPS order
    (empty groups dropped), each view carrying its resolved source `where` (what
    _card() resolves via vr.resolve_ref) — the React page builds its cards from
    this verbatim."""
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


