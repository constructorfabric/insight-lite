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

import difflib
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
        if t in BLOCKED_TABLES:
            continue
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


# What a failed query gets told back. Every one of the fourteen sql_query failures in the
# production transcript was a name or dialect guess — `author` for author_login, `pr` for
# pull_request, `commit` (a reserved word) for commits, ILIKE and information_schema from
# Postgres — and each one came back as the bare sqlite message: "no such column: author".
# That costs a tool round-trip and teaches nothing, so the model would call describe_schema
# again, or SELECT sql FROM sqlite_master, or simply try another guess. An error that names
# the alternatives turns a wasted hop into a corrective one.
_IDENT = re.compile(r"\b(?:from|join|update|into)\s+([a-z_][a-z0-9_]*)", re.I)

# Tables the assistant must not read. `secret` holds credentials as key/value — on
# production, the MCP API token — and sql_query accepts any statement beginning with
# SELECT, so the chat could be asked for it and would run the query. No analytical question
# needs it. Enforced with SQLite's authorizer rather than by pattern-matching the SQL,
# because the authorizer sees the resolved table name: quoting, aliases, views, CTEs and
# subqueries cannot route around it. Also withheld from describe_schema and from the
# assistant's grounding, so it is neither offered nor advertised.
BLOCKED_TABLES = frozenset({"secret"})


def _deny_blocked(action, arg1, arg2, dbname, source):
    """SQLite authorizer: refuse any read of a blocked table."""
    import sqlite3
    if action == sqlite3.SQLITE_READ and (arg1 or "").lower() in BLOCKED_TABLES:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _sql_hint(conn, sql: str, msg: str) -> dict:
    """Extra fields for a failed sql_query: what the right names are, when we can tell."""
    hint = {}
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name") if r[0] not in BLOCKED_TABLES]
    m = re.search(r"no such table:\s*([\w.]+)", msg)
    if m:
        bad = m.group(1)
        hint["tables"] = tables
        near = difflib.get_close_matches(bad, tables, n=3, cutoff=0.5)
        # difflib alone misses the abbreviation, which is the commonest guess of all: 'pr'
        # scores far too low against 'pull_request' to survive any usable cutoff. Match the
        # bad name against the initials of a table's underscore-separated words as well.
        acronym = [t for t in tables
                   if "".join(w[0] for w in t.split("_") if w) == bad.lower()]
        if acronym or near:
            hint["did_you_mean"] = acronym + [t for t in near if t not in acronym]
    m = re.search(r"no such column:\s*([\w.]+)", msg)
    if m:
        bad = m.group(1).split(".")[-1]
        referenced = [t for t in {x.lower() for x in _IDENT.findall(sql)} if t in tables]
        cols = {}
        for t in referenced or tables:
            cols[t] = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        # Only the tables the query actually named, or the model gets the whole schema back
        # as an error payload and we are back to paying for discovery.
        hint["columns"] = cols
        pool = [f"{t}.{c}" for t, cc in cols.items() for c in cc]
        near = difflib.get_close_matches(bad, [p.split(".")[-1] for p in pool], n=3, cutoff=0.5)
        if near:
            hint["did_you_mean"] = sorted({p for p in pool if p.split(".")[-1] in near})
    low = (sql or "").lower()
    if "ilike" in low or "information_schema" in low or "::" in low:
        hint["dialect"] = ("This is SQLite, not Postgres: no ILIKE (LIKE is already "
                           "case-insensitive for ASCII), no information_schema (use "
                           "describe_schema() or sqlite_master), no :: casts.")
    if re.search(r"\bnear \"(commit|order|group|select|table|index|values)\"", msg, re.I):
        hint["reserved_word"] = ("That word is SQL syntax, not your table. The commit table "
                                 "is 'commits'; quote an identifier as \"name\" if you must.")
    return hint


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
        conn.set_authorizer(_deny_blocked)
        cur = conn.execute(s)
        cols = [c[0] for c in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchmany(limit)]
    except Exception as exc:                       # noqa: BLE001
        msg = str(exc)
        try:
            hint = _sql_hint(conn, s, msg)
        except Exception:                          # noqa: BLE001 — a hint must not mask
            hint = {}                              # the error it is trying to explain
        return {"error": msg, **hint}
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


# ---- tools built from what the assistant kept writing by hand ----------------
# Every one of these exists because the chat transcript shows the model composing it in raw
# SQL, repeatedly and in variants: 81 sql_query calls, 26 of them touching person_runs, and
# five near-identical hand-rolled "rank people by commits in this element" queries that
# differed only in how they tried to resolve an element to its repos — the thing _repos()
# already does correctly. Each wraps a store function rather than new SQL, so the numbers
# agree with the report's own tiles instead of being a second opinion.


def top_contributors(since: str = "", until: str = "", scope: str = "",
                     metric: str = "commits", limit: int = 10,
                     member_only: bool = False) -> dict:
    """Rank PEOPLE by their activity in a window (and optional slice): who contributed
    most. `metric` ∈ {commits, prs, specs, bugs, features, epics}. `since`/`until` are
    'YYYY-MM-DD' (empty = all-time / now). `scope` slices to a repo subset, e.g.
    'element:Insight'. Use this instead of writing a GROUP BY author_login query — it
    counts the same way the report's own people tile does."""
    keys = ("commits", "prs", "specs", "bugs", "features", "epics")
    if metric not in keys:
        return {"error": f"metric must be one of {', '.join(keys)}"}
    conn = store.connect()
    try:
        drill = store.people_drill(conn, _iso(since), _iso(until, end=True),
                                   repos=_repos(conn, scope), member_only=member_only,
                                   limit=100000)
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()
    rows = sorted(drill["rows"], key=lambda r: (-(r.get(metric) or 0), r["login"]))
    return {"metric": metric, "since": since, "until": until, "scope": scope,
            "people_total": drill["total"],
            "rows": rows[:max(1, min(int(limit), 200))]}


def person_activity(login: str, since: str = "", until: str = "") -> dict:
    """One person's activity totals for a window — commits, meaningful LOC, specs, PRs
    opened and merged, bugs, features, epics. Use this for "how much did X do lately";
    person(login=…) is the all-time profile instead."""
    conn = store.connect()
    try:
        totals = store.person_totals(conn, login, _iso(since), _iso(until, end=True))
        known = conn.execute("SELECT name, company FROM person WHERE login=?",
                             (login,)).fetchone()
    finally:
        conn.close()
    out = {"login": login, "since": since, "until": until, **totals}
    if known:
        out["name"], out["company"] = known["name"], known["company"]
    else:
        # Not an error: a login with no person row still has commits under it. Say so
        # rather than returning zeros that look like inactivity.
        out["note"] = ("no person record for this login — check the spelling with "
                       "find_person(), or it may be an unmapped commit author")
    return out


def find_person(query: str, limit: int = 10) -> dict:
    """Resolve a human name, partial name or partial login to actual logins — the input
    every other person tool needs. Matches name, login and email. Use this before
    person(), person_activity() or list_items(author=…) whenever you were given a name
    rather than a login."""
    q = (query or "").strip()
    if not q:
        return {"error": "query is required"}
    like = f"%{q}%"
    conn = store.connect()
    try:
        # Ordered by surviving human code as a rough "how substantial is this person"
        # signal, because `person` carries no commit count — the dimension row holds
        # standing totals (surviving code, reviews, cpt), not activity counts. Checked
        # against the schema rather than assumed: I first wrote person.commits here, which
        # does not exist, which is the same guess the transcript is full of.
        rows = [dict(r) for r in conn.execute(
            "SELECT login, name, company, is_member, surviving_code_human, reviews_given "
            "FROM person WHERE login LIKE ? OR name LIKE ? OR emails LIKE ? "
            "ORDER BY surviving_code_human DESC, login LIMIT ?",
            (like, like, like, max(1, min(int(limit), 100))))]
    finally:
        conn.close()
    return {"query": q, "match_count": len(rows), "rows": rows}


def data_freshness() -> dict:
    """How current the data is: the newest collected run and when it was generated. Ask
    this before saying "in the last 7 days" — the window is measured against the data,
    not against today, and a stale collector makes recent windows look empty."""
    conn = store.connect()
    try:
        meta = store.latest_run_meta(conn) or {}
        newest = conn.execute("SELECT MAX(committed_at) FROM commits").fetchone()[0]
    finally:
        conn.close()
    return {"latest_run_date": meta.get("date"),
            "generated_at": meta.get("generated_at"),
            "newest_commit_at": newest}


def metric_definition(name: str) -> dict:
    """The definition of ONE metric — description, exact formula, unit, code snippet and
    where it is computed. Use this, not metrics_catalog, when a question is about a
    specific metric ("how is X calculated?"): the full catalog is 91 metrics and about
    44 KB, and every later tool round in the turn re-sends it. Matches exactly, then by
    substring, then by nearest name."""
    q = (name or "").strip()
    if not q:
        return {"error": "name is required"}
    import metrics_registry
    all_m = metrics_registry.all_metrics()
    # `fn` is an internal dotted path to the producing function; useful to a maintainer
    # reading the registry, noise to an answer about arithmetic.
    strip = lambda m: {k: v for k, v in m.items() if k != "fn"}          # noqa: E731
    names = [m["name"] for m in all_m]
    low = q.lower()
    # Stems, because people ask in plurals: "frictions" is not a substring of
    # flow_friction_per_item, and falling through to a fuzzy name match put
    # pr_median_additions FIRST for that query — a wrong formula at the top of the reply is
    # worse than no reply. Descriptions are searched too, since the question arrives in
    # human words ("friction") rather than in identifiers.
    stems = {low, low.rstrip("s")} - {""}
    def hit(m):
        """Rank: exact name, then name contains a stem, then description does."""
        nm = m["name"].lower()
        if nm == low:
            return 0
        if any(st in nm for st in stems):
            return 1
        if any(st in (m.get("desc") or "").lower() for st in stems):
            return 2
        return None
    scored = sorted(((hit(m), m) for m in all_m if hit(m) is not None),
                    key=lambda p: (p[0], p[1]["name"]))
    if scored:
        how = ("exact" if scored[0][0] == 0
               else "name" if scored[0][0] == 1 else "description")
        return {"query": q, "matched": how,
                "metrics": [strip(m) for _, m in scored[:5]],
                "more": max(0, len(scored) - 5)}
    near = difflib.get_close_matches(q, names, n=5, cutoff=0.4)
    if near:
        return {"query": q, "matched": "nearest name — none of these may be right",
                "metrics": [strip(m) for m in all_m if m["name"] in near]}
    return {"query": q, "error": f"no metric matching '{q}'", "metric_names": names}


def _previous_window(since: str, until: str):
    """The window of equal length immediately before [since, until], or None."""
    from datetime import datetime, timedelta
    try:
        a = datetime.fromisoformat(since.replace("Z", "+00:00"))
        b = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if b <= a:
        return None
    span = b - a
    return ((a - span).strftime("%Y-%m-%dT%H:%M:%SZ"),
            (a - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))


def developer_score(login: str, since: str = "", until: str = "", scope: str = "",
                    compare_previous: bool = False) -> dict:
    """One person's EXPERIMENTAL developer score for a window: the number, its band, the
    rank, each pillar with its weight and band, and every signal beside the TEAM MEDIAN so
    you can say where the gap is. Answers "why is my score X", "how do I raise it" and
    "how is my <signal> computed" — the score is a percentile rank within the people
    scored in this window, so it moves when their numbers move too. Set
    compare_previous=True to also get the change against the preceding window of equal
    length, split into the part from the team's movement and the part from this person's.
    Use metric_definition() for a signal's formula."""
    if not (login or "").strip():
        return {"error": "login is required"}
    lo, hi = _iso(since), _iso(until, end=True)
    conn = store.connect()
    try:
        sc = store.developer_scores(conn, lo, hi, repos=_repos(conn, scope))
        prev = None
        if compare_previous:
            win = _previous_window(lo, hi)
            if win:
                prev = store.developer_scores(conn, win[0], win[1],
                                              repos=_repos(conn, scope))
    except ValueError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()
    row = (sc.get("by_login") or {}).get(login)
    if not row:
        return {"login": login, "scored": False,
                "min_activity": sc.get("min_activity"),
                "n_ranked": sc.get("n_ranked"),
                "note": ("not scored in this window — under the activity floor, or the "
                         "login is wrong; check it with find_person()")}
    spec = store.score_signal_spec()
    medians = sc.get("team_medians") or {}
    drivers = row.get("drivers") or {}
    active = set(sc.get("active_pillars") or [])
    # The whole board is 46 rows deep with raw drivers on each; sending it would repeat
    # the metrics_catalog mistake this tool exists to avoid. Rank and the median carry the
    # comparison; top_contributors() is there for a ranking.
    # `fmt` travels with the value instead of the value being rounded here: 0.1423076923 is
    # the honest number, and the renderer hint is what stops it being quoted to sixteen
    # digits. Rounding in the tool would discard precision a caller may need and still not
    # say how to display anything.
    signals = [{"pillar": sig["pillar"], "signal": sig["key"], "label": sig["label"],
                "yours": drivers.get(sig["key"]), "team_median": medians.get(sig["key"]),
                "higher_is_better": sig["higher_is_better"], "fmt": sig["fmt"]}
               for sig in spec if sig["pillar"] in active]
    out = {"login": login, "name": row.get("name"), "scored": True,
           "score": row.get("score"), "band": row.get("band"),
           "rank": row.get("rank"), "of_scored": sc.get("n_ranked"),
           "experimental": True,
           "how_it_works": ("each signal is a percentile within the people scored in this "
                            "window; pillars are averaged, weighted and normalised"),
           "weights_pct": sc.get("weights"),
           "pillars": row.get("pillars"), "pillar_bands": row.get("pillar_bands"),
           "pillar_points": row.get("contributions"),
           "bands": store.score_band_spec(), "signals": signals,
           "since": since, "until": until, "scope": scope}
    if prev is not None:
        out["change"] = store.score_delta(sc, prev, login)
    return out


# ---- registry + schema introspection ---------------------------------------
# The tools an LLM (or MCP client) may call, in a sensible discovery order.
TOOLS = [
    metrics_catalog, metric_definition, describe_schema, list_dimension, taxonomy,
    data_freshness, contribution, top_contributors, delivery, trend, flow,
    find_person, person, person_activity, developer_score, list_items, sql_query,
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
