#!/usr/bin/env python3
"""Render the collected run (from the SQLite store) into a self-contained HTML report (report.html).

No external CDN/fonts/scripts — everything inlined so it renders identically
in a browser and inside an email client.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone

from jinja2 import Environment, DictLoader

import paths
import shell
import metrics_registry as _mreg
_m = _mreg.metric

# The repo root, one level up from backend/: templates/, assets/ and config.yaml
# live there, not next to the modules.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_tmpl(name: str) -> str:
    """Template body for DictLoader key <name>. A single file templates/<name>.j2, OR
    a directory templates/<name>/ whose *.j2 parts are concatenated in sorted order
    (so a big template — e.g. `panels` — is split into per-area modules on disk while
    staying one template at runtime). Both forms are byte-identical to the original
    inline r-strings; macro names and DictLoader keys are unchanged."""
    d = os.path.join(ROOT, "templates", name)
    if os.path.isdir(d):
        return "".join(
            open(os.path.join(d, f), encoding="utf-8").read()
            for f in sorted(os.listdir(d)) if f.endswith(".j2"))
    with open(os.path.join(ROOT, "templates", name + ".j2"), encoding="utf-8") as fh:
        return fh.read()


def pct(part: float, whole: float) -> float:
    return round(100 * part / whole, 1) if whole else 0.0



def delta_map(cur: dict, prev: dict, keys: tuple = (
        "commits", "meaningful_additions", "prs", "prs_merged",
        "specs", "bugs", "epics", "features", "people")) -> dict:
    """Period-over-period deltas for the KPI tiles. Per metric: absolute diff,
    % change (None when prev is 0 → rendered as 'new'), and direction. Compares
    two `totals` dicts, so metric definitions match exactly. `keys` selects which
    metrics to diff — the org KPIs use the default set, the Person tiles pass their
    own subset of these keys)."""
    out = {}
    for k in keys:
        c = cur.get(k, 0) or 0
        p = prev.get(k, 0) or 0
        diff = c - p
        out[k] = {"diff": diff, "prev": p,
                  "pct": (round(diff / p * 100) if p else None),
                  "dir": ("up" if diff > 0 else "down" if diff < 0 else "flat")}
    return out


def build_model(d: dict) -> dict:
    people = d["people"]
    repos = d["repos"]
    forkers = d["forkers"]

    # backward-compat: run blobs collected before the is_user_story→is_feature rename
    # stored the feature count under 'user_stories'. Normalise so the rest reads
    # 'features' uniformly; ages out on the next collect (which writes 'features').
    for _p in people.values():
        if "features" not in _p:
            _p["features"] = _p.get("user_stories", 0)

    def alive(p):
        return p["total_activity"] > 0

    actives = {l: p for l, p in people.items() if alive(p)}

    # ---- category totals --------------------------------------------------
    tot = {
        "commits": sum(p["commits"] for p in people.values()),
        "additions": sum(p["additions"] for p in people.values()),
        "prs": sum(p["prs_opened"] for p in people.values()),
        "prs_merged": sum(p["prs_merged"] for p in people.values()),
        "specs": sum(p["specs"] for p in people.values()),
        "bugs": sum(p["bugs"] for p in people.values()),
        "epics": sum(p.get("epics", 0) for p in people.values()),
        "features": sum(p["features"] for p in people.values()),
        "people": len(actives),
    }
    tot["meaningful_additions"] = sum(p.get("meaningful_additions", p["additions"]) for p in people.values())
    tot["meaningful_deletions"] = sum(p.get("meaningful_deletions", p.get("deletions", 0)) for p in people.values())

    def ranking(metric, label_total):
        rows = sorted(
            ((p.get(metric, 0), l) for l, p in people.items() if p.get(metric, 0)),
            reverse=True,
        )
        return [
            {"login": l, "value": v, "pct": pct(v, label_total)}
            for v, l in rows
        ]

    categories = [
        {"key": "code", "title": "Code", "unit": "commits + PRs",
         "total": tot["commits"] + tot["prs"],
         "rows": sorted(
             ({"login": l, "value": p["commits"] + p["prs_opened"],
               "pct": pct(p["commits"] + p["prs_opened"], tot["commits"] + tot["prs"])}
              for l, p in people.items() if p["commits"] + p["prs_opened"]),
             key=lambda x: -x["value"])},
        {"key": "code_loc", "title": "Code (LOC)", "unit": "meaningful LOC added",
         "total": tot["meaningful_additions"],
         "rows": sorted(
             ({"login": l, "value": p.get("meaningful_additions", p["additions"]),
               "pct": pct(p.get("meaningful_additions", p["additions"]), tot["meaningful_additions"] or 1)}
              for l, p in people.items() if p.get("meaningful_additions", p["additions"])),
             key=lambda x: -x["value"])},
        {"key": "specs", "title": "Specs", "unit": "commits to spec docs (PRD/DESIGN/ADR…)",
         "total": tot["specs"], "rows": ranking("specs", tot["specs"])},
        {"key": "bugs", "title": "Bugs", "unit": "issues categorised as bug",
         "total": tot["bugs"], "rows": ranking("bugs", tot["bugs"])},
        {"key": "epics", "title": "Epics", "unit": "issues categorised as epic",
         "total": tot["epics"], "rows": ranking("epics", tot["epics"])},
        {"key": "features", "title": "Features",
         "unit": "issues categorised as feature", "total": tot["features"],
         "rows": ranking("features", tot["features"])},
    ]

    # ---- contribution by company -----------------------------------------
    # Colours come from store.company_color_map: derived from the company NAME (or an
    # explicit pin in companies.colors), never from its rank — see the comment there.
    comp: dict = {}
    for l, p in people.items():
        co = p.get("company", "Other")
        a = comp.setdefault(co, {"company": co, "people": 0, "commits": 0,
                                 "additions": 0, "meaningful_additions": 0, "specs": 0, "bugs": 0,
                                 "epics": 0, "features": 0, "prs": 0, "ai_commits": 0, "cpt_lines": 0})
        a["people"] += 1
        a["commits"] += p["commits"]
        a["additions"] += p["additions"]
        a["meaningful_additions"] += p.get("meaningful_additions", p["additions"])
        a["specs"] += p["specs"]
        a["bugs"] += p["bugs"]
        a["epics"] += p.get("epics", 0)
        a["features"] += p["features"]
        a["prs"] += p["prs_opened"]
        a["ai_commits"] += p.get("ai_commits", 0)
        a["cpt_lines"] += p.get("cpt_lines", 0)
    company_rows = sorted(comp.values(), key=lambda x: -x["commits"])
    co_total = sum(c["commits"] for c in company_rows) or 1
    loc_total = sum(c["meaningful_additions"] for c in company_rows) or 1
    import store as _store
    _co_pins = _store.pinned_company_colors()
    _co_colors = _store.company_color_map([c["company"] for c in company_rows], _co_pins)
    for c in company_rows:
        c["pct"] = pct(c["commits"], co_total)
        c["loc_pct"] = pct(c["meaningful_additions"], loc_total)
        c["ai_pct"] = pct(c["ai_commits"], c["commits"])
        c["color"] = _co_colors[c["company"]]

    # ---- AI-tool usage (commit-marker floor) --------------
    ai_total = sum(p.get("ai_commits", 0) for p in people.values())
    tool_agg: dict = {}
    for p in people.values():
        for t, cnt in (p.get("ai") or {}).items():
            x = tool_agg.setdefault(t, {"tool": t, "commits": 0, "loc": 0})
            x["commits"] += cnt["commits"]
            x["loc"] += cnt["loc"]
    ai_prec = d.get("ai_precision", {})
    for x in tool_agg.values():
        x["pct"] = pct(x["commits"], tot["commits"])
        x["precision"] = ai_prec.get(x["tool"], "exact")
    ai_usage = {
        "any_commits": ai_total,
        "total_commits": tot["commits"],
        "pct": pct(ai_total, tot["commits"]),
        "tools": sorted(tool_agg.values(), key=lambda x: -x["commits"]),
    }

    # ---- assistant code-marker lines attributed to people/companies --------
    cpt_people = sorted(
        ({"login": l, "name": p.get("name", ""), "company": p.get("company", "Other"),
          "lines": p.get("cpt_lines", 0)} for l, p in people.items() if p.get("cpt_lines")),
        key=lambda x: -x["lines"])
    cpt_by_company = sorted(
        ({"company": c["company"], "lines": c["cpt_lines"], "color": c["color"]}
         for c in company_rows if c.get("cpt_lines")),
        key=lambda x: -x["lines"])

    # ---- historical trend (accumulated snapshots) ------------------------
    hist = d.get("_history", []) or []
    co_color = {c["company"]: c["color"] for c in company_rows}
    co_seen = set()
    for s in hist:
        co_seen |= set(s.get("by_company", {}))
    latest = hist[-1]["by_company"] if hist else {}
    trend_cos = sorted(co_seen, key=lambda co: -latest.get(co, {}).get("commits", 0))
    # A company present in history but absent from the current company_rows used to fall
    # to a second palette indexed by trend position — a different colour for the same
    # company in the same report. One source now covers both.
    _trend_colors = _store.company_color_map(trend_cos, _co_pins)

    def _series(metric):
        rows, mx = [], 1
        for co in trend_cos:
            vals = [s.get("by_company", {}).get(co, {}).get(metric, 0) for s in hist]
            mx = max([mx] + vals)
            rows.append({"company": co,
                         "color": co_color.get(co) or _trend_colors[co],
                         "vals": vals})
        return rows, mx

    commit_rows, commit_max = _series("commits")
    loc_rows, loc_max = _series("meaningful_additions")
    trend = {
        "points": len(hist),
        "dates": [s["date"] for s in hist],
        "totals_commits": [s.get("totals", {}).get("commits", 0) for s in hist],
        "totals_loc": [s.get("totals", {}).get("meaningful_additions", 0) for s in hist],
        "commit_rows": commit_rows, "commit_max": commit_max,
        "loc_rows": loc_rows, "loc_max": loc_max,
        "window_latest": hist[-1].get("lookback_days") if hist else d.get("lookback_days"),
    }

    # ---- Platform usage rolled up per person (AI-marked commits + assistant LOC)
    fabric_people = sorted(
        ({"login": l, "name": p.get("name", ""), "company": p.get("company", "Other"),
          "ai_commits": p.get("ai_commits", 0), "cpt_lines": p.get("cpt_lines", 0),
          "commits": p["commits"], "ai_pct": pct(p.get("ai_commits", 0), p["commits"])}
         for l, p in people.items() if p.get("ai_commits") or p.get("cpt_lines")),
        key=lambda x: -(x["ai_commits"] + x["cpt_lines"]))
    fabric_company = [c for c in company_rows if c.get("ai_commits") or c.get("cpt_lines")]

    # ---- conventional commit types (exact, parsed from commit subjects) --
    ctype_tot: dict = {}
    for p in people.values():
        for t, c in (p.get("commit_types") or {}).items():
            ctype_tot[t] = ctype_tot.get(t, 0) + c
    ct_total = sum(ctype_tot.values()) or 1
    commit_types = sorted(
        ({"type": t, "count": c, "pct": pct(c, ct_total)} for t, c in ctype_tot.items()),
        key=lambda x: (x["type"] == "other", -x["count"]))  # 'other' (non-conventional) last
    # The commit-type breakdown by company/repo now comes from the granular store
    # (store.worktype_breakdown, via the 'all' aggregate block) so it follows the
    # period + slice and matches the drill.

    # ---- review stats + top reviewers ------------------------------------
    reviews = d.get("reviews", {})
    rev_company: dict = {}
    for p in people.values():
        rc = rev_company.setdefault(p.get("company", "Other"), {"reviews": 0, "approvals": 0})
        rc["reviews"] += p.get("reviews_given", 0)
        rc["approvals"] += p.get("approvals_given", 0)
    rc_ttm = d.get("reviews_company_ttm", {})
    reviews_by_company = sorted(
        ({"company": co, "reviews": v["reviews"], "approvals": v["approvals"],
          "median_ttm_h": rc_ttm.get(co, {}).get("median_ttm_h"),
          "review_latency_h": rc_ttm.get(co, {}).get("median_review_latency_h"),
          "merged": rc_ttm.get(co, {}).get("merged", 0)}
         for co, v in rev_company.items() if v["reviews"]),
        key=lambda x: -x["reviews"])
    reviews_by_repo = sorted(
        ({"repo": r["name"], **(r.get("reviews") or {})}
         for r in repos.values() if r.get("reviews")),
        key=lambda x: -(x.get("total") or 0))

    # ---- commit mix: code vs spec commits (donut widget) -----------------
    commits_total = tot["commits"]
    spec_commits = tot["specs"]
    code_commits = max(commits_total - spec_commits, 0)
    pct_specs = pct(spec_commits, commits_total)
    circ = round(2 * 3.14159265 * 54, 2)
    commit_mix = {
        "total": commits_total, "code": code_commits, "specs": spec_commits,
        "pct_specs": pct_specs, "pct_code": round(100 - pct_specs, 1),
        "circ": circ,
        "specs_len": round(circ * pct_specs / 100, 2),
        "code_len": round(circ * (100 - pct_specs) / 100, 2),
    }

    # ---- concentration / bus-factor per category -------------------------
    SHOWN = 8
    for cat in categories:
        rows = cat["rows"]
        cat["top3"] = round(sum(r["pct"] for r in rows[:3]), 1)
        cum, n = 0.0, 0
        for r in rows:
            cum += r["pct"]; n += 1
            if cum >= 80:
                break
        cat["n80"] = n
        # residual so the visible bars + tail account for 100% (no silent cut)
        cat["tail_n"] = max(len(rows) - SHOWN, 0)
        cat["tail_pct"] = round(sum(r["pct"] for r in rows[SHOWN:]), 1)
        cat["tail_value"] = sum(r["value"] for r in rows[SHOWN:])

    # ---- weekly activity trend -------------------------------------------
    weekly_raw = d.get("weekly", {})
    weeks = sorted({w for cat in weekly_raw.values() for w in cat if w != "?"})
    series = [("commits", "Commits"), ("specs", "Specs"), ("prs", "PRs"), ("issues", "Issues")]
    wk_vals = [c for cat in weekly_raw.values() for c in cat.values()]

    MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def wk_start(iso):  # "2026-W23" -> Monday date
        y, w = iso.split("-W")
        return date.fromisocalendar(int(y), int(w), 1)

    def fmt(dte):
        return f"{dte.day} {MON[dte.month - 1]}"

    starts = [wk_start(w) for w in weeks]
    wlabels = [f"{fmt(s)} – {fmt(s + timedelta(days=6))}" for s in starts]  # bar tooltip
    axis, prev_m = [], None  # sparse dated ticks (≈monthly)
    for i, s in enumerate(starts):
        show = i == 0 or i == len(starts) - 1 or s.month != prev_m
        axis.append(fmt(s) if show else "")
        prev_m = s.month

    weekly = {
        "weeks": weeks, "wlabels": wlabels, "axis": axis,
        "max": max(wk_vals) if wk_vals else 1,
        "rows": [{"key": k, "title": t,
                  "vals": [weekly_raw.get(k, {}).get(w, 0) for w in weeks]}
                 for k, t in series],
    }

    # ---- platform vs app split (per category) ----------------------------
    plat_commits = sum(p["platform_commits"] for p in people.values())
    app_commits = sum(p["app_commits"] for p in people.values())
    plat_prs = sum(p["platform_prs"] for p in people.values())
    app_prs = sum(p["app_prs"] for p in people.values())
    plat_loc = sum(p.get("platform_meaningful", 0) for p in people.values())
    app_loc = sum(p.get("app_meaningful", 0) for p in people.values())
    split = {
        "commits": {"platform": plat_commits, "app": app_commits,
                    "pct_platform": pct(plat_commits, plat_commits + app_commits)},
        "prs": {"platform": plat_prs, "app": app_prs,
                "pct_platform": pct(plat_prs, plat_prs + app_prs)},
        "loc": {"platform": plat_loc, "app": app_loc,
                "pct_platform": pct(plat_loc, plat_loc + app_loc)},
    }

    def contrib_score(p):
        return p["commits"] + p["prs_opened"] + p["specs"] + p["bugs"] + p["features"]

    # ---- SCENARIO 1: contributing to Fabric = ANY activity in ANY org repo
    contributors = sorted(
        ({"login": l, "is_member": p["is_member"], "value": contrib_score(p),
          "commits": p["commits"], "prs": p["prs_opened"], "specs": p["specs"]}
         for l, p in actives.items()),
        key=lambda x: -x["value"],
    )
    members_contrib = [c for c in contributors if c["is_member"]]
    external_contributors = [c for c in contributors if not c["is_member"]]

    # ---- SCENARIO 2: using but NOT contributing back ---------------------
    # Forked an org repo (any repo = using Fabric) but made zero contribution.
    non_contributors = sorted(
        ({"login": l, "is_member": f["is_member"], "forked": f["forked"]}
         for l, f in forkers.items() if not f["has_contributed_back"]),
        key=lambda x: (-len(x["forked"]), x["login"]),
    )

    # ---- per-person master table -----------------------------------------
    table = sorted(
        (
            {
                "login": l, "name": p.get("name", ""),
                "is_member": p["is_member"], "company": p.get("company", "Other"),
                "commits": p["commits"],
                "loc": p.get("meaningful_additions", p["additions"]),
                "raw_loc": p["additions"],
                "prs": p["prs_opened"],
                "specs": p["specs"], "bugs": p["bugs"],
                "epics": p.get("epics", 0),
                "features": p["features"],
                "identity_confidence": p.get("identity_confidence", "unknown"),
                "identity_evidence": ", ".join(p.get("identity_evidence", [])),
                # all-time per-person type mix — data.json only knows platform/app
                # (true N-way per-person arrives once collect stores per-type footprints)
                "by_type": {"platform": p["platform_commits"] + p["platform_prs"],
                            "app": p["app_commits"] + p["app_prs"]},
                "ttm": p.get("median_ttm_h"), "merged_prs": p.get("merged_prs", 0),
                "reviews": p.get("reviews_given", 0), "approvals": p.get("approvals_given", 0),
                "ai_commits": p.get("ai_commits", 0), "cpt_lines": p.get("cpt_lines", 0),
                "surv_code_human": p.get("surviving_code_human", 0),
                "surv_code_ai": p.get("surviving_code_ai", 0),
                "surv_spec": p.get("surviving_spec_human", 0) + p.get("surviving_spec_ai", 0),
                "surv_win_code": p.get("survwin_code_human", 0) + p.get("survwin_code_ai", 0),
                "code_commits": max(p["commits"] - p["specs"], 0),
                "mix_specs_pct": round(100 * p["specs"] / p["commits"], 1) if p["commits"] else 0,
                "mix_code_pct": round(100 * max(p["commits"] - p["specs"], 0) / p["commits"], 1) if p["commits"] else 0,
                "klass": ("member" if p["is_member"] else "external"),
            }
            for l, p in actives.items()
        ),
        key=lambda x: (-x["surv_code_human"], -x["surv_code_ai"],
                       -(x["commits"] + x["prs"] + x["specs"] + x["bugs"] + x["features"])),
    )

    unclassified = [r["name"] for r in repos.values() if r.get("unclassified")]
    plat_repos = [r for r in repos.values() if r["classification"] == "platform"]
    total_forks = sum(r["forks"] for r in plat_repos)
    total_stars = sum(r["stars"] for r in plat_repos)

    # ---- clone traffic (anonymous usage volume, 14d, push-access only) ---
    traffic_repos = [r for r in repos.values() if r.get("traffic_access")]
    no_traffic = sum(1 for r in repos.values()
                     if not r.get("archived") and not r.get("traffic_access"))
    usage_rows = sorted(
        ({"name": r["name"], "clones": r.get("clones_14d", 0),
          "uniques": r.get("unique_cloners_14d", 0),
          "views": r.get("views_14d", 0), "visitors": r.get("unique_visitors_14d", 0),
          "daily": r.get("clones_daily", []), "paths": r.get("popular_paths", []),
          "contributors": r.get("contributor_emails", len(r.get("contributors", [])))}
         for r in traffic_repos),
        key=lambda x: -x["clones"],
    )
    dmax = max([c["count"] for r in usage_rows for c in r["daily"]] or [1])
    traffic = {
        "total_clones": sum(r.get("clones_14d", 0) for r in traffic_repos),
        "unique_cloners": sum(r.get("unique_cloners_14d", 0) for r in traffic_repos),
        "total_views": sum(r.get("views_14d", 0) for r in traffic_repos),
        "total_visitors": sum(r.get("unique_visitors_14d", 0) for r in traffic_repos),
        "n_repos": len(traffic_repos),
        "n_no_access": no_traffic,
        "rows": usage_rows,
        "daily_max": dmax,
    }

    # ---- data quality / trust surface ------------------------------------
    active_repos = [r for r in repos.values() if not r.get("archived")]
    identity = d.get("identity", {})
    api = d.get("api", {}) or {}
    data_quality = {
        "identity_unresolved": identity.get("unresolved_human", 0),
        "identity_resolved": sum(
            identity.get(k, 0) for k in ("verified", "pr_bridge", "name_bridge", "override")
        ),
        "unclassified_repos": len(unclassified),
        "traffic_with_access": len(traffic_repos),
        "traffic_total": len(active_repos),
        "traffic_pct": pct(len(traffic_repos), len(active_repos)),
        "api_rate_limited": bool(api.get("rate_limited") or api.get("partial")),
        "api_reset": api.get("reset"),
        "risk_count": (
            (1 if identity.get("unresolved_human", 0) else 0)
            + (1 if unclassified else 0)
            + (1 if no_traffic else 0)
            + (1 if (api.get("rate_limited") or api.get("partial")) else 0)
        ),
    }

    primary_org = d.get("org")
    primary_active = [r for r in active_repos if r.get("org") == primary_org]
    legacy_active = [r for r in active_repos if r.get("org") != primary_org]
    legacy_only_n = sum(1 for r in active_repos if r.get("legacy_only"))
    # non-primary repos that DO have a same-named primary twin = pre-migration copies
    legacy_dup_n = len(legacy_active) - legacy_only_n
    repo_summary = {
        "total": len(active_repos),
        "distinct": len(primary_active) + legacy_only_n,   # twins collapse into primary
        "primary": len(primary_active),
        "legacy_dup": legacy_dup_n,
        "primary_org": primary_org,
        "platform": sum(1 for r in active_repos if r.get("classification") == "platform"),
        "app": sum(1 for r in active_repos if r.get("classification") == "app"),
        "unclassified": len(unclassified),
        "missing_traffic": no_traffic,
        "legacy_only": legacy_only_n,
    }

    repo_rows = sorted(
        (
            {
                "full_name": key,
                "org": r.get("org", ""),
                "name": r["name"],
                "classification": r["classification"],
                "unclassified": r.get("unclassified", False),
                "stars": r.get("stars", 0),
                "forks": r.get("forks", 0),
                "contributors": r.get("contributor_emails", len(r.get("contributors", []))),
                "traffic_access": r.get("traffic_access", False),
                "clones": r.get("clones_14d", 0),
                "uniques": r.get("unique_cloners_14d", 0),
                "element": r.get("element", "Other"),
                "code_loc": r.get("code_loc"),
                "spec_loc": r.get("spec_loc"),
                "total_loc": r.get("total_loc"),
                "legacy_only": r.get("legacy_only", False),
            }
            for key, r in repos.items()
            if not r.get("archived")
        ),
        key=lambda r: (
            r["classification"] != "platform",
            not r["traffic_access"],
            -r["clones"],
            -r["contributors"],
            r["name"].lower(),
        ),
    )

    emails_by_login = {
        l: ", ".join(p.get("emails", [])) for l, p in people.items() if p.get("emails")
    }

    # ---- per-element rollup ----------------------------------------------
    element_rows = sorted(
        (
            {
                **e,
                "code_kloc": round((e.get("code_loc") or 0) / 1000, 1),
                "spec_kloc": round((e.get("spec_loc") or 0) / 1000, 1),
                "people": e.get("people_members", 0) + e.get("people_external", 0),
            }
            for e in (d.get("elements", {}) or {}).values()
        ),
        key=lambda x: -(x.get("code_loc") or 0),
    )

    # Period panels come from store.aggregate() (computed in load_data and attached
    # as _periods) — one block per preset window, switched client-side.
    window_labels = d.get("window_labels") or ["all"]
    periods = d.get("_periods", [])
    # Degraded-store fallbacks: no periods at all (data.json fallback) or an
    # all-zero aggregate (run row seeded but granular tables empty) would make
    # the headline panels vanish — synthesize one all-time block from this model.
    if tot["commits"] and (not periods
                           or not any(p["totals"]["commits"] for p in periods)):
        periods = [{
            "label": "all", "totals": tot, "categories": categories,
            "company_rows": company_rows, "commit_types": commit_types,
            "loc_added_h": f"{tot['meaningful_additions']:,}",
        }]
        window_labels = ["all"]

    # --- Contributors block: cumulative headcount, total + the biggest companies -----
    # Companies come from the DATA, not from a list in this file. They used to be three
    # hardcoded names, which meant every installation whose contributors work for anyone
    # else got three tiles reading 0 and a chart with three flat lines — with nothing
    # saying why. Picked by current headcount so the block shows whoever actually turned
    # up, and capped at three because that is what the layout has room for.
    contrib_raw = d.get("_contrib", [])
    _latest_by_co = (contrib_raw[-1].get("by_company") or {}) if contrib_raw else {}
    CO3 = [co for co, _ in sorted(_latest_by_co.items(),
                                  key=lambda kv: (-(kv[1] or 0), kv[0]))
           if co and co != "Other" and (_latest_by_co.get(co) or 0) > 0][:3]
    # Was a palette of its own, indexed by rank — so a company could be purple in this
    # chart and amber in the company table of the SAME report. Same source as everywhere.
    CONTRIB_COLORS = {"Total": "#1f2328"}
    CONTRIB_COLORS.update(_store.company_color_map(CO3, _co_pins))
    contrib_block = None
    if contrib_raw:
        cur = contrib_raw[-1]
        # baseline ≈ 90 days before the latest point, for the Δ chips
        try:
            cutoff = (datetime.strptime(contrib_raw[-1]["date"], "%Y-%m-%d")
                      - timedelta(days=90)).strftime("%Y-%m-%d")
        except ValueError:
            cutoff = None
        prev = None
        for c in contrib_raw:
            if cutoff and c["date"] <= cutoff:
                prev = c
        prev = prev or contrib_raw[0]

        def _n(pt, key):
            return pt["total"] if key == "Total" else pt["by_company"].get(key, 0)
        tiles = []
        for key in ["Total"] + CO3:
            tiles.append({"label": "Total contributors" if key == "Total" else key,
                          "now": _n(cur, key), "delta": _n(cur, key) - _n(prev, key),
                          "color": CONTRIB_COLORS[key]})
        series_keys = ["Total"] + CO3
        cmax = max([c["total"] for c in contrib_raw] + [1])
        series = [{"name": k, "color": CONTRIB_COLORS[k],
                   "vals": [_n(c, k) for c in contrib_raw]} for k in series_keys]
        contrib_block = {
            "tiles": tiles, "series": series, "max": cmax,
            "dates": [c["date"][:7] for c in contrib_raw[:-1]] + ["today"],
            "since": prev["date"], "points": len(contrib_raw),
        }

    # All-time block feeding the filterable-panel macros at build time. The
    # /api/period fragment feeds the SAME macros a store.aggregate() window block.
    # Options for the Person-tab selector: EVERY contributor (including the
    # 'Other' bucket), sorted by name. Carries emails so the combobox can search
    # by name, login, email, or company.
    person_options = sorted(
        ({"login": r["login"], "name": r.get("name", ""), "company": r.get("company", ""),
          "emails": emails_by_login.get(r["login"], "")}
         for r in table),
        key=lambda p: (p["name"] or p["login"]).lower())
    person_companies = sorted({p["company"] for p in person_options if p["company"]})

    # All-time filterable panels: assemble a base from the blob, then OVERLAY the
    # store.aggregate('all') window on top. At runtime that window supplies every
    # panel — so the build-time render is the SAME data the /api/period 'all' fetch
    # returns and the two cannot diverge; when only a partial/synthesized block
    # exists (degraded store, tests) the blob base fills the gaps. delivery/flow
    # (taxonomy-derived, full range) are grafted on last: store.aggregate lacks them.
    all_win = next((p for p in periods if p.get("label") == "all"), None)
    all_block = {
        "label": "all", "totals": tot,
        "loc_added_h": f"{tot['meaningful_additions']:,}",
        "company_rows": company_rows, "categories": categories,
        "commit_types": commit_types, "commit_mix": commit_mix,
        "worktype_break": None, "split": split, "element_rows": element_rows,
        "people": table, "traffic": traffic, "spark": None,
        "weekly": weekly, "ctrend": None,
        "ai_usage": ai_usage, "bots": d.get("bots", {}), "reviews": reviews,
        "score": d.get("score_all"),
        **(all_win or {}),
        "delivery": d.get("delivery_all", {}), "flow": d.get("flow_all", {}),
    }

    return {
        "meta": d,
        "generated": d["generated_at"],
        "inter_woff2": _font_b64("inter-latin.woff2"),
        "jakarta_woff2": _font_b64("jakarta-latin.woff2"),
        "sidebar": shell.sidebar_html("report"),
        "shell_css": shell.SHELL_CSS,
        "emails_by_login": emails_by_login,
        "all_block": all_block,
        "scope_targets": d.get("scope_targets", {}),
        "periods": periods,
        "window_labels": window_labels,
        "contrib_block": contrib_block,
        # honest chip label: the "all" preset only spans the collected window
        "all_label": ("All-time" if d.get("all_time", True)
                      else f"Last {d.get('lookback_days')} days"),
        "totals": tot,
        "loc_added_h": f"{tot['meaningful_additions']:,}",
        "raw_loc_added_h": f"{tot['additions']:,}",
        "company_rows": company_rows,
        "categories": categories,
        "split": split,
        "contributors": contributors,
        "members_contrib": members_contrib,
        "external_contributors": external_contributors,
        "non_contributors": non_contributors,
        "traffic": traffic,
        "data_quality": data_quality,
        "repo_summary": repo_summary,
        "repo_rows": repo_rows,
        "element_rows": element_rows,
        "weekly": weekly,
        "commit_mix": commit_mix,
        "ai_usage": ai_usage,
        "studio_prov": d.get("studio_provenance", {}),
        "gears_usage": d.get("gears_usage", {}),
        "fabric_trackers": d.get("fabric_trackers", {}),
        "cpt_people": cpt_people,
        "cpt_by_company": cpt_by_company,
        "commit_types": commit_types,
        "reviews": reviews,
        "reviews_by_company": reviews_by_company,
        "reviews_by_repo": reviews_by_repo,
        "fabric_people": fabric_people,
        "fabric_company": fabric_company,
        "trend": trend,
        "bots": d.get("bots", {}),
        "table": table,
        "person_options": person_options,
        "person_companies": person_companies,
        "unclassified": unclassified,
        "legacy_names": sorted(r["name"] for r in repos.values() if r.get("legacy_only")),
        "platform_repos": sorted(plat_repos, key=lambda r: -r["forks"]),
        "total_forks": total_forks,
        "total_stars": total_stars,
        "n_members": len(d["members"]),
    }


PANELS = _load_tmpl("panels")


TEMPLATE = _load_tmpl("report")


# The fragment reuses the SAME panel macros defined in TEMPLATE (imported by name
# with the render context), so build-time and windowed panels never drift.
FRAGMENT = _load_tmpl("fragment")


# Delivery fragment (served by /api/delivery): the 3 delivery panels for a given
# period AND repo slice. Decoupled from FRAGMENT so the slice filter and the main
# period swap never fight over the same regions.
DELIVERY_FRAGMENT = _load_tmpl("delivery")


# Trend panel alone (served by /api/trend): re-renders the stacked-area chart at a
# chosen granularity without rebuilding the rest of the period fragment.
TREND_FRAGMENT = _load_tmpl("trend")


# Flow tab (served by /api/flow): the friction explainer + timeline-derived flow
# metrics for a period + repo slice. Follows the period and the global slice.
FLOW_FRAGMENT = _load_tmpl("flow")


# Per-person dashboard fragment (served by /api/person): header, KPIs, activity
# heatmap + weekly table, composition and all-time impact. Reuses shared macros.
PERSON_FRAGMENT = _load_tmpl("person")


# Custom dashboard page (served by /dashboard/<id>): page shell + scope/period
# controls + a grid of panel cells filled client-side via /api/dashboard/panel.
DASHBOARD = _load_tmpl("dashboard")


# Custom dashboard editor (served by /dashboard/<id>/edit, owner-only): catalog-driven
# add-panel form + drag-reorderable panel list with live preview via
# /api/dashboard/preview-panel, saved via POST /api/dashboard/<id>.
DASHBOARD_EDITOR = _load_tmpl("dashboard_editor")


_FONT_B64: dict = {}
def _font_b64(filename: str) -> str:
    """Base64 of a vendored woff2 in assets/, so the report is self-contained (no
    runtime CDN). Cached; empty string if missing (falls back to the system stack)."""
    if filename not in _FONT_B64:
        import base64
        try:
            _FONT_B64[filename] = base64.b64encode(
                open(os.path.join(ROOT, "assets", filename), "rb").read()).decode()
        except OSError:
            _FONT_B64[filename] = ""
    return _FONT_B64[filename]


def _to_number(n):
    """Coerce a value (int/float/'7,372,248'/None) to a float, or None."""
    try:
        if isinstance(n, str):
            n = n.replace(",", "").strip()
        if n == "" or n is None:
            return None
        return float(n)
    except (TypeError, ValueError):
        return None


def _num(n) -> str:
    """A plain COUNT: grouped integer with thousands separators — '5,848'. Use for
    commits, PRs, issues, specs, people, reviews, gate runs, line diffs, etc."""
    v = _to_number(n)
    return f"{int(round(v)):,}" if v is not None else "0"


def _loc(n) -> str:
    """A VOLUME (lines of code): compact K/M — 3.49M, 25.7K, 812. Use for every
    LOC/line-volume figure so the same magnitude reads the same everywhere."""
    v = _to_number(n)
    if v is None:
        return "0"
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{v / 1_000:.1f}K"
    return f"{int(round(v))}"


# back-compat alias — existing templates use |compact for LOC; now K/M-aware
_compact = _loc


def _pct(v) -> str:
    """A percentage number (no % sign): one decimal, trailing '.0' stripped —
    50, 72.1. Templates keep the literal % after it."""
    f = _to_number(v)
    if f is None:
        return "0"
    return f"{f:.1f}".rstrip("0").rstrip(".")


def _dur(sec) -> str:
    """A duration in seconds → compact human form: 45s, 2m28s, 1h03m."""
    v = _to_number(sec)
    if v is None:
        return "—"
    s = int(round(v))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def _hours(h) -> str:
    """A duration given in HOURS → compact human form: 45m, 18h, 3.2d. Used by the
    Flow tab's cycle-time medians (which are computed in hours)."""
    v = _to_number(h)
    if v is None:
        return "—"
    if v < 1:
        return f"{int(round(v * 60))}m"
    if v < 48:
        return f"{v:.1f}".rstrip("0").rstrip(".") + "h"
    return f"{v / 24:.1f}".rstrip("0").rstrip(".") + "d"


_ENV: Environment | None = None


def _env() -> Environment:
    """Jinja env with all templates registered so the report and fragment can
    import the shared panel macros from the 'panels' template. Memoised at module
    scope: templates are baked into the constants at import, so the env (and its
    compiled templates) is stable for the process — rebuilding it per render() was
    pure waste, and matters now that the report renders live per request."""
    global _ENV
    if _ENV is not None:
        return _ENV
    env = Environment(autoescape=True,
                      loader=DictLoader({"panels": PANELS, "report": TEMPLATE,
                                         "fragment": FRAGMENT, "person": PERSON_FRAGMENT,
                                         "delivery": DELIVERY_FRAGMENT, "trend": TREND_FRAGMENT,
                                         "flow": FLOW_FRAGMENT, "dashboard": DASHBOARD,
                                         "dashboard_editor": DASHBOARD_EDITOR}))
    env.filters["compact"] = _compact
    env.filters["num"] = _num
    env.filters["loc"] = _loc
    env.filters["pct"] = _pct
    env.filters["dur"] = _dur
    env.filters["hours"] = _hours
    env.globals["ecolor"] = _element_color
    env.globals["stacked_area"] = _stacked_area_vega
    env.globals["line_chart"] = _line_chart_vega
    env.globals["trend_colors"] = _trend_colors
    env.globals["wtcolor"] = _worktype_color
    env.globals["scol"] = _score_color
    _ENV = env
    return env


def _score_color(v) -> str:
    """Traffic-light colour for a 0-100 sub-score (None → muted). Shared by the
    Developer-score gauge, pillar bars and leaderboard so tones never drift."""
    if v is None:
        return "var(--mut)"
    if v >= 67:
        return "#10b981"
    if v >= 45:
        return "#f59e0b"
    return "#ef4444"


# Palette shared with the Config page's elemColor() so element colours match there.
_ELEM_PALETTE = ["#5b5bf0", "#8b5cf6", "#f59e0b", "#06b6d4", "#10b981", "#ef4444", "#2f80ed", "#d946ef"]


def _stacked_area_vega(rows, dates, company_rows, unit="commits", noun=None):
    """Jinja global for `stacked_area(...)`: builds a Vega-Lite stacked-area spec
    and wraps it in a `vl-panel` container for client-side hydration (see
    vega_spec.stacked_area_spec / panel_html). `noun` is accepted for call-site
    compatibility (e.g. the board CFD passes 'items') but unused — the spec
    derives its own tooltip/axis labels. Empty input → Markup("") so the
    template macros' "no data" hint still shows. `vega_spec` is imported lazily
    because it imports `render` at module scope."""
    import vega_spec
    return vega_spec.panel_html(vega_spec.stacked_area_spec(rows, dates, company_rows, unit))


# Keyed lowercase so both the Work-type panel (raw 'feat'/'ci') and the Trend
# breakdown (display 'Feat'/'CI') resolve to the same colour.
_WORKTYPE_COLORS = {"feat": "#5b5bf0", "fix": "#ef4444", "docs": "#06b6d4",
                    "test": "#10b981", "refactor": "#8b5cf6", "chore": "#9aa3b2",
                    "perf": "#f59e0b", "build": "#2f80ed", "ci": "#d946ef",
                    "style": "#a3a3a3", "revert": "#e11d48", "other": "#9aa3b2"}


def _worktype_color(name: str) -> str:
    k = str(name).lower()
    if k in _WORKTYPE_COLORS:
        return _WORKTYPE_COLORS[k]
    h = 0
    for ch in k:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return _ELEM_PALETTE[h % len(_ELEM_PALETTE)]


def _trend_colors(dim, rows, pr):
    """Colour source list [{company,color}] for the main trend chart, matched to the
    active breakdown dimension so the bands/legend reuse each dimension's own palette
    (company colours, configured repo-type colours, element colours, work-type map)."""
    def _get(o, k, d=None):
        return (o.get(k, d) if isinstance(o, dict) else getattr(o, k, d))
    labels = [r["company"] for r in rows]
    if dim == "company":
        cmap = {_get(c, "company"): _get(c, "color") for c in (_get(pr, "company_rows") or [])}
    elif dim == "repo_type":
        cmap = {_get(t, "name"): _get(t, "color")
                for t in (_get(_get(pr, "split") or {}, "types") or [])}
    elif dim == "element":
        cmap = {n: _element_color(n) for n in labels}
    else:  # work_type
        cmap = {n: _worktype_color(n) for n in labels}
    return [{"company": n, "color": cmap.get(n) or "#9aa3b2"} for n in labels]


def _line_chart_vega(series, dates, unit="", area_first=False):
    """Jinja global for `line_chart(...)`: builds a Vega-Lite line/area spec and
    wraps it in a `vl-panel` container for client-side hydration (see
    vega_spec.line_spec / panel_html). Used for PR throughput, time-to-merge and
    active contributors. Empty input → Markup("") so the template macros' "no
    data" hint still shows. `vega_spec` is imported lazily because it imports
    `render` at module scope."""
    import vega_spec
    return vega_spec.panel_html(vega_spec.line_spec(series, dates, unit, area_first))


def _element_color(name: str) -> str:
    """Deterministic colour for a product element (stable hash → palette). Mirrors
    the JS elemColor() in the Config editor so the two never drift."""
    if not name or name == "Other":
        return "#9aa3b2"
    h = 0
    for ch in str(name):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return _ELEM_PALETTE[h % len(_ELEM_PALETTE)]


_BAND_COLORS = {"Strong": "#10b981", "Solid": "#2f80ed",
                "Developing": "#f59e0b", "Building": "#ef4444"}
_PILLAR_COLORS = {"engagement": "#5b5bf0", "delivery": "#06b6d4",
                   "craft": "#10b981", "flow": "#f59e0b"}


def _delta_chip(d: dict | None, lower_better: bool = False) -> dict | None:
    """JSON-able equivalent of the deltachip() Jinja macro (panels/01_helpers.j2).
    `lower_better` flips the chip's COLOUR class only (never the arrow), same as
    the macro — used by the Delivery KPIs (defect/abandon rate, reverts, cycle-
    time medians, CI duration) where "up" is bad; the Overview KPI tiles never
    pass it (always False there). None -> no chip; {'cls','text','tip'} matches
    the '<span class="dlt CLS" data-tip=TIP>TEXT</span>' markup 1:1 so the React
    tile can render it without re-deriving formatting."""
    if not d:
        return None
    if d.get("pct") is not None:
        direction = d["dir"]
        cls = ("flat" if direction == "flat" else
               (("down" if direction == "up" else "up") if lower_better else direction))
        arrow = "▲" if direction == "up" else ("▼" if direction == "down" else "±")
        return {"cls": cls, "text": f"{arrow} {_pct(abs(d['pct']))}%",
                "tip": f"vs previous equal period (was {_num(d['prev'])})"}
    if d.get("diff"):
        return {"cls": "down" if lower_better else "up", "text": "▲ new",
                "tip": "nothing in the previous equal period"}
    return None


def _kpi_tiles_json(pr: dict) -> list:
    """The 8 Overview KPI tiles as JSON — same values/order as panel_kpis()
    (templates/panels/02_overview.j2), pre-formatted with the same filters
    (_num/_loc/_pct) so the React tile is a pure render, never a re-format."""
    totals = pr.get("totals") or {}
    deltas = pr.get("deltas") or {}
    spark = pr.get("spark") or {}

    def tile(icon, value, label, sub, delta_key, spark_key, spark_color, drill, tip=None):
        return {"icon": icon, "value": value, "label": label, "sub": sub, "tip": tip,
                "delta": _delta_chip(deltas.get(delta_key)) if delta_key else None,
                "sparkPts": spark.get(spark_key), "sparkColor": spark_color, "drill": drill}

    meaningful = totals.get("meaningful_additions", 0) or 0
    loc_added_h = pr.get("loc_added_h") or _num(meaningful)
    people = totals.get("people", 0) or 0
    return [
        tile("commit", _num(totals.get("commits")), "commits",
             "vs prev period" if deltas.get("commits") else "in period",
             "commits", "commits_pts", "var(--c-commit)", {"drill": "commit"}),
        tile("loc", _loc(meaningful), "meaningful LOC", f"{loc_added_h} · code volume",
             "meaningful_additions", "loc_pts", "var(--c-loc)", {"drill": "commit"},
             tip=f"{loc_added_h} meaningful lines added"),
        tile("pr", _num(totals.get("prs")), "PRs opened",
             f"{_num(totals.get('prs_merged'))} merged",
             "prs", "prs_pts", "var(--c-pr)", {"drill": "pr"}),
        tile("spec", _num(totals.get("specs")), "spec edits", "commits to spec docs",
             "specs", "specs_pts", "var(--c-spec)", {"drill": "commit", "flag": "is_spec"}),
        tile("bug", _num(totals.get("bugs")), "bugs opened", "issues categorised as bug",
             "bugs", "bugs_pts", "var(--c-bug)", {"drill": "issue", "flag": "is_bug"}),
        tile("epic", _num(totals.get("epics", 0)), "epics opened", "issues categorised as epic",
             "epics", "epics_pts", "var(--c-epic)", {"drill": "issue", "flag": "is_epic"}),
        tile("feature", _num(totals.get("features")), "features opened",
             "issues categorised as feature",
             "features", "features_pts", "var(--c-feature)", {"drill": "issue", "flag": "is_feature"}),
        tile("people", _num(people), "active people", "in period",
             "people", "people_pts", "var(--c-people)", {"drill": "people"} if people else None),
    ]


def _contributors_json(contrib_block: dict | None) -> dict | None:
    """JSON form of the "Contributors" all-time cumulative block (build_model's
    contrib_block) — tiles + the cumulative-by-company line chart spec. All-time,
    not period/scope-filtered (same as the monolith: this block never changes
    with the period/slice controls), so callers can reuse ONE computation for
    every period/scope variant of the Overview payload."""
    if not contrib_block:
        return None
    import vega_spec
    tiles = [{"value": _num(t["now"]), "label": t["label"], "color": t["color"],
              "sub": f"{'+' if t['delta'] > 0 else ''}{t['delta']} in 90d"}
             for t in contrib_block["tiles"]]
    spec = vega_spec.line_spec(contrib_block["series"], contrib_block["dates"], "")
    legend = [{"name": s["name"], "color": s["color"]} for s in contrib_block["series"]]
    return {"tiles": tiles, "chartSpec": spec, "legend": legend,
            "since": contrib_block["since"], "points": contrib_block["points"]}


def _weekly_json(weekly: dict | None) -> dict | None:
    """JSON form of panel_weekly() — one mini line-chart spec per metric (commits/
    specs/prs/issues), area_first=True to match the macro's line_chart(..., true)."""
    if not weekly or not weekly.get("weeks"):
        return None
    import vega_spec
    wkcol = {"commits": "#5b5bf0", "specs": "#8b5cf6", "prs": "#2f80ed", "issues": "#f59e0b"}
    rows = []
    for r in weekly.get("rows", []):
        vals = r.get("vals") or []
        spec = vega_spec.line_spec(
            [{"name": r["title"], "vals": vals, "color": wkcol.get(r["key"], "#8b5cf6")}],
            weekly.get("wlabels_short"), "", True)
        rows.append({"title": r["title"], "total": _num(sum(v or 0 for v in vals)), "chartSpec": spec})
    return {"rows": rows, "weeksCount": len(weekly["weeks"])}


def _worktype_json(pr: dict) -> dict:
    """JSON form of panel_worktype() — the type bar/list (always shown) plus the
    collapsed "breakdown by company & repo" detail (closed <details>, so its
    exact contents don't affect the pixel-parity gate — included anyway for
    completeness/future interactivity)."""
    types = pr.get("commit_types") or []
    total = sum(t.get("count", 0) for t in types)
    rows = [{"type": t["type"], "count": t["count"], "pct": t["pct"],
             "color": _worktype_color(t["type"])} for t in types]
    wb = pr.get("worktype_break")
    breakdown = None
    if wb and (wb.get("by_company") or wb.get("by_repo")):
        breakdown = {
            "typeCols": wb.get("type_cols", []),
            "byCompany": [{"company": r["company"], "types": r["types"], "total": r["total"]}
                          for r in wb.get("by_company", [])],
            "byRepo": [{"repo": r["repo"], "key": r["key"], "legacy": r["legacy"],
                        "types": r["types"], "total": r["total"]}
                       for r in wb.get("by_repo", [])],
        }
    return {"rows": rows, "total": total, "breakdown": breakdown}


def _score_json(score: dict | None) -> dict | None:
    """JSON form of panel_score() (templates/panels/02_overview.j2) — band
    distribution, top-N leaderboard (make-up per pillar, for a client-side
    <SegBar>), by-company medians, and the team's real per-pillar medians."""
    if not score or not score.get("n"):
        return None
    active = score.get("active_pillars") or ["engagement", "delivery", "craft", "flow"]
    bands = [{"band": b["band"], "n": b["n"], "color": _BAND_COLORS.get(b["band"], "#9aa3b2")}
             for b in score.get("bands", [])]
    top = []
    for r in score.get("top", []):
        contributions = {k: r["contributions"].get(k) for k in ("engagement", "delivery", "craft", "flow")
                         if k in active and r["contributions"].get(k)}
        top.append({"rank": r["rank"], "login": r["login"], "name": r.get("name") or r["login"],
                    "score": r["score"], "contributions": contributions})
    by_company = [{"company": c["company"], "median": c["median"], "n": c["n"]}
                  for c in score.get("by_company", [])]
    tm = score.get("team_medians") or {}
    return {
        "n": score["n"], "median": score["median"], "activePillars": active,
        "pillarColors": {k: v for k, v in _PILLAR_COLORS.items() if k in active},
        "bands": bands, "top": top, "byCompany": by_company,
        # Pre-formatted STRINGS (not raw floats) — a whole-number median (2.0)
        # must still print "2.0" (Python's kpi_tile(value|round(1)) does, via
        # Jinja's str() on a float); JS's default String(2) would print "2".
        "teamMedians": {
            "commits": _num(tm.get("commits")) if tm.get("commits") is not None else None,
            "ttm": (f"{round(tm['ttm'], 1)}h" if "delivery" in active and tm.get("ttm") is not None else None),
            "rounds": (f"{round(tm['rounds'], 1)}"
                       if "craft" in active and tm.get("rounds") is not None else None),
            "flow": (f"{round(tm['flow'], 2)}"
                     if "flow" in active and tm.get("flow") is not None else None),
        },
    }


def overview_json(pr: dict, meta: dict) -> dict:
    """The full /api/report/overview payload: the Overview page's data, JSON
    instead of the server-rendered HTML fragment serve_custom_period() returns
    (see server.py's serve_report_overview). `pr` is a period/scope-scoped
    block shaped like store.aggregate()'s return (+ 'score' and, optionally,
    'deltas' attached — same convention serve_custom_period() and
    build_model()'s all_block already use). `meta` carries everything that does
    NOT vary with the period/scope filter: org/window info, scope_targets, the
    all-time contrib_block, and the resolved period/scope echoed back for the
    filter bar to reflect. Pure / never touches the DB — same discipline as
    vega_spec.build_spec: bad input degrades a section to empty, never raises."""
    return {
        "ok": True,
        "meta": {
            "org": meta.get("org"), "allTime": bool(meta.get("all_time", True)),
            "windowStart": meta.get("window_start"), "lookbackDays": meta.get("lookback_days"),
            "generatedText": (meta.get("generated") or "")[:16].replace("T", " "),
        },
        "period": meta.get("period") or {"preset": "all", "label": meta.get("all_label", "All-time"),
                                          "from": None, "to": None},
        "periodPresets": [
            {"key": lbl, "label": {"7d": "7 days", "30d": "30 days", "90d": "90 days",
                                    "365d": "1 year", "all": meta.get("all_label", "All-time")}.get(lbl, lbl)}
            for lbl in (meta.get("window_labels") or ["all"])
        ],
        "scope": meta.get("scope", ""),
        "scopeTargets": meta.get("scope_targets") or {},
        "dataQuality": {"apiRateLimited": bool((meta.get("data_quality") or {}).get("api_rate_limited")),
                        "apiReset": (meta.get("data_quality") or {}).get("api_reset")},
        "kpis": _kpi_tiles_json(pr),
        "contributors": _contributors_json(meta.get("contrib_block")),
        "companies": {"rows": pr.get("company_rows") or []},
        "weekly": _weekly_json(pr.get("weekly")),
        "workType": _worktype_json(pr),
        "score": _score_json(pr.get("score")),
    }


# Category key -> (drill kind, flag) — JSON port of panel_categories()'s CATDRILL
# (templates/report.j2). 'code' has no entry (its rows render a plain number,
# no drill span) — matches CATDRILL.get(...) returning None for that key.
_CATEGORY_DRILL = {
    "code_loc": ("commit", ""), "specs": ("commit", "is_spec"),
    "bugs": ("issue", "is_bug"), "epics": ("issue", "is_epic"), "features": ("issue", "is_feature"),
}


def people_json(pr: dict, meta: dict) -> dict:
    """The full /api/report/people payload: the People view's data — the
    %-contribution-by-category grid, the code-review section (reviewer table,
    conditional on any PRs existing in the window), and the big per-person
    breakdown table — JSON instead of the server-rendered fragments the
    mode="people" mode-sections paint (templates/report.j2 lines ~996-1063:
    panel_categories + panel_reviews + panel_people, panel_categories/
    panel_reviews live in templates/panels/02_overview.j2, panel_people in
    templates/panels/05_people.j2). `pr` is shaped like store.aggregate()'s
    return (+ optional 'score'/'deltas', same convention overview_json/
    build_model's all_block already use) — i.e. `pr['people']`/
    `pr['categories']`/`pr['reviews']`/`pr['split']` are the SAME fields the
    monolith's panels read (People has no separate collector/builder — it
    reads the general aggregate block, exactly like Overview does). `meta`
    carries everything that does NOT vary with the period/scope filter, same
    convention as overview_json — PLUS 'emails_by_login' (the per-login email
    map build_model()'s Jinja globals already carry for the `gh()`/tip_key
    lookups — see server.py's serve_report_people threading it through).
    Pure / never touches the DB — bad/empty input degrades every section to
    its empty shape, never raises."""
    emails = meta.get("emails_by_login") or {}

    # ---- % contribution by category (panel_categories) --------------------
    categories = []
    for cat in (pr.get("categories") or []):
        rows = cat.get("rows") or []
        is_loc = cat.get("key") == "code_loc"
        drill = _CATEGORY_DRILL.get(cat.get("key"))
        categories.append({
            "key": cat.get("key"), "title": cat.get("title"), "unit": cat.get("unit"),
            "total": cat.get("total", 0), "valueIsLoc": is_loc,
            "top3Pct": cat.get("top3", 0), "n80": cat.get("n80", 0),
            "tailN": cat.get("tail_n", 0), "tailPct": cat.get("tail_pct", 0),
            "tailValue": cat.get("tail_value", 0),
            "drillKind": drill[0] if drill else None,
            "drillFlag": (drill[1] if drill and drill[1] else None),
            "rows": [{"login": r["login"], "email": emails.get(r["login"], ""),
                      "value": r["value"], "pct": r["pct"]} for r in rows],
        })

    # ---- code review (panel_reviews) ---------------------------------------
    rv = pr.get("reviews") or {}
    reviews = None
    if rv.get("total_prs"):
        reviewers = rv.get("reviewers") or []
        rvmax = max((r.get("reviews", 0) for r in reviewers), default=0) or 1
        mttm = rv.get("median_ttm_h")
        reviews = {
            "totalPrs": rv.get("total_prs", 0), "reviewedPrs": rv.get("reviewed_prs", 0),
            "coveragePct": rv.get("coverage_pct", 0),
            # PRE-FORMATTED STRINGS, not raw floats — both are interpolated
            # literally (`{{v}}h`) by the monolith without any further
            # |round, so a whole-number median must still print "4.0h", not
            # "4h" (same trailing-".0" convention as person_rows['ttm']).
            "medianTtmH": (f"{round(mttm, 1)}" if mttm is not None else None),
            "merged": rv.get("merged", 0),
            "windowed": bool(rv.get("windowed")),
            "reviewers": [{"login": r["login"], "reviews": r.get("reviews", 0),
                           "approvals": r.get("approvals", 0),
                           "latencyH": (f"{round(lh, 1)}" if (lh := r.get("latency_h")) is not None else None),
                           "barPct": round(100 * r.get("reviews", 0) / rvmax, 1)}
                          for r in reviewers],
        }
        # ---- collapsed "Review load — by company & by repo" detail --------
        # All-time, build-time-computed (meta['reviews_by_company']/['reviews_by_repo']
        # — see build_model(), NOT part of `pr`/the period-scoped aggregate — same
        # convention as _worktype_json's 'breakdown': a closed <details>, so its
        # exact contents don't affect the pixel-parity gate, included for
        # completeness. `f"{round(x,1)}"` pre-formats the two h-medians (raw
        # kind='raw' floats — same trailing-'.0' rationale as ttm/latencyH above).
        def _h(v):
            return f"{round(v, 1)}" if v is not None else None
        rbc = meta.get("reviews_by_company") or []
        rbr = meta.get("reviews_by_repo") or []
        legacy_names = set(meta.get("legacy_names") or [])
        if rbc or rbr:
            reviews["byCompany"] = [
                {"company": c["company"], "reviews": c.get("reviews", 0), "approvals": c.get("approvals", 0),
                 "reviewLatencyH": _h(c.get("review_latency_h")), "medianTtmH": _h(c.get("median_ttm_h")),
                 "merged": c.get("merged", 0)}
                for c in rbc
            ]
            reviews["byRepo"] = [
                {"repo": r["repo"], "legacy": r["repo"] in legacy_names,
                 "total": r.get("total", 0), "reviewed": r.get("reviewed", 0),
                 "coveragePct": r.get("coverage_pct", 0), "medianTtmH": _h(r.get("median_ttm_h"))}
                for r in rbr
            ]

    # ---- per-person breakdown (panel_people) -------------------------------
    people = pr.get("people") or []
    cmax = max((p.get("commits", 0) for p in people), default=0) or 1
    lmax = max((p.get("loc", 0) for p in people), default=0) or 1
    split_types = [{"id": t["id"], "name": t["name"], "color": t["color"]}
                   for t in ((pr.get("split") or {}).get("types") or [])]
    person_rows = []
    for r in people:
        ttm = r.get("ttm")
        person_rows.append({
            **r,
            "email": emails.get(r.get("login", ""), ""),
            "not_member": not r.get("is_member"),
            "commits_pct": round(100 * r.get("commits", 0) / cmax, 1),
            "loc_pct": round(100 * r.get("loc", 0) / lmax, 1),
            "loc_tip": f"{_loc(r.get('raw_loc'))} raw additions",
            # PRE-FORMATTED STRING, not a raw float — the client Column reads
            # this via kind='raw' (a literal String(v), no reformatting), so a
            # whole-number median (4.0) must still print "4.0"; a JSON number
            # would lose that trailing ".0" once parsed back into JS (same
            # convention as delivery_json's flow.pct / Score's teamMedians).
            "ttm": (f"{round(ttm, 1)}" if ttm is not None else None),
        })

    return {
        "ok": True,
        "meta": {
            "org": meta.get("org"), "allTime": bool(meta.get("all_time", True)),
            "windowStart": meta.get("window_start"), "lookbackDays": meta.get("lookback_days"),
            "generatedText": (meta.get("generated") or "")[:16].replace("T", " "),
        },
        "period": meta.get("period") or {"preset": "all", "label": meta.get("all_label", "All-time"),
                                          "from": None, "to": None},
        "periodPresets": [
            {"key": lbl, "label": {"7d": "7 days", "30d": "30 days", "90d": "90 days",
                                    "365d": "1 year", "all": meta.get("all_label", "All-time")}.get(lbl, lbl)}
            for lbl in (meta.get("window_labels") or ["all"])
        ],
        "scope": meta.get("scope", ""),
        "scopeTargets": meta.get("scope_targets") or {},
        "dataQuality": {"apiRateLimited": bool((meta.get("data_quality") or {}).get("api_rate_limited")),
                        "apiReset": (meta.get("data_quality") or {}).get("api_reset")},
        "categories": categories,
        "reviews": reviews,
        "people": {
            "rows": person_rows, "splitTypes": split_types, "cap": 40,
            # JSON port of panel_people()'s `ranked_by` — the "Show all" note's
            # wording depends on WHICH ranking produced `pr['people']`'s order:
            # the all-time fast path (pr['label']=='all', build_model's table,
            # sorted by surviving hand-written code) vs any live re-aggregate
            # (store.aggregate(..., label='custom', ...), sorted by period
            # activity) — including when a scope forces a live re-aggregate
            # over the full all-time span (pr['label'] is 'custom' there too).
            "rankedByLabel": ("surviving hand-written code" if pr.get("label") == "all" else "period activity"),
        },
    }


def _jr(x, p: int = 0) -> str:
    """String form of Jinja's `|round(p)` filter — matches it byte-for-byte
    (method='common' → Python round(value, p), rendered via str). At p=0 that's
    always an "N.0" float (e.g. "43.0"), NOT the "N" you'd get from |round|int —
    the person composition legends / merge-rate use the bare `|round`, so the
    trailing ".0" is part of the monolith's actual output and must be preserved."""
    try:
        return str(round(float(x), p))
    except (TypeError, ValueError):
        return "0"


def _person_kpi_tiles(pf: dict) -> list:
    """The 7 Person KPI tiles as JSON — same values/order/drills as the `.pkpis`
    block of person_dashboard() (templates/panels/05_people.j2), pre-formatted
    with the same filters (_num/_loc/_pct) so the React KpiTile is a pure render.
    `drill` is emitted only when the count is non-zero (matching the macro's
    `if t.commits`/etc guards); the data-* key ORDER mirrors the macro's dict
    literals (drill · [flag] · author · scope) so the DOM matches."""
    t = pf.get("totals") or {}
    dl = pf.get("deltas") or {}
    sp = pf.get("spark") or {}
    shares = pf.get("shares") or {}
    login = pf.get("login", "")

    def sh(k):
        return _pct(shares.get(k, 0))

    return [
        {"icon": "commit", "value": _num(t.get("commits")), "label": "commits",
         "sub": f"{sh('commits')}% of org this period", "delta": _delta_chip(dl.get("commits")),
         "sparkPts": sp.get("commits_pts"), "sparkColor": "var(--c-commit)",
         "drill": ({"drill": "commit", "author": login, "scope": "none"} if t.get("commits") else None)},
        {"icon": "loc", "value": _loc(t.get("meaningful_additions")), "label": "meaningful LOC",
         "sub": f"{sh('meaningful_additions')}% of org", "delta": _delta_chip(dl.get("meaningful_additions")),
         "tip": f"{_loc(t.get('meaningful_additions'))} meaningful lines",
         "sparkPts": sp.get("loc_pts"), "sparkColor": "var(--c-loc)",
         "drill": ({"drill": "commit", "author": login, "scope": "none"} if t.get("commits") else None)},
        {"icon": "pr", "value": _num(t.get("prs")), "label": "PRs opened",
         "sub": f"{_num(t.get('prs_merged'))} merged · {sh('prs')}% of org", "delta": _delta_chip(dl.get("prs")),
         "drill": ({"drill": "pr", "author": login, "scope": "none"} if t.get("prs") else None)},
        {"icon": "spec", "value": _num(t.get("specs")), "label": "spec edits",
         "sub": f"{sh('specs')}% of org", "delta": _delta_chip(dl.get("specs")),
         "drill": ({"drill": "commit", "flag": "is_spec", "author": login, "scope": "none"} if t.get("specs") else None)},
        {"icon": "bug", "value": _num(t.get("bugs")), "label": "bugs opened",
         "sub": "issues categorised as bug", "delta": _delta_chip(dl.get("bugs")),
         "drill": ({"drill": "issue", "flag": "is_bug", "author": login, "scope": "none"} if t.get("bugs") else None)},
        {"icon": "epic", "value": _num(t.get("epics", 0)), "label": "epics opened",
         "sub": "issues categorised as epic", "delta": _delta_chip(dl.get("epics")),
         "drill": ({"drill": "issue", "flag": "is_epic", "author": login, "scope": "none"} if t.get("epics") else None)},
        {"icon": "feature", "value": _num(t.get("features")), "label": "features opened",
         "sub": "issues categorised as feature", "delta": _delta_chip(dl.get("features")),
         "drill": ({"drill": "issue", "flag": "is_feature", "author": login, "scope": "none"} if t.get("features") else None)},
    ]


def _split2_json(a, b) -> dict:
    """JSON form of the split2() macro's data (templates/panels/05_people.j2) — the
    two shares as PRE-FORMATTED strings (bare `|round` → "N.0", see _jr) plus the
    two counts via _num. Widths are the caller's (React) concern (style only, not
    visible text). {'empty': True} when both sides are zero (the macro's hint)."""
    a = a or 0
    b = b or 0
    tot = a + b
    if not tot:
        return {"empty": True}
    return {"empty": False, "a": a, "b": b, "pa": _jr(100 * a / tot), "pb": _jr(100 * b / tot),
            "aNum": _num(a), "bNum": _num(b)}


def _score_availability(p: dict) -> tuple[dict | None, dict | None]:
    """(score, unavailable) for the person payload's Developer-score panel — the one
    gate that decides whether the panel is drawn at all. Exactly one side is set.

    The gate itself is UNCHANGED: the panel needs a `board`, because with nobody
    ranked there is no gauge, no chain and no leaderboard to paint (so a working
    score still yields the DOM the pixel gate baselined). What is new is the second
    value. Dropping the panel was previously the WHOLE response to every kind of
    trouble: server.py's score builder raising and a window with nobody in it
    produced an identical page, which a reader took as "this person has no score"
    and an operator as a normal 200 — the same silence that let a dead collector run
    for ten days in July 2026 (see server.log_degraded / alert.py). So when the panel
    is skipped the caller now learns WHY: `error` (the builder raised; server.py has
    already logged the traceback and passes the reason in `score_unavailable`) or
    `no_data` (it built fine, the window is simply empty). `detail` is finished
    user-facing prose — never an exception message, which belongs in the log.
    """
    score = p.get("score") or None
    if score and score.get("board"):
        return score, None
    reported = p.get("score_unavailable")
    if reported:
        return None, dict(reported)
    floor = (score or {}).get("min_activity")
    if score and not (score.get("n_eligible") or 0) and floor:
        detail = (f"nobody reached the {floor}-commits-and-PRs activity floor "
                  f"in this window")
    else:
        detail = "there is no score data for this window"
    return None, {"reason": "no_data", "detail": detail}


def _person_dashboard_json(p: dict) -> dict:
    """JSON port of the person_dashboard() macro's inputs (templates/panels/05_people.j2).
    Every VISIBLE number is pre-formatted server-side with the SAME filter it uses
    (_num/_loc/_pct, or _jr for the bare `|round` legends/merge-rate/ttm) so the
    React render is a pure paint and can't drift on float formatting. `p` is the
    serve_person payload {profile, alltime, weekly, heat, emails, login, gh_profile,
    score}. Weekly (raw) and score (raw, behind a collapsed <details>) pass through
    for the client to lay out; their counts are all plain `|num`. When the score
    panel is skipped, `scoreUnavailable` says why (see _score_availability) so the
    page can print a one-line note instead of silently losing a section. Never
    raises."""
    pf = p.get("profile") or {}
    at = p.get("alltime") or {}
    t = pf.get("totals") or {}
    login = pf.get("login") or p.get("login") or ""
    has_impact = bool(at.get("surv_code_human") or at.get("surv_code_ai") or at.get("surv_spec")
                      or at.get("reviews") or at.get("merged_prs"))
    empty = not (t.get("commits") or t.get("prs") or t.get("issues") or has_impact)
    if empty:
        return {"empty": True, "login": login}

    ghp = p.get("gh_profile") or {}
    gh_profile = None
    if ghp:
        gh_profile = {"name": ghp.get("name"), "company": ghp.get("company"),
                      "location": ghp.get("location"), "bio": ghp.get("bio")}

    split = pf.get("split") or {}
    split_total = split.get("total") or 0
    repo_types = None
    if split_total:
        repo_types = {"total": split_total, "types": [
            {"name": ty["name"], "color": ty["color"], "commits": ty["commits"],
             "pctText": _jr(100 * ty["commits"] / split_total)}
            for ty in (split.get("types") or []) if ty.get("commits")]}

    mix = pf.get("mix") or {}
    surv_alive = (at.get("surv_code_human") or 0) + (at.get("surv_code_ai") or 0)
    impact = {
        "survHuman": _loc(at.get("surv_code_human") or 0), "survHumanRaw": at.get("surv_code_human") or 0,
        "survAi": _loc(at.get("surv_code_ai") or 0), "survAiRaw": at.get("surv_code_ai") or 0,
        "survSpec": _loc(at.get("surv_spec") or 0), "survAlive": surv_alive,
        "reviews": at.get("reviews") or 0, "approvals": at.get("approvals") or 0,
        "mergedPrs": at.get("merged_prs") or 0,
        "mergeRateText": (f"{_jr(100 * (at.get('merged_prs') or 0) / at['prs'])}%" if at.get("prs") else "—"),
        "ttmText": (f"{at['ttm']} h" if at.get("ttm") else "—"),
        "aiCommits": at.get("ai_commits") or 0, "cptLines": _loc(at.get("cpt_lines") or 0),
        "commitsText": (_num(at["commits"]) if at.get("commits") else "—"),
    }

    score, score_unavailable = _score_availability(p)

    return {
        "empty": False, "login": login,
        "header": {
            "login": login, "name": at.get("name") or login, "company": at.get("company"),
            "isMember": bool(at.get("is_member")), "identityConfidence": at.get("identity_confidence"),
            "identityEvidence": at.get("identity_evidence"), "rank": pf.get("rank"),
            "nPeople": pf.get("n_people"), "emails": p.get("emails") or "",
        },
        "ghProfile": gh_profile,
        "kpis": _person_kpi_tiles(pf),
        "heat": p.get("heat") or [],
        "weekly": p.get("weekly") or {},
        "repos": [{"repo": r["repo"], "name": r["name"], "commits": r["commits"], "add": r.get("add", 0),
                   "commitsText": _num(r["commits"]), "addText": _loc(r.get("add", 0))}
                  for r in (pf.get("repos") or [])],
        "repoTypes": repo_types,
        "codeSpecs": _split2_json(mix.get("code", 0), mix.get("specs", 0)),
        "elements": ([{"element": e["element"], "commits": e["commits"], "loc": e.get("loc", 0),
                       "commitsText": _num(e["commits"]), "locText": _loc(e.get("loc", 0))}
                      for e in pf["elements"]] if pf.get("elements") else None),
        "workType": ([{"type": w["type"], "count": w["count"], "countText": _num(w["count"])}
                      for w in pf["work_type"]] if pf.get("work_type") else None),
        "impact": impact,
        "score": score,
        "scoreUnavailable": score_unavailable,
    }


def person_json(dash: dict | None, meta: dict) -> dict:
    """The full /api/report/person payload: the person mode-section (person picker
    + weekly dashboard) as JSON instead of the server-rendered fragment
    render_person_fragment() returns (see server.py's serve_person). The envelope
    (meta/period/periodPresets/scope/scopeTargets/dataQuality) mirrors people_json;
    PLUS the picker data (`personOptions`/`personCompanies` — the monolith's
    #person-data blob + company <select>), the selected `person` (or None), and —
    when a person is given — the full dashboard under `dashboard` (else None, the
    "pick a person" hint state). `dash` is the serve_person payload (or None); pure
    / never touches the DB — bad/empty input degrades to the no-person shape."""
    envelope = {
        "ok": True,
        "meta": {
            "org": meta.get("org"), "allTime": bool(meta.get("all_time", True)),
            "windowStart": meta.get("window_start"), "lookbackDays": meta.get("lookback_days"),
            "generatedText": (meta.get("generated") or "")[:16].replace("T", " "),
        },
        "period": meta.get("period") or {"preset": "all", "label": meta.get("all_label", "All-time"),
                                          "from": None, "to": None},
        "periodPresets": [
            {"key": lbl, "label": {"7d": "7 days", "30d": "30 days", "90d": "90 days",
                                    "365d": "1 year", "all": meta.get("all_label", "All-time")}.get(lbl, lbl)}
            for lbl in (meta.get("window_labels") or ["all"])
        ],
        "scope": meta.get("scope", ""),
        "scopeTargets": meta.get("scope_targets") or {},
        "dataQuality": {"apiRateLimited": bool((meta.get("data_quality") or {}).get("api_rate_limited")),
                        "apiReset": (meta.get("data_quality") or {}).get("api_reset")},
        "personOptions": meta.get("person_options") or [],
        "personCompanies": meta.get("person_companies") or [],
        "person": meta.get("person"),
        "dashboard": None,
    }
    if dash:
        envelope["dashboard"] = _person_dashboard_json(dash)
    return envelope


def _typebar_json(types: list, field: str, total, drill: str, unit: str) -> dict:
    """JSON port of the typebar() macro (templates/panels/02_overview.j2) — one
    stacked proportional bar + its legend, for the Repositories view's "Where
    effort goes — by repository type" panel (panel_split). Numbers are
    PRE-FORMATTED with the SAME Jinja filters the macro uses so the React render
    can't drift on float formatting:
      • bar segment width = `(100*t[field]/total)|round(2)`  → _jr(x, 2)
      • legend percentage  = `(100*t[field]/total)|round`     → _jr(x)  ("N.0")
                              (or literal "0" when total is falsy — the macro's
                               `… if total else 0` else-branch)
      • bar tip value = `t[field]|num` (ALWAYS num, even the LOC bar)
      • legend value  = `t[field]|loc` for unit=='loc', else `t[field]|num`
    A bar segment renders only when `t[field] and total` (the macro's `<i>`
    guard); a legend entry only when `t[field]` (its own, weaker guard)."""
    bars = []
    legend = []
    for t in (types or []):
        v = t.get(field, 0) or 0
        if v and total:
            bars.append({
                "id": t.get("id"), "color": t.get("color"),
                "width": _jr(100 * v / total, 2),
                "tip": f"{t.get('name')}: {_num(v)}",
            })
        if v:
            legend.append({
                "id": t.get("id"), "color": t.get("color"), "name": t.get("name"),
                "pct": (_jr(100 * v / total) if total else "0"),
                "value": (_loc(v) if unit == "loc" else _num(v)),
            })
    return {"drill": drill, "bars": bars, "legend": legend}


def repositories_json(pr: dict, meta: dict) -> dict:
    """The full /api/report/repositories payload: the Repositories view's data —
    the all-time repo-coverage summary (mini stats + inventory table + the
    "needs classification" chips) and the period-scoped "Where effort goes — by
    repository type" split panel — JSON instead of the server-rendered fragments
    the mode="repos" mode-sections paint (templates/report.j2: the
    `data-modes="repos all"` blocks at lines ~712 (repo coverage), ~991
    (panel_split), ~1066 (⚠ unclassified repos)). The envelope
    (meta/period/periodPresets/scope/scopeTargets/dataQuality) mirrors
    people_json exactly.

    Two data provenances, mirroring what the monolith reads:
      • `meta` carries the ALL-TIME, build-time-computed repo inventory
        (repo_summary / repo_rows / unclassified — from build_model(), NOT the
        period-scoped aggregate), so the whole "Repo coverage" section is
        marked `all-time` and never varies with the period/scope filter.
      • `pr['split']` is the SAME period-scoped split block Overview/People read
        (store.aggregate()), so "Where effort goes" follows the period & slice.
    Pure / never touches the DB — bad/empty input degrades every section to its
    empty shape, never raises."""
    repo_summary = meta.get("repo_summary") or {}
    repo_rows = meta.get("repo_rows") or []
    unclassified = meta.get("unclassified") or []

    split = pr.get("split") or {}
    types = split.get("types") or []
    # panel_split's outer guard: `pr.split.types and pr.split.commits_total`.
    split_present = bool(types and split.get("commits_total"))
    split_bars = []
    if split_present:
        split_bars = [
            {"sub": "Commits by type",
             **_typebar_json(types, "commits", split.get("commits_total"), "commit", "n")},
            {"sub": "Pull requests by type",
             **_typebar_json(types, "prs", split.get("prs_total"), "pr", "n")},
            {"sub": "Meaningful LOC by type",
             **_typebar_json(types, "loc", split.get("loc_total"), "commit", "loc")},
        ]

    return {
        "ok": True,
        "meta": {
            "org": meta.get("org"), "allTime": bool(meta.get("all_time", True)),
            "windowStart": meta.get("window_start"), "lookbackDays": meta.get("lookback_days"),
            "generatedText": (meta.get("generated") or "")[:16].replace("T", " "),
        },
        "period": meta.get("period") or {"preset": "all", "label": meta.get("all_label", "All-time"),
                                          "from": None, "to": None},
        "periodPresets": [
            {"key": lbl, "label": {"7d": "7 days", "30d": "30 days", "90d": "90 days",
                                    "365d": "1 year", "all": meta.get("all_label", "All-time")}.get(lbl, lbl)}
            for lbl in (meta.get("window_labels") or ["all"])
        ],
        "scope": meta.get("scope", ""),
        "scopeTargets": meta.get("scope_targets") or {},
        "dataQuality": {"apiRateLimited": bool((meta.get("data_quality") or {}).get("api_rate_limited")),
                        "apiReset": (meta.get("data_quality") or {}).get("api_reset")},
        "repoSummary": {
            "distinct": repo_summary.get("distinct", 0), "primary": repo_summary.get("primary", 0),
            "primaryOrg": repo_summary.get("primary_org"), "legacyOnly": repo_summary.get("legacy_only", 0),
            "platform": repo_summary.get("platform", 0), "app": repo_summary.get("app", 0),
            "unclassified": repo_summary.get("unclassified", 0),
            "missingTraffic": repo_summary.get("missing_traffic", 0),
            "legacyDup": repo_summary.get("legacy_dup", 0), "total": repo_summary.get("total", 0),
        },
        # Row dicts pass through verbatim — the React DataTable reads the same
        # keys the monolith's REPO_COLS data_table() call does (name, org,
        # classification, element, code_loc, spec_loc, contributors, forks,
        # stars, traffic_access, clones, uniques + the unclassified/legacy_only
        # tag flags).
        "repoRows": repo_rows,
        "unclassified": unclassified,
        "split": {"present": split_present, "bars": split_bars},
    }


def elements_json(pr: dict, meta: dict) -> dict:
    """The full /api/report/elements payload: the "By Element" product-line
    rollup — JSON instead of the server-rendered fragment the mode="elements"
    mode-section paints (templates/report.j2's `data-modes="elements all"` block
    at ~757, which calls panel_elements() in templates/panels/02_overview.j2).
    The envelope (meta/period/periodPresets/scope/scopeTargets/dataQuality)
    mirrors people_json / repositories_json exactly.

    `pr['element_rows']` is the SAME per-element block the monolith's
    panel_elements(pr) reads — period/slice-scoped when a filter is applied
    (store.aggregate()'s element_rows), or build_model()'s all-time rollup for
    the default state (server.py's serve_report_elements picks which). Note each
    row's LOC (code_loc/spec_loc) is all-time even in a windowed block (it comes
    from the repo table, not the windowed commits) while commits/PRs/people/AI%
    are period-scoped — hence the panel's own hint. `meta` carries everything
    that does NOT vary with the filter, same convention as the sibling views.

    Per-row derived fields mirror panel_elements' macro-local computation so the
    React DataTable is a pure render:
      • `_code_bar` = code_kloc / max(code_kloc) * 100 (the Code-LOC bar width;
        the React bar cell runs it through fmtPct, exactly as the monolith's
        bar_cell does `|pct`) — kmax falls back to 1 (the macro's `kmax or 1`).
      • `_scope` = 'element:<element>' (the drill scope every drillable cell
        resolves `@_scope` against).
      • `element_color` = ecolor(element) (the `.edot` swatch colour — computed
        here so the client needn't re-derive the hash).
      • `median_ttm_h` is PRE-FORMATTED to str(v) (the Med-TTM column renders it
        via kind='raw' — a literal `{{ v }}` in the monolith, no reformatting),
        so a whole-number/rounded median keeps whatever the source produced
        (e.g. store.aggregate's round(x,1) → "24.0"); None -> the dash cell.
    Pure / never touches the DB — bad/empty input degrades to an empty table."""
    rows = pr.get("element_rows") or []
    kmax = max((r.get("code_kloc") or 0) for r in rows) if rows else 1
    kmax = kmax or 1
    element_rows = []
    for e in rows:
        m = e.get("median_ttm_h")
        element_rows.append({
            **e,
            "_code_bar": (e.get("code_kloc") or 0) / kmax * 100,
            "_scope": "element:" + str(e.get("element", "")),
            "element_color": _element_color(e.get("element", "")),
            "median_ttm_h": (str(m) if m is not None else None),
        })

    return {
        "ok": True,
        "meta": {
            "org": meta.get("org"), "allTime": bool(meta.get("all_time", True)),
            "windowStart": meta.get("window_start"), "lookbackDays": meta.get("lookback_days"),
            "generatedText": (meta.get("generated") or "")[:16].replace("T", " "),
        },
        "period": meta.get("period") or {"preset": "all", "label": meta.get("all_label", "All-time"),
                                          "from": None, "to": None},
        "periodPresets": [
            {"key": lbl, "label": {"7d": "7 days", "30d": "30 days", "90d": "90 days",
                                    "365d": "1 year", "all": meta.get("all_label", "All-time")}.get(lbl, lbl)}
            for lbl in (meta.get("window_labels") or ["all"])
        ],
        "scope": meta.get("scope", ""),
        "scopeTargets": meta.get("scope_targets") or {},
        "dataQuality": {"apiRateLimited": bool((meta.get("data_quality") or {}).get("api_rate_limited")),
                        "apiReset": (meta.get("data_quality") or {}).get("api_reset")},
        "elementRows": element_rows,
    }


def traffic_json(pr: dict, meta: dict) -> dict:
    """The full /api/report/traffic payload: the "Traffic" view's data — JSON
    instead of the server-rendered fragments the mode="usage" mode-sections paint
    (templates/report.j2: the `data-modes="usage all"` blocks at ~947 "The two
    scenarios", ~986 "Traffic — clones & page views" (panel_traffic), and the
    conditional ~1002 "External contributors" chips). The route is /traffic (the
    migration spec's usage→traffic rename); the monolith's mode stays "usage".
    The envelope (meta/period/periodPresets/scope/scopeTargets/dataQuality)
    mirrors elements_json / repositories_json exactly.

    Two data provenances, mirroring what the monolith reads:
      • `pr['traffic']` is the SAME period/slice-scoped clone/view block the
        monolith's panel_traffic(pr) reads (store.aggregate()'s windowed traffic,
        or build_model()'s all_block traffic for the default state — server.py's
        serve_report_traffic picks which). Numbers PRE-FORMATTED with the same
        Jinja filters (|num for counts, |round(1) for the CI ratio) so the React
        render can't drift.
      • `meta` carries the ALL-TIME scenario data (contributors / members_contrib
        / external_contributors / non_contributors / stars / forks / platform-repo
        count / emails_by_login — from build_model(), NOT the period aggregate),
        so "The two scenarios" and "External contributors" are marked `all-time`
        and never vary with the period/scope filter — exactly like the monolith,
        where those blocks live OUTSIDE any data-period-panel region.
    Pure / never touches the DB — bad/empty input degrades every section to its
    empty shape, never raises."""
    # ---- period-scoped traffic panel (panel_traffic) ----------------------
    traffic = pr.get("traffic") or {}
    dmax = traffic.get("daily_max") or 1
    t_rows = []
    for r in (traffic.get("rows") or []):
        clones = r.get("clones") or 0
        uniques = r.get("uniques") or 0
        t_rows.append({
            "name": r.get("name"),
            "views": _num(r.get("views")), "visitors": _num(r.get("visitors")),
            "clones": _num(clones), "uniques": _num(uniques),
            # `(clones / uniques)|round(1) if uniques else '—'` — Jinja's round
            # method='common' is Python's round(), so this matches byte-for-byte.
            "ci": (str(round(clones / uniques, 1)) if uniques else "—"),
            "daily": [{"h": round(((d.get("count") or 0) / dmax) * 100),
                       "tip": f"{d.get('date')}: {d.get('count')} clones / {d.get('uniques')} cloners"}
                      for d in (r.get("daily") or [])],
            "paths": [{"text": ("/".join(str(p.get("path", "")).split("/")[3:]) or p.get("path", "")),
                       "views": p.get("views", 0),
                       "tip": f"{p.get('uniques', 0)} unique viewers"}
                      for p in (r.get("paths") or [])[:6]],
        })
    traffic_panel = {
        "present": bool(traffic.get("n_repos")),
        "windowed": bool(traffic.get("windowed")),
        "since": traffic.get("since"),
        "nNoAccess": traffic.get("n_no_access", 0),
        "views": _num(traffic.get("total_views")), "visitors": _num(traffic.get("total_visitors")),
        "clones": _num(traffic.get("total_clones")), "cloners": _num(traffic.get("unique_cloners")),
        "rows": t_rows,
    }

    # ---- all-time scenarios (build_model, threaded through meta) -----------
    contributors = meta.get("contributors") or []
    members_contrib = meta.get("members_contrib") or []
    external_contributors = meta.get("external_contributors") or []
    non_contributors = meta.get("non_contributors") or []
    emails = meta.get("emails_by_login") or {}
    # bar width normalised to the top contributor (the macro's `contributors[0].value or 1`)
    cmax = (contributors[0]["value"] if contributors else 1) or 1
    contrib_rows = [{
        "login": c["login"], "isMember": bool(c["is_member"]), "value": c["value"],
        "bar": round(c["value"] / cmax * 100), "email": emails.get(c["login"], ""),
    } for c in contributors]
    noncontrib_rows = [{
        "login": r["login"], "isMember": bool(r["is_member"]),
        "forked": r.get("forked") or [], "email": emails.get(r["login"], ""),
    } for r in non_contributors]
    external_rows = [{
        "login": c["login"], "value": c["value"], "email": emails.get(c["login"], ""),
    } for c in external_contributors]

    return {
        "ok": True,
        "meta": {
            "org": meta.get("org"), "allTime": bool(meta.get("all_time", True)),
            "windowStart": meta.get("window_start"), "lookbackDays": meta.get("lookback_days"),
            "generatedText": (meta.get("generated") or "")[:16].replace("T", " "),
        },
        "period": meta.get("period") or {"preset": "all", "label": meta.get("all_label", "All-time"),
                                          "from": None, "to": None},
        "periodPresets": [
            {"key": lbl, "label": {"7d": "7 days", "30d": "30 days", "90d": "90 days",
                                    "365d": "1 year", "all": meta.get("all_label", "All-time")}.get(lbl, lbl)}
            for lbl in (meta.get("window_labels") or ["all"])
        ],
        "scope": meta.get("scope", ""),
        "scopeTargets": meta.get("scope_targets") or {},
        "dataQuality": {"apiRateLimited": bool((meta.get("data_quality") or {}).get("api_rate_limited")),
                        "apiReset": (meta.get("data_quality") or {}).get("api_reset")},
        "traffic": traffic_panel,
        "scenarios": {
            "contributorsCount": len(contributors),
            "membersCount": len(members_contrib),
            "externalCount": len(external_contributors),
            "contributors": contrib_rows,
            "nonContributors": noncontrib_rows,
            "totalStars": meta.get("total_stars", 0),
            "totalForks": meta.get("total_forks", 0),
            "platformReposCount": len(meta.get("platform_repos") or []),
        },
        "externalContributors": external_rows,
    }


def _marker_table_json(block: dict) -> dict:
    """JSON port of the marker/provenance tables the fabric mode-sections paint by
    hand (templates/report.j2 ~817 studio_prov, ~857 gears_usage, ~879
    fabric_trackers) — each a `<table class="dt">` whose columns are Repo + one
    per content marker, cells "<files> / <lines>", headers carrying a
    `<span class="prec exact|heuristic">` badge (rule #2 — the badge is part of
    the thead, so it MUST render in the React header too).

    `block` is a studio_provenance / gears_usage / fabric_trackers[name] dict:
      markers    ordered list of marker names (the dynamic column set)
      precision  {marker: 'exact'|'heuristic'} — badge class/text, default 'exact'
                 (the macro's `(… .precision or {}).get(m,'exact')`)
      totals     {marker: {files, lines}} — the mini-stat totals (RAW ints, no
                 filter, exactly as the macro prints `{{ totals[m].files }}`)
      by_repo    {full_repo_name: {marker: {files, lines}}} — rows; the repo cell
                 shows the LAST path segment (`repo.split('/')[-1]`).
    Returns the marker list, per-marker header badges, per-marker totals, and rows
    with cells aligned to the marker order (missing marker → 0/0, the macro's
    `vals[m].files if vals.get(m) else 0`)."""
    markers = block.get("markers") or []
    precision = block.get("precision") or {}
    totals = block.get("totals") or {}
    by_repo = block.get("by_repo") or {}
    badges = [{"marker": m, "prec": precision.get(m, "exact")} for m in markers]
    tot = [{"marker": m, "files": (totals.get(m) or {}).get("files", 0),
            "lines": (totals.get(m) or {}).get("lines", 0)} for m in markers]
    rows = []
    for repo, vals in by_repo.items():
        vals = vals or {}
        rows.append({
            "repo": str(repo).split("/")[-1],
            "cells": [{"files": (vals.get(m) or {}).get("files", 0) if vals.get(m) else 0,
                       "lines": (vals.get(m) or {}).get("lines", 0) if vals.get(m) else 0}
                      for m in markers],
        })
    return {"markers": markers, "badges": badges, "totals": tot,
            "repoCount": len(by_repo), "rows": rows}


def ai_tools_json(pr: dict, meta: dict) -> dict:
    """The full /api/report/ai-tools payload: the "AI tools" view's data (the
    monolith's `fabric` mode) — JSON instead of the server-rendered fragments the
    mode="fabric" mode-sections paint (templates/report.j2: the eight
    `data-modes="fabric all"` blocks at ~799 AI-usage panel (panel_aiusage), ~807
    Content provenance (studio_prov + cpt-by-company + cpt-people), ~846
    Gears usage (gears_usage), ~871 per-tracker tables (fabric_trackers), ~891
    Platform usage by company & person (fabric_company / fabric_people), ~915 Bots
    & automation (panel_bots + the per-bot detail table)). The route is /ai-tools
    (the migration spec's fabric→ai-tools rename); the monolith's mode stays
    "fabric". The envelope mirrors traffic_json / people_json exactly.

    Two data provenances, mirroring what the monolith reads:
      • PERIOD/slice-scoped (from `pr` = store.aggregate window, or build_model's
        all_block at the default state): the AI-usage panel (`pr['ai_usage']`) and
        the Bots mini stats (`pr['bots']`) — the only two fabric panels wrapped in
        a `[data-period-panel]` region, so they follow the filter.
      • ALL-TIME (from `meta`, threaded from build_model by server.py): studio
        provenance, framework usage, the generic trackers, assistant-by-company &
        by-person authorship, the fabric usage rollup AND the per-bot detail
        table (`meta['bots_all']['rows']` — top-level `bots`, distinct from the
        period-scoped `pr['bots']` the mini stats use; the monolith's detail table
        reads the top-level all-time roster, marked `all-time`). These live
        OUTSIDE any data-period-panel region and never vary with the filter.
    Pure / never touches the DB — bad/empty input degrades every section to its
    empty shape, never raises."""
    # ---- AI-usage panel (period-scoped, panel_aiusage) --------------------
    ai = pr.get("ai_usage") or {}
    ai_tools_list = ai.get("tools")
    tools = []
    for t in (ai_tools_list or []):
        commits = t.get("commits", 0) or 0
        pv = t.get("pct", 0) or 0
        tools.append({
            "tool": t.get("tool", ""),
            "commits": _num(commits), "commitsRaw": commits,
            "pctStr": _pct(pv), "pctRaw": pv,
            "loc": _loc(t.get("loc", 0) or 0), "locRaw": t.get("loc", 0) or 0,
        })
    ai_usage = {
        "anyCommits": ai.get("any_commits", 0) or 0,
        "totalCommits": ai.get("total_commits", 0) or 0,
        "pct": _pct(ai.get("pct", 0) or 0),
        "anyDrill": bool(ai.get("any_commits")),
        # `pr.ai_usage.tools is none` → the "per-tool split unavailable" note
        # (pre-backfill windows). A list (even empty) means the split renders.
        "toolsAvailable": ai_tools_list is not None,
        "tools": tools,
    }

    # ---- Bots mini stats (period-scoped, panel_bots) ----------------------
    b = pr.get("bots") or {}
    b_reviews = b.get("reviews")
    bots_mini = {
        "count": b.get("count", 0) or 0,
        "commits": _num(b.get("commits", 0) or 0),
        "additions": _loc(b.get("additions", 0) or 0),
        # `pr.bots.reviews is none` → "—" (bots review count not backfilled for
        # this window); windowed → the "all-time" badge on the reviews stat.
        "reviews": (_num(b_reviews) if b_reviews is not None else None),
        "windowed": bool(b.get("windowed")),
    }

    # ---- all-time fabric sections (build_model, threaded through meta) -----
    studio_prov = meta.get("studio_prov") or {}
    gears_usage = meta.get("gears_usage") or {}
    fabric_trackers = meta.get("fabric_trackers") or {}
    cpt_people = meta.get("cpt_people") or []
    cpt_by_company = meta.get("cpt_by_company") or []
    fabric_company = meta.get("fabric_company") or []
    fabric_people = meta.get("fabric_people") or []
    bots_all = (meta.get("bots_all") or {}).get("rows") or []

    # assistant code-marker lines by company — the `.split` bar under the studio
    # provenance table (round(1) width; label shown only when the UNROUNDED share
    # is ≥ 9%, the macro's `if (100*c.lines/cpt_total) >= 9`).
    cpt_total = sum((c.get("lines", 0) or 0) for c in cpt_by_company) or 0
    cpt_segments = []
    for c in cpt_by_company:
        lines = c.get("lines", 0) or 0
        share = (100 * lines / cpt_total) if cpt_total else 0
        cpt_segments.append({
            "company": c.get("company", ""), "color": c.get("color", ""),
            "width": _jr(share, 1),
            "tip": f"{c.get('company', '')}: {lines} marked lines",
            "label": (c.get("company", "") if share >= 9 else ""),
        })

    studio_json = {
        "present": bool(studio_prov.get("markers")),
        # mini: "<files> — files (<lines> lines)" per marker (RAW ints, no filter)
        "mini": [{"marker": m, "files": (studio_prov.get("totals") or {}).get(m, {}).get("files", 0),
                  "lines": (studio_prov.get("totals") or {}).get(m, {}).get("lines", 0)}
                 for m in (studio_prov.get("markers") or [])],
        "table": _marker_table_json(studio_prov),
        # cpt-by-company split + top-authors table (only when cpt_by_company)
        "cptPresent": bool(cpt_by_company),
        "cptSegments": cpt_segments,
        "cptPeopleCount": len(cpt_people),
        "cptPeople": cpt_people,
    }
    gears_json = {
        "present": bool(gears_usage.get("markers")),
        "repoCount": len(gears_usage.get("by_repo") or {}),
        "mini": [{"marker": m, "files": (gears_usage.get("totals") or {}).get(m, {}).get("files", 0),
                  "lines": (gears_usage.get("totals") or {}).get(m, {}).get("lines", 0)}
                 for m in (gears_usage.get("markers") or [])],
        "table": _marker_table_json(gears_usage),
    }
    # fabric_trackers: dict name→tracker; only markers-present ones render (the
    # macro's `{% if t.markers %}`). Mini here shows just "<files> — files" per
    # marker (NO "(N lines)"), unlike studio/gears.
    trackers_json = []
    for name, t in fabric_trackers.items():
        if not (t or {}).get("markers"):
            continue
        trackers_json.append({
            "name": name,
            "repoCount": len((t.get("by_repo") or {})),
            "mini": [{"marker": m, "files": (t.get("totals") or {}).get(m, {}).get("files", 0)}
                     for m in (t.get("markers") or [])],
            "table": _marker_table_json(t),
        })

    # Platform usage rollup. Company rows pass through (DataTable reads company /
    # commits / ai_commits / ai_pct / cpt_lines). Person rows get `aiPctStr` =
    # str(ai_pct) so the "AI%" column (rendered via the macro's kind-less
    # `fmt:'raw'` + `unit:'%'` = a literal `{{ v }}%`, NOT |pct) stays byte-exact
    # with Python's float str ("29.0%", "100.0%", "0.0%") rather than JS's
    # String(29)→"29".
    people_rows = [{
        "login": p.get("login", ""), "company": p.get("company", ""),
        "ai_commits": p.get("ai_commits", 0) or 0,
        "aiPctStr": str(p.get("ai_pct", 0) or 0),
        "cpt_lines": p.get("cpt_lines", 0) or 0,
    } for p in fabric_people]

    bot_rows = [{
        "login": r.get("login", ""), "kind": r.get("kind", ""),
        "commits": r.get("commits", 0) or 0,
        "additions": r.get("additions", 0) or 0,
        "ai_commits": r.get("ai_commits", 0) or 0,
        "reviews_given": r.get("reviews_given", 0) or 0,
        # `_repos` = repos joined, or "—" when none (the macro's update()).
        "repos": (", ".join(r.get("repos") or []) if r.get("repos") else "—"),
        "emails": ", ".join(r.get("emails") or []),
    } for r in bots_all]

    return {
        "ok": True,
        "meta": {
            "org": meta.get("org"), "allTime": bool(meta.get("all_time", True)),
            "windowStart": meta.get("window_start"), "lookbackDays": meta.get("lookback_days"),
            "generatedText": (meta.get("generated") or "")[:16].replace("T", " "),
        },
        "period": meta.get("period") or {"preset": "all", "label": meta.get("all_label", "All-time"),
                                          "from": None, "to": None},
        "periodPresets": [
            {"key": lbl, "label": {"7d": "7 days", "30d": "30 days", "90d": "90 days",
                                    "365d": "1 year", "all": meta.get("all_label", "All-time")}.get(lbl, lbl)}
            for lbl in (meta.get("window_labels") or ["all"])
        ],
        "scope": meta.get("scope", ""),
        "scopeTargets": meta.get("scope_targets") or {},
        "dataQuality": {"apiRateLimited": bool((meta.get("data_quality") or {}).get("api_rate_limited")),
                        "apiReset": (meta.get("data_quality") or {}).get("api_reset")},
        "aiUsage": ai_usage,
        "studioProv": studio_json,
        "gearsUsage": gears_json,
        "trackers": trackers_json,
        "fabricCompany": fabric_company,
        "fabricPeopleCount": len(fabric_people),
        "fabricPeople": people_rows,
        "botsMini": bots_mini,
        "botRows": bot_rows,
    }


def trend_json(pr: dict, meta: dict) -> dict:
    """The full /api/report/trend payload: the Trend view's data (breakdown +
    throughput/activity charts), JSON instead of the server-rendered fragment
    render_trend_fragment() returns (see server.py's serve_report_trend).
    `pr` is a period/scope/granularity-scoped block shaped like
    store.aggregate()'s return with trend_gran/trend_dim applied (same
    convention serve_trend already uses for the HTML fragment) — i.e.
    `pr['ctrend']` is a store.trend_block() dict, and `pr['company_rows']` /
    `pr['split']` supply the colour maps `_trend_colors` resolves the active
    breakdown dimension against (mirrors panels/02_overview.j2's panel_trend
    macro line-for-line). `meta` carries everything that does NOT vary with
    the period/scope filter — same convention as overview_json. Pure / never
    touches the DB — bad/empty input degrades `data` to None (the "no commit
    activity" hint), never raises."""
    envelope = {
        "ok": True,
        "meta": {
            "org": meta.get("org"), "allTime": bool(meta.get("all_time", True)),
            "windowStart": meta.get("window_start"), "lookbackDays": meta.get("lookback_days"),
            "generatedText": (meta.get("generated") or "")[:16].replace("T", " "),
        },
        "period": meta.get("period") or {"preset": "all", "label": meta.get("all_label", "All-time"),
                                          "from": None, "to": None},
        "periodPresets": [
            {"key": lbl, "label": {"7d": "7 days", "30d": "30 days", "90d": "90 days",
                                    "365d": "1 year", "all": meta.get("all_label", "All-time")}.get(lbl, lbl)}
            for lbl in (meta.get("window_labels") or ["all"])
        ],
        "scope": meta.get("scope", ""),
        "scopeTargets": meta.get("scope_targets") or {},
    }
    ct = pr.get("ctrend") or {}
    if not ct.get("points"):
        envelope["data"] = None
        return envelope

    import vega_spec
    dim = ct.get("dim") or "company"
    dims = ct.get("dims") or []
    dim_label = "company"
    for d in dims:
        if d.get("key") == dim:
            dim_label = str(d.get("label") or "").lower()
            break
    gran = ct.get("gran") or "auto"
    noun = {"day": "day", "week": "week", "month": "month", "quarter": "quarter"}.get(gran, "bucket")
    dates = ct.get("dates") or []
    colors = _trend_colors(dim, ct.get("commit_rows") or [], pr)
    throughput = ct.get("throughput") or {}
    ttm_vals = throughput.get("ttm") or []
    has_ttm = any(v is not None for v in ttm_vals)
    envelope["data"] = {
        "dates": dates,
        "dims": dims,
        "dim": dim,
        "dimlabel": dim_label,
        "gran": gran,
        "granreq": ct.get("gran_req") or "auto",
        "points": ct.get("points", 0),
        "noun": noun,
        "legend": colors,
        "commitChartSpec": vega_spec.stacked_area_spec(ct.get("commit_rows"), dates, colors, "commits"),
        "locChartSpec": vega_spec.stacked_area_spec(ct.get("loc_rows"), dates, colors, "LOC"),
        "throughputLineSpec": vega_spec.line_spec(
            [{"name": "Opened", "vals": throughput.get("opened"), "color": "#2f80ed"},
             {"name": "Merged", "vals": throughput.get("merged"), "color": "#10b981"}],
            dates, ""),
        "ttmAreaSpec": (vega_spec.line_spec(
            [{"name": "Median TTM", "vals": ttm_vals, "color": "#f59e0b"}], dates, "hours", True)
            if has_ttm else None),
        "contributorsAreaSpec": vega_spec.line_spec(
            [{"name": "Contributors", "vals": ct.get("contributors"), "color": "#8b5cf6"}],
            dates, "", True),
    }
    return envelope


def _dpct(v) -> str:
    """A percentage value or the dash placeholder — JSON port of the delivery
    template's `dpct()` macro (templates/panels/03_delivery.j2: `{{ v|pct }}%`
    or `—` when None)."""
    return f"{_pct(v)}%" if v is not None else "—"


def _dstr(v, suffix: str = "") -> str:
    """A RAW value (no thousands separator) + optional suffix, or the dash
    placeholder — JSON port of the delivery template's `dnum()` macro. Used for
    the sub-labels that echo a plain count (e.g. "N files"), which the Jinja
    macro never runs through `|num`."""
    return f"{v}{suffix}" if v is not None else "—"


def delivery_json(pr: dict, meta: dict) -> dict:
    """The full /api/report/delivery payload: the Delivery view's data (issue/PR/
    CI KPI tiles, the issues-by-category mix table, the current-board-state flow
    pipe), JSON instead of the server-rendered HTML fragment render_delivery_
    fragment() returns (see server.py's serve_delivery / serve_report_delivery).
    `pr` is shaped like serve_delivery's own `{"delivery": semantic_metrics.
    window_block(...)}` (+ an optional 'deltas' dict, same convention as that
    endpoint) — i.e. `pr['delivery']` is a window_block() dict (issue_metrics +
    pr_metrics + ci_metrics + flow_metrics + 'spark', see semantic_metrics.py).
    `meta` carries everything that does NOT vary with the period/scope filter —
    same convention as overview_json/trend_json. Delivery has NO build-time fast
    path (see templates/report.j2's refreshDelivery(), which always live-fetches
    /api/delivery even for the default state) — every request here is a live
    aggregation, matching that. Pure / never touches the DB — bad/empty input
    degrades every section to its "no data" shape, never raises."""
    d = pr.get("delivery") or {}
    dl = d.get("deltas") or {}
    sp = d.get("spark") or {}

    def tile(value, label, sub, delta_key=None, spark_color="var(--c-people)",
             drill=None, lower_better=False, tip=None):
        spark_key = f"{delta_key}_pts" if delta_key else None
        return {"icon": None, "value": value, "label": label, "sub": sub, "tip": tip,
                "delta": _delta_chip(dl.get(delta_key), lower_better) if delta_key else None,
                "sparkPts": sp.get(spark_key) if spark_key else None,
                "sparkColor": spark_color, "drill": drill}

    issues_total = d.get("issues_total") or 0
    kpis = [
        tile(_num(issues_total), "issues opened", "in period",
             delta_key="issues_total", drill={"drill": "issue"} if d.get("issues_total") else None),
        tile(_dpct(d.get("issue_close_rate")), "close rate", _dstr(d.get("issues_closed") or 0, " closed"),
             delta_key="issue_close_rate"),
        tile(_dpct(d.get("defect_rate")), "defect rate", "bug category",
             delta_key="defect_rate", lower_better=True, spark_color="var(--c-bug)",
             drill={"drill": "issue", "category": "bug"} if d.get("defect_rate") else None),
        tile((f"{_pct(d.get('issue_median_time_to_close_days'))}d"
              if d.get("issue_median_time_to_close_days") is not None else "—"),
             "median time-to-close", "closed issues",
             delta_key="issue_median_time_to_close_days", lower_better=True),
    ]

    ci = [
        tile(_dpct(d.get("ci_pass_rate")), "CI pass rate", _dstr(d.get("ci_gate_runs") or 0, " gate runs"),
             delta_key="ci_pass_rate", spark_color="var(--good)",
             drill={"drill": "ci"} if d.get("ci_gate_runs") else None),
        tile(_dur(d.get("ci_median_duration_s")) if d.get("ci_median_duration_s") is not None else "—",
             "CI median duration", "gate workflows",
             delta_key="ci_median_duration_s", lower_better=True, spark_color="var(--good)",
             drill={"drill": "ci"} if d.get("ci_gate_runs") else None),
    ]

    ma = d.get("pr_median_additions")
    pr_tiles = [
        tile(_num(d.get("prs_total")), "PRs opened", "in period",
             delta_key="prs_total", spark_color="var(--c-pr)",
             drill={"drill": "pr"} if d.get("prs_total") else None),
        tile(_dpct(d.get("pr_merge_rate")), "merge rate", "merged / opened",
             delta_key="pr_merge_rate", spark_color="var(--c-pr)",
             drill={"drill": "pr", "pr-state": "merged"} if d.get("pr_merge_rate") else None),
        tile(_dpct(d.get("pr_abandon_rate")), "abandoned", "closed unmerged",
             delta_key="pr_abandon_rate", lower_better=True, spark_color="var(--c-bug)",
             drill={"drill": "pr", "pr-state": "abandoned"} if d.get("pr_abandon_rate") else None),
        tile(_loc(ma) if ma is not None else "—", "median PR size",
             _dstr(d.get("pr_median_changed_files"), " files"),
             delta_key="pr_median_additions", lower_better=True, spark_color="var(--c-pr)",
             drill=({"drill": "pr", "tip": "the PRs opened in the window this median is taken over"}
                    if ma is not None else None)),
        tile(_dpct(d.get("pr_reviewed_rate")), "reviewed", "≥1 review",
             delta_key="pr_reviewed_rate", spark_color="var(--c-pr)",
             drill={"drill": "pr", "reviewed": "1"} if d.get("pr_reviewed_rate") else None),
        tile(_hours(d.get("pr_time_to_first_review_h")) if d.get("pr_time_to_first_review_h") is not None else "—",
             "time to first review", "reviewer response",
             delta_key="pr_time_to_first_review_h", lower_better=True, spark_color="var(--c-spec)",
             drill={"tip": "from review requested (else opened) to the first review submitted; "
                           "median over reviewed PRs"}),
        tile(str(d.get("pr_reverts") or 0), "reverts", "instability signal",
             delta_key="pr_reverts", lower_better=True, spark_color="var(--c-bug)",
             drill={"drill": "pr", "flag": "is_revert"} if d.get("pr_reverts") else None),
    ]

    mix_src = d.get("issues_by_category") or {}
    mix_total = issues_total
    mix_rows = [{"label": cat, "value": n, "pct": (n / mix_total * 100) if mix_total else 0,
                 "drill": {"drill": "issue", "category": cat}}
                for cat, n in mix_src.items()]

    stages = d.get("flow_stages") or []
    flow_total = d.get("flow_total") or 0
    flow_max = max((s.get("count", 0) for s in stages), default=0) or 1
    # `pct` is PRE-FORMATTED to one decimal (e.g. "55.0") — Jinja's `|round`
    # filter (no precision arg) still returns a FLOAT (Python's round(x, 0),
    # which the macro then stringifies as "55.0", not "55"); a JSON number
    # loses that trailing ".0" once JS parses it, so this must travel as a
    # string, same convention as every other pre-formatted tile value.
    flow = {
        "hasData": bool(flow_total),
        "stages": [{"key": s["key"], "name": s["name"], "color": s["color"], "count": s["count"],
                    "pct": f"{round(100 * s['count'] / flow_total) if flow_total else 0:.1f}",
                    "barPct": round(100 * s["count"] / flow_max, 1)} for s in stages],
        "total": flow_total, "unmapped": d.get("flow_unmapped") or 0,
    }

    return {
        "ok": True,
        "meta": {
            "org": meta.get("org"), "allTime": bool(meta.get("all_time", True)),
            "windowStart": meta.get("window_start"), "lookbackDays": meta.get("lookback_days"),
            "generatedText": (meta.get("generated") or "")[:16].replace("T", " "),
        },
        "period": meta.get("period") or {"preset": "all", "label": meta.get("all_label", "All-time"),
                                          "from": None, "to": None},
        "periodPresets": [
            {"key": lbl, "label": {"7d": "7 days", "30d": "30 days", "90d": "90 days",
                                    "365d": "1 year", "all": meta.get("all_label", "All-time")}.get(lbl, lbl)}
            for lbl in (meta.get("window_labels") or ["all"])
        ],
        "scope": meta.get("scope", ""),
        "scopeTargets": meta.get("scope_targets") or {},
        "kpis": kpis, "ci": ci, "pr": pr_tiles,
        "mix": {"rows": mix_rows},
        "flow": flow,
    }


def _friction_color(v) -> str:
    """Traffic-light colour for a friction/item score — JSON port of the Flow
    template's `fcolor(v)` macro (templates/panels/04_flow.j2)."""
    return "#10b981" if v < 0.5 else ("#f59e0b" if v < 1.5 else "#ef4444")


def flow_json(pr: dict, meta: dict) -> dict:
    """The full /api/report/flow payload: the Flow view's data (friction/flow
    health metrics, cycle-time medians, per-person friction table, and the
    board-movement views — CFD chart, time-in-stage, QA→dev rewinds), JSON
    instead of the server-rendered HTML fragment render_flow_fragment()
    returns (see server.py's serve_flow / serve_report_flow). `pr` is shaped
    like serve_flow's own `{"flow": semantic_metrics.flow_report(...)}` — i.e.
    `pr['flow']` is a flow_report() dict (has_data/n_items/.../cfd/dwell/
    rewinds — see semantic_metrics.py). `meta` carries everything that does
    NOT vary with the period/scope filter — same convention as overview_json/
    trend_json/delivery_json. Mirrors a template quirk on purpose: when there's
    no cohort (no issues/PRs created in the window), the WHOLE view degrades
    to the "no data" hint — even though `board_cfd`/`stage_dwell`/
    `board_rewinds` are computed independently of the cohort and might have
    data of their own (templates/panels/04_flow.j2 gates everything on
    `f.has_data`, not each section on its own flag). Pure / never touches the
    DB — bad/empty input degrades to the "no data" shape, never raises."""
    envelope = {
        "ok": True,
        "meta": {
            "org": meta.get("org"), "allTime": bool(meta.get("all_time", True)),
            "windowStart": meta.get("window_start"), "lookbackDays": meta.get("lookback_days"),
            "generatedText": (meta.get("generated") or "")[:16].replace("T", " "),
        },
        "period": meta.get("period") or {"preset": "all", "label": meta.get("all_label", "All-time"),
                                          "from": None, "to": None},
        "periodPresets": [
            {"key": lbl, "label": {"7d": "7 days", "30d": "30 days", "90d": "90 days",
                                    "365d": "1 year", "all": meta.get("all_label", "All-time")}.get(lbl, lbl)}
            for lbl in (meta.get("window_labels") or ["all"])
        ],
        "scope": meta.get("scope", ""),
        "scopeTargets": meta.get("scope_targets") or {},
    }

    # Work in flight is attached BEFORE the has_data gate on purpose. It is
    # independent of the cohort — a window in which nothing was created can still
    # have open PRs — and folding it behind that flag would hide a real number
    # behind an unrelated one, which is the same silent-blanking the flow template
    # already does to its board sections (see this function's docstring).
    inf = pr.get("in_flight") or {}
    envelope["inFlight"] = {
        "periodScoped": False,
        "n": inf.get("n") or 0,
        "drafts": inf.get("drafts") or 0,
        "unreviewed": inf.get("unreviewed") or 0,
        "staleBefore": inf.get("stale_before"),
        "staleDays": inf.get("stale_days") or 30,
        "medianAgeD": inf.get("median_age_d"),
        "oldestAgeD": inf.get("oldest_age_d"),
        "bands": [{"key": b["key"], "label": b["label"], "n": b["n"]}
                  for b in (inf.get("bands") or [])],
        "people": [{"login": p["login"],
                    "n": p["n"], "drafts": p["drafts"], "unreviewed": p["unreviewed"],
                    "oldestAgeD": p["oldest_age_d"]}
                   for p in (inf.get("people") or [])],
        "staleReviewDays": inf.get("stale_review_days") or 7,
        "staleUnreviewedN": inf.get("stale_unreviewed_n") or 0,
        "staleUnreviewed": [{"repo": i["repo"], "number": i["number"], "login": i["login"],
                             "ageD": i["age_d"], "title": i["title"]}
                            for i in (inf.get("stale_unreviewed") or [])],
        "size": {
            "medianAdditions": (inf.get("size") or {}).get("median_additions"),
            "p90Additions": (inf.get("size") or {}).get("p90_additions"),
            "medianFiles": (inf.get("size") or {}).get("median_files"),
            "rawLines": True,
            "biggest": [{"repo": i["repo"], "number": i["number"], "login": i["login"],
                         "additions": i["additions"], "files": i["files"]}
                        for i in ((inf.get("size") or {}).get("biggest") or [])],
        },
    }

    # Same reasoning as inFlight: abandonment is independent of the created-in-window
    # cohort, so it must not vanish behind the has_data gate either.
    ab = pr.get("abandoned") or {}
    envelope["abandoned"] = {
        "periodScoped": True,
        "n": ab.get("n") or 0,
        "merged": ab.get("merged") or 0,
        "closedTotal": ab.get("closed_total") or 0,
        "ratePct": ab.get("rate_pct"),
        "reviewed": ab.get("reviewed") or 0,
        "unreviewed": ab.get("unreviewed") or 0,
        "reviewsTotal": ab.get("reviews_total") or 0,
        "drafts": ab.get("drafts") or 0,
        "reasons": [{"key": r["key"], "label": r["label"], "sub": r["sub"], "n": r["n"],
                     "reviews": r["reviews"], "medianLivedD": r["median_lived_d"],
                     "oldestLivedD": r["oldest_lived_d"]}
                    for r in (ab.get("reasons") or [])],
        "bands": [{"key": b["key"], "label": b["label"], "n": b["n"]}
                  for b in (ab.get("bands") or [])],
        "repos": [{"repo": r["repo"], "n": r["n"], "reviews": r["reviews"], "swept": r["swept"]}
                  for r in (ab.get("repos") or [])[:8]],
        "swept": [{"repo": i["repo"], "number": i["number"], "login": i["login"],
                   "livedD": i["lived_d"], "title": i["title"]}
                  for i in (ab.get("swept") or [])[:10]],
    }

    f = pr.get("flow") or {}
    if not f.get("has_data"):
        envelope["flow"] = {"hasData": False}
        return envelope

    def hrs(v):
        return _hours(v) if v is not None else None

    cycle_defs = [
        ("ttfr", "Open → first review", "how fast work enters review"),
        ("review_to_merge", "Review → merge", "the review-and-land leg"),
        ("ttm", "Open → merge", "total PR lead time"),
        ("draft_to_ready", "Draft → ready", "time spent in draft"),
        ("ttc", "Open → close", "issue resolution time"),
    ]
    cy = f.get("cycle") or {}
    cycle, cycle_missing = [], []
    for key, label, sub in cycle_defs:
        c = cy.get(key) or {}
        if c.get("h") is not None:
            cycle.append({"key": key, "label": label, "sub": sub, "h": hrs(c["h"]), "n": c.get("n") or 0})
        else:
            # A segment with nothing to measure used to be dropped silently, which made
            # "no data in this window" indistinguishable from "this can never be
            # computed" — two of these five sat permanently empty because they were
            # derived from a column the collector never populates, and the cards simply
            # never appeared. Naming them costs one line and removes that ambiguity.
            cycle_missing.append({"key": key, "label": label})
    envelope["cycleMissing"] = cycle_missing

    people = [
        {
            "login": p["login"], "name": p["name"], "items": p["items"],
            "friction": (str(p["friction"]) if p.get("friction") is not None else None),
            "frictionColor": (_friction_color(p["friction"]) if p.get("friction") is not None else None),
            "reopenPct": p["reopen_pct"], "bouncePct": p["bounce_pct"],
            "crRounds": p["cr_rounds"], "crPrs": p["cr_prs"], "extraReqs": p["extra_reqs"],
            "ttmMed": hrs(p.get("ttm_med")), "ttfrMed": hrs(p.get("ttfr_med")),
        }
        for p in (f.get("people") or [])
    ]

    import vega_spec
    c = f.get("cfd") or {}
    cfd = {
        "hasData": bool(c.get("has_data")),
        "nDates": c.get("n_dates") or 0, "firstDate": c.get("first_date"),
        "series": [{"key": s["key"], "name": s["name"], "color": s["color"]} for s in (c.get("series") or [])],
        "spec": (vega_spec.stacked_area_spec(c.get("series"), c.get("dates"), c.get("series"), "items")
                 if c.get("has_data") else None),
    }

    dw = f.get("dwell") or {}
    dwell = {
        "hasData": bool(dw.get("has_data")),
        "ageMedianH": hrs(dw.get("age_median_h")), "ageN": dw.get("age_n") or 0,
        "ageMaxH": hrs(dw.get("age_max_h")),
        "dwellMedianH": hrs(dw.get("dwell_median_h")), "dwellN": dw.get("dwell_n") or 0,
        "firstDate": dw.get("first_date"),
        "stages": [
            {"key": s["key"], "name": s["name"], "color": s["color"],
             "nCurrent": s.get("n_current") or 0, "ageMedianH": hrs(s.get("age_median_h")),
             "medianH": hrs(s.get("median_h")), "n": s.get("n") or 0}
            for s in (dw.get("stages") or [])
        ],
    }

    rw = f.get("rewinds") or {}
    rewinds = {
        "hasHistory": bool(rw.get("has_history")),
        "nDates": rw.get("n_dates") or 0, "firstDate": rw.get("first_date"),
        "hasEvents": bool(rw.get("events")),
        "qaToDev": rw.get("qa_to_dev") or 0, "ownerCount": len(rw.get("by_person") or {}),
    }

    envelope["flow"] = {
        "hasData": True,
        "nItems": f["n_items"], "nPrs": f["n_prs"], "nIssues": f["n_issues"],
        "health": {
            "crRate": f["cr_rate"], "crPrs": f["cr_prs"], "crRounds": f["cr_rounds"],
            "reopenRate": f["reopen_rate"], "reopenedN": f["reopened_n"],
            "bounceRate": f["bounce_rate"], "bouncedN": f["bounced_n"],
            "rereqRate": f["rereq_rate"], "rereqN": f["rereq_n"],
        },
        "cycle": cycle,
        "minItems": f["min_items"], "people": people,
        "cfd": cfd, "dwell": dwell, "rewinds": rewinds,
    }
    return envelope


def render_report(model: dict) -> str:
    """Render the full report HTML from a build_model() dict (uses the loader env
    so the report's macro imports resolve). Injects the Vega bundle/CSS globals
    (vega_scripts, chart_css) without clobbering model keys of the same name."""
    context = dict(model)
    context.setdefault("vega_scripts", shell.VEGA_SCRIPTS)
    context.setdefault("chart_css", shell.CHART_CSS)
    return _env().get_template("report").render(**context)


def render_period_fragment(pr: dict, ctx: dict | None = None) -> str:
    """Render ALL filterable panels for ONE period block (a store.aggregate window
    or build_model's all_block), each wrapped in [data-period-panel] so the portal
    page swaps them in. `ctx` supplies the globals the macros need (emails_by_login)."""
    context = dict(ctx or {})
    context["pr"] = pr
    return _env().get_template("fragment").render(**context)


def render_delivery_fragment(pr: dict) -> str:
    """Render just the Delivery panels for a period + repo slice (/api/delivery)."""
    return _env().get_template("delivery").render(pr=pr)


def render_trend_fragment(pr: dict) -> str:
    """Render just the Trend panel for a period + slice + granularity (/api/trend)."""
    return _env().get_template("trend").render(pr=pr)


def render_flow_fragment(pr: dict) -> str:
    """Render just the Flow tab (friction explainer + flow metrics) for /api/flow."""
    return _env().get_template("flow").render(pr=pr)


def render_person_fragment(payload: dict) -> str:
    """Render the full per-person dashboard for the /api/person endpoint.
    payload = {profile, alltime, weekly, heat, emails}."""
    return _env().get_template("person").render(p=payload)


def render_panel_macro(macro: str, kwargs: dict) -> str:
    """Render ONE macro from the 'panels' template with the given kwargs (bound by
    name). Used by the dashboard panel resolver so panels reuse the report components."""
    import re as _re
    if not _re.match(r"^[a-z_][a-z0-9_]*$", macro) or not all(
            _re.match(r"^[a-z_][a-z0-9_]*$", k) for k in kwargs):
        raise ValueError(f"invalid macro/param name: {macro}")
    args = ", ".join(f"{k}={k}" for k in kwargs)
    src = ("{% from 'panels' import " + macro + " with context %}"
           + "{{ " + macro + "(" + args + ") }}")
    return _env().from_string(src).render(**kwargs)


def _scope_options() -> str:
    """HTML <optgroup>/<option> markup for the dashboard page's scope picker.
    Reuses the SAME source as the main report's #global-scope select
    (discovery.scope_targets(), see build_model()) so scope names are never
    invented — only real orgs/elements/repos already in the store. '' (no store,
    or nothing collected yet) leaves just the 'Whole org' option, which is fine."""
    from markupsafe import escape
    try:
        import store, discovery
        conn = store.connect()
        try:
            targets = discovery.scope_targets(conn)
        finally:
            conn.close()
    except Exception:                # noqa: BLE001 — no store yet, no options
        return ""
    parts = []
    for label, key in (("Organizations", "org"), ("Elements", "element"), ("Repositories", "repo")):
        vals = targets.get(key) or []
        if not vals:
            continue
        parts.append(f'<optgroup label="{label}">')
        for v in vals:
            ev = escape(v)
            parts.append(f'<option value="{key}:{ev}">{ev}</option>')
        parts.append("</optgroup>")
    return "".join(parts)


def render_dashboard_page(dashboard: dict) -> str:
    """Fill templates/dashboard.j2 for one dashboard row (from store.get_dashboard).
    Shelled like every other manage page (shell.SHELL_CSS + shell.BASE_CSS +
    shell.sidebar_html), so it sits inside the same app frame as the editor/report."""
    spec = dashboard["spec"]
    return _env().get_template("dashboard").render(
        id=dashboard["id"], title=spec.get("title", "Dashboard"),
        panels=spec.get("panels", []), scope_options=_scope_options(),
        shell_css=shell.SHELL_CSS, base_css=shell.BASE_CSS, chart_css=shell.CHART_CSS,
        vega_scripts=shell.VEGA_SCRIPTS, sidebar=shell.sidebar_html("dashboards"))


def render_dashboard_editor(dashboard: dict) -> str:
    """Fill templates/dashboard_editor.j2 for one dashboard row — the owner-only
    editor at /dashboard/<id>/edit (measure-first modal widget picker + drag list +
    live preview, saved via POST /api/dashboard/<id>). Shelled like every other
    manage page (shell.SHELL_CSS + shell.BASE_CSS + shell.sidebar_html), so it sits
    inside the same app frame as the report/portal instead of a bare page."""
    import json as _json
    spec = dashboard["spec"]
    # Escape '</' so a panel title containing '</script>' can't break out of the
    # inline <script> block below — same convention as configstore/directory/semantic_editor.
    spec_json = _json.dumps(spec).replace("</", "<\\/")
    return _env().get_template("dashboard_editor").render(
        id=dashboard["id"], title=spec.get("title", "Untitled dashboard"),
        visibility=dashboard.get("visibility", "private"),
        spec_json=spec_json,
        shell_css=shell.SHELL_CSS, base_css=shell.BASE_CSS, chart_css=shell.CHART_CSS,
        vega_scripts=shell.VEGA_SCRIPTS, sidebar=shell.sidebar_html("dashboards"))


def report_redirect_shim() -> str:
    """The React cutover's client-side hash-redirect page, served at `/report`
    (and `/report.html`) in place of the monolith. The report used to be a single
    page whose section was chosen by `location.hash` (`/report#trend`); each
    section is now its own top-level React route. The hash lives only in the
    browser, so the mapping must run client-side: read `location.hash`, map the
    old mode to its route (incl. the renames repos→repositories, usage→traffic,
    fabric→ai-tools, and the dropped `all`/Full-report → overview), preserve the
    query string (?p=/?slice=/?person=/…), and `location.replace()` so old
    bookmarks/deep-links land on the right view without a history entry.

    The monolith itself is NOT removed — it stays reachable as a fallback at
    `/report/legacy` (clean path, used by the pixel-parity baseline capture) or
    `/report?legacy=1` (see server.py), so this shim only fires on bare /report."""
    # Keep the map in sync with shell.MIGRATED_VIEWS (+ all→overview, ''→overview).
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Redirecting…</title>'
        '<meta http-equiv="refresh" content="0;url=/overview">'
        '<script>(function(){'
        'var M={overview:"/overview",trend:"/trend",delivery:"/delivery",flow:"/flow",'
        'people:"/people",person:"/person",repos:"/repositories",elements:"/elements",'
        'usage:"/traffic",fabric:"/ai-tools",all:"/overview"};'
        'var h=(location.hash||"").replace(/^#/,"").split(/[?&]/)[0];'
        'var t=M[h]||"/overview";'
        'location.replace(t+location.search);'
        '})();</script></head><body>'
        '<noscript><a href="/overview">Open the report</a></noscript>'
        '</body></html>')


def render_spa_page(entry: str, active: str, title: str, *, report_chrome: bool = False,
                    bootstrap: dict | None = None, vega: bool = False,
                    sidebar: bool = True) -> str:
    """Full page for a React-mounted route (see spa.py + frontend/). Chrome is
    byte-identical to the other shelled manage pages (shell.BASE_CSS/SHELL_CSS/
    CHART_CSS in <head> + shell.sidebar_html(active)); the content area is a
    bare `<div id="root">` that the entry's Vite bundle mounts into — so the
    same CSS applies and the pixel-parity gate (P0-T3) has nothing to diff in
    the chrome, only in whatever the React entry renders.

    `report_chrome=True` (report views: Overview, ...) additionally carries
    shell.VEGA_SCRIPTS (same-origin vega/vega-lite/vega-embed, so the page's
    <VegaChart> can call window.vegaEmbed) and the `report-chrome` React bundle
    (frontend/src/components/{ChatWidget,ReportChrome,reportChromeEffects} — the
    floating metrics-assistant chat + the drill-down modal + click-to-sort) — a
    migrated report view must have both to stay pixel-identical to its monolith
    baseline (missing #mx-fab would show up as a diff in the bottom-right
    corner). Non-report SPA pages (e.g. /whats-new) don't opt in: their monolith
    equivalent (changelog.render_page()) never had either, so adding them
    unconditionally would introduce a NEW diff, not remove one.

    If the frontend hasn't been built yet (or this entry isn't in the Vite
    manifest — see spa.entry_assets), degrades to the shell + a small notice
    instead of emitting a broken <script> tag. Never raises/500s on that."""
    import spa
    from html import escape as _e

    assets = spa.entry_assets(entry)
    if assets is None:
        # The UI is React; there is no server-rendered equivalent to fall back to
        # (the Jinja pages still reachable at ?legacy=1 are the pixel-gate baseline on
        # its way out, not a supported path). So this page's whole job is to tell you
        # how to get a bundle — with BOTH routes, because someone who cloned the repo
        # and ran `python server.py` has hit this without necessarily having Node.
        root_inner = (
            '<div style="padding:32px;max-width:620px">'
            '<h1 style="margin:0 0 8px">Frontend not built yet</h1>'
            '<p style="color:var(--mut);margin:0 0 20px">The interface is a React app '
            'and its bundle is not committed to the repository, so it has to be built '
            'once. Either of these gives you a working portal:</p>'
            '<p style="margin:0 0 6px"><b>With Docker</b> — builds the bundle for you:</p>'
            '<pre style="background:var(--panel2);padding:12px;border-radius:8px;margin:0 0 20px">'
            'docker compose up --build</pre>'
            '<p style="margin:0 0 6px"><b>From source</b> — needs Node 20+:</p>'
            '<pre style="background:var(--panel2);padding:12px;border-radius:8px;margin:0 0 20px">'
            'cd frontend &amp;&amp; npm install &amp;&amp; npm run build</pre>'
            '<p style="color:var(--mut);margin:0">Then reload this page. The API is '
            'already running — <code>/health</code> answers, and every '
            '<code>/api/…</code> endpoint works — only the rendered pages need '
            'the bundle.</p>'
            '</div>')
        tags = ""
    else:
        root_inner = ""
        tags = "".join(f'<link rel="stylesheet" href="{_e(c)}">' for c in assets["css"])
        tags += f'<script type="module" src="{_e(assets["js"])}"></script>'

    # Optional server→client handoff: a route (e.g. /dashboard/<id>) embeds the
    # data its React entry needs to render — the entry reads document.getElementById
    # ("spa-bootstrap").textContent. Escaped `</` so the JSON can't close the tag.
    boot = ""
    if bootstrap is not None:
        import json as _json
        boot = ('<script id="spa-bootstrap" type="application/json">'
                + _json.dumps(bootstrap, ensure_ascii=False).replace("</", "<\\/")
                + '</script>')

    # `sidebar=False` (first-run /setup wizard) emits a bare #root with no sidebar
    # chrome / main.wrap — the legacy wizard is a standalone centred page
    # (`<body><div class="wrap">`), so its React port renders its own .wrap inside
    # #root and must not gain the app/sidebar shell every other manage page has.
    if sidebar:
        body = (
            '<div class="app">' + shell.sidebar_html(active) +
            '<main class="wrap">' + boot + '<div id="root">' + root_inner + '</div></main>'
            '</div>')
    else:
        body = boot + '<div id="root">' + root_inner + '</div>'

    # `vega` brings the Vega runtime WITHOUT the report chat/drill/sort chrome —
    # the dashboard view needs charts but (like its legacy Jinja render) has no
    # #mx-fab chat button / drill modal / sort listener. report_chrome implies vega.
    head_scripts = shell.VEGA_SCRIPTS if (report_chrome or vega) else ""
    # Report views also get the floating metrics-assistant chat + the drill-down
    # modal (click a data-drill cell → /api/drill) + click-to-sort. These used to be
    # injected as vanilla shell.CHAT_WIDGET_JS/DRILL_JS/SORT_JS <script> blocks; they
    # are now a React bundle (frontend/src/components/{ChatWidget,ReportChrome,
    # reportChromeEffects}) shipped via the `report-chrome` entry. Its CSS
    # (#mx-fab/#mx-panel/.drill-*) lives in report.css, already loaded by the route
    # entry. Degrades to nothing if unbuilt. Closed by default → no pixel diff.
    body_scripts = ""
    if report_chrome:
        chrome = spa.entry_assets("report-chrome")
        if chrome:
            body_scripts = ("".join(f'<link rel="stylesheet" href="{_e(c)}">' for c in chrome["css"])
                            + f'<script type="module" src="{_e(chrome["js"])}"></script>')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{_e(title)} — Constructor Insight</title>'
        '<style>' + shell.BASE_CSS + shell.SHELL_CSS + shell.CHART_CSS + '</style>'
        + head_scripts +
        '</head><body>' + body + tags + body_scripts + '</body></html>')


def load_history() -> list:
    """Accumulated daily snapshots for trend charts — from the SQLite store,
    falling back to the JSONL export if the DB isn't present."""
    try:
        import store
        conn = store.connect()
        rows = store.read_snapshots(conn)
        if rows:
            return rows
    except Exception:                # noqa: BLE001 — fall back to the text export
        pass
    path = str(paths.data_path("history", "snapshots.jsonl"))
    rows = []
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    rows.sort(key=lambda r: r.get("date", ""))
    return rows


def _periods_from_store(conn, d: dict) -> list:
    """One store.aggregate() block per preset window (for the period filter)."""
    import store
    labels = d.get("window_labels") or ["all"]
    wsince = d.get("window_since") or {}
    until = d.get("generated_at") or "2099-01-01T00:00:00Z"
    fallback = (d.get("window_start") or "2008-01-01") + "T00:00:00Z"
    return [store.aggregate(conn, wsince.get(lbl, fallback), until, label=lbl)
            for lbl in labels]


def _contributors_from_store(conn, d: dict) -> list:
    """Cumulative contributor counts at monthly points (last 12 months + today)."""
    import store
    gen = d.get("generated_at", "")
    try:
        now = datetime.strptime(gen[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        now = datetime.now(timezone.utc)
    dates = []
    for k in range(12, 0, -1):
        yy, mm = now.year, now.month - k
        while mm <= 0:
            mm += 12; yy -= 1
        dates.append(f"{yy:04d}-{mm:02d}-01T00:00:00Z")
    dates.append(now.strftime("%Y-%m-%dT23:59:59Z"))
    return store.contributors_timeseries(conn, dates)


def load_data() -> dict:
    """Report payload from the SQLite store — the single source of truth. Period
    panels are computed from the granular tables via store.aggregate()."""
    import store
    conn = store.connect()
    d = store.read_latest_run(conn)
    if d is None:
        raise SystemExit("No collected data in the store yet — run `python collect.py` first.")
    d["_history"] = store.read_snapshots(conn)
    d["_periods"] = _periods_from_store(conn, d)
    d["_contrib"] = _contributors_from_store(conn, d)
    try:                             # all-time Delivery metrics (taxonomy-derived)
        import semantic_metrics
        since = (d.get("window_start") or "2008-01-01") + "T00:00:00Z"
        _until = d.get("generated_at") or "2099-01-01T00:00:00Z"
        d["delivery_all"] = semantic_metrics.window_block(conn, since, _until)
        d["flow_all"] = semantic_metrics.flow_report(conn, None, since, _until)
    except Exception:                # noqa: BLE001 — optional
        d["delivery_all"] = {}
        d["flow_all"] = {}
    try:                             # all-time developer-score rollup (Overview panel)
        _u = d.get("generated_at") or "2099-01-01T00:00:00Z"
        _s = (d.get("window_start") or "2008-01-01") + "T00:00:00Z"
        d["score_all"] = store.score_summary(conn, _s, _u)
    except Exception:                # noqa: BLE001 — optional, never break the report
        d["score_all"] = None
    try:                             # slice-filter targets for the Delivery scope picker
        import discovery
        d["scope_targets"] = discovery.scope_targets(conn)
    except Exception:                # noqa: BLE001
        d["scope_targets"] = {}
    return d


def main() -> None:
    d = load_data()
    model = build_model(d)
    html = render_report(model)
    with open(paths.data_path("report.html"), "w") as fh:
        fh.write(html)
    print(f"Wrote report.html ({len(html)} bytes)")


if __name__ == "__main__":
    main()


# --- metric registry: metrics computed in this module, tied to their functions ---
_mreg.register_for(delta_map, [
    _m("deltas", type="computed", group="trend", unit="%",
       desc="KPI change vs the immediately preceding equal-length window (▲/▼). Shown on the "
            "Overview and Person KPI tiles; skipped for all-time / >2y spans.",
       formula="diff = cur − prev ; pct = diff / prev × 100 (None when prev=0 → 'new')",
       snippet="pct = round(diff / prev * 100) if prev else None\n"
               "dir = 'up' if diff>0 else 'down' if diff<0 else 'flat'"),
])

_mreg.register_for(build_model, [
    _m("trend", type="computed", group="trend", unit="series",
       desc="Totals across accumulated daily snapshots — the time axis of the report "
            "(inherently all-time).",
       formula="series of totals[metric] per snapshot date",
       snippet="[s['totals']['commits'] for s in history]"),
])


# Report panels computed in build_model() that aren't part of store.aggregate() —
# registered here so the metrics catalog reflects everything the report shows.
_mreg.register_for(build_model, [
    _mreg.metric("repo_coverage", type="computed", group="quality", unit="rollup",
        desc="Repository inventory: distinct repos analysed, split into primary-org vs "
             "legacy-only (old org, pre-migration), with any still-unclassified repos flagged "
             "for triage in Config.",
        formula="distinct repos = primary-org + legacy-only; unclassified = repos with no type set",
        snippet="repo dim grouped by org/classification; legacy-only = present only in the old org"),
    _mreg.metric("external_contributors", type="computed", group="company", unit="count",
        desc="Non-member contributors with activity in the org — a healthy-community signal "
             "(people giving back who aren't on the team).",
        formula="people where is_member=0 AND (commits + prs) > 0",
        snippet="[p for p in people if not p.is_member and (p.commits + p.prs)]"),
    _mreg.metric("surviving_window", type="direct", group="impact", unit="lines",
        desc="Surviving code lines (git blame of today's tree) whose last commit falls inside the "
             "lookback window — recently written code that still lives. All-time (blame-based).",
        formula="blame lines of today's tree whose last-commit date is within the window",
        snippet="git blame HEAD; keep lines whose commit date in [since, until]"),
    _mreg.metric("studio_provenance", type="computed", group="impact", unit="files / lines",
        desc="Surviving lines attributable to each AI content marker, per repo — a "
             "floor on tool-generated code that reached today's tree (only marked generations counted).",
        formula="per marker: files touched + surviving blame lines carrying that marker",
        snippet="scan today's tree for @cpt / studio markers; count files + surviving lines"),
    _mreg.metric("gears_usage", type="computed", group="impact", unit="files / lines",
        desc="Where the Gears framework is used across product repos — files and surviving lines "
             "that reference gears, per repo.",
        formula="per repo: files + surviving lines importing/using gears",
        snippet="scan today's tree for gears usage markers; count files + surviving lines"),
])
