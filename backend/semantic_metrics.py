#!/usr/bin/env python3
"""Metrics derived from the RAW collectors + the semantic taxonomy.

Every issue/PR/CI-run is interpreted through the taxonomy RESOLVED for its own
repo→element→org scope, so the same label can count as a different category per
element. Windowed, pure-derived (no GitHub) — changing the taxonomy re-derives
these instantly. Each metric is registered in the catalog (`/metrics`).
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta

import metrics_registry as _reg
import semantic


def _median(xs):
    return round(statistics.median(xs), 1) if xs else None


def _days_between(a: str, b: str):
    try:
        da = datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = datetime.fromisoformat(b.replace("Z", "+00:00"))
        d = (db - da).total_seconds() / 86400.0
        return d if d >= 0 else None
    except (ValueError, AttributeError):
        return None


def _repo_filter(repos):
    """SQL fragment + params to scope a query to a repo slice. None = all repos."""
    if repos is None:
        return "", ()
    repos = list(repos)
    if not repos:
        return " AND 1=0", ()
    return " AND repo IN (%s)" % ",".join("?" * len(repos)), tuple(repos)


def _resolver(conn):
    """repo → resolved taxonomy config, cached per repo (taxonomy varies by scope)."""
    layers = semantic.load_layers(conn)
    meta = {r["key"]: (r["org"], r["element"])
            for r in conn.execute("SELECT key, org, element FROM repo")}
    cache: dict = {}

    def resolved(repo):
        if repo not in cache:
            org, elem = meta.get(repo, ("", ""))
            cache[repo] = semantic.resolve(
                layers, {"org": org, "element": elem, "repo": repo})["config"]
        return cache[repo]
    return resolved


# unified taxonomy: the is_bug/is_feature/is_epic columns are a MATERIALIZED
# projection of the ONE resolver (semantic.categorize_issue). collect + reconfig call
# this so a taxonomy edit refreshes the whole report without a re-collect. The three
# headline issue tiles are Bugs / Epics / Features, mapping category -> flag:
#   'bug' -> is_bug, 'epic' -> is_epic, 'feature' -> is_feature.
def recategorize_issues(conn, repos=None) -> int:
    """Recompute is_bug/is_feature/is_epic on every issue from the live taxonomy.
    Returns the number of rows touched."""
    resolved = _resolver(conn)
    rf, rp = _repo_filter(repos)
    rows = conn.execute(
        "SELECT repo, number, labels, issue_type FROM issue WHERE 1=1" + rf, rp).fetchall()
    for r in rows:
        try:
            labels = json.loads(r["labels"] or "[]")
        except (ValueError, TypeError):
            labels = []
        cat = semantic.categorize_issue(resolved(r["repo"]), labels, r["issue_type"] or "")
        conn.execute(
            "UPDATE issue SET is_bug=?, is_feature=?, is_epic=? WHERE repo=? AND number=?",
            (1 if cat == "bug" else 0, 1 if cat == "feature" else 0,
             1 if cat == "epic" else 0, r["repo"], r["number"]))
    conn.commit()
    return len(rows)


def issue_metrics(conn, since: str, until: str, repos=None) -> dict:
    """Issue category mix + lifecycle for the window (optionally a repo slice).
    Categories come from the taxonomy resolved per issue's scope (type + labels)."""
    resolved = _resolver(conn)
    rf, rp = _repo_filter(repos)
    rows = conn.execute(
        "SELECT repo, labels, issue_type, created_at, closed_at FROM issue "
        "WHERE is_bot=0 AND is_migration=0 AND created_at>=? AND created_at<=?" + rf,
        (since, until) + rp).fetchall()
    by_cat: dict = {}
    ttc = []
    closed = 0
    for r in rows:
        try:
            labels = json.loads(r["labels"] or "[]")
        except (ValueError, TypeError):
            labels = []
        cat = semantic.categorize_issue(resolved(r["repo"]), labels, r["issue_type"] or "")
        by_cat[cat] = by_cat.get(cat, 0) + 1
        if r["closed_at"]:
            closed += 1
            d = _days_between(r["created_at"], r["closed_at"])
            if d is not None:
                ttc.append(d)
    total = len(rows)
    return {
        "issues_total": total,
        "issues_by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
        "issues_closed": closed,
        "issue_close_rate": round(closed / total * 100, 1) if total else None,
        "defect_rate": round(by_cat.get("bug", 0) / total * 100, 1) if total else None,
        "issue_median_time_to_close_days": _median(ttc),
    }


def drill_issue_category(conn, since: str, until: str, category: str,
                         repos=None, limit: int = 500, offset: int = 0) -> dict:
    """The individual issues whose RESOLVED taxonomy category == `category`, for the
    window (and optional repo slice). Same base filter as issue_metrics() so the count
    matches the delivery mix/defect tiles; shaped like store.drill (rows carry a GitHub
    URL). Newest first, capped. Categories are taxonomy-derived, not a SQL flag — hence
    this Python-side path rather than store.drill."""
    resolved = _resolver(conn)
    rf, rp = _repo_filter(repos)
    rows = conn.execute(
        "SELECT repo, number, author_login, labels, issue_type, state, closed_at, "
        "created_at, is_bug, is_feature, title FROM issue "
        "WHERE is_bot=0 AND is_migration=0 AND created_at>=? AND created_at<=?" + rf
        + " ORDER BY created_at DESC", (since, until) + rp).fetchall()
    out = []
    total = 0
    for r in rows:
        try:
            labels = json.loads(r["labels"] or "[]")
        except (ValueError, TypeError):
            labels = []
        if semantic.categorize_issue(resolved(r["repo"]), labels, r["issue_type"] or "") != category:
            continue
        total += 1
        if total - 1 < max(0, offset) or len(out) >= limit:
            continue
        kinds = [k for k, on in (("bug", r["is_bug"]), ("story", r["is_feature"])) if on]
        out.append({
            "repo": r["repo"], "ref": str(r["number"]), "author": r["author_login"],
            "date": (r["created_at"] or "")[:10], "short": f"#{r['number']}",
            "title": (r["title"] or "").strip(),
            "url": f"https://github.com/{r['repo']}/issues/{r['number']}",
            "meta": " · ".join(x for x in [(r["state"] or "").lower(),
                               r["issue_type"] or "", " ".join(kinds)] if x)})
    return {"entity": "issue", "total": total, "shown": len(out),
            "capped": total > len(out), "rows": out}


def drill_ci_runs(conn, since: str, until: str, repos=None, limit: int = 500, offset: int = 0) -> dict:
    """The individual GATE CI runs behind the CI pass-rate / duration tiles — same
    taxonomy filter as ci_metrics(), so the count matches ci_gate_runs. entity='ci';
    each row links to the workflow run on GitHub Actions. Newest first, capped."""
    resolved = _resolver(conn)
    rf, rp = _repo_filter(repos)
    rows = conn.execute(
        "SELECT repo, run_id, workflow, event, conclusion, duration_s, created_at "
        "FROM ci_run WHERE created_at>=? AND created_at<=?" + rf
        + " ORDER BY created_at DESC", (since, until) + rp).fetchall()
    out = []
    total = 0
    for r in rows:
        cfg = resolved(r["repo"])
        if semantic.ci_role(cfg, r["workflow"]) != "gate":
            continue
        ci = cfg.get("ci") or {}
        events = set(ci.get("count_events") or ["pull_request", "push"])
        ignore = set(ci.get("ignore_conclusions") or ["skipped", "cancelled", "neutral"])
        if r["event"] not in events or r["conclusion"] in ignore or not r["conclusion"]:
            continue
        total += 1
        if total - 1 < max(0, offset) or len(out) >= limit:
            continue
        dur = r["duration_s"]
        dur_txt = (f"{dur // 60}m{dur % 60:02d}s" if dur is not None and dur >= 60
                   else (f"{dur}s" if dur is not None else ""))
        out.append({
            "repo": r["repo"], "ref": str(r["run_id"]), "short": r["workflow"],
            "title": r["workflow"], "author": r["event"], "conclusion": r["conclusion"] or "",
            "duration": dur_txt, "date": (r["created_at"] or "")[:10],
            "url": f"https://github.com/{r['repo']}/actions/runs/{r['run_id']}",
            "meta": " · ".join(x for x in [r["conclusion"] or "", dur_txt] if x)})
    return {"entity": "ci", "total": total, "shown": len(out),
            "capped": total > len(out), "rows": out}


def drill_flow_stage(conn, stage: str, repos=None, limit: int = 500, offset: int = 0) -> dict:
    """The individual work items currently in one flow stage (latest Projects v2
    status per item, resolved via the taxonomy) — matches the Workflow panel counts.
    entity='flow'; each row links to the issue/PR on GitHub."""
    resolved = _resolver(conn)
    rf, rp = _repo_filter(repos)
    rows = conn.execute(
        "SELECT w.repo AS repo, w.number AS number, w.item_type AS item_type, "
        "w.status_raw AS status_raw, w.title AS title FROM work_item_status w "
        "JOIN (SELECT item_id, MAX(date) md FROM work_item_status GROUP BY item_id) x "
        "ON w.item_id=x.item_id AND w.date=x.md "
        "WHERE w.status_raw IS NOT NULL AND w.status_raw<>''"
        + rf.replace(" AND repo IN", " AND w.repo IN")
        + " ORDER BY w.repo, w.number", rp).fetchall()
    out, total = [], 0
    for r in rows:
        if semantic.stage_for(resolved(r["repo"] or ""), r["status_raw"] or "") != stage:
            continue
        total += 1
        if total - 1 < max(0, offset) or len(out) >= limit:
            continue
        repo, num = r["repo"] or "", r["number"]
        seg = "pull" if "pull" in (r["item_type"] or "").lower() else "issues"
        url = f"https://github.com/{repo}/{seg}/{num}" if (repo and num) else ""
        out.append({"repo": repo, "ref": ("#" + str(num)) if num else "—",
                    "title": r["title"] or "", "item_type": r["item_type"] or "",
                    "status": r["status_raw"] or "", "url": url})
    return {"entity": "flow", "total": total, "shown": len(out),
            "capped": total > len(out), "rows": out}


def ci_metrics(conn, since: str, until: str, repos=None) -> dict:
    """Pass-rate + duration over GATE workflows only (per the taxonomy), counting
    only the configured events/conclusions. Optionally scoped to a repo slice."""
    resolved = _resolver(conn)
    rf, rp = _repo_filter(repos)
    rows = conn.execute(
        "SELECT repo, workflow, event, conclusion, duration_s FROM ci_run "
        "WHERE created_at>=? AND created_at<=?" + rf, (since, until) + rp).fetchall()
    total = success = 0
    durations = []
    for r in rows:
        cfg = resolved(r["repo"])
        if semantic.ci_role(cfg, r["workflow"]) != "gate":
            continue
        ci = cfg.get("ci") or {}
        events = set(ci.get("count_events") or ["pull_request", "push"])
        succ = set(ci.get("success_conclusions") or ["success"])
        ignore = set(ci.get("ignore_conclusions") or ["skipped", "cancelled", "neutral"])
        if r["event"] not in events or r["conclusion"] in ignore or not r["conclusion"]:
            continue
        total += 1
        if r["conclusion"] in succ:
            success += 1
        if r["duration_s"] is not None:
            durations.append(r["duration_s"])
    return {
        "ci_gate_runs": total,
        "ci_pass_rate": round(success / total * 100, 1) if total else None,
        "ci_median_duration_s": int(statistics.median(durations)) if durations else None,
    }


def pr_metrics(conn, since: str, until: str, repos=None) -> dict:
    """PR quality/lifecycle for PRs opened in the window (optionally a repo slice)."""
    rf, rp = _repo_filter(repos)
    rows = conn.execute(
        "SELECT repo, number, created_at, review_requested_at, state, merged_at, additions, "
        "changed_files, review_count, is_revert, closes_issues FROM pull_request "
        "WHERE is_bot=0 AND is_migration=0 "
        "AND created_at>=? AND created_at<=?" + rf, (since, until) + rp).fetchall()
    # earliest review submission per PR (any state) — for reviewer response time
    first_rev: dict = {}
    for r in conn.execute(
        "SELECT repo, pr_number, MIN(submitted_at) f FROM review "
        "WHERE submitted_at IS NOT NULL AND submitted_at<>''" + rf
        + " GROUP BY repo, pr_number", rp):
        first_rev[(r["repo"], r["pr_number"])] = r["f"]
    total = merged = abandoned = reverts = linked = 0
    sizes, files, reviews, ttfr = [], [], [], []
    for r in rows:
        total += 1
        if r["merged_at"] or (r["state"] or "").upper() == "MERGED":
            merged += 1
        elif (r["state"] or "").upper() == "CLOSED":
            abandoned += 1                       # closed without merging
        if r["is_revert"]:
            reverts += 1
        if (r["closes_issues"] or 0) > 0:
            linked += 1
        if r["additions"] is not None:
            sizes.append(r["additions"])
        if r["changed_files"] is not None:
            files.append(r["changed_files"])
        if r["review_count"] is not None:
            reviews.append(r["review_count"])
        # time to first review = first review submitted − review requested (else opened)
        fr = first_rev.get((r["repo"], r["number"]))
        if fr:
            h = _hours_between(r["review_requested_at"] or r["created_at"], fr)
            if h is not None:
                ttfr.append(h)
    reviewed = sum(1 for x in reviews if x > 0)
    return {
        "prs_total": total,
        "pr_merge_rate": round(merged / total * 100, 1) if total else None,
        "pr_abandon_rate": round(abandoned / total * 100, 1) if total else None,
        "pr_median_additions": int(statistics.median(sizes)) if sizes else None,
        "pr_median_changed_files": int(statistics.median(files)) if files else None,
        "pr_reverts": reverts,
        "pr_linked_rate": round(linked / total * 100, 1) if total else None,
        "pr_reviewed_rate": round(reviewed / len(reviews) * 100, 1) if reviews else None,
        "pr_time_to_first_review_h": _median(ttfr), "pr_ttfr_n": len(ttfr),
    }


# ordered delivery pipeline for the workflow panel (matches the taxonomy stage axis)
_FLOW_STAGES = [
    ("backlog", "Backlog", "#9aa3b2"), ("ready", "Ready for dev", "#06b6d4"),
    ("in_progress", "In progress", "#8b5cf6"), ("review", "In review", "#f59e0b"),
    ("qa", "QA / Test", "#2f80ed"), ("done", "Done", "#10b981"),
    ("released", "Released", "#5b5bf0"),
]


def flow_metrics(conn, repos=None) -> dict:
    """Current workflow state: the latest Projects-v2 status snapshot per work item,
    resolved to a flow stage via the taxonomy, counted across the ordered pipeline.
    This is a NOW-state (not window-scoped) — status history isn't in GitHub's API."""
    resolved = _resolver(conn)
    rf, rp = _repo_filter(repos)
    rows = conn.execute(
        "SELECT w.repo AS repo, w.status_raw AS status_raw FROM work_item_status w "
        "JOIN (SELECT item_id, MAX(date) md FROM work_item_status GROUP BY item_id) x "
        "ON w.item_id=x.item_id AND w.date=x.md "
        "WHERE w.status_raw IS NOT NULL AND w.status_raw<>''" + rf.replace(" AND repo IN", " AND w.repo IN"),
        rp).fetchall()
    counts: dict = {}
    for r in rows:
        stg = semantic.stage_for(resolved(r["repo"] or ""), r["status_raw"] or "")
        counts[stg] = counts.get(stg, 0) + 1
    known = {k for k, _, _ in _FLOW_STAGES}
    stages = [{"key": k, "name": n, "color": c, "count": counts.get(k, 0)}
              for k, n, c in _FLOW_STAGES]
    return {"flow_stages": stages,
            "flow_total": sum(counts.values()),
            "flow_unmapped": sum(v for k, v in counts.items() if k not in known)}


_FLOW_BACKWARD = {"convert_to_draft", "reopened"}   # lifecycle bounces = rework
_FLOW_MIN_ITEMS = 3                                  # owned tracked items to be scored
# friction weights: hard bounces cost most; re-requesting review / re-assigning are
# softer churn. Combining several rare event types spreads people out more than a
# single binary "had a bounce" (which ties ~60% at zero in a healthy org).
_FLOW_BACK_W = 2.0
_FLOW_CHURN_W = 1.0


def person_flow(conn, repos=None, since=None, until=None) -> dict:
    """Retrospective flow FRICTION per person from issue/PR TIMELINE events (not the
    Projects-v2 board, which has no change-history). For each item a person owns (PR →
    author, issue → first assignee) we score friction = 2·bounces (convert_to_draft /
    reopened) + extra review-requests + extra assignments (churn beyond the first);
    a person's value is friction-per-item — LOWER is smoother flow. Combining several
    rare event types gives more spread than a binary no-bounce ratio. Window-scoped by
    event date when since/until are given (so it moves with the period like the other
    pillars). {login: friction/item} for people with ≥3 owned tracked items."""
    import json
    rf, rp = _repo_filter(repos)
    wf, wp = "", ()
    if since is not None and until is not None:
        wf, wp = " AND created_at>=? AND created_at<=?", (since, until)
    rows = conn.execute(
        "SELECT repo, number, event FROM timeline_event WHERE number IS NOT NULL" + rf + wf,
        rp + wp).fetchall()
    if not rows:
        return {}
    per_item: dict = {}
    for r in rows:
        d = per_item.setdefault((r["repo"], r["number"]), {"back": 0, "rr": 0, "asg": 0})
        ev = r["event"]
        if ev in _FLOW_BACKWARD:
            d["back"] += 1
        elif ev == "review_requested":
            d["rr"] += 1
        elif ev == "assigned":
            d["asg"] += 1
    pr_author = {(r["repo"], r["number"]): r["author_login"] for r in conn.execute(
        "SELECT repo, number, author_login FROM pull_request WHERE author_login<>''")}
    issue_owner: dict = {}
    for r in conn.execute("SELECT repo, number, assignees FROM issue"):
        try:
            a = json.loads(r["assignees"] or "[]")
        except (ValueError, TypeError):
            a = []
        issue_owner[(r["repo"], r["number"])] = a[0] if a else None
    friction: dict = {}
    items: dict = {}
    for key, d in per_item.items():
        owner = pr_author.get(key) or issue_owner.get(key)
        if not owner:
            continue
        f = (_FLOW_BACK_W * d["back"]
             + _FLOW_CHURN_W * (max(0, d["rr"] - 1) + max(0, d["asg"] - 1)))
        friction[owner] = friction.get(owner, 0.0) + f
        items[owner] = items.get(owner, 0) + 1
    return {lg: friction[lg] / items[lg]
            for lg in items if items[lg] >= _FLOW_MIN_ITEMS}


def drill_person_flow(conn, login, repos=None, since=None, until=None,
                      limit=500, offset=0) -> dict:
    """The board items behind a person's Flow pillar: each owned issue/PR with the
    lifecycle events it accrued, worst (highest friction) first. Mirrors person_flow's
    ownership + window rules so the list explains the number."""
    import json
    rf, rp = _repo_filter(repos)
    wf, wp = "", ()
    if since is not None and until is not None:
        wf, wp = " AND created_at>=? AND created_at<=?", (since, until)
    rows = conn.execute(
        "SELECT repo, number, item_type, event FROM timeline_event "
        "WHERE number IS NOT NULL" + rf + wf, rp + wp).fetchall()
    pr_author = {(r["repo"], r["number"]): r["author_login"] for r in conn.execute(
        "SELECT repo, number, author_login FROM pull_request WHERE author_login<>''")}
    issue_owner: dict = {}
    for r in conn.execute("SELECT repo, number, assignees FROM issue"):
        try:
            a = json.loads(r["assignees"] or "[]")
        except (ValueError, TypeError):
            a = []
        issue_owner[(r["repo"], r["number"])] = a[0] if a else None
    per: dict = {}
    for r in rows:
        key = (r["repo"], r["number"])
        owner = pr_author.get(key) or issue_owner.get(key)
        if owner != login:
            continue
        d = per.setdefault(key, {"item_type": r["item_type"], "cd": 0, "ro": 0, "rr": 0, "asg": 0})
        ev = r["event"]
        if ev == "convert_to_draft":
            d["cd"] += 1
        elif ev == "reopened":
            d["ro"] += 1
        elif ev == "review_requested":
            d["rr"] += 1
        elif ev == "assigned":
            d["asg"] += 1
    titles: dict = {}
    for r in conn.execute("SELECT repo, number, title FROM pull_request"):
        titles[("pr", r["repo"], r["number"])] = r["title"]
    for r in conn.execute("SELECT repo, number, title FROM issue"):
        titles[("issue", r["repo"], r["number"])] = r["title"]
    out = []
    for (repo, num), d in per.items():
        churn = max(0, d["rr"] - 1) + max(0, d["asg"] - 1)
        friction = 2 * (d["cd"] + d["ro"]) + churn
        is_pr = "pull" in (d["item_type"] or "").lower()
        parts = []
        if d["ro"]:
            parts.append(f"{d['ro']} reopened")
        if d["cd"]:
            parts.append(f"{d['cd']} back to draft")
        if max(0, d["rr"] - 1):
            parts.append(f"{d['rr'] - 1} extra review req")
        if max(0, d["asg"] - 1):
            parts.append(f"{d['asg'] - 1} reassigned")
        out.append({
            "repo": repo, "ref": f"#{num}", "title": titles.get(
                ("pr" if is_pr else "issue", repo, num)) or "",
            "item_type": "PR" if is_pr else "issue", "friction": friction,
            "detail": " · ".join(parts) or "clean",
            "url": f"https://github.com/{repo}/{'pull' if is_pr else 'issues'}/{num}"})
    out.sort(key=lambda x: (-x["friction"], x["repo"], x["ref"]))
    total = len(out)
    return {"entity": "flowitems", "total": total, "shown": min(limit, max(0, total - offset)),
            "capped": total > offset + limit, "rows": out[offset:offset + limit]}


def _hours_between(a: str, b: str):
    try:
        da = datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = datetime.fromisoformat(b.replace("Z", "+00:00"))
        h = (db - da).total_seconds() / 3600.0
        return h if h >= 0 else None
    except (ValueError, AttributeError):
        return None


_FLOW_PERSON_MIN = 3         # cohort items to appear in the per-person flow table

# ordered pipeline for detecting BACKWARD board moves; "dev" = anything before review
_STAGE_ORDER = {k: i for i, (k, _n, _c) in enumerate(_FLOW_STAGES)}
_STAGE_NAME = {k: n for k, n, _c in _FLOW_STAGES}
_DEV_STAGES = {"backlog", "ready", "in_progress"}   # "back to development" targets


def board_rewind_scan(conn, repos=None) -> dict:
    """Every qa→dev move the snapshots can show, with no window applied.

    This is the expensive half of board_rewinds and NONE of it depends on the window:
    the whole of work_item_status is read (141k rows on the Constructor org — it has to
    be, because a sequence cannot be reconstructed from a slice of itself), each row is
    resolved to a stage, and consecutive pairs are diffed. Only which of the resulting
    moves you COUNT depends on since/until.

    Split out because the Flow page needs two windows — this period and the one before
    it, for the tile's delta — and computing them separately meant doing this twice per
    request, ~400ms each, for two lists that differ only in a date comparison. Callers
    that want both pass the same scan to both board_rewinds() calls."""
    resolved = _resolver(conn)
    rf, rp = _repo_filter(repos)
    rows = [dict(r) for r in conn.execute(
        "SELECT taken_at, date, item_id, repo, number, item_type, status_raw, title "
        "FROM work_item_status WHERE status_raw IS NOT NULL" + rf
        + " ORDER BY item_id, taken_at", rp)]
    snaps = sorted({r["taken_at"] for r in rows})   # distinct snapshot instants

    seq: dict = {}
    for r in rows:
        raw = (r["status_raw"] or "").strip()
        if not raw:
            continue
        stg = semantic.stage_for(resolved(r["repo"] or ""), raw)
        seq.setdefault(r["item_id"], []).append((r["taken_at"], stg, r))

    pr_author = {(r["repo"], r["number"]): r["author_login"] for r in conn.execute(
        "SELECT repo, number, author_login FROM pull_request WHERE author_login<>''")}
    issue_owner: dict = {}
    for r in conn.execute("SELECT repo, number, assignees FROM issue"):
        try:
            a = json.loads(r["assignees"] or "[]")
        except (ValueError, TypeError):
            a = []
        issue_owner[(r["repo"], r["number"])] = a[0] if a else None
    names = {r["login"]: (r["name"] or r["login"])
             for r in conn.execute("SELECT login, name FROM person")}

    events = []
    for item_id, s in seq.items():
        for (_d0, a, _r0), (d1, b, r1) in zip(s, s[1:]):
            if a == "qa" and b in _DEV_STAGES:
                day = d1[:10]
                repo, num = r1["repo"], r1["number"]
                owner = pr_author.get((repo, num)) or issue_owner.get((repo, num))
                is_pr = "pull" in (r1["item_type"] or "").lower()
                events.append({
                    "repo": repo, "ref": (f"#{num}" if num else ""),
                    "title": r1["title"] or "",
                    "from": _STAGE_NAME.get(a, a), "to": _STAGE_NAME.get(b, b),
                    "date": day, "owner": owner, "owner_name": names.get(owner, owner or "—"),
                    "url": (f"https://github.com/{repo}/{'pull' if is_pr else 'issues'}/{num}"
                            if num else ""),
                })
    events.sort(key=lambda x: (x["date"], x["repo"], x["ref"]), reverse=True)
    days = sorted({s[:10] for s in snaps})
    return {
        "has_history": len(snaps) >= 2,
        "n_dates": len(snaps), "first_date": days[0] if days else None,
        "last_date": days[-1] if days else None,
        "events": events,
    }


def board_rewinds(conn, repos=None, since=None, until=None, scan=None) -> dict:
    """Workflow rewinds — items pushed BACKWARD on the board, specifically returned
    to development from testing (stage `qa` → in_progress / ready / backlog).

    Reconstructed by diffing consecutive DAILY board snapshots (work_item_status):
    GitHub Projects-v2 has no status-change history, so this only sees moves that
    happened between two snapshots we captured — forward-only from the first snapshot
    and sampled once a day (a qa→dev→qa within one day is missed). Treat counts as a
    FLOOR. Windowed by the date of the later snapshot; owner = PR author / issue
    first assignee, like the friction metric.

    `scan` accepts a board_rewind_scan() result to window instead of doing the scan
    itself — see that function for why a caller would hold one."""
    if scan is None:
        scan = board_rewind_scan(conn, repos)
    lo = since[:10] if since else None
    hi = until[:10] if until else None
    events = [e for e in scan["events"]
              if not ((lo and e["date"] < lo) or (hi and e["date"] > hi))]
    by_person: dict = {}
    for e in events:
        if e["owner"]:
            by_person[e["owner"]] = by_person.get(e["owner"], 0) + 1
    return {
        "has_history": scan["has_history"], "n_dates": scan["n_dates"],
        "first_date": scan["first_date"], "last_date": scan["last_date"],
        "qa_to_dev": len(events), "events": events, "by_person": by_person,
    }


def drill_board_rewinds(conn, repos=None, since=None, until=None,
                        limit=500, offset=0) -> dict:
    """The items behind the "returned to dev from testing" tile — each qa→dev board
    move with its owner and the day it was detected. Paginates board_rewinds()."""
    ev = board_rewinds(conn, repos, since, until)["events"]
    total = len(ev)
    rows = [{"repo": e["repo"], "ref": e["ref"], "title": e["title"],
             "owner": e["owner"] or "", "owner_name": e["owner_name"],
             "move": e["from"] + " → " + e["to"], "date": e["date"], "url": e["url"]}
            for e in ev[offset:offset + limit]]
    return {"entity": "rewinds", "total": total,
            "shown": min(limit, max(0, total - offset)),
            "capped": total > offset + limit, "rows": rows}


def board_cfd(conn, repos=None, since=None, until=None) -> dict:
    """Cumulative Flow Diagram data: how many board items sat in each stage on each
    DAILY snapshot date. Series are ordered bottom→top as released → … → backlog so
    completed work anchors the base and widening upper bands read as growing WIP.
    Same snapshot caveat as board_rewinds — forward-only, daily-sampled."""
    resolved = _resolver(conn)
    rf, rp = _repo_filter(repos)
    rows = [dict(r) for r in conn.execute(
        "SELECT taken_at, date, repo, status_raw FROM work_item_status "
        "WHERE status_raw IS NOT NULL" + rf, rp)]
    # several snapshots can share a day now — count only the LAST snapshot of each day
    latest: dict = {}
    for r in rows:
        if r["date"] not in latest or r["taken_at"] > latest[r["date"]]:
            latest[r["date"]] = r["taken_at"]
    per: dict = {}
    for r in rows:
        if r["taken_at"] != latest[r["date"]]:
            continue
        raw = (r["status_raw"] or "").strip()
        if not raw:
            continue
        stg = semantic.stage_for(resolved(r["repo"] or ""), raw)
        per.setdefault(r["date"], {})[stg] = per.setdefault(r["date"], {}).get(stg, 0) + 1
    lo = since[:10] if since else None
    hi = until[:10] if until else None
    dates = [d for d in sorted(per) if (not lo or d >= lo) and (not hi or d <= hi)]
    series = []
    for k, name, color in reversed(_FLOW_STAGES):     # released first → sits at the bottom
        vals = [per[d].get(k, 0) for d in dates]
        if any(vals):
            series.append({"company": name, "name": name, "key": k,
                           "color": color, "vals": vals})
    return {
        "has_data": len(dates) >= 2 and bool(series),
        "n_dates": len(dates), "dates": dates, "series": series,
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
    }


_TERMINAL_STAGES = {"done", "released"}   # "waiting" in these is meaningless


def stage_dwell(conn, repos=None, since=None, until=None) -> dict:
    """Time items spend in each board stage — two lenses:

    • AGE (waiting now): as of the latest snapshot, how long each current item has sat
      in its stage = latest_snapshot − item.updatedAt. Needs no history — available as
      soon as we capture updatedAt. Terminal stages (Done/Released) excluded.
    • DWELL (completed): how long items historically spent in a stage before moving on,
      from consecutive snapshots. A stage run is bounded by entry and exit; transition
      times use the item's updatedAt (≈ the actual move) when present, else the snapshot
      time. Only runs with an observed entry AND exit count; windowed by exit date.

    Honest limit: updatedAt bumps on ANY item edit, not strictly a status change, so
    both are close estimates, not exact; dwell for older history (no updatedAt) falls
    back to snapshot resolution."""
    resolved = _resolver(conn)
    rf, rp = _repo_filter(repos)
    rows = [dict(r) for r in conn.execute(
        "SELECT taken_at, updated_at, item_id, repo, status_raw FROM work_item_status "
        "WHERE status_raw IS NOT NULL" + rf + " ORDER BY item_id, taken_at", rp)]
    lo = since[:10] if since else None
    hi = until[:10] if until else None
    days = sorted({r["taken_at"][:10] for r in rows})
    ref = max((r["taken_at"] for r in rows), default=None)   # latest snapshot instant

    seq: dict = {}
    for r in rows:
        raw = (r["status_raw"] or "").strip()
        if not raw:
            continue
        stg = semantic.stage_for(resolved(r["repo"] or ""), raw)
        seq.setdefault(r["item_id"], []).append((r["taken_at"], stg, r["updated_at"]))

    dwell: dict = {}     # stage -> [hours]  (completed runs)
    age: dict = {}       # stage -> [hours]  (current items, as of ref)
    for _item, s in seq.items():
        # completed dwell: collapse into runs, timed by updatedAt when available
        runs, cur, enter_t, enter_u = [], s[0][1], s[0][0], s[0][2]
        for t, stg, u in s[1:]:
            if stg != cur:
                runs.append((cur, enter_u or enter_t, u or t)); cur, enter_t, enter_u = stg, t, u
        for i, (stg, in_t, out_t) in enumerate(runs):
            if i == 0:                        # first observed stage — entry unseen
                continue
            if (lo and out_t[:10] < lo) or (hi and out_t[:10] > hi):
                continue
            h = _hours_between(in_t, out_t)
            if h is not None:
                dwell.setdefault(stg, []).append(h)
        # age: only items present in the latest snapshot, in a non-terminal stage
        last_t, last_stg, last_u = s[-1]
        if ref and last_t == ref and last_stg not in _TERMINAL_STAGES:
            h = _hours_between(last_u or last_t, ref)
            if h is not None:
                age.setdefault(last_stg, []).append(h)

    all_dwell, all_age, stages = [], [], []
    for k, name, color in _FLOW_STAGES:
        ds, ag = dwell.get(k, []), age.get(k, [])
        if ds or ag:
            stages.append({"key": k, "name": name, "color": color,
                           "median_h": _median(ds), "avg_h": (round(sum(ds) / len(ds), 1) if ds else None),
                           "n": len(ds), "age_median_h": _median(ag), "n_current": len(ag)})
            all_dwell += ds; all_age += ag
    return {
        "has_data": bool(all_dwell or all_age),
        "dwell_median_h": _median(all_dwell), "dwell_n": len(all_dwell),
        "age_median_h": _median(all_age), "age_n": len(all_age),
        "age_max_h": (max(all_age) if all_age else None),
        "stages": stages, "n_dates": len(days), "first_date": days[0] if days else None,
    }


def _flow_item_facts(conn, repos=None, since=None, until=None) -> list:
    """Per-item lifecycle facts for the Flow cohort — the ONE load every flow number
    is derived from.

    Cohort = issues + PRs CREATED in [since, until] (optional repo slice; bots and
    migration PRs excluded). For each item we read its lifecycle: back-to-draft and
    reopened bounces, review-request churn, and the cycle-time segments between real
    events (created → first submitted review → merge, draft → ready, created → close).

    Returned as one record per item rather than as finished aggregates on purpose.
    Three consumers need the same cohort at different granularities — the headline
    rates (_flow_scalars), the per-person table, and the in-window mini-trends
    (flow_spark), which re-aggregates the SAME records over sub-windows. Had each
    re-derived its own numbers from SQL, a sparkline could disagree with the tile it
    sits under, which is the one failure a trend line must not have.

    Honest scope: these are LIFECYCLE segments from timeline events + PR timestamps,
    not per-board-column dwell time — GitHub Projects-v2 status carries no change
    history, so true "time in In-Progress/In-Review" is not derivable."""
    rf, rp = _repo_filter(repos)
    wf, wp = "", ()
    if since is not None and until is not None:
        wf, wp = " AND created_at>=? AND created_at<=?", (since, until)

    prs, issues = {}, {}
    for r in conn.execute(
        "SELECT repo, number, author_login, created_at, review_requested_at, merged_at, "
        "closed_at, state, title FROM pull_request "
        "WHERE is_bot=0 AND is_migration=0" + rf + wf, rp + wp):
        prs[(r["repo"], r["number"])] = dict(r)
    # First review actually SUBMITTED per PR. This — not review_requested_at — is what
    # "Open → first review" means to a reader: when a human looked, not when a bot
    # assigned one. review_requested_at is also unusable: collect.py hardcodes it to
    # None, so the column is empty on every row and both segments built on it came back
    # h=None, n=0 — two of the five cycle cards silently never rendered. Measured on
    # prod: requests cover 35% of PRs with p50 0.3h (39% land within a minute of
    # opening, i.e. auto-assignment), while submitted reviews cover 64% with p50 3.5h.
    first_review: dict = {}
    for r in conn.execute(
        "SELECT repo, pr_number, MIN(submitted_at) first_at FROM review "
        "WHERE IFNULL(submitted_at,'')<>'' GROUP BY repo, pr_number"):
        first_review[(r["repo"], r["pr_number"])] = r["first_at"]
    for r in conn.execute(
        "SELECT repo, number, assignees, created_at, closed_at, state_reason, title "
        "FROM issue WHERE is_bot=0" + rf + wf, rp + wp):
        issues[(r["repo"], r["number"])] = dict(r)

    keys = set(prs) | set(issues)
    if not keys:
        return []

    # lifecycle events for the cohort items (any date — a bounce counts wherever it fell)
    ev: dict = {}
    for r in conn.execute(
        "SELECT repo, number, event, created_at FROM timeline_event WHERE number IS NOT NULL"
        + rf, rp):
        key = (r["repo"], r["number"])
        if key not in keys:
            continue
        d = ev.setdefault(key, {"cd": 0, "ro": 0, "rr": 0, "asg": 0, "ready_at": None})
        e = r["event"]
        if e == "convert_to_draft":
            d["cd"] += 1
        elif e == "reopened":
            d["ro"] += 1
        elif e == "review_requested":
            d["rr"] += 1
        elif e == "assigned":
            d["asg"] += 1
        elif e == "ready_for_review":
            if d["ready_at"] is None or (r["created_at"] and r["created_at"] < d["ready_at"]):
                d["ready_at"] = r["created_at"]

    # CHANGES_REQUESTED reviews per cohort PR — the count of times a reviewer
    # explicitly asked for changes ("rework rounds"). A cleaner true-cycle proxy
    # than review count (which also includes plain comments / approvals). Reviews
    # are attributed to a PR by membership in the cohort, not by review date.
    cr: dict = {}
    for r in conn.execute(
        "SELECT repo, pr_number, COUNT(*) c FROM review WHERE state='CHANGES_REQUESTED'"
        + rf + " GROUP BY repo, pr_number", rp):
        key = (r["repo"], r["pr_number"])
        if key in prs:
            cr[key] = r["c"]

    def owner_of(key):
        if key in prs:
            return prs[key]["author_login"] or None
        try:
            a = json.loads(issues[key]["assignees"] or "[]")
        except (ValueError, TypeError):
            a = []
        return a[0] if a else None

    items = []
    for key in keys:
        e = ev.get(key) or {"cd": 0, "ro": 0, "rr": 0, "asg": 0, "ready_at": None}
        pr, iss = prs.get(key), issues.get(key)
        rec = {
            "key": key, "repo": key[0], "is_pr": pr is not None,
            "created_at": (pr or iss)["created_at"], "owner": owner_of(key),
            "has_ro": e["ro"] > 0, "has_cd": e["cd"] > 0,
            "extra_rr": max(0, e["rr"] - 1), "crn": cr.get(key, 0),
            "ttfr": None, "r2m": None, "ttm": None, "d2r": None, "ttc": None,
        }
        if pr:
            reviewed_at = first_review.get(key)
            if reviewed_at:
                rec["ttfr"] = _hours_between(pr["created_at"], reviewed_at)
            if pr["merged_at"]:
                rec["ttm"] = _hours_between(pr["created_at"], pr["merged_at"])
                if reviewed_at:
                    rec["r2m"] = _hours_between(reviewed_at, pr["merged_at"])
            if e["ready_at"]:
                rec["d2r"] = _hours_between(pr["created_at"], e["ready_at"])
        if iss and iss["closed_at"]:
            rec["ttc"] = _hours_between(iss["created_at"], iss["closed_at"])
        items.append(rec)
    # `keys` is a set, so the scan order is arbitrary. Sorting makes every aggregate
    # derived from these records — and the JSON the report ships — identical between
    # renders instead of depending on SQLite's row order.
    items.sort(key=lambda r: r["key"])
    return items


def _flow_scalars(items: list) -> dict:
    """The headline flow-health rates + cycle-time medians for a set of item facts.

    The single definition of those numbers: flow_report calls it for the whole
    cohort, flow_spark calls it once per sub-window, and flow_kpis calls it over the
    preceding window that the period-over-period deltas compare against. Rates only a
    PR can exhibit (back-to-draft, re-review) are still divided by ALL cohort items
    rather than by the PR count — that is how they have always been computed, and
    quietly changing the denominator would move every number the report has shown."""
    n = len(items)
    n_prs = sum(1 for r in items if r["is_pr"])
    reopened_n = sum(1 for r in items if r["has_ro"])
    bounced_n = sum(1 for r in items if r["has_cd"])
    rereq_n = sum(1 for r in items if r["extra_rr"])
    cr_prs = sum(1 for r in items if r["crn"])
    cr_rounds = sum(r["crn"] for r in items)

    def pct(k):
        return round(100.0 * k / n, 1) if n else 0.0

    def seg(k):
        vals = [r[k] for r in items if r[k] is not None]
        return {"h": _median(vals), "n": len(vals)}

    # public segment names on the left, the internal short names this module has
    # always used on the right
    cycle = {"ttfr": seg("ttfr"), "review_to_merge": seg("r2m"), "ttm": seg("ttm"),
             "draft_to_ready": seg("d2r"), "ttc": seg("ttc")}
    return {
        "n_items": n, "n_prs": n_prs, "n_issues": n - n_prs,
        "reopen_rate": pct(reopened_n), "reopened_n": reopened_n,
        "bounce_rate": pct(bounced_n), "bounced_n": bounced_n,
        "rereq_rate": pct(rereq_n), "rereq_n": rereq_n,
        "cr_rate": (round(100.0 * cr_prs / n_prs, 1) if n_prs else 0.0),
        "cr_prs": cr_prs, "cr_rounds": cr_rounds,
        "cycle": cycle,
        # flat aliases, so delta_map()/flow_spark() can address a cycle segment with a
        # single key the same way they address a rate
        **{f"cycle_{k}": v["h"] for k, v in cycle.items()},
    }


# the flow numbers that carry a period-over-period delta + a mini-trend, in tile order
FLOW_KPI_KEYS = ("cr_rate", "reopen_rate", "bounce_rate", "rereq_rate",
                 "cycle_ttfr", "cycle_review_to_merge", "cycle_ttm",
                 "cycle_draft_to_ready", "cycle_ttc")

# + the board-rewind count, which gets a delta but no sparkline: a mini-trend would
# mean re-diffing the snapshot history once per bucket, and the snapshots are the
# thinnest data on the page — eight noisy points would read as a signal.
FLOW_DELTA_KEYS = FLOW_KPI_KEYS + ("rewinds_qa_to_dev",)

_FLOW_SPARK_BUCKETS = 8      # sub-windows behind each flow tile's mini-trend
# Completed PRs a repo needs before its median is ranked. Ten, not three, because
# three was measured against real data and ranked noise: sorted slowest-first, the top
# two rows were repos with n=3 (184h and 136h medians) while the repo with n=200 sat
# last with the narrowest bar. A reader scans bar widths, not the n column, so the
# table presented its least trustworthy row as its headline finding.
#
# The cost of ten is 3% of the cohort (on that data: 6 repos kept of 22, covering
# 476 of 510 PRs). A threshold of 20 would also drop a genuinely active repo (n=18),
# and five keeps two-PR samples in the ranking, so ten is where the noise stops and
# real repositories start.
_CYCLE_BAR_REPO_MIN = 10
_CYCLE_BAR_REPOS = 6         # repos shown in the per-repo cycle breakdown


def flow_spark(items: list, since, until, nbuckets: int = _FLOW_SPARK_BUCKETS) -> dict:
    """Per-metric sparkline point-strings for the flow tiles: re-aggregate the SAME
    item facts over ~8 equal sub-windows (bucketed by each item's creation date), so
    every rate and median carries a mini-trend computed under the exact definition of
    the number above it. Mirrors delivery_spark's contract without its cost — the
    cohort is already in memory, so a trend line here adds no query.

    The window start is clamped forward to the earliest item when the requested
    `since` predates all the data. All-time is the DEFAULT period and its nominal
    start is 2008: without the clamp most buckets would be structurally empty and
    every sparkline would read as a flat line with a spike at the end — which asserts
    something false about the trend rather than nothing about it."""
    import store

    def day(s):
        try:
            return store._day(s)
        except (ValueError, TypeError):
            return None

    dated = [(d, r) for r, d in ((r, day(r["created_at"])) for r in items) if d]
    s0, s1 = day(since), day(until)
    if not dated or s0 is None or s1 is None:
        return {}
    s0 = max(s0, min(d for d, _r in dated))
    span = max((s1 - s0).days, 0)
    if span <= 0:
        return {}
    nb = min(nbuckets, span) or 1
    buckets: list = [[] for _ in range(nb)]
    for d, r in dated:
        b = int((d - s0).days / (span + 1) * nb)
        buckets[0 if b < 0 else (nb - 1 if b >= nb else b)].append(r)
    series: dict = {k: [] for k in FLOW_KPI_KEYS}
    for i, bucket in enumerate(buckets):
        if not bucket:
            continue    # nothing created in this slice: no rate, no median, no point
        m = _flow_scalars(bucket)
        for k in FLOW_KPI_KEYS:
            if m.get(k) is not None:
                series[k].append((i, m[k]))
    return {k + "_pts": _spark_xy(v, nb) for k, v in series.items()}


def _spark_xy(points: list, n: int, w: float = 100.0, h: float = 26.0, pad: float = 3.0) -> str:
    """SVG polyline for values at KNOWN bucket positions — store._spark_points' output,
    but taking (bucket index, value) pairs so a sub-window with nothing in it can be
    omitted rather than drawn.

    That distinction matters more here than it does on the activity sparklines. A slice
    of the period with no items has no median and no rate, and plotting 0 for it draws
    a cycle time collapsing to nothing — the opposite of what happened. Omitted points
    leave a straight line across the gap, which is how a line chart with missing
    samples already reads."""
    vals = [v for _i, v in points]
    if len(points) < 2 or n < 2 or max(vals) <= 0:
        return ""
    mx = max(vals)
    dx = w / (n - 1)
    return " ".join(f"{i * dx:.1f},{h - pad - (v / mx) * (h - 2 * pad):.1f}"
                    for i, v in points)


def flow_cycle_bar(items: list) -> dict:
    """The whole PR cycle as ONE length with its legs inside it, plus the same split
    per repository.

    This is the thing the five median cards above it cannot show. Each of those is
    measured over whichever PRs happen to have that segment, so they belong to
    different populations and a total assembled from them would be fiction. Here the
    cohort is fixed once — merged PRs that also got a review — and over THAT set
    open→review and review→merge add up to open→merge for every single pull request.

    The bar's LENGTH is the median total lead time. Its SPLIT is the mean of each pull
    request's own share of its own total — not the two leg medians laid end to end.
    That first version was wrong on real data in a way no tidy fixture reveals: median
    open→review was 2.8h and median review→merge 1.8h while the median total was 17.5h,
    so the bar claimed a 4.6h journey directly above a line reading 17.5h.

    The medians were not lying and neither was the total; adding medians was. Pull
    requests fall into two camps — a long wait for a first look then an immediate merge,
    or an instant look then days in review — and in each camp ONE leg is small. Taking
    each leg's median across both camps lands in the small values of both, while every
    individual total stays large.

    Shares fix it by construction: ttfr/ttm + r2m/ttm = 1 for every pull request, so
    the means of those two shares also sum to exactly 1 and the segments always fill
    the bar. A mean is safe here precisely because it averages a RATIO bounded in
    [0, 1] rather than hours — a five-day outlier contributes at most 1.0, the same as
    a two-hour one, so it cannot stretch the split the way a mean of durations would.
    The leg medians stay, reported as their own numbers instead of as bar widths.

    p75/p90 come from the same cohort's totals, because a median alone hides the tail
    people actually remember about a slow review."""
    # ttm > 0 is part of the cohort, not a separate filter applied later: a pull request
    # merged in the instant it was opened has no split to measure, and letting it into
    # the medians while leaving it out of the shares would re-create the two-populations
    # problem this panel exists to remove.
    full = [r for r in items
            if r["is_pr"] and r["ttfr"] is not None and r["r2m"] is not None
            and r["ttm"] is not None and r["ttm"] > 0]
    if not full:
        return {"has_data": False, "n": 0}

    def ttfr_share_of(rows):
        """Mean of each row's own first-review share of its own total lead time."""
        return sum(r["ttfr"] / r["ttm"] for r in rows) / len(rows)

    share = ttfr_share_of(full)
    legs = [{"key": "ttfr", "label": "Waiting for a first review",
             "sub": "opened → somebody looked", "h": _median([r["ttfr"] for r in full]),
             "pct": round(100.0 * share, 1), "color": "#f59e0b"},
            {"key": "review_to_merge", "label": "In review until merged",
             "sub": "first review → merged", "h": _median([r["r2m"] for r in full]),
             "pct": round(100.0 - 100.0 * share, 1), "color": "#2f80ed"}]
    totals = sorted(r["ttm"] for r in full)

    def q(p):
        return round(totals[min(len(totals) - 1, int(round((len(totals) - 1) * p)))], 1)

    drafts = [r["d2r"] for r in full if r["d2r"] is not None]
    by_repo, groups = [], {}
    for r in full:
        groups.setdefault(r["repo"], []).append(r)
    for repo, rs in groups.items():
        if len(rs) < _CYCLE_BAR_REPO_MIN:
            continue
        by_repo.append({"repo": repo, "n": len(rs),
                        "ttfr_h": _median([x["ttfr"] for x in rs]),
                        "r2m_h": _median([x["r2m"] for x in rs]),
                        "total_h": _median([x["ttm"] for x in rs]),
                        "ttfr_share": ttfr_share_of(rs)})
    by_repo.sort(key=lambda x: (-(x["total_h"] or 0), x["repo"]))
    return {
        "has_data": True, "n": len(full), "legs": legs,
        "ttfr_share": round(share, 4), "median_total_h": _median(totals),
        "p75_total_h": q(0.75), "p90_total_h": q(0.90),
        "draft": ({"n": len(drafts), "h": _median(drafts)} if drafts else None),
        "by_repo": by_repo[:_CYCLE_BAR_REPOS], "repos_shown": min(len(by_repo), _CYCLE_BAR_REPOS),
        "repos_total": len(groups), "repo_min": _CYCLE_BAR_REPO_MIN,
    }


def flow_kpis(conn, repos=None, since=None, until=None, rewind_scan=None) -> dict:
    """The scalar flow numbers for one window — the same definitions flow_report puts
    on the tiles, computed over the PRECEDING window so those tiles can carry a
    period-over-period delta (delivery_metrics plays this role for Delivery).

    Includes the QA→dev rewind count, which is not part of the cohort aggregate but is
    the one number on the page a reader could least do anything with on its own: "15
    returned to dev" only becomes information next to what it was last period.

    `rewind_scan` is passed straight to board_rewinds — see board_rewind_scan."""
    out = _flow_scalars(_flow_item_facts(conn, repos, since, until))
    out["rewinds_qa_to_dev"] = (board_rewinds(conn, repos, since, until, rewind_scan)
                                .get("qa_to_dev") or 0)
    return out


def flow_report(conn, repos=None, since=None, until=None, rewind_scan=None) -> dict:
    """The Flow tab dataset — retrospective delivery-flow health that EXPLAINS what
    "friction" means and adds the metrics the timeline stream makes possible.

    The cohort, the sources and the honest scope of every segment are documented on
    _flow_item_facts, which does the single load this aggregates. The per-person
    `friction` column reuses person_flow() so it matches the Developer-score card.

    Also carries the board-movement views (cumulative flow, time-in-stage, QA→dev
    rewinds) so the Flow tab is the single home for how work MOVES and how long it
    takes; Delivery keeps the point-in-time output + current board state.

    `rewind_scan` is passed straight to board_rewinds — a caller that also wants the
    preceding window's rewinds hands the same scan to both and pays for it once."""
    board = {"cfd": board_cfd(conn, repos, since, until),
             "dwell": stage_dwell(conn, repos, since, until),
             "rewinds": board_rewinds(conn, repos, since, until, rewind_scan)}
    items = _flow_item_facts(conn, repos, since, until)
    if not items:
        return {"has_data": False, **board}

    ppl: dict = {}
    for r in items:
        if not r["owner"]:
            continue
        p = ppl.setdefault(r["owner"], {"items": 0, "ro": 0, "cd": 0, "rr": 0,
                                        "cr": 0, "cr_prs": 0, "ttm": [], "ttfr": []})
        p["items"] += 1
        p["ro"] += 1 if r["has_ro"] else 0
        p["cd"] += 1 if r["has_cd"] else 0
        p["rr"] += r["extra_rr"]
        p["cr"] += r["crn"]
        p["cr_prs"] += 1 if r["crn"] else 0
        if r["ttm"] is not None:
            p["ttm"].append(r["ttm"])
        if r["ttfr"] is not None:
            p["ttfr"].append(r["ttfr"])

    friction_map = person_flow(conn, repos, since, until)
    names = {r["login"]: (r["name"] or r["login"])
             for r in conn.execute("SELECT login, name FROM person")}
    people = []
    for lg, p in ppl.items():
        if p["items"] < _FLOW_PERSON_MIN:
            continue
        people.append({
            "login": lg, "name": names.get(lg, lg), "items": p["items"],
            "friction": (round(friction_map[lg], 2) if lg in friction_map else None),
            "reopen_pct": round(100.0 * p["ro"] / p["items"]),
            "bounce_pct": round(100.0 * p["cd"] / p["items"]),
            "extra_reqs": p["rr"],
            "cr_rounds": p["cr"], "cr_prs": p["cr_prs"],
            "ttm_med": _median(p["ttm"]), "ttfr_med": _median(p["ttfr"]),
        })
    # login is the final, unique tiebreaker: without it, people with equal
    # friction/reopen/items keep ppl.items() insertion order, which follows an
    # unordered SQL scan and so varies between renders (non-deterministic HTML).
    people.sort(key=lambda x: (x["friction"] is None, -(x["friction"] or 0),
                               -x["reopen_pct"], -x["items"], x["login"]))

    return {
        "has_data": True, **_flow_scalars(items),
        "cycle_bar": flow_cycle_bar(items),
        "spark": flow_spark(items, since, until),
        # lifted out of the nested board block so delta_map() can address it by key
        "rewinds_qa_to_dev": board["rewinds"].get("qa_to_dev") or 0,
        "min_items": _FLOW_PERSON_MIN, "people": people, **board,
    }


_reg.register_for(flow_report, [
    _reg.metric("flow_returned_to_dev", type="computed", group="flow",
                desc="Items pushed backward on the board from testing to development "
                     "(stage qa → in_progress/ready/backlog), reconstructed by diffing "
                     "consecutive daily board snapshots. Forward-only from the first snapshot "
                     "and sampled once a day — a floor, not an exact count.",
                formula="count consecutive snapshot pairs where stage goes qa -> a dev stage",
                snippet="if prev=='qa' and cur in {backlog,ready,in_progress}: rewinds += 1"),
    _reg.metric("flow_reopen_rate", type="computed", group="flow", unit="%",
                desc="Share of cohort items (issues + PRs created in the window) that were "
                     "reopened at least once — work that came back after being closed.",
                formula="items with >=1 reopened timeline event / cohort items * 100",
                snippet="if reopened_events: reopened_n += 1"),
    _reg.metric("flow_bounce_rate", type="computed", group="flow", unit="%",
                desc="Share of cohort PRs sent back to draft at least once "
                     "(convert_to_draft) — a review-readiness miss.",
                formula="PRs with >=1 convert_to_draft event / cohort items * 100",
                snippet="if convert_to_draft_events: bounced_n += 1"),
    _reg.metric("flow_rereview_rate", type="computed", group="flow", unit="%",
                desc="Share of cohort PRs where review was re-requested (more than one "
                     "review_requested event) — a rework-churn proxy.",
                formula="PRs with review_requested count > 1 / cohort items * 100",
                snippet="extra_rr = max(0, review_requested_count - 1)"),
    _reg.metric("flow_changes_requested_rate", type="computed", group="flow", unit="%",
                desc="Share of cohort PRs a reviewer explicitly sent back for changes at least "
                     "once (a CHANGES_REQUESTED review). The cleanest true rework signal — a "
                     "reviewer said “needs work”, not just commented.",
                formula="PRs with >=1 CHANGES_REQUESTED review / cohort PRs * 100",
                snippet="cr = COUNT reviews WHERE state='CHANGES_REQUESTED' GROUP BY pr"),
    _reg.metric("flow_rework_rounds", type="direct", group="flow",
                desc="Total rework rounds — the count of CHANGES_REQUESTED reviews across a "
                     "person's cohort PRs. Each is one explicit “send back for changes”, the "
                     "closest proxy we have to review→fix cycles without push-level history.",
                formula="SUM of CHANGES_REQUESTED reviews over the person's cohort PRs",
                snippet="p['cr'] += changes_requested_count_for(pr)"),
    _reg.metric("flow_time_to_first_review", type="computed", group="flow", unit="hours",
                desc="Median time from PR opened to the first review actually SUBMITTED — how "
                     "long until a human looked. Measured from review.submitted_at, not from a "
                     "review REQUEST: requests are usually auto-assigned (39% land within a "
                     "minute of opening), so timing them measures the bot, and the column they "
                     "would come from (pull_request.review_requested_at) is never populated.",
                formula="median(first review.submitted_at - created_at) over cohort PRs",
                snippet="SELECT MIN(submitted_at) FROM review GROUP BY repo, pr_number"),
    _reg.metric("flow_review_to_merge", type="computed", group="flow", unit="hours",
                desc="Median time from the first submitted review to merge — the review-and-land "
                     "leg. Same source as flow_time_to_first_review, for the same reason.",
                formula="median(merged_at - first review.submitted_at) over merged cohort PRs",
                snippet="r2m.append(hours(first_review_submitted_at, merged_at))"),
    _reg.metric("flow_time_to_merge", type="computed", group="flow", unit="hours",
                desc="Median time from PR opened to merge, over PRs created in the window.",
                formula="median(merged_at - created_at) over merged cohort PRs",
                snippet="ttm.append(hours(created_at, merged_at))"),
    _reg.metric("flow_draft_to_ready", type="computed", group="flow", unit="hours",
                desc="Median time a PR spent in draft before being marked ready for review.",
                formula="median(ready_for_review_event - created_at) over cohort PRs",
                snippet="d2r.append(hours(created_at, ready_for_review_at))"),
    _reg.metric("flow_lead_time_decomposed", type="computed", group="flow", unit="hours",
                desc="Median total PR lead time, split into the share spent waiting for a "
                     "first review versus in review until merged. One cohort: PRs that were "
                     "reviewed and merged with a non-zero lead time, so the two legs add up "
                     "to the total for every single PR. The split is the MEAN of each PR's "
                     "own share of its own total, which sums to 100% by construction — "
                     "adding the two leg medians does not, and on real data understated the "
                     "journey four-fold, because PRs are slow in one leg or the other and "
                     "rarely both. A mean is safe here only because it averages a ratio in "
                     "[0,1], not hours. Leg medians and p75/p90 of the total are reported "
                     "beside it; also split per repository (≥3 qualifying PRs).",
                formula="cohort = reviewed AND merged AND ttm>0; length = median(ttm); "
                        "split = mean(ttfr_i / ttm_i) and its complement",
                snippet="share = sum(r['ttfr'] / r['ttm'] for r in full) / len(full)"),
    _reg.metric("flow_kpi_trend", type="computed", group="flow", unit="series",
                desc="The mini-trend under each flow tile: the same rate or median "
                     "re-aggregated over ~8 equal sub-windows of the selected period, from "
                     "the same per-item facts as the headline, so a sparkline can never "
                     "disagree with the number above it. The window start is clamped to the "
                     "first item's date, or all-time trends would be mostly empty buckets.",
                formula="for each sub-window: _flow_scalars(items created in it)",
                snippet="buckets[int((d - s0).days / (span + 1) * nb)].append(r)"),
    _reg.metric("flow_friction_per_item", type="computed", group="flow",
                desc="Per-person friction/item — 2×(back-to-draft + reopened) + review-request "
                     "and assignment churn, averaged over owned items. The Flow pillar of the "
                     "Developer score; lower is smoother.",
                formula="person_flow(): sum(2*bounces + churn) / owned items",
                snippet="friction = 2*(cd+ro) + max(0,rr-1) + max(0,asg-1)"),
])


# the scalar Delivery KPIs that carry a period-over-period delta + a sparkline
DELIVERY_KPI_KEYS = (
    "issues_total", "issue_close_rate", "defect_rate", "issue_median_time_to_close_days",
    "prs_total", "pr_merge_rate", "pr_abandon_rate", "pr_median_additions",
    "pr_reviewed_rate", "pr_time_to_first_review_h", "pr_reverts",
    "ci_pass_rate", "ci_median_duration_s")


def delivery_metrics(conn, since: str, until: str, repos=None) -> dict:
    """The scalar Delivery KPIs (issues + PRs + CI) for one window — the numbers that
    get tiles. Used both for the window itself and, over the preceding window, for the
    period-over-period deltas."""
    return {**issue_metrics(conn, since, until, repos),
            **pr_metrics(conn, since, until, repos),
            **ci_metrics(conn, since, until, repos)}


def delivery_spark(conn, since: str, until: str, repos=None, nbuckets: int = 8) -> dict:
    """Per-metric sparkline point-strings for the Delivery KPIs: replay the metric
    functions over ~8 equal sub-windows so every tile (count, rate or median) gets a
    correct mini-trend, using the exact same definitions as the headline number."""
    import store
    try:
        s0, s1 = store._day(since), store._day(until)
    except (ValueError, TypeError):
        return {}
    span = max((s1 - s0).days, 0)
    if span <= 0:
        return {}
    nb = min(nbuckets, span) or 1
    series: dict = {k: [] for k in DELIVERY_KPI_KEYS}
    for i in range(nb):
        bs = (s0 + timedelta(days=round(span * i / nb))).strftime("%Y-%m-%dT00:00:00Z")
        be = (s0 + timedelta(days=round(span * (i + 1) / nb))).strftime("%Y-%m-%dT00:00:00Z")
        m = delivery_metrics(conn, bs, be, repos)
        for k in DELIVERY_KPI_KEYS:
            series[k].append(m.get(k) or 0)
    return {k + "_pts": store._spark_points(v) for k, v in series.items()}


def window_block(conn, since: str, until: str, repos=None) -> dict:
    """Everything the Delivery panels need for one period + optional repo slice."""
    return {**issue_metrics(conn, since, until, repos),
            **pr_metrics(conn, since, until, repos),
            **ci_metrics(conn, since, until, repos),
            **flow_metrics(conn, repos),
            "spark": delivery_spark(conn, since, until, repos)}


_reg.register_for(stage_dwell, [
    _reg.metric("stage_dwell_time", type="computed", group="flow", unit="hours",
                desc="Median time an item spends in each board stage before moving on "
                     "(time between statuses), reconstructed from consecutive board "
                     "snapshots. Snapshot-resolution; only runs with an observed entry AND "
                     "exit count.",
                formula="median over items of (first snapshot in next stage − first snapshot in stage)",
                snippet="dwell = hours(entered_at, exited_at) per observed stage run"),
])


_reg.register_for(board_cfd, [
    _reg.metric("board_cfd", type="computed", group="flow", unit="rollup",
                desc="Cumulative Flow Diagram — count of board items per stage on each daily "
                     "snapshot date, for the stacked-area chart. Reconstructed from "
                     "work_item_status snapshots (forward-only, daily-sampled).",
                formula="per snapshot date: COUNT items grouped by stage_for(resolved(repo), status)",
                snippet="per[date][stage] += 1"),
])


_reg.register_for(flow_metrics, [
    _reg.metric("flow_stages", type="computed", group="flow", unit="rollup",
                desc="Current workflow state — the latest Projects v2 status of each work item, "
                     "resolved to a flow stage (Backlog → Released) via the taxonomy. A snapshot "
                     "of where work sits now, not window-scoped.",
                formula="latest status_raw per item -> stage_for(resolved(repo)); count per stage",
                snippet="stage = semantic.stage_for(resolved(repo), latest_status_raw)"),
])


_reg.register_for(issue_metrics, [
    _reg.metric("issues_by_category", type="computed", group="delivery",
                desc="Issues opened in the window, split by the taxonomy category "
                     "resolved for each issue's scope (native Issue Type + labels).",
                formula="group issues by categorize_issue(resolved(repo), labels, type)",
                snippet="cat = semantic.categorize_issue(resolved(repo), labels, issue_type)"),
    _reg.metric("issue_close_rate", type="computed", group="delivery", unit="%",
                desc="Share of window-opened issues that have been closed.",
                formula="issues_closed / issues_total * 100",
                snippet="closed += 1 if row['closed_at'] else 0"),
    _reg.metric("defect_rate", type="computed", group="delivery", unit="%",
                desc="Share of window issues resolving to the 'bug' category.",
                formula="by_category['bug'] / issues_total * 100",
                snippet="by_cat.get('bug', 0) / total * 100"),
    _reg.metric("issue_median_time_to_close_days", type="computed", group="delivery",
                unit="days",
                desc="Median days from open to close, for issues closed in the window.",
                formula="median(closed_at - created_at) in days",
                snippet="ttc.append(_days_between(created_at, closed_at))"),
])

_reg.register_for(pr_metrics, [
    _reg.metric("pr_merge_rate", type="computed", group="delivery", unit="%",
                desc="Share of window-opened PRs that merged.",
                formula="merged / prs_total * 100",
                snippet="if merged_at or state=='MERGED': merged += 1"),
    _reg.metric("pr_abandon_rate", type="computed", group="delivery", unit="%",
                desc="Share of window PRs closed without merging (abandoned).",
                formula="closed_unmerged / prs_total * 100",
                snippet="elif state=='CLOSED': abandoned += 1"),
    _reg.metric("pr_median_additions", type="direct", group="delivery",
                desc="Median additions per PR (median is robust to vendored/generated "
                     "outliers).",
                formula="median(additions)", snippet="statistics.median(sizes)"),
    _reg.metric("pr_median_changed_files", type="direct", group="delivery",
                desc="Median changed files per PR.",
                formula="median(changed_files)", snippet="statistics.median(files)"),
    _reg.metric("pr_reverts", type="direct", group="delivery",
                desc="PRs whose title starts with 'Revert' — an instability signal.",
                formula="COUNT PRs where is_revert=1", snippet="if is_revert: reverts += 1"),
    _reg.metric("pr_linked_rate", type="computed", group="delivery", unit="%",
                desc="Share of PRs that close at least one issue (traceability).",
                formula="linked / prs_total * 100", snippet="if closes_issues > 0: linked += 1"),
    _reg.metric("pr_reviewed_rate", type="computed", group="delivery", unit="%",
                desc="Share of PRs with at least one review.",
                formula="reviewed / prs_with_review_data * 100",
                snippet="reviewed = sum(1 for x in reviews if x > 0)"),
    _reg.metric("pr_time_to_first_review_h", type="computed", group="delivery", unit="hours",
                desc="Median reviewer response time — from review requested (else PR opened) to "
                     "the first review submitted, over PRs opened in the window that got a review.",
                formula="median(first review.submitted_at − review_requested_at) over reviewed PRs",
                snippet="ttfr.append(hours(review_requested_at or created_at, first_review))"),
])

_reg.register_for(ci_metrics, [
    _reg.metric("ci_pass_rate", type="computed", group="flow", unit="%",
                desc="Green rate over GATE workflows (per taxonomy) on the configured "
                     "events/conclusions — scheduled/skipped runs excluded.",
                formula="gate_success / gate_runs * 100",
                snippet="if semantic.ci_role(cfg, workflow) != 'gate': continue"),
    _reg.metric("ci_gate_runs", type="direct", group="flow",
                desc="Count of gate-workflow runs counted toward pass-rate.",
                formula="COUNT gate runs in window on counted events",
                snippet="total += 1"),
    _reg.metric("ci_median_duration_s", type="direct", group="flow", unit="s",
                desc="Median wall-clock duration of gate runs.",
                formula="median(duration_s) over gate runs",
                snippet="durations.append(row['duration_s'])"),
])
