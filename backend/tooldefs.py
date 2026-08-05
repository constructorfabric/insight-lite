#!/usr/bin/env python3
"""Shared read-only tool functions for the report data.

Single source of truth for the data-access "tools": the MCP server
(``mcp_server.py``) wraps these with ``@mcp.tool()`` for external clients, and the
in-process metrics chat (``chat_agent.py``) calls the same functions directly and
exposes them to the LLM. Every function is READ-ONLY and returns a plain ``dict``.

Docstrings and type hints are load-bearing: FastMCP derives the MCP tool schema
from them, and ``schema_for()`` derives the Gemini function-declaration from them.
Keep them accurate.
"""
from __future__ import annotations

import inspect
import re
from datetime import datetime, timezone

import discovery
import semantic_metrics
import store

_SELECT_RE = re.compile(r"(?is)^\s*(select|with)\b")


def _iso(d: str, end: bool = False) -> str:
    """'YYYY-MM-DD' | ISO | '' → UTC ISO. Empty since = all-time start, empty until = now."""
    d = (d or "").strip()
    if not d:
        return (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if end else "2008-01-01T00:00:00Z")
    if "T" in d:
        return d
    return d + ("T23:59:59Z" if end else "T00:00:00Z")


def _repos(conn, scope: str):
    """scope 'level:target' (org/element/repo/project) → repo-key list, or None (all)."""
    scope = (scope or "").strip()
    if not scope:
        return None
    level, _, target = scope.partition(":")
    if level not in ("org", "element", "repo", "project") or not target:
        raise ValueError("scope must be '<org|element|repo|project>:<target>'")
    repos, _proj = discovery.repos_for_scope(conn, level, target)
    return repos


# ---- tools -----------------------------------------------------------------
# Columns that carry a person's GitHub login, and therefore join person.login. Listed
# because the name differs per table (author_login / reviewer_login / actor_login / login)
# and guessing costs a tool round-trip.
_LOGIN_COLUMNS = ("author_login", "reviewer_login", "actor_login", "login")


def describe_schema() -> dict:
    """List the report database's tables and their columns, and how they join — start
    here to know what sql_query can select from (commits, pull_request, issue, ci_run,
    work_item_status, person, repo, traffic, review, snapshots, override, …). Read
    `joins` before writing a WHERE or a JOIN: the repo key is the trap."""
    conn = store.connect()
    out = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                             "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        out[t] = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
    conn.close()
    # The schema declares no foreign keys, so nothing in the columns above says which of
    # repo's two identifiers a `repo` column refers to — and the wrong guess does not
    # error. Measured on production: for all twelve tables with a `repo` column, joining
    # repo.key matches every row and joining repo.name matches ZERO. A query that filters
    # `repo IN (SELECT name FROM repo WHERE element=?)` therefore succeeds and returns an
    # empty result, which is how the assistant answered a question about an element with
    # nothing at all and then spent three more hops hunting for why.
    repo_tables = sorted(t for t, cols in out.items() if t != "repo" and "repo" in cols)
    login_tables = sorted(f"{t}.{c}" for t, cols in out.items() if t != "person"
                          for c in _LOGIN_COLUMNS if c in cols)
    return {"tables": out, "joins": {
        "repo": {
            "columns": [f"{t}.repo" for t in repo_tables],
            "join_to": "repo.key",
            "warning": "repo.key is the 'org/name' form and is what every `repo` column "
                       "holds. repo.name is the bare name and joins NOTHING — filtering "
                       "on it does not error, it silently returns zero rows. "
                       "list_dimension(kind='repos') returns keys, ready to use.",
        },
        "person": {"columns": login_tables, "join_to": "person.login"},
    }}


def sql_query(sql: str, limit: int = 200) -> dict:
    """Run a READ-ONLY SQL query (a single SELECT/WITH) over the report database
    and return up to `limit` rows. Writes and multiple statements are rejected.
    Use describe_schema() first. Dates are UTC ISO strings; is_bot/is_migration
    flag rows to usually exclude."""
    s = (sql or "").strip().rstrip(";").strip()
    if ";" in s:
        return {"error": "one statement only"}
    if not _SELECT_RE.match(s):
        return {"error": "only SELECT / WITH queries are allowed"}
    limit = max(1, min(int(limit), 2000))
    conn = store.connect()
    try:
        conn.execute("PRAGMA query_only=ON")
        cur = conn.execute(s)
        cols = [c[0] for c in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchmany(limit)]
    except Exception as exc:                       # noqa: BLE001
        return {"error": str(exc)}
    finally:
        conn.close()
    return {"columns": cols, "row_count": len(rows), "rows": rows}


def contribution(since: str = "", until: str = "", scope: str = "",
                 member_only: bool = False) -> dict:
    """Contribution KPIs for a window (and optional slice): totals (commits, LOC,
    PRs, specs, bugs, epics, features, people), by-company breakdown and %-by-category.
    Note: `features` is the issue count for the semantic 'feature' category.
    `since`/`until` are 'YYYY-MM-DD' (empty = all-time / now). `scope` slices to a
    repo subset, e.g. 'element:Insight', 'org:your-old-org', 'repo:org/name'."""
    conn = store.connect()
    try:
        agg = store.aggregate(conn, _iso(since), _iso(until, end=True),
                              member_only=member_only, repos=_repos(conn, scope))
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()
    return {"totals": agg["totals"],
            "by_company": [{k: c[k] for k in ("company", "people", "commits",
                            "meaningful_additions", "prs", "specs", "bugs",
                            "epics", "features", "pct")} for c in agg["company_rows"]],
            "categories": [{"key": c["key"], "title": c["title"], "total": c["total"],
                            "top3_pct": c["top3"]} for c in agg["categories"]]}


def delivery(since: str = "", until: str = "", scope: str = "") -> dict:
    """Taxonomy-derived delivery metrics for a window (and optional slice): issue
    category mix, close rate, defect rate, median time-to-close; PR merge/abandon
    rate, median size, reverts, reviewed rate; CI gate pass-rate and duration.
    `scope` slices like contribution()."""
    conn = store.connect()
    try:
        return semantic_metrics.window_block(conn, _iso(since), _iso(until, end=True),
                                             _repos(conn, scope))
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def person(login: str) -> dict:
    """Identity + GitHub-profile hint + all-time contribution dimension for one
    GitHub login (name, company, emails, surviving code, reviews, cpt)."""
    conn = store.connect()
    try:
        row = conn.execute("SELECT * FROM person WHERE login=?", (login,)).fetchone()
        if not row:
            return {"error": f"no person '{login}'"}
        d = dict(row)
        d["gh_profile"] = store.gh_profile(conn, login)
        return d
    finally:
        conn.close()


def list_dimension(kind: str = "elements") -> dict:
    """List a report dimension. kind ∈ {elements, repos, projects, companies,
    people}. Use the returned names as `scope` targets or in sql_query."""
    conn = store.connect()
    try:
        if kind == "repos":
            vals = [r["key"] for r in conn.execute("SELECT key FROM repo ORDER BY key")]
        elif kind == "elements":
            vals = [r[0] for r in conn.execute("SELECT DISTINCT element FROM repo "
                    "WHERE element<>'' ORDER BY element")]
        elif kind == "projects":
            vals = [r[0] for r in conn.execute("SELECT DISTINCT project FROM "
                    "work_item_status WHERE project IS NOT NULL ORDER BY project")]
        elif kind == "companies":
            vals = [r[0] for r in conn.execute("SELECT DISTINCT company FROM person "
                    "WHERE company<>'' ORDER BY company")]
        elif kind == "people":
            vals = [{"login": r["login"], "name": r["name"], "company": r["company"]}
                    for r in conn.execute("SELECT login, name, company FROM person "
                    "ORDER BY login")]
        else:
            return {"error": "kind must be elements/repos/projects/companies/people"}
        return {"kind": kind, "count": len(vals), "values": vals}
    finally:
        conn.close()


def taxonomy(level: str = "global", target: str = "") -> dict:
    """The effective semantic taxonomy resolved for a scope (which labels/native
    Issue Types → which categories, statuses → stages, workflows → CI roles), with
    per-item provenance. level ∈ {global, org, element, repo, project}."""
    import semantic_editor
    conn = store.connect()
    try:
        return semantic_editor.effective_data(conn, level, target)
    finally:
        conn.close()


def trend(since: str = "", until: str = "", scope: str = "",
          dim: str = "company", gran: str = "auto") -> dict:
    """Time series over a window (and optional slice), bucketed at day/week/month/
    quarter (gran='auto'|day|week|month|quarter scales to the span). Returns a shared
    date axis with commits & meaningful-LOC broken down by `dim`
    (company|work_type|repo_type|element), PR throughput (opened/merged + median
    time-to-merge) and active-contributor count. `since`/`until`/`scope` as in
    contribution(). Use this for "how did X change over time", not point totals."""
    conn = store.connect()
    try:
        return store.trend_block(conn, _iso(since), _iso(until, end=True),
                                 _repos(conn, scope), gran, dim)
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def flow(since: str = "", until: str = "", scope: str = "") -> dict:
    """Delivery-flow health for a window (and optional slice): reopen / bounce /
    re-request rates, code-review rounds, cycle-time segments (time-to-first-review,
    review-to-merge, time-to-merge, draft-to-ready, time-to-close), and a per-person
    friction table (2×(back-to-draft + reopened) + review-request & assignment churn,
    per owned item — lower is smoother). `scope` slices like contribution()."""
    conn = store.connect()
    try:
        return semantic_metrics.flow_report(conn, _repos(conn, scope),
                                            _iso(since), _iso(until, end=True))
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def list_items(entity: str = "commit", since: str = "", until: str = "",
               scope: str = "", author: str = "", company: str = "",
               flag: str = "", category: str = "", commit_type: str = "",
               pr_state: str = "", ai_tool: str = "", limit: int = 200) -> dict:
    """The rows behind a metric — the drill-through the report does when a number is
    clicked. entity ∈ {commit, pr, issue}. Optional filters: author (login), company,
    flag (a boolean column, e.g. is_bug / is_spec / ai_marked), category (issue only —
    a semantic category id from taxonomy()), commit_type (feat/fix/…), pr_state
    ('merged'|'abandoned'), ai_tool. Each row carries a GitHub URL. `since`/`until`/
    `scope` as in contribution(); newest first, capped at `limit` (max 1000)."""
    if entity not in ("commit", "pr", "issue"):
        return {"error": "entity must be commit, pr or issue"}
    if category and entity != "issue":
        return {"error": "category filter is issue-only"}
    try:
        limit = max(1, min(int(limit or 200), 1000))
    except (TypeError, ValueError):
        limit = 200
    conn = store.connect()
    try:
        repos = _repos(conn, scope)
        if category:
            return semantic_metrics.drill_issue_category(
                conn, _iso(since), _iso(until, end=True), category,
                repos=repos, limit=limit)
        return store.drill(conn, entity, _iso(since), _iso(until, end=True),
                           repos=repos, author=author, company=company, flag=flag,
                           commit_type=commit_type, pr_state=pr_state,
                           ai_tool=ai_tool, limit=limit)
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def views_catalog() -> dict:
    """The catalog of reusable visual components for building dashboards or artifacts
    — KPI tiles, charts, chips. Each entry has a purpose, when-to-use, parameters, a
    usage example and an html_contract (CSS classes + structure) so a component can be
    reproduced outside this repo. Use it to choose a display method for a number or
    trend before building a custom dashboard panel or a Claude artifact."""
    import view_registry
    return {"groups": [{"id": g, "title": t} for g, t in view_registry.GROUPS],
            "views": view_registry.all_views()}


def metrics_catalog() -> dict:
    """The catalog of every metric the report computes — name, group, description,
    exact formula, unit, a code snippet and where it's computed. Use it to learn how a
    number is defined (and cite it correctly) before quoting or comparing metrics."""
    import metrics_registry
    return {"groups": [{"id": g, "title": t} for g, t in metrics_registry.GROUPS],
            "metrics": metrics_registry.all_metrics()}


# ---- registry + schema introspection ---------------------------------------
# The tools an LLM (or MCP client) may call, in a sensible discovery order.
TOOLS = [
    metrics_catalog, describe_schema, list_dimension, taxonomy,
    contribution, delivery, trend, flow, person, list_items, sql_query,
    views_catalog,
]
DISPATCH = {fn.__name__: fn for fn in TOOLS}

_PY_TO_JSON = {str: "string", bool: "boolean", int: "integer", float: "number"}


def schema_for(fn) -> dict:
    """Build an OpenAPI-subset parameter schema for `fn` from its signature and
    annotations — the shape Gemini `FunctionDeclaration.parameters` wants. First
    line of the docstring is the function description."""
    props, required = {}, []
    for name, p in inspect.signature(fn).parameters.items():
        jtype = _PY_TO_JSON.get(p.annotation, "string")
        props[name] = {"type": jtype}
        if p.default is inspect.Parameter.empty:
            required.append(name)
    doc = inspect.getdoc(fn) or ""
    return {
        "name": fn.__name__,
        "description": " ".join(doc.split()),
        "parameters": {"type": "object", "properties": props, "required": required},
    }


def declarations() -> list[dict]:
    """Gemini function declarations for every tool."""
    return [schema_for(fn) for fn in TOOLS]
