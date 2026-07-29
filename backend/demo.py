#!/usr/bin/env python3
"""Generate a complete, invented dataset so the report can be seen without a token.

    python reportctl.py demo-seed      # into the configured store
    python demo.py --db /tmp/demo.db   # or straight into a file

Why this exists. Everything the report renders comes from a run blob that only a real
`collect.py` produces, so until now there were exactly two ways to look at the UI: point
the collector at a real organisation, or read the source. That makes the tool impossible
to evaluate, impossible to screenshot for documentation, and it leaves the test suite
without a fixture that exercises the full render — the granular tables can be seeded
easily, the blob could not.

Everything here is invented. People use the Alice/Bob convention precisely so that no
screenshot or test fixture can be mistaken for real data about real people, and the
companies and repositories are equally fictional.

Determinism: no randomness anywhere; every value is a function of an index. The `anchor`
date defaults to today so a fresh demo looks alive (the 7-day period has data in it), and
tests pass a fixed anchor instead, which makes the whole dataset reproducible byte for
byte.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone

# Obviously-synthetic names. The convention is load-bearing, not decorative: it has to be
# impossible to mistake a demo screenshot for a real person's activity.
PEOPLE = [
    ("alice", "Alice Anderson", "Northwind Systems", True),
    ("bob", "Bob Baker", "Northwind Systems", True),
    ("carol", "Carol Chen", "Northwind Systems", True),
    ("dave", "Dave Diaz", "Northwind Systems", True),
    ("erin", "Erin Evans", "Contoso Labs", True),
    ("frank", "Frank Fischer", "Contoso Labs", True),
    ("grace", "Grace Green", "Contoso Labs", True),
    ("heidi", "Heidi Hoffman", "Initech", True),
    ("ivan", "Ivan Ilić", "Initech", True),
    ("judy", "Judy Jones", "Initech", False),
    ("niaj", "Niaj Nasir", "Other", False),
    ("olivia", "Olivia Ortiz", "Other", False),
]

# (name, classification, element) — two repo types and four product areas, so the
# platform/app split and the element rollup both have something to show.
REPOS = [
    ("platform-core", "platform", "Core"),
    ("platform-sdk", "platform", "Core"),
    ("platform-cli", "platform", "Tooling"),
    ("design-system", "platform", "Core"),
    ("web-app", "app", "Web"),
    ("web-api", "app", "Web"),
    ("mobile-app", "app", "Mobile"),
    ("docs-site", "app", "Docs"),
]
ORG = "demo-org"
AI_TOOLS = ["Claude Code", "Copilot (mention)", "Devin"]
WEEKS = 26


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def build(anchor: str | None = None) -> dict:
    """The run blob, shaped exactly as collect.py writes it. Pure — no DB, no clock
    unless `anchor` is omitted — so a test can diff two calls."""
    end = (datetime.strptime(anchor, "%Y-%m-%d").replace(tzinfo=timezone.utc)
           if anchor else datetime.now(timezone.utc).replace(
               hour=6, minute=0, second=0, microsecond=0))
    start = end - timedelta(weeks=WEEKS)

    people: dict = {}
    for i, (login, name, company, member) in enumerate(PEOPLE):
        # a deliberate spread: the first few people are the most active, the last two
        # barely appear, so tables and rankings are not flat
        w = max(1, 12 - i)
        people[login] = {
            "name": name, "company": company, "is_member": member,
            "commits": 18 * w, "meaningful_additions": 1450 * w,
            "prs_opened": 6 * w, "prs_merged": 5 * w,
            "specs": 2 * w, "bugs": 3 * w, "features": 2 * w,
            "reviews_given": 7 * w, "approvals_given": 5 * w,
            "ai_commits": 4 * w, "cpt_lines": 120 * w,
            "surviving_code_human": 900 * w, "surviving_code_ai": 260 * w,
            "surviving_spec_human": 70 * w,
            "median_ttm_h": round(3.5 + i * 0.7, 1),
            "total_activity": 29 * w,
            # raw LOC alongside the meaningful figures: the report shows both, and the
            # difference between them is the point of the meaningful-LOC filter
            "additions": 2100 * w, "deletions": 640 * w,
            "meaningful_deletions": 430 * w,
            "epics": max(0, w // 4),
            "surviving_spec_ai": 40 * w,
            # the platform/app split, per person
            "platform_commits": 11 * w, "app_commits": 7 * w,
            "platform_meaningful": 900 * w, "app_meaningful": 550 * w,
            "platform_prs": 4 * w, "app_prs": 2 * w,
            "commit_types": {"feat": 8 * w, "fix": 6 * w, "docs": 2 * w,
                             "refactor": 2 * w},
            "emails": [f"{login}@example.com"],
            "repos": [f"{ORG}/{REPOS[i % len(REPOS)][0]}"],
            "identity_confidence": "high" if member else "medium",
            "identity_evidence": "GitHub-verified author" if member else "name bridge",
        }

    repos: dict = {}
    for i, (name, cls, elem) in enumerate(REPOS):
        key = f"{ORG}/{name}"
        repos[key] = {
            "org": ORG, "name": name, "classification": cls, "element": elem,
            "legacy_only": False, "archived": False,
            "stars": 40 - i * 3, "forks": 9 - i,
            "commits_window": 260 - i * 22, "ai_commits_window": 60 - i * 5,
            "prs_opened_window": 70 - i * 6, "prs_merged_window": 61 - i * 5,
            "code_loc": 48000 - i * 3800, "spec_loc": 6200 - i * 500,
            "total_loc": 54200 - i * 4300,
            "clones_14d": 120 - i * 9, "views_14d": 900 - i * 70,
            "traffic_access": True,
            "reviews": {"total": 48 - i * 4, "approvals": 35 - i * 3,
                        "reviewed": 44 - i * 4, "coverage_pct": 82.0 - i,
                        "median_ttm_h": round(4.0 + i * 0.8, 1)},
        }

    companies = sorted({p["company"] for p in people.values()})

    def by_company(field: str) -> dict:
        out: dict = {}
        for p in people.values():
            out[p["company"]] = out.get(p["company"], 0) + p[field]
        return out

    totals = {
        "commits": sum(p["commits"] for p in people.values()),
        "meaningful_additions": sum(p["meaningful_additions"] for p in people.values()),
        "prs": sum(p["prs_opened"] for p in people.values()),
        "specs": sum(p["specs"] for p in people.values()),
        "people": len(people),
        "ai_commits": sum(p["ai_commits"] for p in people.values()),
    }

    # The blob's `weekly` is {category: {ISO week: count}} — "2026-W23", not a date —
    # because the report labels its bars from isocalendar(). `_weeks` keeps the same
    # numbers in row form for the daily snapshots below, so the trend view and the
    # weekly-activity chart cannot disagree.
    _weeks = []
    for w in range(WEEKS):
        d0 = start + timedelta(weeks=w)
        y, iso_w, _ = d0.isocalendar()
        _weeks.append({
            "week": f"{y}-W{iso_w:02d}",
            "date": d0.strftime("%Y-%m-%d"),
            "commits": 150 + (w * 7) % 90,
            "specs": 12 + w % 9,
            "prs": 40 + (w * 3) % 25,
            "issues": 25 + (w * 5) % 20,
        })
    weekly = {cat: {r["week"]: r[cat] for r in _weeks}
              for cat in ("commits", "specs", "prs", "issues")}

    return {
        "generated_at": _iso(end),
        "org": ORG,
        "orgs": [ORG],
        "lookback_days": 0,
        "all_time": True,
        "window_start": start.strftime("%Y-%m-%d"),
        "window_labels": ["all"],
        "window_since": {"all": _iso(start)},
        "members": sorted(lg for lg, _, _, m in PEOPLE if m),
        "repos": repos,
        "people": people,
        "forkers": {
            # accounts that forked but never contributed — the "using, not giving back"
            # side of the contribute/use split. `forked` is the repo list and
            # has_contributed_back is what the scenario-2 panel filters on.
            "quinn": {"is_member": False, "forked": [f"{ORG}/platform-sdk"],
                      "has_contributed_back": False},
            "rupert": {"is_member": False,
                       "forked": [f"{ORG}/platform-core", f"{ORG}/web-api"],
                       "has_contributed_back": False},
            "trent": {"is_member": True, "forked": [f"{ORG}/design-system"],
                      "has_contributed_back": False},
            # a forker who DID contribute, so the panel has something to exclude
            "alice": {"is_member": True, "forked": [f"{ORG}/web-app"],
                      "has_contributed_back": True},
        },
        "weekly": weekly,
        "_weeks": _weeks,          # row form, for snapshot seeding (not read by render)
        "studio_provenance": {"enabled": False, "repos": {}, "by_company": {},
                              "people": {}},
        "gears_usage": {"enabled": False, "repos": {}},
        "fabric_trackers": {"trackers": {}},
        "elements": _elements(repos, people),
        "ai_precision": {t: ("exact" if t != "Copilot (mention)" else "heuristic")
                         for t in AI_TOOLS},
        "reviews": {
            "reviewers": {lg: p["reviews_given"] for lg, p in people.items()},
            "approvals": {lg: p["approvals_given"] for lg, p in people.items()},
            "coverage_pct": 78.0,
            "median_rounds": 1.0,
        },
        "reviews_company_ttm": {
            c: {"median_ttm_h": round(4.0 + n * 0.9, 1),
                "median_review_latency_h": round(1.5 + n * 0.6, 1),
                "merged": 90 - n * 12}
            for n, c in enumerate(companies)},
        "bots": {"[bot]": 340, "dependabot": 190, "github-actions": 610},
        "identity": {"verified": 9, "pr_bridge": 2, "name_bridge": 1,
                     "override": 0, "unresolved_human": 0},
        "api": {"degraded": [], "warnings": [], "rate_events": 0, "reset": None,
                "core_remaining": 4870, "core_limit": 5000, "search_remaining": 29},
        "pct": {"commits": by_company("commits"),
                "meaningful_additions": by_company("meaningful_additions"),
                "prs": by_company("prs_opened"),
                "specs": by_company("specs")},
        "totals": totals,
        "dir": {"companies": companies, "people": len(people)},
        "scope_targets": {"org": [ORG], "element": sorted({e for _, _, e in REPOS}),
                          "repo": sorted(repos)},
    }


def _elements(repos: dict, people: dict) -> dict:
    """{element name: rollup} — a dict, keyed by name, like collect.py writes it."""
    out: dict = {}
    for key, r in repos.items():
        e = out.setdefault(r["element"], {
            "element": r["element"], "code_loc": 0, "spec_loc": 0, "repos": 0,
            "commits": 0, "people": 0, "prs_merged": 0, "review_ttm_h": None})
        e["code_loc"] += r["code_loc"]
        e["spec_loc"] += r["spec_loc"]
        e["repos"] += 1
        e["commits"] += r["commits_window"]
        e["prs_merged"] += r["prs_merged_window"]
    for n, e in enumerate(out.values()):
        # split members vs external: the elements table adds the two
        e["people_members"] = max(2, len(people) // (n + 2))
        e["people_external"] = 1 + n % 2
        e["people"] = e["people_members"] + e["people_external"]
        e["review_ttm_h"] = round(4.0 + n * 1.1, 1)
    return out


def seed(db_path: str | None = None, anchor: str | None = None) -> dict:
    """Write the demo dataset: granular tables, the run blob, snapshots, traffic."""
    if db_path:
        os.environ["REPORT_DB"] = db_path
    import store

    blob = build(anchor)
    end = datetime.strptime(blob["generated_at"][:10], "%Y-%m-%d").replace(
        tzinfo=timezone.utc)
    start = datetime.strptime(blob["window_start"], "%Y-%m-%d").replace(
        tzinfo=timezone.utc)

    conn = store.connect()
    try:
        for login, p in blob["people"].items():
            conn.execute(
                "INSERT OR REPLACE INTO person (login, name, company, is_member, emails,"
                " surviving_code_human, surviving_code_ai, surviving_spec, cpt_lines,"
                " reviews_given, approvals_given, median_ttm_h, identity_confidence,"
                " identity_evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (login, p["name"], p["company"], int(p["is_member"]),
                 ",".join(p["emails"]), p["surviving_code_human"],
                 p["surviving_code_ai"], p["surviving_spec_human"], p["cpt_lines"],
                 p["reviews_given"], p["approvals_given"], p["median_ttm_h"],
                 p["identity_confidence"], p["identity_evidence"]))
        for key, r in blob["repos"].items():
            conn.execute(
                "INSERT OR REPLACE INTO repo (key, org, name, classification, element,"
                " legacy_only, archived, stars, forks, code_loc, spec_loc)"
                " VALUES (?,?,?,?,?,0,0,?,?,?,?)",
                (key, r["org"], r["name"], r["classification"], r["element"],
                 r["stars"], r["forks"], r["code_loc"], r["spec_loc"]))

        logins = list(blob["people"])
        keys = list(blob["repos"])
        # How often each person shows up in the granular tables, in proportion to the
        # volume the blob already gives them. The two have to agree in SHAPE: the blob
        # feeds the run-based report, these tables feed every React view and the MCP
        # tools, and a dataset that is varied in one and flat in the other tells two
        # different stories about the same fictional company.
        #
        # This replaces `if (n + pi) % 3 == 0: continue`, which read as "not everyone
        # every day" and was not. n advances once per person per day, so with 12 people
        # it advances by a multiple of 3 every day and the test collapsed to `pi % 3`:
        # the same four people (indices 0, 3, 6, 9) were skipped on EVERY day and never
        # appeared in the granular tables at all, while the other eight committed every
        # single weekday and so came out with byte-identical totals. The People view
        # showed 8 of 12 people, all with the same numbers, and every ranking on demo
        # data was meaningless. GranularTablesTest now pins both properties.
        weights = [p["commits"] for p in blob["people"].values()]
        top = max(weights) or 1
        # One accumulator per person, advanced by their share each weekday and spent
        # when it reaches a whole day — an even spread rather than a burst, and the
        # busiest person lands every day while the quietest lands roughly every 12th.
        due = [0.0] * len(logins)
        n = 0
        day = start
        while day <= end:
            if day.weekday() < 5:                     # weekdays only, so heatmaps read
                for pi, login in enumerate(logins):
                    due[pi] += weights[pi] / top
                    if due[pi] < 1.0:                 # not everyone every day
                        continue
                    due[pi] -= 1.0
                    key = keys[(n + pi) % len(keys)]
                    n += 1
                    ai = n % 5 == 0
                    conn.execute(
                        "INSERT OR REPLACE INTO commits (repo, sha, committed_at,"
                        " author_email, author_login, additions, deletions,"
                        " meaningful_additions, meaningful_deletions, is_spec,"
                        " commit_type, ai_marked, ai_loc, ai_tools, is_bot, title)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
                        (key, f"d{n:07d}", _iso(day.replace(hour=9 + pi % 8)),
                         f"{login}@example.com", login,
                         55 + n % 130, 11 + n % 28, 42 + n % 85, 7 + n % 17,
                         int(n % 9 == 0), ("feat", "fix", "docs", "refactor")[n % 4],
                         int(ai), 30 + n % 45,
                         AI_TOOLS[n % len(AI_TOOLS)] if ai else "",
                         f"demo commit {n}"))
                    merged = day.replace(hour=15 + n % 6)
                    conn.execute(
                        "INSERT OR REPLACE INTO pull_request (repo, number, org,"
                        " author_login, created_at, merged_at, review_requested_at,"
                        " classification, is_migration, is_bot, state, closed_at,"
                        " additions, deletions, changed_files, review_count,"
                        " comment_count, is_revert, is_draft, title)"
                        " VALUES (?,?,?,?,?,?,?,?,0,0,'closed',?,?,?,?,?,?,0,0,?)",
                        (key, n, ORG, login, _iso(day.replace(hour=9)),
                         _iso(merged), _iso(day.replace(hour=10)),
                         ("feature", "bug", "chore")[n % 3], _iso(merged),
                         62 + n % 150, 14 + n % 35, 3 + n % 8, 1 + n % 4, 2 + n % 5,
                         f"demo pull request {n}"))
                    # issues and reviews too, so the work-type tiles (bugs / features /
                    # epics) and the review-coverage panels have something to show —
                    # without them the report renders but half of it reads as zero.
                    if n % 3 == 0:
                        # derived from n//3, NOT n%9: issues only exist for multiples of
                        # three, so n%9 could only ever be 0, 3 or 6 and the epic branch
                        # was unreachable — the KPI tile read 0 and looked like a bug in
                        # the report rather than in the fixture.
                        kind = (n // 3) % 9
                        conn.execute(
                            "INSERT OR REPLACE INTO issue (repo, number, org,"
                            " author_login, created_at, is_bug, is_feature, is_epic,"
                            " is_migration, is_bot, issue_type, labels, state,"
                            " state_reason, closed_at, assignees, milestone, title)"
                            " VALUES (?,?,?,?,?,?,?,?,0,0,?,?,?,?,?,?,?,?)",
                            (key, n, ORG, login, _iso(day.replace(hour=10)),
                             int(kind < 4), int(4 <= kind < 7), int(kind == 7),
                             ("Bug" if kind < 4 else "Feature" if kind < 7 else "Epic"),
                             ("bug" if kind < 4 else "feature" if kind < 7 else "epic"),
                             "closed" if n % 4 else "open", "completed" if n % 4 else None,
                             _iso(day + timedelta(days=2 + n % 5)) if n % 4 else None,
                             login, None, f"demo issue {n}"))
                    # Lifecycle events, so the flow panels have rework to show. Without
                    # these, "sent back for changes", "reopened", "back to draft" and
                    # "re-reviewed" are all 0% — the one story the report exists to tell.
                    tl = [("review_requested", 10), ("assigned", 10)]
                    if n % 7 == 0:
                        tl.append(("convert_to_draft", 11))
                        tl.append(("ready_for_review", 13))
                    if n % 11 == 0:
                        tl.append(("reopened", 16))
                    if n % 5 == 0:
                        tl.append(("review_requested", 14))   # a second ask = re-review
                    for ev, hh in tl:
                        conn.execute(
                            "INSERT INTO timeline_event (repo, item_type, number, event,"
                            " actor_login, created_at) VALUES (?,?,?,?,?,?)",
                            (key, "pr", n, ev, login, _iso(day.replace(hour=hh))))
                    if n % 6 == 0:
                        conn.execute(
                            "INSERT INTO timeline_event (repo, item_type, number, event,"
                            " actor_login, created_at) VALUES (?,?,?,?,?,?)",
                            (key, "issue", n, "reopened", login,
                             _iso(day.replace(hour=17))))
                    for ri2 in range(1 + n % 3):
                        reviewer = logins[(pi + 1 + ri2) % len(logins)]
                        # CHANGES_REQUESTED is what "sent back for changes" and the
                        # rework-rounds figure count; approvals and plain comments do
                        # not, so a dataset with only those reads 0% there.
                        state = ("CHANGES_REQUESTED" if (n + ri2) % 4 == 0
                                 else "APPROVED" if ri2 == 0 else "COMMENTED")
                        conn.execute(
                            "INSERT OR REPLACE INTO review (repo, pr_number,"
                            " reviewer_login, state, submitted_at) VALUES (?,?,?,?,?)",
                            (key, n, reviewer, state,
                             _iso(day.replace(hour=12 + ri2))))
            day += timedelta(days=1)

        for w, row in enumerate(blob["_weeks"]):
            conn.execute(
                "INSERT OR REPLACE INTO snapshots (date, generated_at, lookback_days,"
                " totals, by_company) VALUES (?,?,?,?,?)",
                (row["date"], row["date"] + "T06:00:00Z", 0,
                 __import__("json").dumps({
                     "commits": row["commits"], "prs": row["prs"],
                     "specs": row["specs"],
                     "meaningful_additions": row["commits"] * 12,
                     "people": len(logins), "ai_commits": row["commits"] // 5}),
                 __import__("json").dumps({
                     c: {"commits": row["commits"] // 3, "prs": row["prs"] // 3,
                         "people": max(1, len(logins) // 4),
                         "meaningful_additions": row["commits"] * 4,
                         "ai_commits": row["commits"] // 15}
                     for c in blob["dir"]["companies"]})))

        for i, key in enumerate(keys):
            for d in range(14):
                dt = (end - timedelta(days=d)).strftime("%Y-%m-%d")
                conn.execute(
                    "INSERT OR REPLACE INTO traffic (repo, date, clones, clone_uniques,"
                    " views, view_uniques) VALUES (?,?,?,?,?,?)",
                    (key, dt, 12 - i % 6, 5 - i % 3, 70 - i * 4, 30 - i * 2))

        store.upsert_run(conn, blob)
        conn.commit()
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("person", "repo", "commits", "pull_request", "issue",
                            "review", "timeline_event", "snapshots", "traffic", "runs")}
    finally:
        conn.close()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", help="write to this SQLite file instead of the configured one")
    ap.add_argument("--anchor", help="last day of data, YYYY-MM-DD (default: today)")
    args = ap.parse_args()
    counts = seed(args.db, args.anchor)
    print("Demo data written:")
    for t, n in counts.items():
        print(f"  {t:14} {n}")
    print("\nEverything in it is invented. Start the portal to see it:")
    print("  python reportctl.py serve --port 8080")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
