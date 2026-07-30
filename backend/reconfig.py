#!/usr/bin/env python3
"""Fast config re-apply — no GitHub, seconds.

Re-derives repo classification (platform/app/ignore) and product element from the
current (base + overlay) config and folds them into the data that was ALREADY
collected: the repo dimension, the granular commit/PR rows, and the run blob
(per-person platform/app splits + the per-element rollup). Then re-renders.

This is what the /config editor's Save calls so classification / element edits show
up immediately, without a full `reportctl all` (which re-fetches from GitHub and
recomputes git-blame LOC). Adding a brand-new org or repo still needs a collect —
its events aren't in the DB yet.

Caveat: per-repo code/spec LOC (git blame) is not recomputed here, so an element's
LOC totals re-bucket with the moved repo but each repo keeps its last-measured LOC
until the next full collect. Activity (commits/PRs/splits) folds exactly.
"""
from __future__ import annotations

import os
from datetime import datetime

import collect
import ghclient
import render
import semantic_metrics
import store

# The repo root, one level up from backend/: templates/, assets/ and config.yaml
# live there, not next to the modules.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dt(s: str):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _norm(cls: str, default_type: str = "app") -> str:
    """'unclassified' resolves to the config's default type; 'ignore' stays ignore."""
    return default_type if cls == "unclassified" else cls


def apply(do_render: bool = True) -> dict:
    """Apply the merged config to the collected data in place; return a summary."""
    cfg = ghclient.load_config()                 # base config.yaml + overlay
    element_of = collect.make_element(cfg)
    default_type = collect.default_repo_type(cfg)

    def cls_of(name: str) -> str:
        return _norm(collect.classify(name, cfg), default_type)

    conn = store.connect()

    # 1) repo dimension + granular rows: classification & element from repo name
    repos = conn.execute("SELECT key, name FROM repo").fetchall()
    changed = 0
    for r in repos:
        cls, elem = cls_of(r["name"]), element_of(r["name"])
        conn.execute("UPDATE repo SET classification=?, element=? WHERE key=?",
                     (cls, elem, r["key"]))
        conn.execute("UPDATE commits SET classification=? WHERE repo=?", (cls, r["key"]))
        conn.execute("UPDATE pull_request SET classification=? WHERE repo=?", (cls, r["key"]))
        changed += 1
    conn.commit()

    # 1b) unified taxonomy: apply the one-time bug/feature/epic/story split (guarded,
    # idempotent), then re-derive is_bug/is_feature/is_epic on every issue from the
    # current resolver, so a taxonomy edit re-tags the whole report with no re-collect
    # (labels + native type are already in the DB).
    import semantic_editor
    semantic_editor.seed_split_categories(conn)
    recat = semantic_metrics.recategorize_issues(conn)

    # 2) run blob: recompute per-person platform/app splits, repo meta, element rollup
    blob = store.read_latest_run(conn)
    if blob and isinstance(blob.get("people"), dict):
        _recompute_blob(conn, blob, cls_of, element_of)
        store.upsert_run(conn, blob)          # DB run table is the source of truth
    conn.close()

    if do_render:
        render.main()
    return {"repos": changed, "issues_recategorised": recat}


def _recompute_blob(conn, blob, cls_of, element_of) -> None:
    ppl = blob["people"]
    SPLIT = ("platform_commits", "app_commits", "platform_meaningful",
             "app_meaningful", "platform_prs", "app_prs")
    for p in ppl.values():
        for k in SPLIT:
            p[k] = 0
    for row in conn.execute(
            "SELECT author_login login, classification cls, COUNT(*) n, "
            "IFNULL(SUM(meaningful_additions),0) m FROM commits "
            "WHERE is_bot=0 AND author_login<>'' GROUP BY author_login, classification"):
        p = ppl.get(row["login"])
        if not p:
            continue
        if row["cls"] == "platform":
            p["platform_commits"] += row["n"]; p["platform_meaningful"] += row["m"] or 0
        elif row["cls"] != "ignore":
            p["app_commits"] += row["n"]; p["app_meaningful"] += row["m"] or 0
    for row in conn.execute(
            "SELECT author_login login, classification cls, COUNT(*) n FROM pull_request "
            "WHERE is_bot=0 AND is_migration=0 AND author_login<>'' "
            "GROUP BY author_login, classification"):
        p = ppl.get(row["login"])
        if not p:
            continue
        if row["cls"] == "platform":
            p["platform_prs"] += row["n"]
        elif row["cls"] != "ignore":
            p["app_prs"] += row["n"]

    # per-person issue counts from the freshly recategorised columns, so the baked
    # all-time block matches what the window view reads live from the DB
    for p in ppl.values():
        p["bugs"] = 0; p["features"] = 0; p["epics"] = 0
    for row in conn.execute(
            "SELECT author_login login, SUM(is_bug) bugs, SUM(is_feature) features, "
            "SUM(is_epic) epics FROM issue WHERE is_bot=0 AND is_migration=0 "
            "AND author_login<>'' GROUP BY author_login"):
        p = ppl.get(row["login"])
        if p:
            p["bugs"] = row["bugs"] or 0
            p["features"] = row["features"] or 0
            p["epics"] = row["epics"] or 0

    # repo meta classification + element (blame LOC kept as last measured)
    for key, meta in (blob.get("repos") or {}).items():
        name = meta.get("name") or key.split("/")[-1]
        meta["classification"] = cls_of(name)
        meta["element"] = element_of(name)

    # per-element rollup: re-bucket repo meta by new element; TTM from granular PRs
    ttm: dict = {}
    for row in conn.execute(
            "SELECT rr.element el, pr.created_at c, pr.merged_at m FROM pull_request pr "
            "JOIN repo rr ON rr.key=pr.repo WHERE pr.merged_at IS NOT NULL "
            "AND pr.merged_at<>'' AND pr.is_bot=0 AND pr.is_migration=0"):
        t0, t1 = _dt(row["c"]), _dt(row["m"])
        if t0 and t1:
            ttm.setdefault(row["el"], []).append((t1 - t0).total_seconds() / 3600)
    blob["elements"] = collect.build_elements_rollup(blob.get("repos") or {}, ppl, ttm)


if __name__ == "__main__":
    r = apply()
    print(f"Reconfigured: {r['repos']} repos re-classified, report.html re-rendered.")
