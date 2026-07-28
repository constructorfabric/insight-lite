#!/usr/bin/env python3
"""Semantic-config discovery — scan the raw tables for the vocab an org actually
uses (labels, native Issue Types, Projects statuses, workflow names) and propose a
default mapping. Pure heuristics + SQL; NO GitHub calls (the raw collectors already
populated the DB).

Representation: taxonomy is stored as **item → bucket** maps, so a narrow scope can
override a SINGLE item (deep-merge per key) — the same label can resolve to a
different category per element/repo. See docs/semantic-config.md.

Scope-aware: `scan(conn, repos, project)` restricts the vocabulary to the repos (and
project) a scope actually covers, so you map the labels that really occur there.
"""
from __future__ import annotations

import json
import re

# Work-type categories mirror GitHub's native Issue Types (Bug / Feature / Task) plus
# Epic, and Spec / Docs / Test for the doc-ish labels. Order matters: more specific
# buckets (epic, spec) are tried before the broad ones (feature, task).
_CATEGORY_RULES = [
    ("bug", ["bug", "defect", "regression", "hotfix"]),
    ("epic", ["epic", "initiative"]),
    ("feature", ["feature", "story", "enhancement", "feat"]),
    ("spec", ["spec", "design doc", "adr", "prd", "rfc", "proposal"]),
    ("docs", ["doc", "readme"]),
    ("test", ["test", "e2e", "coverage", "qa"]),
    ("task", ["chore", "depend", "build", "release", "infra", "refactor",
              "tooling", "ci", "pipeline", "workflow", "maintenance", "task"]),
]
_TYPE_CATEGORY = {"bug": "bug", "feature": "feature", "story": "feature",
                  "epic": "epic", "task": "task"}
# Ordered delivery pipeline — backlog all the way to release.
_STAGE_RULES = [
    ("released", ["released", "deployed", "shipped", "live", "production", "in prod", "rolled out"]),
    ("done", ["done", "closed", "complete", "merged", "resolved"]),
    ("qa", ["qa", "testing", "verify", "validation", "uat"]),
    ("review", ["review"]),
    ("in_progress", ["progress", "in dev", "doing", "wip", "implement"]),
    ("ready", ["ready", "todo", "to do", "selected", "next", "up next"]),
    ("backlog", ["backlog", "triage", "to triage", "not started", "new", "icebox"]),
]
_CI_RULES = [
    ("ignore", ["cache", "cleanup", "dependency graph", "pages-build", "stale", "labeler"]),
    ("release", ["release", "publish", "deploy"]),
    ("nightly", ["nightly", "fuzz", "schedule", "cron"]),
    ("gate", ["ci", "build", "test", "lint", "clippy", "check", "code", "contract"]),
]
_STAGE_ORDER = ["backlog", "ready", "in_progress", "review", "qa", "done", "released"]
_PCT = re.compile(r"^\s*(\d{1,3})\s*%\s*$")


def _bucket(value, rules):
    v = (value or "").lower()
    for bucket, keys in rules:
        if any(k in v for k in keys):
            return bucket
    return None


def _stage_for(status):
    m = _PCT.match(status or "")
    if m:
        pct = int(m.group(1))
        return "backlog" if pct == 0 else "done" if pct >= 100 else "in_progress"
    return _bucket(status, _STAGE_RULES)


# ---- scan (scope-aware) ----------------------------------------------------
def _repo_clause(repos):
    """('' , ()) for no filter, else ('AND repo IN (?,?)', (r1,r2))."""
    if repos is None:
        return "", ()
    repos = list(repos)
    if not repos:
        return "AND 1=0", ()          # scope with no repos → empty vocab
    return "AND repo IN (%s)" % ",".join("?" * len(repos)), tuple(repos)


def _label_counts(conn, table, clause, params):
    out = {}
    for (val,) in conn.execute(
            f"SELECT labels FROM {table} WHERE labels<>'' AND labels<>'[]' {clause}", params):
        try:
            names = json.loads(val or "[]")
        except (ValueError, TypeError):
            continue
        for name in names:
            out[name] = out.get(name, 0) + 1
    return out


def _counts(conn, sql, params=()):
    return [{"name": r[0], "count": r[1]} for r in conn.execute(sql, params) if r[0]]


def scan(conn, repos=None, project=None):
    """Vocabulary present in the DB, optionally scoped to `repos` (a repo-key list)
    and — for a project scope — a `project`. Each item carries a usage count."""
    rc, rp = _repo_clause(repos)
    labels = _label_counts(conn, "issue", rc, rp)
    for name, n in _label_counts(conn, "pull_request", rc, rp).items():
        labels[name] = labels.get(name, 0) + n
    label_list = sorted(({"name": k, "count": v} for k, v in labels.items()),
                        key=lambda d: (-d["count"], d["name"]))
    issue_types = _counts(conn, f"SELECT issue_type, COUNT(*) FROM issue "
                          f"WHERE issue_type<>'' {rc} GROUP BY issue_type ORDER BY 2 DESC", rp)
    workflows = _counts(conn, f"SELECT workflow, COUNT(*) FROM ci_run "
                        f"WHERE workflow IS NOT NULL AND workflow<>'' {rc} "
                        f"GROUP BY workflow ORDER BY 2 DESC", rp)
    if project:
        statuses = _counts(conn, "SELECT status_raw, COUNT(*) FROM work_item_status "
                           "WHERE status_raw IS NOT NULL AND status_raw<>'' AND project=? "
                           "GROUP BY status_raw ORDER BY 2 DESC", (project,))
    else:
        statuses = _counts(conn, f"SELECT status_raw, COUNT(*) FROM work_item_status "
                           f"WHERE status_raw IS NOT NULL AND status_raw<>'' {rc} "
                           f"GROUP BY status_raw ORDER BY 2 DESC", rp)
    return {"labels": label_list, "issue_types": issue_types,
            "statuses": statuses, "workflows": workflows}


# ---- suggest (item → bucket) -----------------------------------------------
def suggest(scanned):
    """Heuristic item→bucket maps + the leftovers needing manual assignment."""
    labels_map, types_map, statuses_map, roles_map = {}, {}, {}, {}
    unmapped = {"labels": [], "issue_types": [], "statuses": [], "workflows": []}
    for t in scanned.get("issue_types", []):
        cat = _TYPE_CATEGORY.get(t["name"].lower())
        types_map[t["name"]] = cat if cat else None
        if not cat:
            unmapped["issue_types"].append(t["name"])
            del types_map[t["name"]]
    for lb in scanned.get("labels", []):
        cat = _bucket(lb["name"], _CATEGORY_RULES)
        (labels_map.__setitem__(lb["name"], cat) if cat
         else unmapped["labels"].append(lb["name"]))
    for s in scanned.get("statuses", []):
        stg = _stage_for(s["name"])
        (statuses_map.__setitem__(s["name"], stg) if stg
         else unmapped["statuses"].append(s["name"]))
    for w in scanned.get("workflows", []):
        role = _bucket(w["name"], _CI_RULES)
        (roles_map.__setitem__(w["name"], role) if role
         else unmapped["workflows"].append(w["name"]))
    config = {
        "categories": {"labels": labels_map, "types": types_map,
                       "prefer_source": ["issue_type", "label", "title"],
                       "unmatched": "uncategorized"},
        "stages": {"statuses": statuses_map, "order": _STAGE_ORDER,
                   "terminal": ["done"], "unmatched": "other"},
        "ci": {"roles": roles_map, "count_events": ["pull_request", "push"],
               "default_branch_only": True, "success_conclusions": ["success"],
               "failure_conclusions": ["failure", "timed_out", "startup_failure"],
               "ignore_conclusions": ["skipped", "cancelled", "neutral"]},
    }
    return {"config": config, "unmapped": unmapped}


# ---- scope helpers ---------------------------------------------------------
def repos_for_scope(conn, level, target):
    """(repo-key list | None, project | None) covered by a scope. None = all repos."""
    if level == "global" or not target:
        return None, None
    if level == "org":
        return [r[0] for r in conn.execute("SELECT key FROM repo WHERE org=?", (target,))], None
    if level == "element":
        return [r[0] for r in conn.execute("SELECT key FROM repo WHERE element=?", (target,))], None
    if level == "repo":
        return [target], None
    if level == "project":
        repos = [r[0] for r in conn.execute(
            "SELECT DISTINCT repo FROM work_item_status WHERE project=? AND repo IS NOT NULL",
            (target,))]
        return repos, target
    return None, None


def scope_targets(conn):
    """The pickable targets per level, for the editor's scope selector."""
    return {
        "org": [r[0] for r in conn.execute(
            "SELECT DISTINCT org FROM repo WHERE org<>'' ORDER BY org")],
        "element": [r[0] for r in conn.execute(
            "SELECT DISTINCT element FROM repo WHERE element<>'' ORDER BY element")],
        "repo": [r[0] for r in conn.execute("SELECT key FROM repo ORDER BY key")],
        "project": [r[0] for r in conn.execute(
            "SELECT DISTINCT project FROM work_item_status "
            "WHERE project IS NOT NULL ORDER BY project")],
    }


def discover(conn, repos=None, project=None):
    """scan + suggest for a scope in one call."""
    scanned = scan(conn, repos, project)
    result = suggest(scanned)
    result["scan"] = scanned
    return result
