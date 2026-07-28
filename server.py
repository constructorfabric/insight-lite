#!/usr/bin/env python3
"""Local web portal for viewing, refreshing, and exporting the report."""
from __future__ import annotations

import ast
import base64
import hmac
import html
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import traceback
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml

import paths
import shell

ROOT = Path(__file__).resolve().parent
EXPORTS = paths.data_path("exports")
RUNTIME = paths.data_path(".runtime")
LAST_LOG = RUNTIME / "last-job.log"
MAX_UPLOAD_BYTES = 2_000_000
# Usage-beacon limits: cap the body and the number of events per request so a
# hostile or buggy client can't flood the usage_event table in one call.
MAX_USAGE_BYTES = 64_000
MAX_USAGE_EVENTS = 100
# Assistant transcript retention (days). 0 / unset = keep forever; set to prune old
# conversations. Applied opportunistically after each turn.
try:
    CHAT_HISTORY_DAYS = int(os.environ.get("CHAT_HISTORY_DAYS", "") or 0)
except ValueError:
    CHAT_HISTORY_DAYS = 0
# Substrings that mark a non-human User-Agent — excluded from page-open counts so
# health checks and crawlers don't inflate the adoption number.
_BOT_UA = ("bot", "spider", "crawler", "slurp", "curl", "wget", "python-requests",
           "monitor", "uptime", "healthcheck", "pingdom", "headless", "go-http")

# Built-in optional HTTP Basic auth. When PORTAL_PASSWORD is set, EVERY request
# (pages, /data.json, /exports, APIs) must present matching credentials. When it
# is unset the portal is open — appropriate only behind a reverse proxy that adds
# its own auth, or when bound to localhost; serve() warns loudly otherwise.
PORTAL_USER = os.environ.get("PORTAL_USER", "insight")
PORTAL_PASSWORD = os.environ.get("PORTAL_PASSWORD") or ""

# How old the newest stored run may be before /health/data reports the data as
# stale. The scheduled refresh runs nightly, so a healthy report is at most ~24h
# old; 36h leaves room for one late or slow run before it reads as broken.
HEALTH_MAX_AGE_HOURS = float(os.environ.get("HEALTH_MAX_AGE_HOURS", "36"))

JOB = {
    "running": False,
    "kind": "",
    "status": "idle",
    "started_at": "",
    "finished_at": "",
    "exit_code": None,
    "message": "",
    "log": "",
}
LOCK = threading.Lock()

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log_degraded(where: str, exc: BaseException) -> None:
    """Report a caught-and-degraded failure, with its traceback, to the server log.

    The many best-effort blocks below exist so one broken enrichment cannot take a
    whole page down, and that part is right. Doing it in SILENCE is the defect: it
    is the same shape as the July 2026 outage described in data_freshness() above —
    the refresh died ten nights running and the only trace was in a file nobody had
    reason to open, so the portal looked healthy while serving stale data. A
    swallowed exception with no log line is that outage at request scope: the user
    reads a missing panel as "no data for this person", the operator sees a normal
    200. So every degraded path names WHERE it broke and prints the traceback to
    stderr, next to the request log, where the container log picks it up.
    """
    print(f"{utc_now()}  degraded: {where} — {type(exc).__name__}: {exc}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)


def data_freshness(max_age_hours: float | None = None) -> tuple[dict, bool]:
    """Age of the newest stored run, as (payload, ok).

    The point of this is to make a dead collector visible. In July 2026 the nightly
    refresh failed ten nights running while the portal kept happily serving the last
    good day: cron.d had MAILTO="" and the traceback only ever reached a log file, so
    nothing anywhere reported a problem. Freshness is therefore read from the `runs`
    table — the collector's own output — not from the job runner's in-memory state,
    which a restart resets, nor from the DB file mtime, which WAL makes meaningless.
    """
    limit = HEALTH_MAX_AGE_HOURS if max_age_hours is None else max_age_hours
    import store
    conn = store.connect()
    try:
        meta = store.latest_run_meta(conn)
    finally:
        conn.close()
    out: dict = {"max_age_hours": limit}
    stamp = (meta or {}).get("generated_at") or ""
    try:
        ts = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        ts = None
    if ts is None:
        out.update({"ok": False, "stale": True,
                    "reason": "no run stored" if not meta else "unreadable generated_at",
                    "last_run": (meta or {}).get("date", "")})
        return out, False
    age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    ok = age <= limit
    out.update({"ok": ok, "stale": not ok, "last_run": meta.get("date", ""),
                "generated_at": stamp, "age_hours": round(age, 1)})
    if not ok:
        out["reason"] = f"newest run is {age:.1f}h old (limit {limit:g}h)"
    return out, ok


def file_stamp(path: Path) -> str:
    if not path.exists():
        return "missing"
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def count_files(path: Path, pattern: str = "*") -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob(pattern) if item.is_file())


def count_clone_dirs(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.iterdir() if (item / ".git").is_dir())


def newest_stamp(path: Path, pattern: str = "*") -> str:
    if not path.exists():
        return "missing"
    newest = max(
        (item.stat().st_mtime for item in path.rglob(pattern) if item.is_file()),
        default=None,
    )
    if newest is None:
        return "empty"
    return datetime.fromtimestamp(newest, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def store_state() -> dict:
    """Freshness of the SQLite source of truth — latest run date + row counts."""
    info = {"present": False, "latest_run": "empty", "db_stamp": "empty",
            "runs": 0, "people": 0, "repos": 0, "snapshots": 0}
    try:
        import store
        info["db_stamp"] = file_stamp(Path(store.db_path()))
        conn = store.connect()
        row = conn.execute("SELECT date FROM runs ORDER BY date DESC LIMIT 1").fetchone()
        info["latest_run"] = row["date"] if row else "empty"
        for tbl, key in (("runs", "runs"), ("person_runs", "people"),
                         ("repo_runs", "repos"), ("snapshots", "snapshots")):
            info[key] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        info["present"] = info["runs"] > 0
        conn.close()
    except Exception as exc:         # noqa: BLE001 — portal must render even w/o a DB
        # Kept a swallow (the portal has to render to be fixable) but no longer a
        # quiet one. store.connect() CREATES the DB lazily, so a fresh install
        # reaches here never: an exception means the real thing — unwritable data
        # dir, corrupt or locked DB — and the defaults above would report it as the
        # ordinary "nothing collected yet".
        log_degraded("portal store state (latest run + row counts)", exc)
    return info


def token_status() -> str:
    """The ACTIVE token source — 'db', 'env', or 'none'. Never the value. The DB
    (UI-managed) secret wins over the env var, matching ghclient.token()."""
    try:
        import store
        conn = store.connect()
        has = store.has_secret(conn, "gh_token")
        conn.close()
        if has:
            return "db"
    except Exception as exc:         # noqa: BLE001 — fall back to the env var
        # A failed secrets read must not claim "no token": that is what the setup
        # page shows when nothing is configured, so an unreadable DB would send an
        # already-onboarded operator round the whole connect-your-org loop again.
        log_degraded("GitHub token source lookup (DB secret)", exc)
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        return "env"
    return "none"


def data_present() -> bool:
    try:
        import store
        conn = store.connect()
        n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        conn.close()
        return n > 0
    except Exception as exc:         # noqa: BLE001 — treat as "not collected yet"
        # False routes the visitor to the setup wizard, which is right when nothing
        # has been collected and wrong-but-harmless otherwise; either way the reason
        # belongs in the log, since a broken DB looks exactly like a fresh install.
        log_degraded("data-present check", exc)
        return False


def needs_setup() -> bool:
    """First run = nothing collected yet. The wizard guides token + org + collect."""
    return not data_present()


def snapshot_state() -> dict:
    with LOCK:
        job = dict(JOB)
    cache_dir = paths.data_path(".cache")
    clone_dir = Path(os.environ.get("CLONE_DIR") or paths.data_path(".repos"))
    return {
        "job": job,
        "files": {
            "report_html": "live",          # rendered on demand from the DB, not baked
            # (no "people_yaml": the roster lives in the override table only — the file
            # it used to stamp was a mirror, and reading it back corrupted the table)
            "identity_editor": file_stamp(paths.data_path("identity-editor.html")),
        },
        "store": store_state(),
        "cache": {
            "enabled": os.environ.get("NO_CACHE", "") == "",
            "api_files": count_files(cache_dir, "*.json"),
            "api_newest": newest_stamp(cache_dir, "*.json"),
            "clone_repos": count_clone_dirs(clone_dir),
        },
        "has_token": token_status() != "none",
        "token_source": token_status(),
        # never expose the bearer token in the polled status JSON — only whether one
        # is set (mirrors the GitHub-token handling). The value is shown once, server-
        # side, in the auth-gated portal page for the copy button.
        "mcp": {"has_token": bool(_mcp_token()), "path": "/mcp", "public_url": _public_url()},
    }


def _public_url() -> str:
    """Configured public base URL (e.g. https://insight.example.com) used to build the
    MCP connect instructions, since location.origin varies by how the portal is opened."""
    try:
        import store
        conn = store.connect()
        s = store.read_overrides(conn, "setting")
        conn.close()
        return (s.get("public_url") or {}).get("value") or ""
    except Exception:                              # noqa: BLE001
        return ""


def _mcp_token() -> str:
    """MCP bearer token from the DB (shown openly in the portal — it's a connection
    credential the user copies into their MCP client)."""
    try:
        import store
        conn = store.connect()
        v = store.get_secret(conn, "mcp_token") or ""
        conn.close()
        return v or os.environ.get("MCP_TOKEN", "")
    except Exception:                              # noqa: BLE001
        return os.environ.get("MCP_TOKEN", "")


# _backup_people_yaml() kept 50 dated copies of people.yaml under history/people/ before
# each save, "so browser edits are always recoverable". On 2026-07-28 a roster actually had
# to be recovered and every one of those 50 files turned out to be test-fixture output (one
# or two people, `alice` / `bob`) — the suite had been saving through this path into the
# checkout. Recovery came from a report.db snapshot. A backup that silently becomes
# fixtures is worse than none, because it still reads as a safety net, so the file and its
# history are gone; history/backups/*.db (written before every deploy) is the real one.


# How much of the stored roster one save may drop without an explicit confirmation.
# A save REPLACES the whole person scope, so a payload that arrives short silently
# deletes curation: a truncated single-person body was accepted during testing and
# took a local roster with it. Deliberate removals come one or two at a time (a merge
# folds one login into another, leaving one row fewer), so a floor of 2 covers normal
# editing on a small roster and 10% covers a merge-heavy session on a large one, while
# a truncation — which shows up as "3 people" against a stored 200 — cannot pass.
# Measured on roster SIZE, not on which logins vanished: a merge legitimately makes a
# login disappear, so identity-diffing would flag every merge as a drop.
PEOPLE_DROP_FRACTION = 0.10
PEOPLE_DROP_FLOOR = 2


def _check_roster_drop(stored: int, incoming: int, allow_drop: int) -> None:
    dropped = stored - incoming
    if dropped <= 0:
        return
    budget = max(PEOPLE_DROP_FLOOR, int(stored * PEOPLE_DROP_FRACTION))
    if dropped <= budget or allow_drop >= dropped:
        return
    raise ValueError(
        f"Refusing to save: this would drop {dropped} of {stored} people from the "
        f"roster (the payload has {incoming}). That is what a truncated or partly "
        f"loaded editor looks like. If the removal is intended, resend the same "
        f"payload with the header X-Allow-Drop: {dropped}.")


def save_people(people: dict, *, allow_drop: int = 0) -> None:
    """Persist an identity edit from a roster mapping {login: {company,name,…}}.

    The DB override table is the ONLY place this lands (atomic). No YAML mirror is
    written: it duplicated the table, and the code that read it back imported test
    fixtures into it (see store.py, where the seed used to live).
    """
    if not isinstance(people, dict):
        raise ValueError("Expected a 'people' mapping")
    if not people:
        raise ValueError("Refusing to save an empty roster")
    import directory
    import store
    rows = {}
    seen_emails: dict[str, str] = {}       # normalized email -> first login that claimed it
    for login, info in people.items():
        if info is not None and not isinstance(info, dict):
            raise ValueError(f"{login}: expected a mapping of fields")
        info = info or {}
        v = {}
        for k in ("company", "name"):
            if info.get(k):
                if not isinstance(info[k], str):
                    raise ValueError(f"{login}: {k} must be text")
                v[k] = info[k]
        for k in ("aliases", "emails"):
            if info.get(k):
                if (not isinstance(info[k], list)
                        or not all(isinstance(x, str) for x in info[k])):
                    raise ValueError(f"{login}: {k} must be a list of strings")
                v[k] = info[k]
        if info.get("is_bot") is not None:
            v["is_bot"] = bool(info["is_bot"])
        # Contact handles (Discord / Telegram). Validated and normalised in directory,
        # next to the roster model rather than here in the HTTP layer, and kept out of
        # identity RESOLUTION on purpose: the resolver attributes commits through email
        # evidence, and a chat handle says nothing about authorship.
        try:
            v.update(directory.normalize_contacts(info))
        except ValueError as exc:
            raise ValueError(f"{login}: {exc}") from exc
        # an email may belong to exactly one person — a duplicate would split a
        # single human's contributions across two rows once the next Collect runs
        for email in v.get("emails", []) or []:
            key = str(email).strip().lower()
            if not key:
                continue
            if key in seen_emails and seen_emails[key] != login:
                raise ValueError(
                    f"Email {email} is assigned to both '{seen_emails[key]}' and "
                    f"'{login}'. Each email must belong to one person — merge them.")
            seen_emails[key] = login
        rows[login] = v
    conn = store.connect()
    try:
        _check_roster_drop(len(store.read_overrides(conn, "person")), len(rows), allow_drop)
        store.replace_overrides(conn, "person", rows)
    finally:
        conn.close()


def write_people_yaml(text: str, *, allow_drop: int = 0) -> None:
    """Save an identity edit supplied as a YAML roster in the REQUEST BODY.

    Not the editor transport any more (both editors POST JSON) and not a file path —
    nothing reads or writes people.yaml. Kept only because a browser tab still running
    a pre-JSON bundle posts `text/yaml`, and rejecting it would strand that tab's edit.
    """
    try:
        parsed = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("people"), dict):
        raise ValueError("Expected a top-level 'people' mapping")
    save_people(parsed["people"], allow_drop=allow_drop)


def run_job(kind: str, args: list[str]) -> bool:
    with LOCK:
        if JOB["running"]:
            return False
        JOB.update(
            {
                "running": True,
                "kind": kind,
                "status": "running",
                "started_at": utc_now(),
                "finished_at": "",
                "exit_code": None,
                "message": f"{kind} started",
                "log": "",
            }
        )

    def worker() -> None:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "reportctl.py", *args]
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        lines: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            with LOCK:
                JOB["log"] = "".join(lines[-200:])
        code = proc.wait()
        log = "".join(lines)
        LAST_LOG.write_text(log)
        with LOCK:
            JOB.update(
                {
                    "running": False,
                    "status": "success" if code == 0 else "failed",
                    "finished_at": utc_now(),
                    "exit_code": code,
                    "message": f"{kind} {'finished' if code == 0 else 'failed'}",
                    "log": log[-12000:],
                }
            )

    threading.Thread(target=worker, daemon=True).start()
    return True


def portal_html() -> bytes:
    state = snapshot_state()
    token_note = {"db": "Saved in the app", "env": "From environment",
                  "none": "Not set"}.get(state["token_source"], "Not set")
    _job = state["job"]
    job_class = ("run" if _job.get("running")
                 else {"success": "ok", "failed": "bad"}.get(_job.get("status"), ""))
    cache_note = (
        f"{state['cache']['api_files']} files, newest {state['cache']['api_newest']}"
        if state["cache"]["enabled"]
        else "disabled by NO_CACHE=1"
    )
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Update — Constructor Insight</title>
<style>
  :root{{--bg:#f6f8fa;--panel:#fff;--line:#d0d7de;--ink:#1f2328;--mut:#656d76;--acc:#0969da;--good:#1a7f37;--bad:#cf222e;--warn:#9a6700}}
  *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
  {shell.SHELL_CSS}
  main.wrap{{padding:24px 28px 56px}} h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 10px}}
  @media(max-width:900px){{main.wrap{{padding:16px}}}}
  .sub{{color:var(--mut);margin:0}} .hero{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin-bottom:18px}}
  .hero-actions{{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}}
  button,a.btn{{border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:8px 12px;border-radius:7px;text-decoration:none;cursor:pointer;font:inherit}}
  button.primary{{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}} button:disabled{{opacity:.55;cursor:not-allowed}}
  .status-strip{{display:grid;grid-template-columns:1.2fr repeat(3,1fr);gap:10px;margin:16px 0 18px}}
  .status-card,.op-card,.link-card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:13px 14px}}
  .status-card.good{{border-color:#a0d8b0}} .status-card.warn{{border-color:#e8d8a0}} .status-card.bad{{border-color:#e6a0a0}}
  .label{{font-size:12px;color:var(--mut)}} .value{{font-weight:700;margin-top:3px}} .fine{{color:var(--mut);font-size:12px;margin-top:4px}}
  .ok{{color:var(--good)}} .bad{{color:var(--bad)}} .warn{{color:var(--warn)}}
  .ops{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:10px 0 18px}}
  .op-card{{display:flex;flex-direction:column;gap:10px}} .op-card h3{{font-size:14px;margin:0}} .op-card p{{color:var(--mut);font-size:12px;margin:0;min-height:36px}}
  .op-meta{{font-size:12px;color:var(--mut)}} .op-card button{{align-self:flex-start}}
  .quick-links{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:10px 0 18px}}
  .link-card{{display:block;color:var(--ink);text-decoration:none;transition:border-color .12s}}
  .link-card:hover{{border-color:var(--acc)}} .link-card b{{display:block;margin-bottom:3px}} .link-card span{{color:var(--mut);font-size:12px}}
  .jobbar{{display:flex;align-items:center;gap:10px;background:var(--panel);border:1px solid var(--line);
    border-radius:8px;padding:11px 14px;margin:4px 0 8px;font-size:13px}}
  .jobbar .dot{{width:9px;height:9px;border-radius:50%;background:var(--mut);flex:none}}
  .jobbar.run{{border-color:#a9c7ea;background:#f0f6fc}} .jobbar.run .dot{{background:var(--acc);animation:pulse 1.2s infinite}}
  .jobbar.ok .dot{{background:var(--good)}} .jobbar.bad{{border-color:#e6a0a0}} .jobbar.bad .dot{{background:var(--bad)}}
  .jobbar b{{font-weight:700}} .jobbar .jmsg{{color:var(--mut)}}
  @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.35}}}}
  details.log{{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin-top:10px}} details.log summary{{cursor:pointer;padding:12px 14px;font-weight:700}}
  pre{{margin:0;border-top:1px solid var(--line);background:#1f2328;color:#fff;padding:12px;overflow:auto;max-height:340px;font-size:12px}}
  code{{background:#eaeef2;padding:1px 4px;border-radius:4px}} @media(max-width:780px){{.hero{{display:block}}.hero-actions{{justify-content:flex-start;margin-top:12px}}.status-strip,.ops,.quick-links{{grid-template-columns:1fr}}}}
  {shell.BASE_CSS}
  .status-card,.op-card,.link-card{{border-radius:var(--r-sm);box-shadow:var(--sh)}}
  .status-card.good{{border-color:var(--good)}} .status-card.warn{{border-color:var(--warn)}} .status-card.bad{{border-color:var(--bad)}}
</style></head><body>
<div class="app">
{shell.sidebar_html("update")}
<main class="wrap">
<div class="hero">
  <div>
    <h1>Update workspace</h1>
    <p class="sub">Collect from GitHub, curate identities, and configure what's measured. The
    database is the source of truth; the YAML files are auto-generated backups.</p>
  </div>
  <div class="hero-actions">
    <a class="btn primary" href="/report">Open report</a>
  </div>
</div>

<div class="jobbar {job_class}" id="jobbar">
  <span class="dot"></span>
  <b id="job-status">{html.escape(_job['status'])}</b>
  <span class="jmsg" id="job-message">{html.escape(_job.get('message') or 'No job is running.')}</span>
</div>

<h2>Status</h2>
<div class="status-strip">
  <div class="status-card"><div class="label">Database · source of truth</div><div class="value" id="store-run">{state['store']['latest_run']}</div><div class="fine" id="store-rows">{state['store']['runs']} runs · {state['store']['people']} people · {state['store']['repos']} repos · {state['store']['snapshots']} snapshots</div></div>
  <div class="status-card"><div class="label">Report</div><div class="value" id="report-stamp">{state['files']['report_html']}</div><div class="fine">rendered live from the DB</div></div>
  <div class="status-card"><div class="label">GitHub token</div><div class="value {'ok' if state['has_token'] else 'bad'}" id="token-note">{token_note}</div><div class="fine"><a href="/setup">Change / add →</a></div></div>
  <div class="status-card"><div class="label">Cache &amp; clones</div><div class="value" id="cache-note">{html.escape(cache_note)}</div><div class="fine"><span id="clone-note">{state['cache']['clone_repos']} repos</span> cloned</div></div>
</div>

<h2>Operations</h2>
<div class="ops">
  <div class="op-card">
    <h3>Collect from GitHub</h3>
    <p>Fetch new commits, PRs, issues and traffic, refresh clones, then rebuild the report. Reuses cache and local clones.</p>
    <div class="op-meta">Needs a token · can take minutes on a large org.</div>
    <button class="primary" onclick="startJob('refresh')">Collect &amp; rebuild</button>
  </div>
  <div class="op-card">
    <h3>Regenerate editors</h3>
    <p>Rebuild the Identity &amp; Config editor pages from the database. No GitHub.</p>
    <div class="op-meta">Edits themselves apply instantly on Save — this is for after updates.</div>
    <button onclick="startJob('directory')">Regenerate</button>
  </div>
  <div class="op-card">
    <h3>Export snapshot</h3>
    <p>Copy the current report, data, and identity directory into <code>exports/</code> as timestamped files.</p>
    <div class="op-meta">A shareable point-in-time snapshot.</div>
    <button onclick="startJob('export')">Export snapshot</button>
  </div>
</div>

<h2>Configure &amp; explore</h2>
<div class="quick-links">
  <a class="link-card" href="/setup"><b>Setup</b><span>GitHub token, primary org, extra orgs / repos.</span></a>
  <a class="link-card" href="/config"><b>Config</b><span>Repo classification, elements, company domains.</span></a>
  <a class="link-card" href="/identity"><b>Identity</b><span>People, companies, bots, aliases, merges.</span></a>
  <a class="link-card" href="/metrics"><b>Metrics catalog</b><span>Every number — description, formula, code.</span></a>
  <a class="link-card" href="/views"><b>View catalog</b><span>Reusable visual components — for dashboards &amp; MCP.</span></a>
  <a class="link-card" href="/mcp-info"><b>MCP access</b><span>Connection URL, bearer token, and the tool catalog.</span></a>
  <a class="link-card" href="/usage-insights"><b>Usage insights</b><span>Who opens the report and which widgets they view.</span></a>
</div>

<details class="log">
  <summary>Last job log</summary>
  <pre id="log">{html.escape(_job.get('log') or 'No job has run from this portal yet.')}</pre>
</details>
<script>
// Build API URLs against location.origin — a relative fetch inherits any userinfo
// from a basic-auth URL (http://user:pass@host/…), which fetch() rejects outright.
function api(p){{ return location.origin + p; }}
async function refreshStatus(){{
  const r = await fetch(api('/api/status')); const s = await r.json();
  const bar = document.getElementById('jobbar');
  bar.className = 'jobbar ' + (s.job.running ? 'run'
    : (s.job.status === 'success' ? 'ok' : s.job.status === 'failed' ? 'bad' : ''));
  document.getElementById('job-status').textContent = s.job.status + (s.job.running ? '…' : '');
  document.getElementById('job-message').textContent = s.job.message || 'No job is running.';
  document.getElementById('report-stamp').textContent = s.files.report_html;
  if(s.store){{
    document.getElementById('store-run').textContent = s.store.latest_run;
    document.getElementById('store-rows').textContent =
      `${{s.store.runs}} runs · ${{s.store.people}} people · ${{s.store.repos}} repos · ${{s.store.snapshots}} snapshots`;
  }}
  document.getElementById('cache-note').textContent = s.cache.enabled ? `${{s.cache.api_files}} files, newest ${{s.cache.api_newest}}` : 'disabled by NO_CACHE=1';
  document.getElementById('clone-note').textContent = `${{s.cache.clone_repos}} repos`;
  var tn=document.getElementById('token-note');
  tn.textContent = {{db:'Saved in the app', env:'From environment', none:'Not set'}}[s.token_source] || 'Not set';
  tn.className = 'value ' + (s.has_token ? 'ok' : 'bad');
  document.getElementById('log').textContent = s.job.log || 'No job has run from this portal yet.';
  document.querySelectorAll('.ops button').forEach(b => b.disabled = s.job.running);
}}
async function startJob(kind){{
  await fetch(api('/api/' + kind), {{method:'POST'}});
  await refreshStatus();
}}
setInterval(refreshStatus, 2000);
refreshStatus();
</script>
</main></div></body></html>"""
    return body.encode()


def _dec_name(n) -> str:
    """Dotted name of a decorator node ('mcp.tool' for @mcp.tool())."""
    if isinstance(n, ast.Call):
        n = n.func
    if isinstance(n, ast.Attribute):
        base = n.value.id if isinstance(n.value, ast.Name) else ""
        return f"{base}.{n.attr}"
    if isinstance(n, ast.Name):
        return n.id
    return ""


def _mcp_tools() -> list:
    """The MCP tool catalog, built from tooldefs.TOOLS — the shared tool functions the
    MCP server registers (no `mcp` framework import needed; tooldefs only needs the
    data modules the portal already has). Each entry: {name, sig, doc}. Drift-proof:
    adding a function to tooldefs.TOOLS shows up here."""
    try:
        import inspect
        import tooldefs
    except Exception as exc:                           # noqa: BLE001
        # An empty catalog renders as "no tools", indistinguishable from a tooldefs
        # that failed to import — the same absent-vs-broken ambiguity as the score
        # panel, so name it in the log.
        log_degraded("MCP tool catalog (tooldefs import)", exc)
        return []
    empty = inspect.Parameter.empty
    out = []
    for fn in tooldefs.TOOLS:
        params = []
        for name, p in inspect.signature(fn).parameters.items():
            a = p.annotation                           # str under `from __future__`
            ann = "" if a is empty else f": {a if isinstance(a, str) else getattr(a, '__name__', a)}"
            dv = f" = {p.default!r}" if p.default is not empty else ""
            params.append(f"{name}{ann}{dv}")
        out.append({"name": fn.__name__, "sig": ", ".join(params),
                    "doc": " ".join((inspect.getdoc(fn) or "").split())})
    return out


def _usage_range(qs):
    """(since, until) YYYY-MM-DD from a query dict: `from`+`to` if both valid,
    else the last `days` (default 30, capped). Never raises — bad input → default."""
    import re as _re
    from datetime import date, timedelta
    frm = (qs.get("from", [""])[0] or "").strip()
    to = (qs.get("to", [""])[0] or "").strip()
    days = (qs.get("days", [""])[0] or "").strip()
    iso = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if iso.match(frm) and iso.match(to):
        return frm, to
    n = max(1, min(int(days) if days.isdigit() else 30, 3660))
    return (date.today() - timedelta(days=n)).isoformat(), date.today().isoformat()


def usage_page() -> bytes:
    """Manage → Usage insights: meta-analytics on the report itself — opens, unique
    personas, per-widget and per-tab views, and per-persona engagement over a
    selectable period. All figures are fetched client-side from /api/usage-summary."""
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Usage insights — Constructor Insight</title>
<style>
  :root{{--bg:#f6f8fa;--panel:#fff;--line:#d0d7de;--ink:#1f2328;--mut:#656d76;--acc:#0969da;--good:#1a7f37;--bad:#cf222e;--warn:#9a6700}}
  *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
  {shell.SHELL_CSS}
  main.wrap{{padding:24px 28px 56px}} h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 10px}}
  @media(max-width:900px){{main.wrap{{padding:16px}}}}
  .sub{{color:var(--mut);margin:0 0 6px;max-width:74ch}}
  .kpis{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:14px 0 4px}}
  .kpi{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:13px 14px}}
  .kpi .n{{font-size:26px;font-weight:700}} .kpi .l{{font-size:12px;color:var(--mut)}}
  .chips{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:10px 0}}
  .chip{{border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:5px 12px;cursor:pointer;font-size:13px;color:var(--ink)}}
  .chip.active{{background:var(--acc);border-color:var(--acc);color:#fff}}
  .chips input{{border:1px solid var(--line);border-radius:6px;padding:4px 7px;font-size:13px}}
  table.u{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden;font-size:13px}}
  table.u th,table.u td{{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line)}}
  table.u thead th{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut)}}
  table.u td.n,table.u th.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
  table.u td.code{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;
    white-space:pre-wrap;word-break:break-word;color:var(--ink);line-height:1.4}}
  table.u tbody tr:last-child td{{border-bottom:none}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
  @media(max-width:900px){{.grid2{{grid-template-columns:1fr}}.kpis{{grid-template-columns:1fr 1fr}}}}
  .mut{{color:var(--mut)}} .empty{{color:var(--mut);padding:12px;font-size:13px}}
  tr.clk{{cursor:pointer}} tr.clk:hover td{{background:var(--panel2,#eef1f5)}}
  .kpi.clk{{cursor:pointer;transition:border-color .12s,box-shadow .12s}}
  .kpi.clk:hover{{border-color:var(--acc);box-shadow:var(--sh-lift,0 6px 20px rgba(0,0,0,.10))}}
  .dov{{position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:flex-start;justify-content:center;padding:8vh 16px;z-index:50}}
  .dov[hidden]{{display:none}}
  .dbox{{background:var(--panel);border:1px solid var(--line);border-radius:10px;max-width:520px;width:100%;max-height:80vh;overflow:auto;box-shadow:0 12px 40px rgba(0,0,0,.25)}}
  .dhead{{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel)}}
  .dhead b{{font-size:15px}} .dhead button{{border:none;background:transparent;font-size:20px;cursor:pointer;color:var(--mut);line-height:1}}
  #d-body{{padding:6px 16px 16px}} #d-body h3{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:14px 0 6px}}
  {shell.BASE_CSS}
  .kpi,table.u{{border-radius:var(--r-sm);box-shadow:var(--sh)}}
</style></head><body>
<div class="app">
{shell.sidebar_html("usage")}
<main class="wrap">
<h1>Usage insights</h1>
<p class="sub">How this report itself is used: who opens it and which widgets they view. Opens are
counted server-side (reliable); tab &amp; panel views come from the browser and are a floor, not
exact. Whole-report <b>All</b>-tab scrolls are tracked separately and excluded from the per-widget
ranking.</p>

<div class="chips" id="chips">
  <button class="chip" data-days="7">7d</button>
  <button class="chip active" data-days="30">30d</button>
  <button class="chip" data-days="90">90d</button>
  <button class="chip" data-days="365">1y</button>
  <button class="chip" data-days="3660">All</button>
  <span class="mut">·</span>
  <input type="date" id="from"> <span class="mut">→</span> <input type="date" id="to">
  <button class="chip" id="apply">Apply</button>
  <span class="mut" id="range"></span>
</div>

<div class="kpis">
  <div class="kpi clk" data-kpi="opens"><div class="n" id="k-opens">–</div><div class="l">report opens</div></div>
  <div class="kpi clk" data-kpi="personas"><div class="n" id="k-personas">–</div><div class="l">unique personas</div></div>
  <div class="kpi clk" data-kpi="widgets"><div class="n" id="k-widgets">–</div><div class="l">widgets viewed</div></div>
  <div class="kpi clk" data-kpi="tabs"><div class="n" id="k-tabs">–</div><div class="l">tabs opened</div></div>
</div>

<p class="mut" style="font-size:12px;margin:2px 0 0">Tip: click any row to see who — or, for a person, what they viewed.</p>
<div class="grid2">
  <div><h2>Widgets by views</h2><div id="widgets"></div></div>
  <div><h2>Tabs by views</h2><div id="tabs"></div></div>
</div>
<h2>Drill-downs by opens</h2><div id="drills"></div>

<h2>Metrics assistant</h2>
<p class="sub" style="margin:0 0 8px">Adoption of the in-report chat. Opens = panel opened;
questions = messages sent; each question is tagged with the report view it was asked from.</p>
<div class="kpis" style="grid-template-columns:repeat(6,minmax(0,1fr))">
  <div class="kpi"><div class="n" id="k-chat-opens">–</div><div class="l">assistant opens</div></div>
  <div class="kpi clk" data-detail="chatlog"><div class="n" id="k-chat-msgs">–</div><div class="l">questions asked</div></div>
  <div class="kpi"><div class="n" id="k-chat-users">–</div><div class="l">unique askers</div></div>
  <div class="kpi"><div class="n" id="k-chat-tokens">–</div><div class="l">tokens used</div></div>
  <div class="kpi"><div class="n" id="k-chat-cache">–</div><div class="l">cache hit</div></div>
  <div class="kpi"><div class="n" id="k-chat-cost">–</div><div class="l">est. cost</div></div>
</div>
<div id="chatviews" style="margin-top:12px"></div>
<h3 style="font-size:13px;margin:18px 0 6px">Tools called</h3>
<p class="sub" style="margin:0 0 8px">Which read-only tools the assistant invoked. Click a
row to see recent calls and their arguments — for <code>sql_query</code> the argument is the
SQL itself, so recurring queries flag which raw SQL deserves its own tool.</p>
<div id="chattools"></div>

<h2>People</h2><div id="people"></div>

<div id="detail" class="dov" hidden><div class="dbox">
  <div class="dhead"><b id="d-title"></b><button id="d-close" type="button" aria-label="Close">×</button></div>
  <div id="d-body"></div>
</div></div>

<script>
function api(p){{ return location.origin + p; }}
function esc(s){{ return String(s==null?'':s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c])); }}
function fmtNum(n){{ return (n||0).toLocaleString('en-US'); }}
function fmtCost(c){{ return (c==null) ? 'n/a' : '$' + Number(c).toFixed(4); }}
function chatReqTable(rows){{
  const fr = (rows||[]).map(r=>Object.assign({{}}, r, {{
    ts_f:(r.ts||'').replace('T',' ').slice(0,16),
    tokens_f:fmtNum(r.tokens||0), cost_f:fmtCost(r.cost)}}));
  // Kept to fit the 520px drill without horizontal overflow: per-row Period and
  // Cached are dropped (period is context; cache hit-rate lives in the KPIs).
  return table(fr, [{{h:'When', k:'ts_f'}}, {{h:'Who', k:'who'}}, {{h:'View', k:'view'}},
    {{h:'Tokens', k:'tokens_f', n:1}}, {{h:'Cost', k:'cost_f', n:1}}]);
}}
function table(rows, cols, by){{
  if(!rows || !rows.length) return '<div class="empty">No data in this period yet.</div>';
  const cls = c => [c.n?'n':'', c.cls||''].filter(Boolean).join(' ');
  const head = '<thead><tr>' + cols.map(c=>`<th class="${{cls(c)}}">${{esc(c.h)}}</th>`).join('') + '</tr></thead>';
  const kf = cols[0].k;
  const body = rows.map(r=>{{
    const a = by ? ` class="clk" data-by="${{by}}" data-key="${{esc(r[kf])}}"` : '';
    return `<tr${{a}}>` + cols.map(c=>`<td class="${{cls(c)}}">${{esc(r[c.k])}}</td>`).join('') + '</tr>';
  }}).join('');
  return `<table class="u">${{head}}<tbody>${{body}}</tbody></table>`;
}}
let curDays = 30, curFrom = '', curTo = '', last = null;
async function load(){{
  const q = curFrom && curTo ? `from=${{curFrom}}&to=${{curTo}}` : `days=${{curDays}}`;
  let s;
  try {{ s = await (await fetch(api('/api/usage-summary?' + q))).json(); }}
  catch(e) {{ document.getElementById('range').textContent = 'failed to load'; return; }}
  if(!s.ok){{ document.getElementById('range').textContent = s.error || 'error'; return; }}
  last = s;
  document.getElementById('k-opens').textContent = s.opens;
  document.getElementById('k-personas').textContent = s.unique_personas;
  document.getElementById('k-widgets').textContent = s.by_widget.length;
  document.getElementById('k-tabs').textContent = s.by_tab.length;
  document.getElementById('range').textContent = `${{s.since}} → ${{s.until}}`;
  document.getElementById('widgets').innerHTML = table(s.by_widget, [
    {{h:'Widget', k:'target'}}, {{h:'Views', k:'views', n:1}}, {{h:'People', k:'unique_viewers', n:1}}], 'widget');
  document.getElementById('tabs').innerHTML = table(s.by_tab, [
    {{h:'Tab', k:'target'}}, {{h:'Views', k:'views', n:1}}, {{h:'People', k:'unique_viewers', n:1}}], 'tab');
  document.getElementById('drills').innerHTML = table(s.by_drill || [], [
    {{h:'Drill-down', k:'target'}}, {{h:'Opens', k:'views', n:1}}, {{h:'People', k:'unique_viewers', n:1}}], 'drill');
  document.getElementById('k-chat-opens').textContent = s.chat_opens ?? 0;
  document.getElementById('k-chat-msgs').textContent = s.chat_msgs ?? 0;
  document.getElementById('k-chat-users').textContent = s.chat_users ?? 0;
  document.getElementById('k-chat-tokens').textContent = fmtNum(s.chat_tokens ?? 0);
  document.getElementById('k-chat-cache').textContent = (s.chat_cache_hit_pct ?? 0) + '%';
  document.getElementById('k-chat-cost').textContent = fmtCost(s.chat_cost_usd);
  document.getElementById('chatviews').innerHTML = table(
    (s.by_chat_view || []).map(r => Object.assign({{}}, r, {{tokens_f: fmtNum(r.tokens || 0), cost_f: fmtCost(r.cost)}})), [
    {{h:'Asked from view', k:'target'}}, {{h:'Questions', k:'views', n:1}},
    {{h:'People', k:'unique_viewers', n:1}}, {{h:'Tokens', k:'tokens_f', n:1}}, {{h:'Cost', k:'cost_f', n:1}}], 'chat');
  document.getElementById('chattools').innerHTML = table(s.by_chat_tool || [], [
    {{h:'Tool', k:'tool_name'}}, {{h:'Calls', k:'calls', n:1}},
    {{h:'Callers', k:'unique_callers', n:1}}, {{h:'Errors', k:'errors', n:1}}], 'tool');
  document.getElementById('people').innerHTML = table(s.by_persona, [
    {{h:'Person', k:'login'}}, {{h:'Opens', k:'opens', n:1}}, {{h:'Widgets seen', k:'widgets_seen', n:1}},
    {{h:'Asked', k:'chat_msgs', n:1}}], 'persona');
}}
function rangeQ(){{ return curFrom && curTo ? `from=${{curFrom}}&to=${{curTo}}` : `days=${{curDays}}`; }}
async function openDetail(by, key){{
  let s;
  try {{ s = await (await fetch(api(`/api/usage-detail?by=${{encodeURIComponent(by)}}&key=${{encodeURIComponent(key)}}&` + rangeQ()))).json(); }}
  catch(e){{ return; }}
  if(!s.ok) return;
  const label = {{widget:'Widget', tab:'Tab', drill:'Drill-down', chat:'Assistant · view', tool:'Tool', persona:'Person'}}[by] || by;
  document.getElementById('d-title').textContent =
    (by === 'chatlog') ? 'Assistant requests' : (label + ': ' + key);
  let html;
  if(by === 'persona'){{
    const sec = (t, rows, l) => `<h3>${{t}}</h3>` + table(rows, [{{h:l, k:'target'}}, {{h:'Views', k:'views', n:1}}]);
    html = sec('Widgets', s.widgets, 'Widget') + sec('Tabs', s.tabs, 'Tab') + sec('Drill-downs', s.drills, 'Drill-down');
    if(s.chat_log && s.chat_log.length){{ html += '<h3>Assistant requests</h3>' + chatReqTable(s.chat_log); }}
  }} else if(by === 'chat' || by === 'chatlog'){{
    html = chatReqTable(s.requests);
  }} else if(by === 'tool'){{
    const fr = (s.calls || []).map(r => {{
      let a = {{}}; try {{ a = JSON.parse(r.args || '{{}}'); }} catch(e) {{}}
      // clean, not raw JSON: sql_query → the SQL; else compact key=value.
      const argStr = (a && a.sql != null) ? String(a.sql)
        : Object.keys(a||{{}}).map(k => k + '=' + a[k]).join(', ');
      return {{ts_f:(r.ts||'').replace('T',' ').slice(0,16), who:r.who,
              status: r.ok ? 'ok' : 'error', args_f: argStr}};
    }});
    html = table(fr, [{{h:'When', k:'ts_f'}}, {{h:'Who', k:'who'}}, {{h:'Status', k:'status'}},
      {{h:'Arguments', k:'args_f', cls:'code'}}]);
  }} else {{
    html = table(s.viewers, [{{h:'Person', k:'who'}}, {{h:'Views', k:'views', n:1}}]);
  }}
  document.getElementById('d-body').innerHTML = html;
  document.getElementById('detail').hidden = false;
}}
function openKpi(kind){{
  if(!last) return;
  const openCols = [{{h:'Person', k:'who'}}, {{h:'Opens', k:'views', n:1}}];
  let title, rows, cols, by = null;
  if(kind === 'opens' || kind === 'personas'){{
    rows = (last.by_persona || []).map(p => ({{who:p.login, views:p.opens}}));
    cols = openCols; by = 'persona';
    if(kind === 'opens'){{
      const sum = rows.reduce((a, r) => a + (r.views || 0), 0);
      const un = (last.opens || 0) - sum;
      if(un > 0) rows = rows.concat([{{who:'(unresolved)', views:un}}]);  // not a person → not clickable
      title = 'Report opens';
    }} else {{ title = 'People who opened'; }}
  }} else if(kind === 'widgets'){{
    title = 'Widgets by views'; rows = last.by_widget || []; by = 'widget';
    cols = [{{h:'Widget', k:'target'}}, {{h:'Views', k:'views', n:1}}, {{h:'People', k:'unique_viewers', n:1}}];
  }} else if(kind === 'tabs'){{
    title = 'Tabs by views'; rows = last.by_tab || []; by = 'tab';
    cols = [{{h:'Tab', k:'target'}}, {{h:'Views', k:'views', n:1}}, {{h:'People', k:'unique_viewers', n:1}}];
  }} else {{ return; }}
  document.getElementById('d-title').textContent = title;
  // rows whose key is a real person/target stay clickable into the per-item detail;
  // the synthetic '(unresolved)' row has no target so it just won't match [data-key].
  document.getElementById('d-body').innerHTML = table(rows, cols, by);
  document.getElementById('detail').hidden = false;
}}
document.addEventListener('click', e=>{{
  const kpi = e.target.closest('.kpi.clk');
  if(kpi){{
    const det = kpi.getAttribute('data-detail');
    if(det){{ openDetail(det, ''); }} else {{ openKpi(kpi.getAttribute('data-kpi')); }}
    return;
  }}
  const tr = e.target.closest('tr.clk');
  if(tr && tr.getAttribute('data-key')){{ openDetail(tr.getAttribute('data-by'), tr.getAttribute('data-key')); return; }}
  if(e.target.id === 'd-close' || e.target.id === 'detail'){{ document.getElementById('detail').hidden = true; }}
}});
document.addEventListener('keydown', e=>{{ if(e.key === 'Escape') document.getElementById('detail').hidden = true; }});
document.getElementById('chips').addEventListener('click', e=>{{
  const b = e.target.closest('.chip'); if(!b) return;
  if(b.dataset.days){{
    curFrom = curTo = '';
    document.getElementById('from').value=''; document.getElementById('to').value='';
    curDays = +b.dataset.days;
    document.querySelectorAll('#chips .chip[data-days]').forEach(x=>x.classList.toggle('active', x===b));
    load();
  }} else if(b.id==='apply'){{
    const f=document.getElementById('from').value, t=document.getElementById('to').value;
    if(f && t){{ curFrom=f; curTo=t;
      document.querySelectorAll('#chips .chip[data-days]').forEach(x=>x.classList.remove('active'));
      load(); }}
  }}
}});
load();
</script>
</main></div></body></html>"""
    return body.encode("utf-8")


def chat_log_page() -> bytes:
    """Assistant conversation viewer — the stored transcripts (question + answer) with
    each turn's tool calls. Deliberately NOT linked from the sidebar; reachable at
    /chat-log by URL only. Portal auth still applies."""
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Assistant conversations — Constructor Insight</title>
<style>
  :root{{--bg:#f6f8fa;--panel:#fff;--line:#d0d7de;--ink:#1f2328;--mut:#656d76;--acc:#0969da;--good:#1a7f37;--bad:#cf222e;--warn:#9a6700}}
  *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
  {shell.SHELL_CSS}
  main.wrap{{padding:24px 28px 56px}} h1{{font-size:24px;margin:0 0 4px}}
  @media(max-width:900px){{main.wrap{{padding:16px}}}}
  .sub{{color:var(--mut);margin:0 0 6px;max-width:74ch}}
  .chips{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:10px 0}}
  .chip{{border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:5px 12px;cursor:pointer;font-size:13px;color:var(--ink)}}
  .chip.active{{background:var(--acc);border-color:var(--acc);color:#fff}}
  .chips input{{border:1px solid var(--line);border-radius:6px;padding:4px 7px;font-size:13px}}
  .cl-grid{{display:grid;grid-template-columns:320px 1fr;gap:16px;margin-top:12px}}
  @media(max-width:860px){{.cl-grid{{grid-template-columns:1fr}}}}
  .cl-list{{display:flex;flex-direction:column;gap:6px;max-height:76vh;overflow:auto}}
  .cl-item{{text-align:left;border:1px solid var(--line);background:var(--panel);border-radius:10px;
    padding:9px 11px;cursor:pointer;font:inherit;color:var(--ink)}}
  .cl-item:hover{{border-color:var(--acc)}} .cl-item.active{{border-color:var(--acc);box-shadow:0 0 0 2px rgba(9,105,218,.15)}}
  .cl-item .who{{font-weight:700;font-size:13px}} .cl-item .meta{{color:var(--mut);font-size:12px;margin-top:2px}}
  .cl-panel{{border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:16px;
    min-height:200px;max-height:76vh;overflow:auto}}
  .cl-msg{{max-width:80%;padding:9px 12px;border-radius:12px;margin:8px 0;white-space:pre-wrap;
    word-break:break-word;font-size:13.5px;line-height:1.5}}
  .cl-msg.user{{margin-left:auto;background:var(--acc);color:#fff;border-bottom-right-radius:4px}}
  .cl-msg.bot{{background:#eef1f5;color:var(--ink);border-bottom-left-radius:4px}}
  .cl-meta{{color:var(--mut);font-size:11px;margin:2px 0}}
  .cl-tools{{margin:2px 0 12px;display:flex;flex-direction:column;gap:4px}}
  .cl-tool{{font-size:12px;border-left:3px solid var(--line);padding:3px 8px;background:#f6f8fa;border-radius:0 6px 6px 0}}
  .cl-tool.err{{border-left-color:var(--bad)}} .cl-tool.ok{{border-left-color:var(--good)}}
  .cl-tool .tn{{font-weight:700}} .cl-tool code{{font-family:ui-monospace,Menlo,Consolas,monospace;
    font-size:11.5px;white-space:pre-wrap;word-break:break-word;display:block;margin-top:2px;color:var(--ink)}}
  .empty{{color:var(--mut);padding:12px;font-size:13px}} .mut{{color:var(--mut)}}
  {shell.BASE_CSS}
</style></head><body>
<div class="app">
{shell.sidebar_html("")}
<main class="wrap">
<h1>Assistant conversations</h1>
<p class="sub">Stored transcripts of the metrics assistant — each question, the answer, and
the tools the assistant called. Not linked in the sidebar; this page is reachable by URL.</p>
<div class="chips" id="chips">
  <button class="chip" data-days="7">7d</button>
  <button class="chip active" data-days="30">30d</button>
  <button class="chip" data-days="90">90d</button>
  <button class="chip" data-days="365">1y</button>
  <button class="chip" data-days="3660">All</button>
  <span class="mut">·</span>
  <input type="date" id="from"> <span class="mut">→</span> <input type="date" id="to">
  <button class="chip" id="apply">Apply</button>
  <span class="mut" id="range"></span>
</div>
<div class="cl-grid">
  <div class="cl-list" id="list"></div>
  <div class="cl-panel" id="panel"><div class="empty">Pick a conversation on the left.</div></div>
</div>

<script>
function api(p){{ return location.origin + p; }}
function esc(s){{ return String(s==null?'':s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c])); }}
function fmtNum(n){{ return (n||0).toLocaleString('en-US'); }}
function fmtCost(c){{ return (c==null) ? 'n/a' : '$' + Number(c).toFixed(4); }}
function when(ts){{ return (ts||'').replace('T',' ').slice(0,16); }}
let curDays = 30, curFrom = '', curTo = '', activeId = null;

function rangeQ(){{ return curFrom && curTo ? `from=${{curFrom}}&to=${{curTo}}` : `days=${{curDays}}`; }}

async function loadList(){{
  let s;
  try {{ s = await (await fetch(api('/api/chat-sessions?' + rangeQ()))).json(); }}
  catch(e) {{ document.getElementById('range').textContent = 'failed to load'; return; }}
  const list = document.getElementById('list');
  const rows = (s.sessions || []);
  if(!rows.length){{ list.innerHTML = '<div class="empty">No conversations in this period yet.</div>'; return; }}
  list.innerHTML = rows.map(r => `<button class="cl-item" data-id="${{esc(r.session_id)}}">
    <div class="who">${{esc(r.who)}}</div>
    <div class="meta">${{when(r.last)}} · ${{r.questions}} Q · ${{fmtNum(r.tokens)}} tok · ${{fmtCost(r.cost)}}</div></button>`).join('');
}}

function toolLine(t){{
  let a = {{}}; try {{ a = JSON.parse(t.args || '{{}}'); }} catch(e) {{}}
  const argStr = (a && a.sql != null) ? String(a.sql)
    : Object.keys(a||{{}}).map(k => k + '=' + a[k]).join(', ');
  return `<div class="cl-tool ${{t.ok ? 'ok' : 'err'}}"><span class="tn">${{esc(t.tool_name)}}</span>`
       + (t.ok ? '' : ' <span class="mut">(error)</span>')
       + (argStr ? `<code>${{esc(argStr)}}</code>` : '') + '</div>';
}}

async function openSession(id, btn){{
  activeId = id;
  document.querySelectorAll('.cl-item').forEach(x => x.classList.toggle('active', x === btn));
  const panel = document.getElementById('panel');
  panel.innerHTML = '<div class="empty">Loading…</div>';
  let s;
  try {{ s = await (await fetch(api('/api/chat-session?id=' + encodeURIComponent(id)))).json(); }}
  catch(e) {{ panel.innerHTML = '<div class="empty">Failed to load.</div>'; return; }}
  const tools = s.tools || {{}};
  const html = (s.messages || []).map(m => {{
    if(m.role === 'user'){{
      return `<div class="cl-meta" style="text-align:right">${{when(m.ts)}}${{m.view ? ' · ' + esc(m.view) : ''}}${{m.period ? ' · ' + esc(m.period) : ''}}</div>`
           + `<div class="cl-msg user">${{esc(m.text)}}</div>`;
    }}
    const tl = (tools[m.id] || []).map(toolLine).join('');
    return `<div class="cl-msg bot">${{esc(m.text)}}</div>`
         + (tl ? `<div class="cl-tools">${{tl}}</div>` : '')
         + `<div class="cl-meta">${{fmtNum((m.tokens_in||0)+(m.tokens_out||0))}} tok · ${{fmtCost(m.cost_usd)}}</div>`;
  }}).join('');
  panel.innerHTML = html || '<div class="empty">Empty conversation.</div>';
}}

document.getElementById('list').addEventListener('click', e => {{
  const b = e.target.closest('.cl-item'); if(b) openSession(b.getAttribute('data-id'), b);
}});
document.getElementById('chips').addEventListener('click', e => {{
  const b = e.target.closest('.chip'); if(!b) return;
  if(b.dataset.days){{
    curFrom = curTo = ''; document.getElementById('from').value=''; document.getElementById('to').value='';
    curDays = +b.dataset.days;
    document.querySelectorAll('#chips .chip[data-days]').forEach(x=>x.classList.toggle('active', x===b));
    loadList();
  }} else if(b.id==='apply'){{
    const f=document.getElementById('from').value, t=document.getElementById('to').value;
    if(f && t){{ curFrom=f; curTo=t;
      document.querySelectorAll('#chips .chip[data-days]').forEach(x=>x.classList.remove('active'));
      loadList(); }}
  }}
}});
loadList();
</script>
</main></div></body></html>"""
    return body.encode("utf-8")


def dashboards_list_page(rows: list, login) -> bytes:
    """Manage → Dashboards: list of custom dashboards (own + shared, newest first —
    see store.list_dashboards()). The New button creates a blank dashboard via the
    CRUD API and jumps straight into its editor. Linked from the sidebar."""
    def esc(v) -> str:
        return html.escape("" if v is None else str(v))

    if rows:
        trs = []
        for r in rows:
            did = esc(r.get("id"))
            edit = (f'<a href="/dashboard/{did}/edit">Edit</a>'
                    if r.get("owner_login") == login else "")
            trs.append(
                f'<tr><td><a href="/dashboard/{did}">'
                f'{esc(r.get("title") or "Untitled dashboard")}</a></td>'
                f'<td>{esc(r.get("owner_login"))}</td>'
                f'<td>{esc(r.get("visibility"))}</td>'
                f'<td>{esc((r.get("updated_ts") or "")[:16])}</td>'
                f'<td>{edit}</td></tr>')
        table_html = (
            '<table class="u"><thead><tr><th>Title</th><th>Owner</th>'
            '<th>Visibility</th><th>Updated</th><th></th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table>')
    else:
        table_html = ('<div class="empty">No dashboards yet — click '
                       '&ldquo;New dashboard&rdquo; to create one.</div>')
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboards — Constructor Insight</title>
<style>
  :root{{--bg:#f6f8fa;--panel:#fff;--line:#d0d7de;--ink:#1f2328;--mut:#656d76;--acc:#0969da;--good:#1a7f37;--bad:#cf222e;--warn:#9a6700}}
  *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
  {shell.SHELL_CSS}
  main.wrap{{padding:24px 28px 56px}} h1{{font-size:24px;margin:0 0 4px}}
  @media(max-width:900px){{main.wrap{{padding:16px}}}}
  .sub{{color:var(--mut);margin:0 0 14px;max-width:74ch}}
  table.u{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden;font-size:13px}}
  table.u th,table.u td{{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line)}}
  table.u thead th{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut)}}
  table.u tbody tr:last-child td{{border-bottom:none}}
  .empty{{color:var(--mut);padding:12px;font-size:13px}}
  {shell.BASE_CSS}
  table.u{{border-radius:var(--r-sm);box-shadow:var(--sh)}}
</style></head><body>
<div class="app">
{shell.sidebar_html("dashboards")}
<main class="wrap">
<h1>Dashboards</h1>
<p class="sub">Custom dashboards — your own and any shared with you. Build one from
reusable panels and share it with the team.</p>
<p><button id="new-dash" class="primary" type="button">New dashboard</button></p>
{table_html}
<script>
document.getElementById('new-dash').addEventListener('click', function(){{
  fetch('/api/dashboard', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{spec:{{title:'Untitled dashboard', panels:[]}}}})}})
    .then(function(r){{return r.json();}}).then(function(s){{ if(s.ok) location.href='/dashboard/'+s.id+'/edit'; }});
}});
</script>
</main></div></body></html>"""
    return body.encode("utf-8")


def mcp_page() -> bytes:
    """The MCP access page (sidebar → Manage → MCP): the connection URL + bearer
    token to wire an MCP client into this instance, plus the read-only tool catalog."""
    public_url = _public_url()
    token = _mcp_token()
    rows = "".join(
        '<tr><td><code>{name}</code></td><td>{sig}</td><td>{doc}</td></tr>'.format(
            name=html.escape(t["name"]),
            sig=(f'<code>{html.escape(t["sig"])}</code>' if t["sig"]
                 else '<span class="mut">—</span>'),
            doc=html.escape(t["doc"]))
        for t in _mcp_tools())
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCP — Constructor Insight</title>
<style>
  :root{{--bg:#f6f8fa;--panel:#fff;--line:#d0d7de;--ink:#1f2328;--mut:#656d76;--acc:#0969da;--good:#1a7f37;--bad:#cf222e;--warn:#9a6700}}
  *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
  {shell.SHELL_CSS}
  main.wrap{{padding:24px 28px 56px}} h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 10px}}
  @media(max-width:900px){{main.wrap{{padding:16px}}}}
  .sub{{color:var(--mut);margin:0 0 6px;max-width:70ch}}
  .card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px}}
  .label{{font-size:12px;color:var(--mut)}} .fine{{color:var(--mut);font-size:12px;margin-top:4px}}
  .mut{{color:var(--mut)}}
  table.mcp{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);
    border-radius:8px;overflow:hidden;font-size:13px}}
  table.mcp th,table.mcp td{{text-align:left;vertical-align:top;padding:9px 12px;border-bottom:1px solid var(--line)}}
  table.mcp thead th{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);background:var(--panel2,#eef1f5)}}
  table.mcp tbody tr:last-child td{{border-bottom:none}}
  table.mcp td:nth-child(1){{white-space:nowrap}} table.mcp td code{{font-size:12px;white-space:normal}}
  {shell.BASE_CSS}
  .card{{border-radius:var(--r-sm);box-shadow:var(--sh)}}
</style></head><body>
<div class="app">
{shell.sidebar_html("mcp")}
<main class="wrap">
<h1>MCP access</h1>
<p class="sub">Connect an AI client (Claude, etc.) to this instance over the Model Context
Protocol for read-only access to the contribution &amp; delivery data. Point the client at
the URL below with the bearer token, then call the tools listed underneath.</p>

<div class="card" style="margin-top:12px">
  <div class="label">Public URL — used in the connect instructions (set it to how clients reach this host, e.g. https://insight.example.com)</div>
  <div style="display:flex;gap:8px;align-items:center;margin:8px 0">
    <input id="mcp-baseurl" type="text" value="{html.escape(public_url)}"
      placeholder="https://your-host" style="flex:1;font-size:13px;padding:7px 9px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink)">
    <button onclick="mcpSaveUrl()">Save</button>
  </div>
  <div class="label" style="margin-top:10px">Bearer token — read-only data access for MCP clients</div>
  <div style="display:flex;gap:8px;align-items:center;margin:8px 0">
    <code id="mcp-token" data-token="{html.escape(token)}"
      style="flex:1;font-family:ui-monospace,Menlo,monospace;font-size:13px;padding:7px 9px;border:1px solid var(--line);border-radius:6px;background:var(--panel2);color:var(--ink);overflow-x:auto;white-space:nowrap">{html.escape(token) or '(none — set MCP_TOKEN on the server)'}</code>
    <button onclick="mcpCopy()">Copy</button>
  </div>
  <div class="fine">Connect an MCP client to <code id="mcp-url">/mcp</code> and send
    <code>Authorization: Bearer &lt;token&gt;</code>. Shown in the clear on purpose so you
    can copy it. Empty token = unauthenticated (only safe behind this proxy).</div>
  <div class="fine" style="margin-top:6px">Rotating the token would invalidate every connected MCP
    client at once, so it's intentionally not a button here — rotate it from the server
    (<code>MCP_TOKEN</code> env / secret) only when a token is leaked.</div>
</div>

<h2>Tools</h2>
<p class="sub">Every tool is read-only. Descriptions are read straight from the MCP server,
so this list always matches what a connected client sees.</p>
<table class="mcp">
  <thead><tr><th>Tool</th><th>Parameters</th><th>What it does</th></tr></thead>
  <tbody>{rows or '<tr><td colspan="3" class="mut">No tools found.</td></tr>'}</tbody>
</table>
<script>
function api(p){{ return location.origin + p; }}
function mcpBase(){{ var s=((document.getElementById('mcp-baseurl')||{{}}).value||'').trim()||location.origin;
  while(s.length && s.charAt(s.length-1)==='/') s=s.slice(0,-1); return s; }}
function mcpRefreshUrl(){{ var u=document.getElementById('mcp-url'); if(u) u.textContent = mcpBase() + '/mcp'; }}
(function(){{ var b=document.getElementById('mcp-baseurl'); if(b) b.addEventListener('input', mcpRefreshUrl); mcpRefreshUrl(); }})();
async function mcpSaveUrl(){{
  var url=(document.getElementById('mcp-baseurl').value||'').trim();
  var r=await fetch(api('/api/mcp/public-url'),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{url}})}});
  var j=await r.json(); if(j.ok){{ document.getElementById('mcp-baseurl').value=j.url; mcpRefreshUrl(); }} else {{ alert(j.error||'save failed'); }}
}}
function mcpCopy(){{ var el=document.getElementById('mcp-token'); if(!el) return;
  var t=(el.getAttribute('data-token')||'').trim(); if(!t) return;
  if(navigator.clipboard&&navigator.clipboard.writeText){{ navigator.clipboard.writeText(t).catch(function(){{}}); return; }}
  var ta=document.createElement('textarea'); ta.value=t; document.body.appendChild(ta); ta.select();
  try{{ document.execCommand('copy'); }}catch(e){{}} document.body.removeChild(ta); }}
</script>
</main></div></body></html>"""
    return body.encode()


SETUP_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Set up — Constructor Insight</title>
<style>
  :root{--bg:#f6f8fa;--panel:#fff;--line:#d0d7de;--ink:#1f2328;--mut:#656d76;
    --acc:#0969da;--good:#1a7f37;--bad:#cf222e;--warn:#9a6700;--panel2:#eaeef2}
  *{box-sizing:border-box}
  [hidden]{display:none!important}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  .wrap{max-width:720px;margin:0 auto;padding:32px 20px 80px}
  h1{font-size:24px;margin:0 0 4px} .lead{color:var(--mut);margin:0 0 24px}
  .step{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:0 0 16px}
  .step h2{font-size:16px;margin:0 0 4px} .step.done{border-color:var(--good)}
  .snum{display:inline-block;width:22px;height:22px;border-radius:50%;background:var(--panel2);
    color:var(--mut);text-align:center;font-size:12px;font-weight:700;line-height:22px;margin-right:8px}
  .step.done .snum{background:var(--good);color:#fff}
  .hint{color:var(--mut);font-size:13px;margin:4px 0 12px}
  label{display:block;font-size:13px;font-weight:600;margin:10px 0 4px}
  input{width:100%;border:1px solid var(--line);border-radius:8px;padding:8px 11px;
    font:inherit;font-size:14px;background:var(--bg);color:var(--ink)}
  .row{display:flex;gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap}
  .btn{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:8px;
    padding:8px 15px;font:600 14px inherit;cursor:pointer}
  .btn.primary{background:var(--acc);border-color:var(--acc);color:#fff}
  .btn:disabled{opacity:.5;cursor:default}
  .msg{font-size:13px} .msg.ok{color:var(--good)} .msg.err{color:var(--bad)}
  code{background:var(--panel2);padding:1px 5px;border-radius:5px;font-size:12px}
  pre{background:#0d1117;color:#c9d1d9;border-radius:8px;padding:12px;overflow:auto;font-size:12px;max-height:220px}
  a{color:var(--acc)}
  """ + shell.BASE_CSS + r"""
  .step{border-radius:var(--r-sm);box-shadow:var(--sh)} .step.done{border-color:var(--good)}
</style></head>
<body><div class="wrap">
<h1>Set up Constructor&nbsp;Insight</h1>
<p class="lead">Connect a GitHub organization and run the first collection. You can refine
classification, elements and identities afterwards from the report portal.</p>

<div class="step" id="s1">
  <h2><span class="snum">1</span>GitHub token</h2>
  <p class="hint">Create a <b>fine-grained</b> personal access token with <b>read-only</b> access to the
    org's repositories (Contents, Metadata, Pull requests, Issues) at
    <a href="https://github.com/settings/tokens?type=beta" target="_blank" rel="noopener">github.com/settings/tokens</a>.
    It is stored server-side only, never shown again, never written to files or git.</p>
  <label for="token">Token</label>
  <input id="token" type="password" autocomplete="off" placeholder="github_pat_… / ghp_…">
  <div class="row">
    <button class="btn primary" id="tok-save">Verify &amp; save token</button>
    <button class="btn" id="tok-clear" hidden>Clear saved token</button>
    <span class="msg" id="tok-msg"></span>
  </div>
</div>

<div class="step" id="s2">
  <h2><span class="snum">2</span>Organization &amp; repositories</h2>
  <p class="hint">Primary org whose history is collected. Extra orgs / individual repos are optional
    (comma-separated) — e.g. an old org after a migration.</p>
  <label for="org">Primary org</label>
  <input id="org" placeholder="my-github-org" value="__ORG__">
  <label for="extra-orgs">Extra orgs (comma-separated)</label>
  <input id="extra-orgs" placeholder="old-org, another-org" value="__EXTRA_ORGS__">
  <label for="extra-repos">Extra repos (org/repo, comma-separated)</label>
  <input id="extra-repos" placeholder="vendor/pkg-repo" value="__EXTRA_REPOS__">
  <div class="row">
    <button class="btn primary" id="scope-save">Save scope</button>
    <span class="msg" id="scope-msg"></span>
  </div>
</div>

<div class="step" id="s3">
  <h2><span class="snum">3</span>First collection</h2>
  <p class="hint">Clones the repos and fetches PRs / issues from GitHub, then builds the report.
    This can take a few minutes on a large org.</p>
  <div class="row">
    <button class="btn primary" id="collect-start">Start first collection</button>
    <span class="msg" id="collect-msg"></span>
  </div>
  <pre id="collect-log" hidden></pre>
  <div class="row" id="done-row" hidden><a class="btn primary" href="/report">Open report →</a></div>
</div>

<script>
const api = (p) => location.origin + p;
const TOKEN_STATUS = "__TOKEN_STATUS__";
function msg(id, t, cls){ const s=document.getElementById(id); s.textContent=t; s.className='msg'+(cls?(' '+cls):''); }
function markDone(id){ document.getElementById(id).classList.add('done'); }

if(TOKEN_STATUS === 'db'){
  msg('tok-msg','A token is saved here. Enter a new one to replace it.','ok'); markDone('s1');
  document.getElementById('tok-clear').hidden=false;
} else if(TOKEN_STATUS === 'env'){
  msg('tok-msg','Using the environment token. Save one here to manage it from the UI (it takes over).'); markDone('s1');
}
document.getElementById('tok-clear').addEventListener('click', async () => {
  if(!confirm('Remove the saved token? Collection falls back to the environment token if one is set.')) return;
  const r=await fetch(api('/api/setup/token/clear'),{method:'POST'}); const j=await r.json();
  msg('tok-msg','Cleared — active source: '+(j.token_source||'none'), j.token_source==='none'?'err':'');
  document.getElementById('tok-clear').hidden = (j.token_source!=='db');
});

document.getElementById('tok-save').addEventListener('click', async () => {
  const token = document.getElementById('token').value.trim();
  if(!token){ msg('tok-msg','Paste a token first.','err'); return; }
  const b=document.getElementById('tok-save'); b.disabled=true; msg('tok-msg','Verifying…');
  try{
    const r=await fetch(api('/api/setup/token'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
    const j=await r.json(); if(!r.ok||!j.ok) throw new Error(j.error||'failed');
    document.getElementById('token').value='';
    msg('tok-msg', 'Saved ✓ '+(j.login?('as '+j.login+' · '):'')+(j.remaining!=null?(j.remaining+' API calls left'):''), 'ok');
    markDone('s1');
  }catch(e){ msg('tok-msg', e.message, 'err'); }
  b.disabled=false;
});
function csv(id){ return document.getElementById(id).value.split(',').map(s=>s.trim()).filter(Boolean); }
document.getElementById('scope-save').addEventListener('click', async () => {
  const org=document.getElementById('org').value.trim();
  if(!org){ msg('scope-msg','Primary org is required.','err'); return; }
  const b=document.getElementById('scope-save'); b.disabled=true; msg('scope-msg','Saving…');
  try{
    const r=await fetch(api('/api/setup/save'),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({org, extra_orgs:csv('extra-orgs'), extra_repos:csv('extra-repos')})});
    const j=await r.json(); if(!r.ok||!j.ok) throw new Error(j.error||'failed');
    msg('scope-msg','Saved ✓','ok'); markDone('s2');
  }catch(e){ msg('scope-msg', e.message, 'err'); }
  b.disabled=false;
});
let poll=null;
document.getElementById('collect-start').addEventListener('click', async () => {
  const b=document.getElementById('collect-start'); b.disabled=true; msg('collect-msg','Starting…');
  try{
    const r=await fetch(api('/api/setup/collect'),{method:'POST'});
    const j=await r.json(); if(!r.ok||!j.ok) throw new Error(j.error||'failed');
    document.getElementById('collect-log').hidden=false;
    if(poll) clearInterval(poll); poll=setInterval(pollStatus, 2000); pollStatus();
  }catch(e){ msg('collect-msg', e.message, 'err'); b.disabled=false; }
});
async function pollStatus(){
  try{
    const s=await (await fetch(api('/api/status'))).json();
    document.getElementById('collect-log').textContent = s.job.log || s.job.message || '';
    document.getElementById('collect-log').scrollTop = 1e9;
    msg('collect-msg', s.job.status + (s.job.running?'…':''));
    if(!s.job.running && s.job.status==='success'){ clearInterval(poll); markDone('s3');
      msg('collect-msg','Done ✓','ok'); document.getElementById('done-row').hidden=false; }
    else if(!s.job.running && s.job.status==='failed'){ clearInterval(poll);
      msg('collect-msg','Collection failed — see log.','err');
      document.getElementById('collect-start').disabled=false; }
  }catch(e){}
}
</script>
</div></body></html>"""


def setup_html() -> bytes:
    import configstore
    cfg = {}
    try:
        import ghclient
        cfg = ghclient.load_config()
    except Exception as exc:         # noqa: BLE001 — the wizard must still open
        # Worth a log line beyond the usual reason: the fields below fall back to
        # EMPTY, so a failed load shows a configured org as unconfigured, and saving
        # the form as presented would write those blanks back over it.
        log_degraded("setup wizard config load (legacy page)", exc)
    org = html.escape(str(cfg.get("org", "")))
    extra_orgs = html.escape(", ".join(cfg.get("extra_orgs", []) or []))
    extra_repos = html.escape(", ".join(cfg.get("extra_repos", []) or []))
    return (SETUP_HTML.replace("__TOKEN_STATUS__", token_status())
            .replace("__ORG__", org).replace("__EXTRA_ORGS__", extra_orgs)
            .replace("__EXTRA_REPOS__", extra_repos)).encode()


# Live-render cache. The report is served fresh from the DB (no baked report.html);
# build_model + render_report run on the first hit after any collect/edit and are
# cached until the DB changes. Keyed on store.report_version() — a content token that
# moves the instant any run blob or override is written, unlike the DB file mtime
# which under WAL needn't change on an in-process write. One shared model feeds both
# the /report HTML and period_ctx (kills the old double build_model).
_RENDER: dict = {"version": None, "model": None, "ctx": None, "html": None}
_RENDER_LOCK = threading.Lock()

# Distinct from None, which is a REAL version here (_report_version() degrades to it
# when the version token can't be read), so it can't double as "nothing logged yet".
_UNSET_VERSION = object()

# Set while _report_model() is falling back to the last-good model because a rebuild
# raised. This is the ONE degraded path here that is not per-request: the cached
# version never advances past a failing rebuild, so every later request retries, fails
# and serves the same stale model — the portal answers 200 everywhere with numbers
# that quietly stop moving. That is the July 2026 outage's exact shape, which is why
# it gets more than a log line: /health/data reports it and answers 503, so a monitor
# can page on it. Guarded by _RENDER_LOCK. `logged_version` keeps the traceback to
# once per version instead of once per request (a broken rebuild would otherwise
# flood the log and pay build_model's cost on every hit).
_STALE_MODEL: dict = {"since": None, "version": None, "error": None,
                      "logged_version": _UNSET_VERSION}


def _report_version():
    import store
    conn = store.connect()
    try:
        return store.report_version(conn)
    except Exception:                       # noqa: BLE001 — degrade to always-rebuild
        return None
    finally:
        conn.close()


def _report_model(version):
    """Cached build_model for `version`; rebuilds on a version change. Renders OUTSIDE
    the lock (build_model is slow) then swaps in. On a render failure — e.g. no data
    collected yet, where load_data() raises SystemExit — keeps the last-good model if
    there is one (serve stale-but-working) and otherwise re-raises.

    Serving stale beats 500-ing every page, but it is the most dangerous of the
    degraded paths in this file, because it is invisible from the outside AND
    self-perpetuating: nothing advances the cached version, so every subsequent
    request re-runs the same failing build and serves the same frozen model, on every
    endpoint, with a 200. So the fallback records itself (see _STALE_MODEL): traceback
    to the log once per version, and a 503 with a reason on /health/data until a
    rebuild succeeds. A recovery clears it."""
    import render
    with _RENDER_LOCK:
        if _RENDER["version"] == version and _RENDER["model"] is not None:
            return _RENDER["model"]
    try:
        model = render.build_model(render.load_data())
    except BaseException as exc:             # incl. SystemExit ("no data yet")
        with _RENDER_LOCK:
            stale = _RENDER["model"]
            if stale is None:
                # Nothing to fall back on: the caller turns this into a visible
                # error, so it needs no degradation record.
                raise
            first = _STALE_MODEL["logged_version"] != version
            _STALE_MODEL.update(since=_STALE_MODEL["since"] or utc_now(),
                                version=_RENDER["version"],
                                error=f"{type(exc).__name__}: {exc}",
                                logged_version=version)
        if first:
            log_degraded(f"report model rebuild — serving the last good model "
                         f"instead (stale since {_STALE_MODEL['since']})", exc)
        return stale
    with _RENDER_LOCK:
        # a newer version may have won the race; only reset caches if we're advancing
        if _RENDER["version"] != version:
            _RENDER.update(version=version, model=model, ctx=None, html=None)
        recovered_from = _STALE_MODEL["since"]
        if recovered_from:
            _STALE_MODEL.update(since=None, version=None, error=None,
                                logged_version=_UNSET_VERSION)
        current = _RENDER["model"]
    if recovered_from:
        # Outside the lock, like the failure path: whoever is waiting on a rebuild
        # should not also wait on stderr. Worth saying at all because the 503 it
        # clears disappears on its own, and an alert that goes quiet without a
        # reason is its own small mystery.
        print(f"{utc_now()}  recovered: report model rebuilt "
              f"(was stale since {recovered_from})", file=sys.stderr)
    return current


def stale_model_state() -> dict | None:
    """The _STALE_MODEL record for /health/data, or None when the model is current.

    Separate from data_freshness(), which asks whether the COLLECTOR is still
    landing runs. This asks whether what the portal renders is still being rebuilt
    from them. Both can make the same sentence true — "the portal looks healthy and
    the numbers are old" — and they fail independently: a perfect nightly collect
    still shows yesterday's report if build_model has been raising since.

    Limitation worth knowing before trusting this: alert.py — the thing that actually
    pages — runs from cron as its OWN process, so it reads data_freshness() from the
    DB and cannot see this in-memory state at all. A frozen model is therefore caught
    by whatever polls /health/data over HTTP, not by the nightly check. Giving the
    pager this signal too means persisting it outside the process (a row, a file),
    which is a deliberate design step rather than a detail to slip in here.
    """
    with _RENDER_LOCK:
        if not _STALE_MODEL["since"]:
            return None
        return {"stale_since": _STALE_MODEL["since"],
                "serving_version": str(_STALE_MODEL["version"]),
                "error": _STALE_MODEL["error"]}


def health_data_payload() -> tuple[dict, bool]:
    """What /health/data answers: data freshness AND whether the report model is
    still rebuilding from it, folded into one (payload, ok).

    A function rather than inline handler code so the 503 is reachable from a test
    without a socket — the branch that must never silently become a 200 is exactly
    the branch worth pinning down."""
    payload, ok = data_freshness()
    stale_model = stale_model_state()
    if stale_model:
        payload["report_model"] = stale_model
        payload["ok"] = False
        earlier = payload.get("reason")
        payload["reason"] = (
            f"report model has not rebuilt since {stale_model['stale_since']} "
            f"({stale_model['error']}); pages are served from the last good model"
            + (f" — also: {earlier}" if earlier else ""))
        ok = False
    return payload, ok


def period_ctx() -> dict:
    """Globals the filterable-panel macros need (emails_by_login, legacy_names,
    per-person all-time rows), derived from the shared cached model."""
    version = _report_version()
    with _RENDER_LOCK:
        if _RENDER["version"] == version and _RENDER["ctx"] is not None:
            return _RENDER["ctx"]
    model = _report_model(version)
    ctx = {"emails_by_login": model.get("emails_by_login", {}),
           "legacy_names": model.get("legacy_names", []),
           "ai_precision": (model.get("meta") or {}).get("ai_precision", {}),
           # all-time per-person rows (surviving code, reviews, identity) keyed by
           # login — for the Person dashboard's cumulative/blame impact block.
           "person_alltime": {r["login"]: r for r in model.get("table", [])}}
    with _RENDER_LOCK:
        # only cache if the slot still holds the exact model we derived from — a
        # concurrent newer render (or a last-good fallback) must not be clobbered.
        if _RENDER["model"] is model:
            _RENDER["ctx"] = ctx
    return ctx


def report_html() -> str:
    """The full report page, rendered live from the DB and cached until it changes."""
    import render
    version = _report_version()
    with _RENDER_LOCK:
        if _RENDER["version"] == version and _RENDER["html"] is not None:
            return _RENDER["html"]
    model = _report_model(version)
    html = render.render_report(model)
    with _RENDER_LOCK:
        # only cache if the slot still holds the exact model this html came from,
        # so a slower render can't bind stale html to a newer version's slot.
        if _RENDER["model"] is model:
            _RENDER["html"] = html
    return html


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, value: dict, status: int = 200) -> None:
        self.send_bytes(json.dumps(value, indent=2).encode(), "application/json", status)

    def send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_bytes(path.read_bytes(), ctype)

    def send_html_file_with_nav(self, path: Path, active: str) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        # The report/identity pages now bake the full sidebar shell (incl. the
        # Update/Report/Identity switch at the sidebar bottom), so no top-bar
        # injection is needed — serve the file as-is.
        self.send_bytes(path.read_bytes(), "text/html; charset=utf-8")

    def _oauth_user(self) -> str:
        """Best-effort identity of the signed-in user from the oauth2-proxy headers
        (varies by proxy config). Falls back to 'anon' so calibration still works."""
        for h in ("X-Forwarded-Email", "X-Auth-Request-Email",
                  "X-Forwarded-Preferred-Username", "X-Forwarded-User",
                  "X-Auth-Request-User"):
            v = (self.headers.get(h) or "").strip()
            if v:
                return v[:120]
        return "anon"

    def _oauth_idents(self) -> list:
        """Signed-in identities from the proxy headers, GITHUB USERNAME FIRST — the
        login-bearing headers before the email ones — so whoami resolves to a person
        login directly (sign-in is GitHub-based). Order matters; first match wins."""
        out = []
        for h in ("X-Forwarded-Preferred-Username", "X-Forwarded-User",
                  "X-Auth-Request-User", "X-Forwarded-Email", "X-Auth-Request-Email"):
            v = (self.headers.get(h) or "").strip()
            if v and v not in out:
                out.append(v[:120])
        return out

    def _resolve_viewer(self, conn):
        """(login, ident) for the signed-in viewer, resolved SERVER-SIDE from the
        proxy headers — never from a client payload. login is None when the identity
        maps to no person; ident is the raw identity or 'anon'. Never raises."""
        import store
        idents = self._oauth_idents()
        ident = idents[0] if idents else "anon"
        login = None
        try:
            for i in idents:
                login = store.person_login_for(conn, i)
                if login:
                    break
        except Exception as exc:        # noqa: BLE001 — attribution is best-effort
            # The identity itself stays OUT of the log line: it is usually the
            # viewer's email, and a failure message is not a reason to write one
            # into the container log.
            log_degraded("viewer attribution from proxy identity", exc)
            login = None
        return login, ident

    def _looks_like_bot(self) -> bool:
        ua = (self.headers.get("User-Agent") or "").lower()
        return (not ua) or any(b in ua for b in _BOT_UA)

    def _log_page_open(self) -> None:
        """Best-effort server-side page-open event. Swallows every error (a locked
        DB during a collect, or any write failure, must never break the report
        render) and skips obvious bots. This is the authoritative adoption signal —
        so a write that fails FOREVER quietly zeroes the adoption number, which is
        why the swallow is logged even though a single lost event doesn't matter."""
        if self._looks_like_bot():
            return
        try:
            import store
            conn = store.connect()
            try:
                login, ident = self._resolve_viewer(conn)
                store.record_page_open(conn, login, ident)
            finally:
                conn.close()
        except Exception as exc:        # noqa: BLE001 — never break the page
            log_degraded("page-open event (adoption signal)", exc)

    def require_auth(self) -> bool:
        """When PORTAL_PASSWORD is configured, enforce HTTP Basic auth on every
        request. Returns True (and sends a 401 challenge) when the request is
        unauthenticated and should not proceed; False when auth is disabled or
        the credentials check out."""
        if not PORTAL_PASSWORD:
            return False
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
            except Exception:
                user = pw = ""
            # constant-time compare on both fields so timing can't leak either
            if hmac.compare_digest(user, PORTAL_USER) and hmac.compare_digest(pw, PORTAL_PASSWORD):
                return False
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Constructor Insight", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def reject_cross_origin(self) -> bool:
        """Reject browser requests whose Origin does not match the Host header.

        Same-origin browser requests and non-browser clients (no Origin header,
        e.g. curl) pass through. Behind a reverse proxy the backend's Host may be
        rewritten (e.g. nginx `$host` strips the port), so the original host is
        also accepted from X-Forwarded-Host. Returns True when rejected."""
        origin = self.headers.get("Origin")
        if not origin:
            return False
        netloc = urlparse(origin).netloc
        allowed = {(self.headers.get("Host") or "").strip(),
                   (self.headers.get("X-Forwarded-Host") or "").strip()}
        if netloc and netloc in allowed:
            return False
        self.send_json({"ok": False, "error": "cross-origin request rejected"}, 403)
        return True

    def serve_custom_period(self) -> None:
        """Custom period over the granular tables (any range = a SQL query).
        Accepts either `from`/`to` (YYYY-MM-DD) or `days=N`; returns the rendered
        period-panels fragment + the resolved range."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        qs = parse_qs(urlparse(self.path).query)
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if frm or to:
            if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
                self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
                return
            since = (frm or "2008-01-01") + "T00:00:00Z"
            until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
            label_range = f"{(frm or '2008-01-01')} → {to or 'today'}"
        else:
            try:
                days = max(1, min(int(qs.get("days", ["30"])[0]), 36500))
            except (ValueError, IndexError):
                self.send_json({"ok": False, "error": "invalid days"}, 400)
                return
            since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            label_range = f"last {days} days (since {since[:10]})"
        if since > until:
            self.send_json({"ok": False, "error": "from is after to"}, 400)
            return
        if not Path(store.db_path()).exists():
            # connect() would create an empty DB and return zero panels
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return
        # optional global slice: scope every windowed panel to a repo subset
        import re as _re2
        scope = (qs.get("scope", [""])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re2.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        conn = None
        try:
            conn = store.connect()
            repos = None
            if scope:
                import discovery
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            pr = store.aggregate(conn, since, until, label="custom", repos=repos)
            pr["delivery"] = {}          # delivery panels are served by /api/delivery
            try:                         # developer-score rollup for the Overview panel
                pr["score"] = store.score_summary(conn, since, until, repos=repos)
            except Exception as exc:     # noqa: BLE001 — never break the period fetch
                log_degraded("Overview score rollup (/api/period)", exc)
                pr["score"] = None
            # Period-over-period deltas vs the immediately preceding equal-length
            # window. Skipped for very long / all-time spans (>2y), where the prior
            # window predates collection and a delta would be meaningless.
            try:
                ds = datetime.fromisoformat(since.replace("Z", "+00:00"))
                du = datetime.fromisoformat(until.replace("Z", "+00:00"))
                span = du - ds
                if 0 < span.days <= 731:
                    p_since = (ds - span).strftime("%Y-%m-%dT%H:%M:%SZ")
                    prev = store.aggregate(conn, p_since, since, label="prev", repos=repos)
                    pr["deltas"] = render.delta_map(pr["totals"], prev["totals"])
            except Exception as exc:        # noqa: BLE001 — deltas are best-effort
                log_degraded("Overview period-over-period deltas (/api/period)", exc)
            self.send_json({"ok": True, "range": label_range,
                            "since": since[:10], "until": until[:10],
                            "html": render.render_period_fragment(pr, period_ctx())})
        except Exception as exc:           # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 500)
        finally:
            if conn is not None:
                conn.close()

    _OVERVIEW_PRESET_DAYS = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}

    def serve_report_overview(self) -> None:
        """JSON data for the /overview React route (see render.overview_json):
        GET /api/report/overview?p=<preset>&slice=<level:target>&from=&to= .
        Same period/scope semantics as /api/period (serve_custom_period) — this
        is that endpoint's JSON sibling for the migrated view, built on the SAME
        store.aggregate()/score_summary() calls so the numbers can never drift
        from what /api/period would render as HTML for the identical query.

        The "default" query (no p/from/to/scope) skips the DB round-trip
        entirely and serves render.build_model()'s cached `all_block` — the
        EXACT block the build-time page/report.j2 paints, so the default
        Overview state matches the monolith's fast path byte-for-byte, not
        just numerically."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        qs = parse_qs(urlparse(self.path).query)
        preset = (qs.get("p", [""])[0] or "").strip()
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        scope = (qs.get("slice", [qs.get("scope", [""])[0]])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
            self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
            return
        if preset and preset not in self._OVERVIEW_PRESET_DAYS and preset != "all":
            self.send_json({"ok": False, "error": "invalid p"}, 400)
            return

        default_state = not (preset or frm or to or scope)
        since = until = None
        period_kind = None    # ('custom'|'preset'|'all', ...) resolved into `period` once meta exists
        if not default_state:
            now = datetime.now(timezone.utc).replace(microsecond=0)
            if frm or to:
                since = (frm or "2008-01-01") + "T00:00:00Z"
                until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("custom", frm, to)
            elif preset and preset != "all":
                days = self._OVERVIEW_PRESET_DAYS[preset]
                since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
                until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("preset", preset)
            else:  # preset == 'all' (possibly with a scope) — mirrors the client's
                   # _personQS = 'from=2008-01-01' path, NOT the build-time snapshot,
                   # since a scope forces a live re-aggregate over repos=<scoped>.
                since = "2008-01-01T00:00:00Z"
                until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("all",)
            # Same guard as /api/period (serve_custom_period) — checked BEFORE the
            # DB/model lookups below so a bad range 400s even with no data collected.
            if since > until:
                self.send_json({"ok": False, "error": "from is after to"}, 400)
                return

        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return

        try:
            version = _report_version()
            model = _report_model(version)
        except Exception as exc:           # noqa: BLE001 — SystemExit et al: no model yet
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        meta = dict(model.get("meta") or {})
        meta["all_label"] = model.get("all_label")
        meta["contrib_block"] = model.get("contrib_block")
        meta["scope_targets"] = model.get("scope_targets")
        meta["data_quality"] = model.get("data_quality")
        meta["generated"] = model.get("generated")
        meta["window_labels"] = model.get("window_labels")

        if default_state:
            meta["scope"] = ""
            meta["period"] = {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
            self.send_json(render.overview_json(model["all_block"], meta))
            return

        labels = {"7d": "7 days", "30d": "30 days", "90d": "90 days", "365d": "1 year"}
        if period_kind[0] == "custom":
            _, frm, to = period_kind
            period = {"preset": "custom", "label": f"{frm or '2008-01-01'} → {to or 'today'}",
                      "from": frm or None, "to": to or None}
        elif period_kind[0] == "preset":
            period = {"preset": preset, "label": labels[preset], "from": None, "to": None}
        else:
            period = {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
        meta["period"] = period
        meta["scope"] = scope

        conn = None
        try:
            conn = store.connect()
            repos = None
            if scope:
                import discovery
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            pr = store.aggregate(conn, since, until, label="custom", repos=repos)
            try:
                pr["score"] = store.score_summary(conn, since, until, repos=repos)
            except Exception as exc:       # noqa: BLE001 — never break the endpoint
                log_degraded("Overview score rollup (/api/report/overview)", exc)
                pr["score"] = None
            try:
                ds = datetime.fromisoformat(since.replace("Z", "+00:00"))
                du = datetime.fromisoformat(until.replace("Z", "+00:00"))
                span = du - ds
                if 0 < span.days <= 731:
                    p_since = (ds - span).strftime("%Y-%m-%dT%H:%M:%SZ")
                    prev = store.aggregate(conn, p_since, since, label="prev", repos=repos)
                    pr["deltas"] = render.delta_map(pr["totals"], prev["totals"])
            except Exception as exc:        # noqa: BLE001 — deltas are best-effort
                log_degraded("Overview deltas (/api/report/overview)", exc)
            self.send_json(render.overview_json(pr, meta))
        except Exception as exc:           # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 500)
        finally:
            if conn is not None:
                conn.close()

    def serve_report_trend(self) -> None:
        """JSON data for the /trend React route (see render.trend_json):
        GET /api/report/trend?p=<preset>&slice=<level:target>&from=&to=&gran=&dim= .
        Same period/scope semantics as /api/report/overview (serve_report_overview)
        — this is the Trend view's JSON sibling, built on the SAME
        store.aggregate(..., trend_gran=, trend_dim=) call the /api/trend HTML
        endpoint (serve_trend) uses, so the numbers can never drift from what
        /api/trend renders as HTML for the identical query. Unlike Overview,
        Trend has no build-time cached block to fast-path (build_model's
        all_block always carries `ctrend: None` — see render.build_model) so
        every request is a live DB round-trip, even the default state."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        qs = parse_qs(urlparse(self.path).query)
        preset = (qs.get("p", [""])[0] or "").strip()
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        scope = (qs.get("slice", [qs.get("scope", [""])[0]])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
            self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
            return
        if preset and preset not in self._OVERVIEW_PRESET_DAYS and preset != "all":
            self.send_json({"ok": False, "error": "invalid p"}, 400)
            return
        gran = (qs.get("gran", ["auto"])[0] or "auto").strip()
        if gran not in ("auto", "day", "week", "month", "quarter"):
            self.send_json({"ok": False, "error": "invalid gran"}, 400)
            return
        dim = (qs.get("dim", ["company"])[0] or "company").strip()
        if dim not in ("company", "work_type", "repo_type", "element"):
            self.send_json({"ok": False, "error": "invalid dim"}, 400)
            return

        now = datetime.now(timezone.utc).replace(microsecond=0)
        period = None   # resolved to the all-time label once `meta` exists, below
        if frm or to:
            since = (frm or "2008-01-01") + "T00:00:00Z"
            until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
            period = {"preset": "custom", "label": f"{frm or '2008-01-01'} → {to or 'today'}",
                      "from": frm or None, "to": to or None}
        elif preset and preset != "all":
            days = self._OVERVIEW_PRESET_DAYS[preset]
            since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            labels = {"7d": "7 days", "30d": "30 days", "90d": "90 days", "365d": "1 year"}
            period = {"preset": preset, "label": labels[preset], "from": None, "to": None}
        else:
            since = "2008-01-01T00:00:00Z"
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        if since > until:
            self.send_json({"ok": False, "error": "from is after to"}, 400)
            return

        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return

        try:
            version = _report_version()
            model = _report_model(version)
        except Exception as exc:           # noqa: BLE001 — SystemExit et al: no model yet
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        meta = dict(model.get("meta") or {})
        meta["scope_targets"] = model.get("scope_targets")
        meta["generated"] = model.get("generated")
        meta["window_labels"] = model.get("window_labels")
        meta["all_label"] = model.get("all_label")
        meta["period"] = period or {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
        meta["scope"] = scope

        conn = None
        try:
            conn = store.connect()
            repos = None
            if scope:
                import discovery
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            pr = store.aggregate(conn, since, until, label="trend", repos=repos,
                                  trend_gran=gran, trend_dim=dim)
            self.send_json(render.trend_json(pr, meta))
        except Exception as exc:           # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 500)
        finally:
            if conn is not None:
                conn.close()

    def serve_report_delivery(self) -> None:
        """JSON data for the /delivery React route (see render.delivery_json):
        GET /api/report/delivery?p=<preset>&slice=<level:target>&from=&to= .
        Same period/scope query contract as /api/report/overview and
        /api/report/trend (serve_report_overview/serve_report_trend) — this is
        the Delivery view's JSON sibling, built on the SAME semantic_metrics.
        window_block()/delivery_metrics() calls the /api/delivery HTML endpoint
        (serve_delivery, which uses days=/scope=) uses, so the numbers can never
        drift from what /api/delivery renders as HTML for an equivalent window.
        Delivery has no build-time cached block either (like Trend) —
        templates/report.j2's refreshDelivery() always live-fetches /api/delivery,
        even for the default state — so every request here is a live DB
        round-trip too."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        import discovery
        import semantic_metrics
        qs = parse_qs(urlparse(self.path).query)
        preset = (qs.get("p", [""])[0] or "").strip()
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        scope = (qs.get("slice", [qs.get("scope", [""])[0]])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
            self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
            return
        if preset and preset not in self._OVERVIEW_PRESET_DAYS and preset != "all":
            self.send_json({"ok": False, "error": "invalid p"}, 400)
            return

        now = datetime.now(timezone.utc).replace(microsecond=0)
        period = None   # resolved to the all-time label once `meta` exists, below
        if frm or to:
            since = (frm or "2008-01-01") + "T00:00:00Z"
            until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
            period = {"preset": "custom", "label": f"{frm or '2008-01-01'} → {to or 'today'}",
                      "from": frm or None, "to": to or None}
        elif preset and preset != "all":
            days = self._OVERVIEW_PRESET_DAYS[preset]
            since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            labels = {"7d": "7 days", "30d": "30 days", "90d": "90 days", "365d": "1 year"}
            period = {"preset": preset, "label": labels[preset], "from": None, "to": None}
        else:
            since = "2008-01-01T00:00:00Z"
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        if since > until:
            self.send_json({"ok": False, "error": "from is after to"}, 400)
            return

        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return

        try:
            version = _report_version()
            model = _report_model(version)
        except Exception as exc:           # noqa: BLE001 — SystemExit et al: no model yet
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        meta = dict(model.get("meta") or {})
        meta["scope_targets"] = model.get("scope_targets")
        meta["generated"] = model.get("generated")
        meta["window_labels"] = model.get("window_labels")
        meta["all_label"] = model.get("all_label")
        meta["period"] = period or {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
        meta["scope"] = scope

        conn = None
        try:
            conn = store.connect()
            repos = None
            if scope:
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            block = semantic_metrics.window_block(conn, since, until, repos)
            # period-over-period deltas vs the preceding equal window (skipped for
            # all-time / >2y spans) — same rule serve_delivery uses for the HTML path.
            try:
                ds = datetime.fromisoformat(since.replace("Z", "+00:00"))
                du = datetime.fromisoformat(until.replace("Z", "+00:00"))
                span = du - ds
                if 0 < span.days <= 731:
                    p_since = (ds - span).strftime("%Y-%m-%dT%H:%M:%SZ")
                    prev = semantic_metrics.delivery_metrics(conn, p_since, since, repos)
                    block["deltas"] = render.delta_map(block, prev, keys=semantic_metrics.DELIVERY_KPI_KEYS)
            except Exception as exc:        # noqa: BLE001 — deltas are best-effort
                log_degraded("Delivery deltas (/api/report/delivery)", exc)
            pr = {"delivery": block}
            self.send_json(render.delivery_json(pr, meta))
        except Exception as exc:           # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 500)
        finally:
            if conn is not None:
                conn.close()

    def serve_report_flow(self) -> None:
        """JSON data for the /flow React route (see render.flow_json):
        GET /api/report/flow?p=<preset>&slice=<level:target>&from=&to= .
        Same period/scope query contract as /api/report/overview/trend/
        delivery — this is the Flow view's JSON sibling, built on the SAME
        semantic_metrics.flow_report() call the /api/flow HTML endpoint
        (serve_flow, which uses days=/scope=) uses, so the numbers can never
        drift from what /api/flow renders as HTML for an equivalent window.
        Flow has no build-time cached block either (like Trend/Delivery) —
        every request here is a live DB round-trip, even the default state."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        import discovery
        import semantic_metrics
        qs = parse_qs(urlparse(self.path).query)
        preset = (qs.get("p", [""])[0] or "").strip()
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        scope = (qs.get("slice", [qs.get("scope", [""])[0]])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
            self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
            return
        if preset and preset not in self._OVERVIEW_PRESET_DAYS and preset != "all":
            self.send_json({"ok": False, "error": "invalid p"}, 400)
            return

        now = datetime.now(timezone.utc).replace(microsecond=0)
        period = None   # resolved to the all-time label once `meta` exists, below
        if frm or to:
            since = (frm or "2008-01-01") + "T00:00:00Z"
            until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
            period = {"preset": "custom", "label": f"{frm or '2008-01-01'} → {to or 'today'}",
                      "from": frm or None, "to": to or None}
        elif preset and preset != "all":
            days = self._OVERVIEW_PRESET_DAYS[preset]
            since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            labels = {"7d": "7 days", "30d": "30 days", "90d": "90 days", "365d": "1 year"}
            period = {"preset": preset, "label": labels[preset], "from": None, "to": None}
        else:
            since = "2008-01-01T00:00:00Z"
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        if since > until:
            self.send_json({"ok": False, "error": "from is after to"}, 400)
            return

        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return

        try:
            version = _report_version()
            model = _report_model(version)
        except Exception as exc:           # noqa: BLE001 — SystemExit et al: no model yet
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        meta = dict(model.get("meta") or {})
        meta["scope_targets"] = model.get("scope_targets")
        meta["generated"] = model.get("generated")
        meta["window_labels"] = model.get("window_labels")
        meta["all_label"] = model.get("all_label")
        meta["period"] = period or {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
        meta["scope"] = scope

        conn = None
        try:
            conn = store.connect()
            repos = None
            if scope:
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            # in_flight takes no since/until on purpose — it is a point-in-time
            # quantity that must not move with the period control (see store.in_flight).
            pr = {"flow": semantic_metrics.flow_report(conn, repos, since, until),
                  "in_flight": store.in_flight(conn, repos),
                  # abandoned IS windowed (by closed_at) — the opposite of in_flight,
                  # deliberately; see store.abandoned_prs.
                  "abandoned": store.abandoned_prs(conn, since, until, repos)}
            self.send_json(render.flow_json(pr, meta))
        except Exception as exc:           # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 500)
        finally:
            if conn is not None:
                conn.close()

    def serve_report_people(self) -> None:
        """JSON data for the /people React route (see render.people_json):
        GET /api/report/people?p=<preset>&slice=<level:target>&from=&to= .
        Same period/scope query contract as /api/report/overview (serve_report_
        overview) — People has no separate collector, it reads the SAME
        store.aggregate()/build_model() aggregate block Overview does (pr['people']/
        ['categories']/['reviews']/['split']), so this endpoint is a near-copy of
        serve_report_overview's shape, just handed to render.people_json instead.

        The "default" query (no p/from/to/scope) skips the DB round-trip
        entirely and serves render.build_model()'s cached `all_block` — the
        EXACT block the build-time page/report.j2 paints, so the default
        People state matches the monolith's fast path byte-for-byte, not just
        numerically (mirrors serve_report_overview's own fast path)."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        qs = parse_qs(urlparse(self.path).query)
        preset = (qs.get("p", [""])[0] or "").strip()
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        scope = (qs.get("slice", [qs.get("scope", [""])[0]])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
            self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
            return
        if preset and preset not in self._OVERVIEW_PRESET_DAYS and preset != "all":
            self.send_json({"ok": False, "error": "invalid p"}, 400)
            return

        default_state = not (preset or frm or to or scope)
        since = until = None
        period_kind = None    # ('custom'|'preset'|'all', ...) resolved into `period` once meta exists
        if not default_state:
            now = datetime.now(timezone.utc).replace(microsecond=0)
            if frm or to:
                since = (frm or "2008-01-01") + "T00:00:00Z"
                until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("custom", frm, to)
            elif preset and preset != "all":
                days = self._OVERVIEW_PRESET_DAYS[preset]
                since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
                until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("preset", preset)
            else:  # preset == 'all' (possibly with a scope) — same rationale as
                   # serve_report_overview: a scope forces a live re-aggregate.
                since = "2008-01-01T00:00:00Z"
                until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("all",)
            if since > until:
                self.send_json({"ok": False, "error": "from is after to"}, 400)
                return

        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return

        try:
            version = _report_version()
            model = _report_model(version)
        except Exception as exc:           # noqa: BLE001 — SystemExit et al: no model yet
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        meta = dict(model.get("meta") or {})
        meta["all_label"] = model.get("all_label")
        meta["scope_targets"] = model.get("scope_targets")
        meta["data_quality"] = model.get("data_quality")
        meta["generated"] = model.get("generated")
        meta["window_labels"] = model.get("window_labels")
        meta["emails_by_login"] = model.get("emails_by_login", {})
        meta["reviews_by_company"] = model.get("reviews_by_company")
        meta["reviews_by_repo"] = model.get("reviews_by_repo")
        meta["legacy_names"] = model.get("legacy_names")

        if default_state:
            meta["scope"] = ""
            meta["period"] = {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
            self.send_json(render.people_json(model["all_block"], meta))
            return

        labels = {"7d": "7 days", "30d": "30 days", "90d": "90 days", "365d": "1 year"}
        if period_kind[0] == "custom":
            _, frm, to = period_kind
            period = {"preset": "custom", "label": f"{frm or '2008-01-01'} → {to or 'today'}",
                      "from": frm or None, "to": to or None}
        elif period_kind[0] == "preset":
            period = {"preset": preset, "label": labels[preset], "from": None, "to": None}
        else:
            period = {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
        meta["period"] = period
        meta["scope"] = scope

        conn = None
        try:
            conn = store.connect()
            repos = None
            if scope:
                import discovery
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            pr = store.aggregate(conn, since, until, label="custom", repos=repos)
            self.send_json(render.people_json(pr, meta))
        except Exception as exc:           # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 500)
        finally:
            if conn is not None:
                conn.close()

    def _developer_score_block(self, conn, login: str, since: str, until: str):
        """The person page's EXPERIMENTAL developer-score payload, as
        (score, unavailable) — exactly one of the two is None.

        The board is org-relative for this window; every row is compared to the
        SUBJECT of the page (usually you), so it reads as "how to catch up / where
        I lead" rather than neighbour-vs-neighbour, and `is_self_view` decides
        whether the copy addresses the viewer as "you".

        The panel is optional by design — a broken score must not take the person
        dashboard with it. But "optional" was implemented as `except Exception:
        score = None`, and render drops the block whenever the board is empty, so a
        raising builder and a window with nobody in it produced the SAME page and
        NO log line anywhere: silent by construction (see log_degraded). Hence the
        second return value. It carries the reason to the UI so the page can say
        which of the two happened, while the traceback goes to the log for whoever
        has to fix it. Shared by both person endpoints (/api/report/person and the
        legacy /api/person fragment) so they cannot diverge on this.
        """
        import store
        try:
            sc = store.developer_scores(conn, since, until)
            subj = sc["by_login"].get(login)
            board = sc["board"]           # full board; the UI reveals past the top 15
            if subj is not None:
                for r in board:
                    r["vs_self"] = store.compare_row_to(r, subj, sc["active_pillars"])
            viewer = None
            for ident in self._oauth_idents():
                viewer = store.person_login_for(conn, ident)
                if viewer:
                    break
            return {"self": subj, "board": board, "weights": sc["weights"],
                    "n_eligible": sc["n_eligible"], "n_ranked": sc["n_ranked"],
                    "active_pillars": sc["active_pillars"], "team_medians": sc["team_medians"],
                    "min_activity": sc["min_activity"],
                    "is_self_view": (viewer is not None and viewer == login)}, None
        except Exception as exc:           # noqa: BLE001 — never break the dashboard
            log_degraded(f"developer score for {login} ({since[:10]}→{until[:10]})", exc)
            return None, {"reason": "error",
                          "detail": "it could not be computed for this window "
                                    "(the failure is in the server log)"}

    def serve_report_person(self) -> None:
        """JSON data for the /person React route (see render.person_json):
        GET /api/report/person?person=<login>&p=<preset>&from=&to=&slice= .

        Two shapes share one envelope: WITHOUT `person`, just the picker
        (personOptions/personCompanies from the cached model) + the "pick a
        person" hint state; WITH `person`, the full per-person dashboard for the
        report's period window (profile totals + deltas + shares, all-time impact,
        gh-profile, heat, the weekly table, and the experimental score block),
        built from the SAME store.* builders serve_person uses.

        The window follows the period params (p/from/to) exactly like
        serve_report_people; `slice` is accepted (so the shared FilterBar can show
        it) but — as in the monolith, whose loadPerson() sends only the period
        query — it does NOT scope the person dashboard."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        qs = parse_qs(urlparse(self.path).query)
        person = (qs.get("person", [""])[0] or "").strip()
        if person and not _re.match(r"^[A-Za-z0-9-]{1,39}$", person):
            self.send_json({"ok": False, "error": "invalid person"}, 400)
            return
        preset = (qs.get("p", [""])[0] or "").strip()
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        scope = (qs.get("slice", [qs.get("scope", [""])[0]])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
            self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
            return
        if preset and preset not in self._OVERVIEW_PRESET_DAYS and preset != "all":
            self.send_json({"ok": False, "error": "invalid p"}, 400)
            return

        now = datetime.now(timezone.utc).replace(microsecond=0)
        if frm or to:
            since = (frm or "2008-01-01") + "T00:00:00Z"
            until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
            period = {"preset": "custom", "label": f"{frm or '2008-01-01'} → {to or 'today'}",
                      "from": frm or None, "to": to or None}
        elif preset and preset != "all":
            days = self._OVERVIEW_PRESET_DAYS[preset]
            since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            period = {"preset": preset,
                      "label": {"7d": "7 days", "30d": "30 days", "90d": "90 days",
                                "365d": "1 year"}[preset], "from": None, "to": None}
        else:
            since = "2008-01-01T00:00:00Z"
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            period = None    # filled from the model's all_label once it's loaded
        if since > until:
            self.send_json({"ok": False, "error": "from is after to"}, 400)
            return

        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return

        try:
            model = _report_model(_report_version())
        except Exception as exc:           # noqa: BLE001 — SystemExit et al: no model yet
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        meta = dict(model.get("meta") or {})
        meta["all_label"] = model.get("all_label")
        meta["scope_targets"] = model.get("scope_targets")
        meta["data_quality"] = model.get("data_quality")
        meta["generated"] = model.get("generated")
        meta["window_labels"] = model.get("window_labels")
        meta["scope"] = scope
        meta["person"] = person or None
        meta["person_options"] = model.get("person_options") or []
        meta["person_companies"] = model.get("person_companies") or []
        meta["period"] = period or {"preset": "all", "label": model.get("all_label"),
                                    "from": None, "to": None}

        if not person:
            self.send_json(render.person_json(None, meta))
            return

        conn = None
        try:
            conn = store.connect()
            pw = store.person_weekly(conn, person, since, until)
            prof = store.person_profile(conn, person, since, until)
            # period-over-period deltas for the KPI tiles vs the preceding equal
            # window (skipped for all-time / >2y spans — same rule as serve_person)
            try:
                ds = datetime.fromisoformat(since.replace("Z", "+00:00"))
                du = datetime.fromisoformat(until.replace("Z", "+00:00"))
                span = du - ds
                if 0 < span.days <= 731:
                    p_since = (ds - span).strftime("%Y-%m-%dT%H:%M:%SZ")
                    prev = store.person_totals(conn, person, p_since, since)
                    prof["deltas"] = render.delta_map(
                        prof["totals"], prev,
                        keys=("commits", "meaningful_additions", "prs", "specs", "bugs", "epics", "features"))
            except Exception as exc:        # noqa: BLE001 — deltas are best-effort
                # Left as a swallow on purpose: a missing delta drops the small
                # "+12% vs previous" line under a KPI, the tile still shows its real
                # number, and there is nothing for a reader to act on. It logs
                # because "nothing to act on" is not the same as "nobody may know".
                log_degraded(f"person KPI deltas for {person} (/api/report/person)", exc)
            alltime = {r["login"]: r for r in (model.get("table") or [])}.get(person, {})
            heat = [{"week": r["week"], "commits": sum(c["commits"] for c in r["cells"] if c),
                     "issues": r["issues"]} for r in pw["rows"]]
            score, score_unavailable = self._developer_score_block(conn, person, since, until)
            dash = {"weekly": pw, "profile": prof, "alltime": alltime, "heat": heat,
                    "emails": (model.get("emails_by_login") or {}).get(person, ""),
                    "login": person, "gh_profile": store.gh_profile(conn, person), "score": score,
                    "score_unavailable": score_unavailable}
            self.send_json(render.person_json(dash, meta))
        except Exception as exc:           # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 500)
        finally:
            if conn is not None:
                conn.close()

    def serve_report_repositories(self) -> None:
        """JSON data for the /repositories React route (see
        render.repositories_json): GET /api/report/repositories?p=<preset>&slice=
        <level:target>&from=&to= . Same period/scope query contract as
        /api/report/people (serve_report_people).

        The Repositories view is mostly ALL-TIME: the repo-coverage summary,
        inventory table and unclassified chips come straight from the cached
        build_model() (repo_summary/repo_rows/unclassified — threaded through
        `meta`), unaffected by the period/scope. Only the "Where effort goes —
        by repository type" split panel is period-scoped (pr['split'], the SAME
        block Overview/People read). So the default (no p/from/to/scope) query
        skips the DB and serves build_model()'s cached `all_block`, exactly like
        serve_report_people's fast path; any other query re-aggregates."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        qs = parse_qs(urlparse(self.path).query)
        preset = (qs.get("p", [""])[0] or "").strip()
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        scope = (qs.get("slice", [qs.get("scope", [""])[0]])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
            self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
            return
        if preset and preset not in self._OVERVIEW_PRESET_DAYS and preset != "all":
            self.send_json({"ok": False, "error": "invalid p"}, 400)
            return

        default_state = not (preset or frm or to or scope)
        since = until = None
        period_kind = None
        if not default_state:
            now = datetime.now(timezone.utc).replace(microsecond=0)
            if frm or to:
                since = (frm or "2008-01-01") + "T00:00:00Z"
                until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("custom", frm, to)
            elif preset and preset != "all":
                days = self._OVERVIEW_PRESET_DAYS[preset]
                since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
                until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("preset", preset)
            else:  # preset == 'all' (possibly with a scope) — a scope forces a live re-aggregate.
                since = "2008-01-01T00:00:00Z"
                until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("all",)
            if since > until:
                self.send_json({"ok": False, "error": "from is after to"}, 400)
                return

        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return

        try:
            model = _report_model(_report_version())
        except Exception as exc:           # noqa: BLE001 — SystemExit et al: no model yet
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        meta = dict(model.get("meta") or {})
        meta["all_label"] = model.get("all_label")
        meta["scope_targets"] = model.get("scope_targets")
        meta["data_quality"] = model.get("data_quality")
        meta["generated"] = model.get("generated")
        meta["window_labels"] = model.get("window_labels")
        # All-time repo inventory (build-time, NOT period-scoped) — the whole
        # "Repo coverage" section reads these regardless of the filter.
        meta["repo_summary"] = model.get("repo_summary")
        meta["repo_rows"] = model.get("repo_rows")
        meta["unclassified"] = model.get("unclassified")

        if default_state:
            meta["scope"] = ""
            meta["period"] = {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
            self.send_json(render.repositories_json(model["all_block"], meta))
            return

        labels = {"7d": "7 days", "30d": "30 days", "90d": "90 days", "365d": "1 year"}
        if period_kind[0] == "custom":
            _, frm, to = period_kind
            period = {"preset": "custom", "label": f"{frm or '2008-01-01'} → {to or 'today'}",
                      "from": frm or None, "to": to or None}
        elif period_kind[0] == "preset":
            period = {"preset": preset, "label": labels[preset], "from": None, "to": None}
        else:
            period = {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
        meta["period"] = period
        meta["scope"] = scope

        conn = None
        try:
            conn = store.connect()
            repos = None
            if scope:
                import discovery
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            pr = store.aggregate(conn, since, until, label="custom", repos=repos)
            self.send_json(render.repositories_json(pr, meta))
        except Exception as exc:           # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 500)
        finally:
            if conn is not None:
                conn.close()

    def serve_report_elements(self) -> None:
        """JSON data for the /elements React route (see render.elements_json):
        GET /api/report/elements?p=<preset>&slice=<level:target>&from=&to= . Same
        period/scope query contract as /api/report/repositories.

        The "By Element" rollup is period/slice-scoped (each row's commits / PRs /
        people / AI% follow the window, its LOC stays all-time). So the default
        (no p/from/to/scope) query skips the DB and serves build_model()'s cached
        `all_block` element_rows, exactly like serve_report_repositories' fast
        path; any other query re-aggregates (store.aggregate → element_rows)."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        qs = parse_qs(urlparse(self.path).query)
        preset = (qs.get("p", [""])[0] or "").strip()
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        scope = (qs.get("slice", [qs.get("scope", [""])[0]])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
            self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
            return
        if preset and preset not in self._OVERVIEW_PRESET_DAYS and preset != "all":
            self.send_json({"ok": False, "error": "invalid p"}, 400)
            return

        default_state = not (preset or frm or to or scope)
        since = until = None
        period_kind = None
        if not default_state:
            now = datetime.now(timezone.utc).replace(microsecond=0)
            if frm or to:
                since = (frm or "2008-01-01") + "T00:00:00Z"
                until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("custom", frm, to)
            elif preset and preset != "all":
                days = self._OVERVIEW_PRESET_DAYS[preset]
                since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
                until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("preset", preset)
            else:  # preset == 'all' (possibly with a scope) — a scope forces a live re-aggregate.
                since = "2008-01-01T00:00:00Z"
                until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("all",)
            if since > until:
                self.send_json({"ok": False, "error": "from is after to"}, 400)
                return

        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return

        try:
            model = _report_model(_report_version())
        except Exception as exc:           # noqa: BLE001 — SystemExit et al: no model yet
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        meta = dict(model.get("meta") or {})
        meta["all_label"] = model.get("all_label")
        meta["scope_targets"] = model.get("scope_targets")
        meta["data_quality"] = model.get("data_quality")
        meta["generated"] = model.get("generated")
        meta["window_labels"] = model.get("window_labels")

        if default_state:
            meta["scope"] = ""
            meta["period"] = {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
            self.send_json(render.elements_json(model["all_block"], meta))
            return

        labels = {"7d": "7 days", "30d": "30 days", "90d": "90 days", "365d": "1 year"}
        if period_kind[0] == "custom":
            _, frm, to = period_kind
            period = {"preset": "custom", "label": f"{frm or '2008-01-01'} → {to or 'today'}",
                      "from": frm or None, "to": to or None}
        elif period_kind[0] == "preset":
            period = {"preset": preset, "label": labels[preset], "from": None, "to": None}
        else:
            period = {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
        meta["period"] = period
        meta["scope"] = scope

        conn = None
        try:
            conn = store.connect()
            repos = None
            if scope:
                import discovery
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            pr = store.aggregate(conn, since, until, label="custom", repos=repos)
            self.send_json(render.elements_json(pr, meta))
        except Exception as exc:           # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 500)
        finally:
            if conn is not None:
                conn.close()

    def serve_report_traffic(self) -> None:
        """JSON data for the /traffic React route (see render.traffic_json):
        GET /api/report/traffic?p=<preset>&slice=<level:target>&from=&to= . Same
        period/scope query contract as /api/report/elements.

        Two provenances (mirroring the monolith's mode="usage" sections):
          • "The two scenarios" (contributors / non-contributors / stars / forks)
            and "External contributors" are ALL-TIME — they live outside any
            data-period-panel region and never vary with the filter, so they come
            straight from the cached build_model() (contributors / members_contrib
            / external_contributors / non_contributors / total_stars / total_forks
            / platform_repos / emails_by_login — threaded through `meta`).
          • Only the "Traffic — clones & page views" panel is period/slice-scoped
            (pr['traffic']). So the default (no p/from/to/scope) query skips the DB
            and serves build_model()'s cached `all_block`, exactly like
            serve_report_elements' fast path; any other query re-aggregates."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        qs = parse_qs(urlparse(self.path).query)
        preset = (qs.get("p", [""])[0] or "").strip()
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        scope = (qs.get("slice", [qs.get("scope", [""])[0]])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
            self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
            return
        if preset and preset not in self._OVERVIEW_PRESET_DAYS and preset != "all":
            self.send_json({"ok": False, "error": "invalid p"}, 400)
            return

        default_state = not (preset or frm or to or scope)
        since = until = None
        period_kind = None
        if not default_state:
            now = datetime.now(timezone.utc).replace(microsecond=0)
            if frm or to:
                since = (frm or "2008-01-01") + "T00:00:00Z"
                until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("custom", frm, to)
            elif preset and preset != "all":
                days = self._OVERVIEW_PRESET_DAYS[preset]
                since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
                until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("preset", preset)
            else:  # preset == 'all' (possibly with a scope) — a scope forces a live re-aggregate.
                since = "2008-01-01T00:00:00Z"
                until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("all",)
            if since > until:
                self.send_json({"ok": False, "error": "from is after to"}, 400)
                return

        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return

        try:
            model = _report_model(_report_version())
        except Exception as exc:           # noqa: BLE001 — SystemExit et al: no model yet
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        meta = dict(model.get("meta") or {})
        meta["all_label"] = model.get("all_label")
        meta["scope_targets"] = model.get("scope_targets")
        meta["data_quality"] = model.get("data_quality")
        meta["generated"] = model.get("generated")
        meta["window_labels"] = model.get("window_labels")
        # All-time scenario data (build-time, NOT period-scoped) — "The two
        # scenarios" + "External contributors" read these regardless of filter.
        meta["contributors"] = model.get("contributors")
        meta["members_contrib"] = model.get("members_contrib")
        meta["external_contributors"] = model.get("external_contributors")
        meta["non_contributors"] = model.get("non_contributors")
        meta["total_stars"] = model.get("total_stars")
        meta["total_forks"] = model.get("total_forks")
        meta["platform_repos"] = model.get("platform_repos")
        meta["emails_by_login"] = model.get("emails_by_login")

        if default_state:
            meta["scope"] = ""
            meta["period"] = {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
            self.send_json(render.traffic_json(model["all_block"], meta))
            return

        labels = {"7d": "7 days", "30d": "30 days", "90d": "90 days", "365d": "1 year"}
        if period_kind[0] == "custom":
            _, frm, to = period_kind
            period = {"preset": "custom", "label": f"{frm or '2008-01-01'} → {to or 'today'}",
                      "from": frm or None, "to": to or None}
        elif period_kind[0] == "preset":
            period = {"preset": preset, "label": labels[preset], "from": None, "to": None}
        else:
            period = {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
        meta["period"] = period
        meta["scope"] = scope

        conn = None
        try:
            conn = store.connect()
            repos = None
            if scope:
                import discovery
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            pr = store.aggregate(conn, since, until, label="custom", repos=repos)
            self.send_json(render.traffic_json(pr, meta))
        except Exception as exc:           # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 500)
        finally:
            if conn is not None:
                conn.close()

    def serve_report_ai_tools(self) -> None:
        """JSON data for the /ai-tools React route (see render.ai_tools_json):
        GET /api/report/ai-tools?p=<preset>&slice=<level:target>&from=&to= . Same
        period/scope query contract as /api/report/traffic. This is the monolith's
        `fabric` mode, renamed ai-tools per the migration spec's redirect table.

        Two provenances (mirroring the monolith's mode="fabric" sections):
          • Only the AI-usage panel (pr['ai_usage']) and the Bots mini stats
            (pr['bots']) are period/slice-scoped — they're the sole fabric panels
            wrapped in a [data-period-panel] region. So the default (no p/from/to/
            scope) query skips the DB and serves build_model()'s cached
            `all_block`, exactly like serve_report_traffic's fast path; any other
            query re-aggregates.
          • Everything else (studio provenance, gears usage, fabric trackers,
            assistant-by-company/-person, the fabric-usage rollup and the per-bot
            detail table) is ALL-TIME content that lives outside any period panel,
            threaded through `meta` from the cached build_model() and never varies
            with the filter. `meta['bots_all']` is the TOP-LEVEL all-time bots
            roster (the detail table's source), distinct from the period-scoped
            `pr['bots']` the mini stats read."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        qs = parse_qs(urlparse(self.path).query)
        preset = (qs.get("p", [""])[0] or "").strip()
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        scope = (qs.get("slice", [qs.get("scope", [""])[0]])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
            self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
            return
        if preset and preset not in self._OVERVIEW_PRESET_DAYS and preset != "all":
            self.send_json({"ok": False, "error": "invalid p"}, 400)
            return

        default_state = not (preset or frm or to or scope)
        since = until = None
        period_kind = None
        if not default_state:
            now = datetime.now(timezone.utc).replace(microsecond=0)
            if frm or to:
                since = (frm or "2008-01-01") + "T00:00:00Z"
                until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("custom", frm, to)
            elif preset and preset != "all":
                days = self._OVERVIEW_PRESET_DAYS[preset]
                since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
                until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("preset", preset)
            else:  # preset == 'all' (possibly with a scope) — a scope forces a live re-aggregate.
                since = "2008-01-01T00:00:00Z"
                until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                period_kind = ("all",)
            if since > until:
                self.send_json({"ok": False, "error": "from is after to"}, 400)
                return

        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return

        try:
            model = _report_model(_report_version())
        except Exception as exc:           # noqa: BLE001 — SystemExit et al: no model yet
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        meta = dict(model.get("meta") or {})
        meta["all_label"] = model.get("all_label")
        meta["scope_targets"] = model.get("scope_targets")
        meta["data_quality"] = model.get("data_quality")
        meta["generated"] = model.get("generated")
        meta["window_labels"] = model.get("window_labels")
        # All-time fabric data (build-time, NOT period-scoped) — the provenance /
        # framework / tracker tables, assistant authorship, fabric-usage rollup and the
        # per-bot detail roster read these regardless of the filter.
        meta["studio_prov"] = model.get("studio_prov")
        meta["gears_usage"] = model.get("gears_usage")
        meta["fabric_trackers"] = model.get("fabric_trackers")
        meta["cpt_people"] = model.get("cpt_people")
        meta["cpt_by_company"] = model.get("cpt_by_company")
        meta["fabric_company"] = model.get("fabric_company")
        meta["fabric_people"] = model.get("fabric_people")
        meta["bots_all"] = model.get("bots")

        if default_state:
            meta["scope"] = ""
            meta["period"] = {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
            self.send_json(render.ai_tools_json(model["all_block"], meta))
            return

        labels = {"7d": "7 days", "30d": "30 days", "90d": "90 days", "365d": "1 year"}
        if period_kind[0] == "custom":
            _, frm, to = period_kind
            period = {"preset": "custom", "label": f"{frm or '2008-01-01'} → {to or 'today'}",
                      "from": frm or None, "to": to or None}
        elif period_kind[0] == "preset":
            period = {"preset": preset, "label": labels[preset], "from": None, "to": None}
        else:
            period = {"preset": "all", "label": model.get("all_label"), "from": None, "to": None}
        meta["period"] = period
        meta["scope"] = scope

        conn = None
        try:
            conn = store.connect()
            repos = None
            if scope:
                import discovery
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            pr = store.aggregate(conn, since, until, label="custom", repos=repos)
            self.send_json(render.ai_tools_json(pr, meta))
        except Exception as exc:           # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 500)
        finally:
            if conn is not None:
                conn.close()

    def serve_drill(self) -> None:
        """Rows behind a metric: /api/drill?entity=commit|pr|issue&(days=N|from&to)
        &scope=&author=&company=&classification=&flag= . Read-only, capped, each row
        carries a GitHub URL. Filters mirror the aggregate query the tile counted."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import discovery
        qs = parse_qs(urlparse(self.path).query)
        entity = (qs.get("entity", [""])[0] or "").strip()
        if entity not in ("commit", "pr", "issue", "people", "ci", "flow", "flowitems", "rewinds"):
            self.send_json({"ok": False, "error": "entity must be commit/pr/issue/people/ci/flow/flowitems/rewinds"}, 400)
            return
        stage = (qs.get("stage", [""])[0] or "").strip()
        if stage and not _re.match(r"^[a-z_]{1,20}$", stage):
            self.send_json({"ok": False, "error": "invalid stage"}, 400)
            return
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if frm or to:
            if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
                self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
                return
            since = (frm or "2008-01-01") + "T00:00:00Z"
            until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            try:
                days = max(1, min(int(qs.get("days", ["3650"])[0]), 36500))
            except (ValueError, IndexError):
                self.send_json({"ok": False, "error": "invalid days"}, 400)
                return
            since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        scope = (qs.get("scope", [""])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        author = (qs.get("author", [""])[0] or "").strip()
        company = (qs.get("company", [""])[0] or "").strip()
        classification = (qs.get("classification", [""])[0] or "").strip()
        flag = (qs.get("flag", [""])[0] or "").strip()
        category = (qs.get("category", [""])[0] or "").strip()
        commit_type = (qs.get("commit_type", [""])[0] or "").strip()
        pr_state = (qs.get("pr_state", [""])[0] or "").strip()
        abandon_reason = (qs.get("abandon_reason", [""])[0] or "").strip()
        ai_tool = (qs.get("ai_tool", [""])[0] or "").strip()
        spec = (qs.get("spec", [""])[0] or "").strip()
        reviewed = (qs.get("reviewed", [""])[0] or "").strip()
        try:
            limit = max(1, min(int(qs.get("limit", ["500"])[0]), 1000))
        except (ValueError, IndexError):
            limit = 500
        try:
            offset = max(0, min(int(qs.get("offset", ["0"])[0]), 1_000_000))
        except (ValueError, IndexError):
            offset = 0
        if author and not _re.match(r"^[A-Za-z0-9-]{1,39}$", author):
            self.send_json({"ok": False, "error": "invalid author"}, 400)
            return
        if commit_type and not _re.match(r"^[a-z]{1,20}$", commit_type):
            self.send_json({"ok": False, "error": "invalid commit_type"}, 400)
            return
        # AI-tool names are display labels stored in commits.ai_tools and can
        # contain parentheses, e.g. "Assistant (commit mention)" — allow () (and the
        # existing space/._-[]) so drilling those rows isn't rejected. The value is
        # a bound SQL param downstream, so this is validation only.
        if ai_tool and not _re.match(r"^[A-Za-z0-9 _.\-\[\]()]{1,40}$", ai_tool):
            self.send_json({"ok": False, "error": "invalid ai_tool"}, 400)
            return
        if pr_state and pr_state not in ("merged", "abandoned", "open", "open_unreviewed"):
            self.send_json({"ok": False, "error": "invalid pr_state"}, 400)
            return
        _reasons = {k for k, _l, _s in store.ABANDON_REASONS} | {"unknown"}
        if abandon_reason and abandon_reason not in _reasons:
            self.send_json({"ok": False, "error": "invalid abandon_reason"}, 400)
            return
        if spec and spec not in ("0", "1"):
            self.send_json({"ok": False, "error": "invalid spec"}, 400)
            return
        if category and not _re.match(r"^[A-Za-z0-9_-]{1,40}$", category):
            self.send_json({"ok": False, "error": "invalid category"}, 400)
            return
        if category and entity != "issue":
            self.send_json({"ok": False, "error": "category drill is issue-only"}, 400)
            return
        members_only = (qs.get("members", [""])[0] or "").strip() == "1"
        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return
        conn = store.connect()
        try:
            repos, _proj = discovery.repos_for_scope(conn, level, target) if scope else (None, None)
            if entity == "people":
                res = store.people_drill(conn, since, until, repos=repos,
                                         company=company, member_only=members_only,
                                         limit=limit, offset=offset)
            elif entity == "ci":
                import semantic_metrics
                res = semantic_metrics.drill_ci_runs(conn, since, until, repos=repos,
                                                     limit=limit, offset=offset)
            elif entity == "flow":
                import semantic_metrics
                res = semantic_metrics.drill_flow_stage(conn, stage, repos=repos,
                                                        limit=limit, offset=offset)
            elif entity == "flowitems":
                import semantic_metrics
                res = semantic_metrics.drill_person_flow(conn, author, repos=repos,
                                                         since=since, until=until,
                                                         limit=limit, offset=offset)
            elif entity == "rewinds":
                import semantic_metrics
                res = semantic_metrics.drill_board_rewinds(conn, repos=repos,
                                                           since=since, until=until,
                                                           limit=limit, offset=offset)
            elif category:
                import semantic_metrics
                res = semantic_metrics.drill_issue_category(conn, since, until, category,
                                                            repos=repos, limit=limit, offset=offset)
            else:
                res = store.drill(conn, entity, since, until, repos=repos, author=author,
                                  company=company, classification=classification, flag=flag,
                                  commit_type=commit_type, pr_state=pr_state,
                                  abandon_reason=abandon_reason,
                                  ai_tool=ai_tool, spec=spec, reviewed=reviewed,
                                  limit=limit, offset=offset)
        finally:
            conn.close()
        res["ok"] = True
        self.send_json(res)

    def serve_delivery(self) -> None:
        """Delivery panels for a period + repo SLICE: /api/delivery?(days=N|from&to)
        &scope=<level:target>. scope empty = whole org. Only the delivery panels are
        recomputed for the slice; the rest of the report stays global (variant A)."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        import discovery
        import semantic_metrics
        qs = parse_qs(urlparse(self.path).query)
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if frm or to:
            if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
                self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
                return
            since = (frm or "2008-01-01") + "T00:00:00Z"
            until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            try:
                days = max(1, min(int(qs.get("days", ["30"])[0]), 36500))
            except (ValueError, IndexError):
                self.send_json({"ok": False, "error": "invalid days"}, 400)
                return
            since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        scope = (qs.get("scope", [""])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return
        conn = store.connect()
        try:
            repos = None
            if scope:
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            block = semantic_metrics.window_block(conn, since, until, repos)
            # period-over-period deltas vs the preceding equal window (skipped for
            # all-time / >2y spans, same rule as the main KPIs)
            try:
                ds = datetime.fromisoformat(since.replace("Z", "+00:00"))
                du = datetime.fromisoformat(until.replace("Z", "+00:00"))
                span = du - ds
                if 0 < span.days <= 731:
                    p_since = (ds - span).strftime("%Y-%m-%dT%H:%M:%SZ")
                    prev = semantic_metrics.delivery_metrics(conn, p_since, since, repos)
                    block["deltas"] = render.delta_map(
                        block, prev, keys=semantic_metrics.DELIVERY_KPI_KEYS)
            except Exception as exc:        # noqa: BLE001 — deltas are best-effort
                log_degraded("Delivery deltas (/api/delivery)", exc)
            pr = {"delivery": block}
            html = render.render_delivery_fragment(pr)
        finally:
            conn.close()
        self.send_json({"ok": True, "html": html, "since": since[:10], "until": until[:10]})

    def serve_flow(self) -> None:
        """Flow tab for a period + repo SLICE: /api/flow?(days=N|from&to)&scope=…
        The friction explainer + timeline-derived flow metrics for the window."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        import discovery
        import semantic_metrics
        qs = parse_qs(urlparse(self.path).query)
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if frm or to:
            if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
                self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
                return
            since = (frm or "2008-01-01") + "T00:00:00Z"
            until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            try:
                days = max(1, min(int(qs.get("days", ["30"])[0]), 36500))
            except (ValueError, IndexError):
                self.send_json({"ok": False, "error": "invalid days"}, 400)
                return
            since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        scope = (qs.get("scope", [""])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return
        conn = store.connect()
        try:
            repos = None
            if scope:
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            pr = {"flow": semantic_metrics.flow_report(conn, repos, since, until)}
            html = render.render_flow_fragment(pr)
        finally:
            conn.close()
        self.send_json({"ok": True, "html": html, "since": since[:10], "until": until[:10]})

    def serve_trend(self) -> None:
        """Trend panel alone at a chosen granularity: /api/trend?(days=N|from&to)
        &scope=<level:target>&gran=auto|day|week|month|quarter. Recomputes only the
        stacked-area chart so the granularity control needn't reload the whole page."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        import discovery
        qs = parse_qs(urlparse(self.path).query)
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        gran = (qs.get("gran", ["auto"])[0] or "auto").strip()
        if gran not in ("auto", "day", "week", "month", "quarter"):
            self.send_json({"ok": False, "error": "invalid gran"}, 400)
            return
        dim = (qs.get("dim", ["company"])[0] or "company").strip()
        if dim not in ("company", "work_type", "repo_type", "element"):
            self.send_json({"ok": False, "error": "invalid dim"}, 400)
            return
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if frm or to:
            if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
                self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
                return
            since = (frm or "2008-01-01") + "T00:00:00Z"
            until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            try:
                days = max(1, min(int(qs.get("days", ["30"])[0]), 36500))
            except (ValueError, IndexError):
                self.send_json({"ok": False, "error": "invalid days"}, 400)
                return
            since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        scope = (qs.get("scope", [""])[0] or "").strip()
        level, _, target = scope.partition(":")
        if scope and (level not in ("org", "element", "repo") or
                      not _re.match(r"^[A-Za-z0-9_./-]{1,120}$", target)):
            self.send_json({"ok": False, "error": "invalid scope"}, 400)
            return
        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return
        conn = store.connect()
        try:
            repos = None
            if scope:
                repos, _proj = discovery.repos_for_scope(conn, level, target)
            pr = store.aggregate(conn, since, until, label="trend", repos=repos,
                                 trend_gran=gran, trend_dim=dim)
            html = render.render_trend_fragment(pr)
        finally:
            conn.close()
        self.send_json({"ok": True, "html": html, "since": since[:10], "until": until[:10]})

    def serve_person(self) -> None:
        """Weekly per-person activity: /api/person?login=X&(days=N | from&to).
        Commits + git line diff per repo + issues opened, bucketed by week."""
        import re as _re
        from urllib.parse import parse_qs
        from datetime import timedelta
        import store
        import render
        qs = parse_qs(urlparse(self.path).query)
        login = (qs.get("login", [""])[0] or "").strip()
        if not _re.match(r"^[A-Za-z0-9-]{1,39}$", login):
            self.send_json({"ok": False, "error": "invalid login"}, 400)
            return
        frm = (qs.get("from", [""])[0] or "").strip()
        to = (qs.get("to", [""])[0] or "").strip()
        DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if frm or to:
            if (frm and not DATE.match(frm)) or (to and not DATE.match(to)):
                self.send_json({"ok": False, "error": "dates must be YYYY-MM-DD"}, 400)
                return
            since = (frm or "2008-01-01") + "T00:00:00Z"
            until = (to + "T23:59:59Z") if to else now.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            try:
                days = max(1, min(int(qs.get("days", ["90"])[0]), 36500))
            except (ValueError, IndexError):
                self.send_json({"ok": False, "error": "invalid days"}, 400)
                return
            since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            until = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        if since > until:
            self.send_json({"ok": False, "error": "from is after to"}, 400)
            return
        if not Path(store.db_path()).exists():
            self.send_json({"ok": False, "error": "no collected data"}, 404)
            return
        conn = None
        try:
            conn = store.connect()
            pw = store.person_weekly(conn, login, since, until)
            prof = store.person_profile(conn, login, since, until)
            # period-over-period deltas for the KPI tiles vs the preceding equal
            # window (skipped for all-time / >2y spans, same rule as /api/period)
            try:
                ds = datetime.fromisoformat(since.replace("Z", "+00:00"))
                du = datetime.fromisoformat(until.replace("Z", "+00:00"))
                span = du - ds
                if 0 < span.days <= 731:
                    p_since = (ds - span).strftime("%Y-%m-%dT%H:%M:%SZ")
                    prev = store.person_totals(conn, login, p_since, since)
                    prof["deltas"] = render.delta_map(
                        prof["totals"], prev,
                        keys=("commits", "meaningful_additions", "prs", "specs", "bugs", "epics", "features"))
            except Exception as exc:        # noqa: BLE001 — deltas are best-effort
                log_degraded(f"person KPI deltas for {login} (/api/person)", exc)
            ctx = period_ctx()
            alltime = (ctx.get("person_alltime") or {}).get(login, {})
            # weekly commit intensity (for the activity heatmap strip)
            heat = [{"week": r["week"], "commits": sum(c["commits"] for c in r["cells"] if c),
                     "issues": r["issues"]} for r in pw["rows"]]
            # EXPERIMENTAL v0 developer score (org-relative, this window). Tucked
            # behind a collapsed <details> in the dashboard — best-effort, and now
            # logged when it degrades (_developer_score_block). The Jinja fragment
            # has no place to SHOW the reason the way the React page does, so here
            # the second return value only reaches the payload (harmless to the
            # macro) — the log line is what this path gains.
            score, score_unavailable = self._developer_score_block(conn, login, since, until)
            payload = {"weekly": pw, "profile": prof, "alltime": alltime, "heat": heat,
                       "emails": (ctx.get("emails_by_login") or {}).get(login, ""),
                       "login": login, "gh_profile": store.gh_profile(conn, login),
                       "score": score, "score_unavailable": score_unavailable}
            self.send_json({"ok": True, "html": render.render_person_fragment(payload)})
        except Exception as exc:           # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 500)
        finally:
            if conn is not None:
                conn.close()

    def do_GET(self) -> None:
        if self.require_auth():
            return
        path = unquote(urlparse(self.path).path)
        if path == "/setup":
            # React route (Manage migration); ?legacy=1 keeps the server-Jinja
            # wizard as the pixel-gate baseline + fallback. The wizard is a
            # standalone centred page (no sidebar) — render_spa_page(sidebar=False).
            from urllib.parse import parse_qs as _pq
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                self.send_bytes(setup_html(), "text/html; charset=utf-8")
            else:
                import render as _render
                cfg = {}
                try:
                    import ghclient
                    cfg = ghclient.load_config()
                except Exception as exc:       # noqa: BLE001 — same as setup_html()
                    log_degraded("setup wizard config load (React boot)", exc)
                boot = {"token_status": token_status(),
                        "org": str(cfg.get("org", "")),
                        "extra_orgs": ", ".join(cfg.get("extra_orgs", []) or []),
                        "extra_repos": ", ".join(cfg.get("extra_repos", []) or [])}
                self.send_bytes(
                    _render.render_spa_page("setup", "setup", "Set up",
                                            bootstrap=boot, sidebar=False).encode(),
                    "text/html; charset=utf-8")
        elif path == "/metrics":
            import metrics_catalog
            from urllib.parse import parse_qs as _pq
            # React cutover (Manage migration): the server Jinja render stays
            # reachable at ?legacy=1 (pixel-gate baseline + fallback).
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                self.send_bytes(metrics_catalog.render_page().encode(), "text/html; charset=utf-8")
            else:
                import render as _render
                self.send_bytes(
                    _render.render_spa_page("metrics", "metrics", "Metrics catalog").encode(),
                    "text/html; charset=utf-8")
        elif path == "/views":
            import views_catalog
            from urllib.parse import parse_qs as _pq
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                self.send_bytes(views_catalog.render_page().encode(), "text/html; charset=utf-8")
            else:
                import render as _render
                self.send_bytes(
                    _render.render_spa_page("views", "views", "View catalog").encode(),
                    "text/html; charset=utf-8")
        elif path == "/calibrate":
            import calibrate
            from urllib.parse import parse_qs as _pq
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                self.send_bytes(calibrate.render_page(self._oauth_user()).encode(),
                                "text/html; charset=utf-8")
            else:
                import render as _render
                self.send_bytes(
                    _render.render_spa_page("calibrate", "calibrate", "Calibrate").encode(),
                    "text/html; charset=utf-8")
        elif path == "/api/manage/calibrate.json":
            import calibrate
            self.send_json({"ok": True, **calibrate.calibrate_json(self._oauth_user())})
        elif path in ("/whats-new", "/changelog"):
            # React pilot route (see docs/superpowers/plans/2026-07-22-react-phase0.md
            # P0-T4): data comes from /api/whats-new; changelog.render_page() stays
            # available as a fallback but is no longer wired to this route.
            import render
            self.send_bytes(render.render_spa_page("whatsnew", "changelog", "What's new").encode(),
                            "text/html; charset=utf-8")
        elif path == "/data-health":
            # Manage page: trust surface + dataset stats, from the shared cached model.
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup"); self.end_headers(); return
            try:
                model = _report_model(_report_version())
            except SystemExit:                  # nothing collected yet
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup"); self.end_headers(); return
            import datahealth
            from urllib.parse import parse_qs as _pq
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                self.send_bytes(datahealth.render_page(model).encode(), "text/html; charset=utf-8")
            else:
                import render as _render
                self.send_bytes(
                    _render.render_spa_page("datahealth", "datahealth", "Data health").encode(),
                    "text/html; charset=utf-8")
        elif path == "/api/manage/data-health.json":
            import datahealth
            if needs_setup():
                self.send_json({"ok": False, "error": "setup required"}, 409); return
            try:
                model = _report_model(_report_version())
            except SystemExit:
                self.send_json({"ok": False, "error": "no data collected yet"}, 409); return
            self.send_json({"ok": True, **datahealth.health_json(model)})
        elif path in ("/semantic", "/semantic/advanced"):
            # React routes (Manage migration); ?legacy=1 keeps the server-Jinja
            # editors as the pixel-gate baselines + fallbacks. The wizard lives at
            # /semantic, the dense grid at /semantic/advanced. Both fetch their data
            # from the existing GET /api/semantic/{wizard,scope,effective} endpoints.
            from urllib.parse import parse_qs as _pq
            advanced = path.endswith("advanced")
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                try:
                    import semantic_editor
                    html = (semantic_editor.render_page() if advanced
                            else semantic_editor.render_wizard_page())
                    self.send_bytes(html.encode(), "text/html; charset=utf-8")
                except Exception as exc:           # noqa: BLE001
                    self.send_json({"ok": False, "error": f"taxonomy: {exc}"}, 500)
            else:
                import render as _render
                entry, title = (("semantic-advanced", "Taxonomy") if advanced
                                else ("semantic-wizard", "Taxonomy setup"))
                self.send_bytes(
                    _render.render_spa_page(entry, "semantic", title).encode(),
                    "text/html; charset=utf-8")
        elif path in ("/", "/latest"):
            # first run (nothing collected yet) → guide through the setup wizard;
            # otherwise the report Overview is the landing page (Update lives at /update).
            # Post-React-cutover the landing view is the /overview route directly
            # (the old monolith at /report is now a hash-redirect shim).
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/setup" if needs_setup() else "/overview")
            self.end_headers()
        elif path == "/update":
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            # React route (Manage migration); ?legacy=1 keeps the server-Jinja
            # portal as the pixel-gate baseline + fallback. Both the React page and
            # the legacy JS poll GET /api/status (= snapshot_state) for live data.
            from urllib.parse import parse_qs as _pq
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                self.send_bytes(portal_html(), "text/html; charset=utf-8")
            else:
                import render as _render
                self.send_bytes(
                    _render.render_spa_page("update", "update", "Update").encode(),
                    "text/html; charset=utf-8")
        elif path == "/mcp-info":
            from urllib.parse import parse_qs as _pq
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                self.send_bytes(mcp_page(), "text/html; charset=utf-8")
            else:
                import render as _render
                self.send_bytes(
                    _render.render_spa_page("mcp", "mcp", "MCP access").encode(),
                    "text/html; charset=utf-8")
        elif path in ("/report", "/report.html", "/report/legacy", "/report/legacy.html"):
            # rendered live from the DB (no baked file). Fresh install → setup wizard;
            # a render failure with no prior good render also routes to setup rather
            # than 500 (load_data raises SystemExit when nothing is collected yet).
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            # React cutover: bare /report is now a client hash-redirect shim that
            # maps the old `#<mode>` deep-links to their per-view React routes. The
            # monolith stays reachable as a fallback at /report/legacy (clean path,
            # used by the pixel-parity baseline capture) or /report?legacy=1.
            from urllib.parse import parse_qs as _parse_qs
            _legacy = (path.startswith("/report/legacy")
                       or "1" in _parse_qs(urlparse(self.path).query).get("legacy", []))
            if not _legacy:
                import render as _render
                self._log_page_open()
                self.send_bytes(_render.report_redirect_shim().encode("utf-8"),
                                "text/html; charset=utf-8")
                return
            try:
                html = report_html()
            except SystemExit:                  # load_data(): nothing collected yet
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            # a genuine render error propagates → 500 (surfaced, not masked); a
            # render failure with a prior good render is already served stale-but-
            # working by _report_model's last-good fallback.
            self._log_page_open()
            self.send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/overview":
            # React pilot for the report views (see
            # docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P1):
            # data comes from /api/report/overview; `/report` (the monolith, mode
            # 'overview' by default) keeps serving every view during the hybrid
            # migration. Same setup-wizard redirect as /report — an unconfigured
            # install has no data for either.
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            import render
            self._log_page_open()
            self.send_bytes(
                render.render_spa_page("overview", "overview", "Overview", report_chrome=True).encode(),
                "text/html; charset=utf-8")
        elif path == "/trend":
            # React route for the Trend view (see
            # docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P2):
            # data comes from /api/report/trend; `/report#trend` (the monolith)
            # keeps working during the hybrid migration. Same setup-wizard
            # redirect as /overview.
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            import render
            self._log_page_open()
            self.send_bytes(
                render.render_spa_page("trend", "trend", "Trend", report_chrome=True).encode(),
                "text/html; charset=utf-8")
        elif path == "/delivery":
            # React route for the Delivery view (see
            # docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task
            # R-P3): data comes from /api/report/delivery; `/report#delivery`
            # (the monolith) keeps working during the hybrid migration. Same
            # setup-wizard redirect as /overview and /trend.
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            import render
            self._log_page_open()
            self.send_bytes(
                render.render_spa_page("delivery", "delivery", "Delivery", report_chrome=True).encode(),
                "text/html; charset=utf-8")
        elif path == "/flow":
            # React route for the Flow view (see
            # docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task
            # R-P4): data comes from /api/report/flow; `/report#flow` (the
            # monolith) keeps working during the hybrid migration. Same
            # setup-wizard redirect as /overview, /trend and /delivery.
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            import render
            self._log_page_open()
            self.send_bytes(
                render.render_spa_page("flow", "flow", "Flow", report_chrome=True).encode(),
                "text/html; charset=utf-8")
        elif path == "/people":
            # React route for the People view (see
            # docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task
            # R-P5): data comes from /api/report/people; `/report#people` (the
            # monolith) keeps working during the hybrid migration. Same
            # setup-wizard redirect as /overview, /trend, /delivery and /flow.
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            import render
            self._log_page_open()
            self.send_bytes(
                render.render_spa_page("people", "people", "People", report_chrome=True).encode(),
                "text/html; charset=utf-8")
        elif path == "/person":
            # React route for the Person view (see
            # docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task
            # R-P6): data comes from /api/report/person; `/report#person` (the
            # monolith) keeps working during the hybrid migration. report_chrome
            # carries the KPI/gauge chrome the person dashboard needs (same as
            # /people). Same setup-wizard redirect as the other report routes.
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            import render
            self._log_page_open()
            self.send_bytes(
                render.render_spa_page("person", "person", "Person", report_chrome=True).encode(),
                "text/html; charset=utf-8")
        elif path == "/repositories":
            # React route for the Repositories view (see
            # docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task
            # R-P7): data comes from /api/report/repositories; `/report#repos`
            # (the monolith) keeps working during the hybrid migration. NOTE the
            # rename repos→repositories (migration spec's redirect table) — the
            # route is /repositories, but the sidebar active-key stays "repos"
            # (the monolith's mode). report_chrome=True carries the floating
            # metrics-assistant #mx-fab every report view shows (the repos view
            # has no Vega charts, but the fab must be present for pixel parity).
            # Same setup-wizard redirect as the other report routes.
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            import render
            self._log_page_open()
            self.send_bytes(
                render.render_spa_page("repositories", "repos", "Repositories", report_chrome=True).encode(),
                "text/html; charset=utf-8")
        elif path == "/elements":
            # React route for the "By Element" view (see
            # docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task
            # R-P8): data comes from /api/report/elements; `/report#elements`
            # (the monolith) keeps working during the hybrid migration. Sidebar
            # active-key = "elements" (the monolith's mode). report_chrome=True
            # carries the floating metrics-assistant #mx-fab every report view
            # shows (the elements view has no Vega charts — a single per-element
            # table — but the fab must be present for pixel parity). Same
            # setup-wizard redirect as the other report routes.
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            import render
            self._log_page_open()
            self.send_bytes(
                render.render_spa_page("elements", "elements", "Elements", report_chrome=True).encode(),
                "text/html; charset=utf-8")
        elif path == "/traffic":
            # React route for the "Traffic" view (see
            # docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task
            # R-P9): data comes from /api/report/traffic; `/report#usage` (the
            # monolith) keeps working during the hybrid migration. NOTE the
            # rename usage→traffic (migration spec's redirect table) — the route
            # is /traffic, but the sidebar active-key stays "usage" (the
            # monolith's mode). report_chrome=True carries the floating
            # metrics-assistant #mx-fab every report view shows (the traffic view
            # has no Vega charts — hand-rolled scenario bars + traffic panel — but
            # the fab must be present for pixel parity). Same setup-wizard
            # redirect as the other report routes.
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            import render
            self._log_page_open()
            self.send_bytes(
                render.render_spa_page("traffic", "usage", "Traffic", report_chrome=True).encode(),
                "text/html; charset=utf-8")
        elif path == "/ai-tools":
            # React route for the "AI tools" view (see
            # docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task
            # R-P10 — the LAST report view): data comes from
            # /api/report/ai-tools; `/report#fabric` (the monolith) keeps working
            # during the hybrid migration. NOTE the rename fabric→ai-tools
            # (migration spec's redirect table) — the route is /ai-tools, but the
            # sidebar active-key stays "fabric" (the monolith's mode).
            # report_chrome=True carries the floating metrics-assistant #mx-fab
            # every report view shows (the AI-usage panel has NO Vega chart —
            # hand-rolled per-tool split bar + a plain table — but the fab must be
            # present for pixel parity; the vega runtime it also loads is inert
            # here). Same setup-wizard redirect as the other report routes.
            if needs_setup():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/setup")
                self.end_headers()
                return
            import render
            self._log_page_open()
            self.send_bytes(
                render.render_spa_page("ai-tools", "fabric", "AI tools", report_chrome=True).encode(),
                "text/html; charset=utf-8")
        elif path == "/identity":
            # React route (Manage migration); ?legacy=1 keeps the server-Jinja
            # editor as the pixel-gate baseline + fallback. The legacy render is
            # live from the DB roster — no baked file, so its concurrency token and
            # sidebar are always current (see directory.render_page docstring).
            from urllib.parse import parse_qs as _pq
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                try:
                    import directory
                    self.send_bytes(directory.render_page().encode(), "text/html; charset=utf-8")
                except Exception as exc:           # noqa: BLE001
                    self.send_json({"ok": False, "error": f"identity editor: {exc}"}, 500)
            else:
                import render as _render
                self.send_bytes(
                    _render.render_spa_page("identity", "identity", "Identity & company").encode(),
                    "text/html; charset=utf-8")
        elif path == "/api/manage/identity.json":
            try:
                import directory
                self.send_json({"ok": True, **directory.directory_json()})
            except Exception as exc:               # noqa: BLE001
                self.send_json({"ok": False, "error": f"identity editor: {exc}"}, 500)
        elif path == "/config":
            # React route (Manage migration); ?legacy=1 keeps the server-Jinja
            # editor as the pixel-gate baseline + fallback.
            from urllib.parse import parse_qs as _pq
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                try:
                    import configstore
                    self.send_bytes(configstore.render_page().encode(), "text/html; charset=utf-8")
                except Exception as exc:           # noqa: BLE001
                    self.send_json({"ok": False, "error": f"config editor: {exc}"}, 500)
            else:
                import render as _render
                self.send_bytes(
                    _render.render_spa_page("config", "config", "Config").encode(),
                    "text/html; charset=utf-8")
        elif path == "/api/manage/config.json":
            try:
                import configstore
                self.send_json({"ok": True, **configstore.editor_data()})
            except Exception as exc:               # noqa: BLE001
                self.send_json({"ok": False, "error": f"config editor: {exc}"}, 500)
        elif path in ("/api/semantic/scope", "/api/semantic/effective", "/api/semantic/wizard"):
            from urllib.parse import parse_qs
            import re as _re
            import store
            import semantic_editor
            qs = parse_qs(urlparse(self.path).query)
            level = (qs.get("level", ["global"])[0] or "global").strip()
            target = (qs.get("target", [""])[0] or "").strip()
            if level not in ("global", "org", "element", "repo", "project") or \
                    not _re.match(r"^[A-Za-z0-9_./-]{0,120}$", target):
                self.send_json({"ok": False, "error": "invalid scope"}, 400)
                return
            fn = (semantic_editor.effective_data if path.endswith("effective")
                  else semantic_editor.wizard_data if path.endswith("wizard")
                  else semantic_editor.scope_data)
            conn = store.connect()
            try:
                data = fn(conn, level, target)
            finally:
                conn.close()
            self.send_json({"ok": True, "data": data})
        elif path in ("/assets/jakarta.woff2", "/assets/inter.woff2"):
            fp = ROOT / "assets" / ("jakarta-latin.woff2" if "jakarta" in path else "inter-latin.woff2")
            if not fp.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            data = fp.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "font/woff2")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(data)
        elif path.startswith("/assets/vega/") and path.endswith(".min.js"):
            # Vendored Vega/Vega-Lite/vega-embed bundle, served same-origin (no
            # runtime CDN — supply-chain rule). Allowlist the exact three known
            # filenames: basename-only + membership check blocks path traversal
            # and arbitrary file serving even though the path already matched.
            basename = path.rsplit("/", 1)[-1]
            if basename not in ("vega.min.js", "vega-lite.min.js", "vega-embed.min.js"):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            fp = ROOT / "assets" / "vega" / basename
            if not fp.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            data = fp.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(data)
        elif path.startswith("/assets/app/"):
            # Vite-built React bundle (see spa.py / frontend/), served same-
            # origin under the base path baked into frontend/vite.config.ts.
            # Filenames are content-hashed (immutable cache-forever) EXCEPT
            # the manifest itself, whose name is stable — that one must
            # revalidate so a rebuild is picked up without a hard-refresh.
            base_dir = (ROOT / "assets" / "app").resolve()
            rel = path[len("/assets/app/"):]
            fp = (base_dir / rel).resolve() if rel else base_dir
            if fp != base_dir and base_dir not in fp.parents:
                self.send_error(HTTPStatus.NOT_FOUND)   # traversal attempt
                return
            if not fp.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content_type = {
                ".js": "application/javascript",
                ".css": "text/css",
                ".json": "application/json",
                ".map": "application/json",
                ".woff2": "font/woff2",
            }.get(fp.suffix.lower(), "application/octet-stream")
            data = fp.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            if rel == ".vite/manifest.json":
                self.send_header("Cache-Control", "no-cache")
            else:
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(data)
        elif path == "/data.json":
            # the run blob lives in the DB (source of truth); serialise it on demand
            import store
            conn = store.connect()
            try:
                blob = store.read_latest_run(conn)
            finally:
                conn.close()
            if blob is None:
                self.send_json({"error": "no collected data yet"}, 404)
            else:
                self.send_json(blob)
        elif path == "/api/status":
            self.send_json(snapshot_state())
        elif path == "/api/period":
            self.serve_custom_period()
        elif path == "/api/report/overview":
            self.serve_report_overview()
        elif path == "/api/report/trend":
            self.serve_report_trend()
        elif path == "/api/report/delivery":
            self.serve_report_delivery()
        elif path == "/api/report/flow":
            self.serve_report_flow()
        elif path == "/api/report/people":
            self.serve_report_people()
        elif path == "/api/report/person":
            self.serve_report_person()
        elif path == "/api/report/repositories":
            self.serve_report_repositories()
        elif path == "/api/report/elements":
            self.serve_report_elements()
        elif path == "/api/report/traffic":
            self.serve_report_traffic()
        elif path == "/api/report/ai-tools":
            self.serve_report_ai_tools()
        elif path == "/api/delivery":
            self.serve_delivery()
        elif path == "/api/flow":
            self.serve_flow()
        elif path == "/api/trend":
            self.serve_trend()
        elif path == "/api/drill":
            self.serve_drill()
        elif path == "/api/person":
            self.serve_person()
        elif path == "/api/whoami":
            # per-request (never cached): the signed-in viewer resolved to a person
            # login, so the Person tab can default to "me". null when not resolvable.
            import store
            login, conn = None, None
            try:
                conn = store.connect()
                for ident in self._oauth_idents():
                    login = store.person_login_for(conn, ident)
                    if login:
                        break
            except Exception as exc:        # noqa: BLE001 — never break the page
                log_degraded("whoami identity resolution", exc)
                login = None
            finally:
                if conn is not None:
                    conn.close()
            self.send_json({"ok": True, "login": login})
        elif path == "/api/whats-new":
            import changelog
            self.send_json({"ok": True, "releases": changelog.releases()})
        elif path == "/api/manage/metrics.json":
            import metrics_catalog
            self.send_json({"ok": True, **metrics_catalog.catalog_json()})
        elif path == "/api/manage/views.json":
            import views_catalog
            self.send_json({"ok": True, **views_catalog.catalog_json()})
        elif path == "/api/manage/mcp.json":
            self.send_json({"ok": True, "publicUrl": _public_url(),
                            "token": _mcp_token(), "tools": _mcp_tools()})
        elif path == "/api/manage/dashboards.json":
            import store
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                self.send_json({"ok": True, "login": login,
                                "dashboards": store.list_dashboards(conn, login)})
            finally:
                conn.close()
        elif path == "/usage-insights":
            from urllib.parse import parse_qs as _pq
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                self.send_bytes(usage_page(), "text/html; charset=utf-8")
            else:
                import render as _render
                self.send_bytes(
                    _render.render_spa_page("usage", "usage", "Usage insights").encode(),
                    "text/html; charset=utf-8")
        elif path == "/chat-log":
            # Assistant conversation viewer — intentionally NOT linked in the sidebar;
            # reachable by URL only. Portal auth still applies. React route (Manage
            # migration); ?legacy=1 keeps the server-Jinja page as the pixel-gate
            # baseline + fallback. Data comes from the existing GET
            # /api/chat-sessions + /api/chat-session endpoints.
            from urllib.parse import parse_qs as _pq
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                self.send_bytes(chat_log_page(), "text/html; charset=utf-8")
            else:
                import render as _render
                self.send_bytes(
                    _render.render_spa_page("chatlog", "", "Assistant conversations").encode(),
                    "text/html; charset=utf-8")
        elif path == "/dashboards":
            # Custom-dashboard list — linked from the sidebar.
            from urllib.parse import parse_qs as _pq
            if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                import store
                conn = store.connect()
                try:
                    login, _ = self._resolve_viewer(conn)
                    rows = store.list_dashboards(conn, login)
                finally:
                    conn.close()
                self.send_bytes(dashboards_list_page(rows, login), "text/html; charset=utf-8")
            else:
                import render as _render
                self.send_bytes(
                    _render.render_spa_page("dashboards", "dashboards", "Dashboards").encode(),
                    "text/html; charset=utf-8")
        elif path == "/api/chat-sessions":
            from urllib.parse import parse_qs
            import store
            since, until = _usage_range(parse_qs(urlparse(self.path).query))
            conn = store.connect()
            try:
                data = {"sessions": store.chat_sessions(conn, since, until)}
            finally:
                conn.close()
            self.send_json({"ok": True, **data})
        elif path == "/api/chat-session":
            from urllib.parse import parse_qs
            import store
            sid = (parse_qs(urlparse(self.path).query).get("id", [""])[0] or "")[:120]
            conn = store.connect()
            try:
                data = store.chat_session_detail(conn, sid)
            finally:
                conn.close()
            self.send_json({"ok": True, **data})
        elif path == "/api/usage-summary":
            from urllib.parse import parse_qs
            import store
            since, until = _usage_range(parse_qs(urlparse(self.path).query))
            conn = store.connect()
            try:
                data = store.usage_summary(conn, since, until)
            finally:
                conn.close()
            self.send_json({"ok": True, **data})
        elif path == "/api/usage-detail":
            from urllib.parse import parse_qs
            import store
            qs = parse_qs(urlparse(self.path).query)
            by = (qs.get("by", [""])[0] or "").strip()
            key = (qs.get("key", [""])[0] or "").strip()
            # chatlog is the "all requests" log → no key; every other drill needs one.
            if by not in ("widget", "tab", "drill", "persona", "chat", "chatlog", "tool") \
                    or (not key and by != "chatlog"):
                self.send_json({"ok": False, "error": "invalid by/key"}, 400)
                return
            since, until = _usage_range(qs)
            conn = store.connect()
            try:
                data = store.usage_detail(conn, since, until, by, key)
            finally:
                conn.close()
            self.send_json({"ok": True, **data})
        elif path == "/api/dashboard/catalog":
            import dashboards
            self.send_json({"ok": True, **dashboards.dashboard_catalog()})
        elif path == "/api/dashboard/measures":
            import dashboards
            self.send_json({"ok": True, **dashboards.measures_payload()})
        elif path == "/api/dashboard/panel":
            from urllib.parse import parse_qs
            import store, dashboards
            qs = parse_qs(urlparse(self.path).query)
            did = qs.get("id", [""])[0]
            pid = qs.get("panel", [""])[0]
            scope = qs.get("scope", [""])[0]
            period = qs.get("period", ["all"])[0]
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                d = store.get_dashboard(conn, did)
                if not d or (d["visibility"] != "shared" and d["owner_login"] != login):
                    self.send_error(HTTPStatus.NOT_FOUND); return
                panel = next((p for p in d["spec"].get("panels", []) if p.get("id") == pid), None)
                if not panel:
                    self.send_error(HTTPStatus.NOT_FOUND); return
                html = dashboards.render_panel(panel, scope=scope, period=period)
            finally:
                conn.close()
            self.send_bytes(html.encode(), "text/html; charset=utf-8")
        elif path == "/api/dashboard/panel.json":
            # Resolved-DATA twin of /api/dashboard/panel (same query params + the
            # same viewer/visibility gate) — returns {viz,title,pin,data} JSON for
            # the React <PanelRenderer>, where the HTML endpoint returns rendered
            # HTML. build_spec stays server-side: a chart's `data` IS its Vega-Lite
            # spec dict.
            from urllib.parse import parse_qs
            import store, dashboards
            qs = parse_qs(urlparse(self.path).query)
            did = qs.get("id", [""])[0]
            pid = qs.get("panel", [""])[0]
            scope = qs.get("scope", [""])[0]
            period = qs.get("period", ["all"])[0]
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                d = store.get_dashboard(conn, did)
                if not d or (d["visibility"] != "shared" and d["owner_login"] != login):
                    self.send_error(HTTPStatus.NOT_FOUND); return
                panel = next((p for p in d["spec"].get("panels", []) if p.get("id") == pid), None)
                if not panel:
                    self.send_error(HTTPStatus.NOT_FOUND); return
                resolved = dashboards.resolve_panel_data(panel, scope=scope, period=period)
            finally:
                conn.close()
            self.send_json({"ok": True, **resolved})
        elif path.startswith("/dashboard/") and path.endswith("/edit"):
            import store, render
            did = path[len("/dashboard/"):-len("/edit")]
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                d = store.get_dashboard(conn, did)
                if not d or d["owner_login"] != login:      # edit is owner-only
                    self.send_error(HTTPStatus.NOT_FOUND); return
                # React route (Manage migration); ?legacy=1 keeps the server-Jinja
                # editor as the pixel-gate baseline + fallback. The owner-only gate
                # above guards both paths. React reads the spec via bootstrap and
                # fetches measures/catalog/preview from the existing endpoints.
                from urllib.parse import parse_qs as _pq
                if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                    page = render.render_dashboard_editor(d)
                else:
                    spec = d["spec"]
                    boot = {"id": d["id"], "title": spec.get("title", "Untitled dashboard"),
                            "visibility": d.get("visibility", "private"), "spec": spec}
                    page = render.render_spa_page(
                        "dashboard-editor", "dashboards",
                        "Edit: " + spec.get("title", "Untitled dashboard"),
                        vega=True, bootstrap=boot)
            finally:
                conn.close()
            self.send_bytes(page.encode(), "text/html; charset=utf-8")
        elif path.startswith("/dashboard/"):
            import store, render
            did = path[len("/dashboard/"):]
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                d = store.get_dashboard(conn, did)
                if not d or (d["visibility"] != "shared" and d["owner_login"] != login):
                    self.send_error(HTTPStatus.NOT_FOUND); return
                # React cutover (widget system Phase 2): render via the shared
                # <PanelRenderer> catalog. The server Jinja + .vl-panel render
                # stays reachable at ?legacy=1 (the pixel-gate baseline + fallback,
                # mirroring the report's /report/legacy).
                from urllib.parse import parse_qs as _pq
                if "1" in _pq(urlparse(self.path).query).get("legacy", []):
                    page = render.render_dashboard_page(d)
                else:
                    import discovery
                    spec = d["spec"]
                    boot = {"id": d["id"], "title": spec.get("title", "Dashboard"),
                            "panels": spec.get("panels", []),
                            "scopeTargets": discovery.scope_targets(conn)}
                    page = render.render_spa_page(
                        "dashboard", "dashboards", spec.get("title", "Dashboard"),
                        vega=True, bootstrap=boot)
            finally:
                conn.close()
            self.send_bytes(page.encode(), "text/html; charset=utf-8")
        elif path == "/api/dashboard" or (path.startswith("/api/dashboard/")
                                          and path != "/api/dashboard/panel"
                                          and path != "/api/dashboard/catalog"
                                          and path != "/api/dashboard/measures"):
            import store
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                did = path[len("/api/dashboard/"):] if path != "/api/dashboard" else ""
                if not did:
                    self.send_json({"ok": True, "dashboards": store.list_dashboards(conn, login)})
                    return
                d = store.get_dashboard(conn, did)
                if not d or (d["visibility"] != "shared" and d["owner_login"] != login):
                    self.send_json({"ok": False, "error": "not found"}, 404)
                    return
                self.send_json({"ok": True, "dashboard": d})
            finally:
                conn.close()
        elif path == "/health":
            self.send_bytes(b"ok\n", "text/plain")
        elif path == "/health/data":
            # Deliberately separate from /health: that one is liveness (is the
            # process up) and anything already polling it must keep seeing 200.
            # This one is about whether the DATA is still being refreshed, and
            # answers 503 when it isn't, so a monitor can page on it.
            # Covers BOTH ways this endpoint's promise can break: a collector that
            # stopped landing runs, and a report model that stopped rebuilding from
            # them (fresh rows, every page still painted from the last model that
            # built). See health_data_payload / stale_model_state.
            payload, ok = health_data_payload()
            self.send_json(payload, HTTPStatus.OK if ok
                           else HTTPStatus.SERVICE_UNAVAILABLE)
        elif path == "/favicon.ico":
            self.send_bytes(b"", "image/x-icon", HTTPStatus.NO_CONTENT)
        elif path == "/exports":
            EXPORTS.mkdir(parents=True, exist_ok=True)
            items = sorted(EXPORTS.glob("*.html"), reverse=True)
            links = "".join(
                f'<li><a href="/exports/{html.escape(p.name)}">{html.escape(p.name)}</a></li>'
                for p in items
            ) or "<li>No exports yet.</li>"
            self.send_bytes(
                f"<!doctype html><title>Exports</title><h1>Exports</h1><ul>{links}</ul>".encode(),
                "text/html; charset=utf-8",
            )
        elif path.startswith("/exports/"):
            name = Path(path.removeprefix("/exports/")).name
            self.send_file(EXPORTS / name)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def serve_chat(self) -> None:
        """POST /api/chat — the metrics assistant. Body: {message, history?, scope?,
        view?, period?, session_id?}. `message` is the CLEAN question; the server adds
        the trusted context (scope/period/view) and verified identity. Streams SSE
        frames as the Gemini agent answers via the read-only tools:
        {type:'text'|'tool'|'error'} then {type:'done'}. Persists analytics
        (usage_event) and the clean transcript (chat_message). Same-origin + portal-auth
        gated by do_POST before we get here."""
        payload = self._read_json_body()
        if payload is None:
            return
        message = (payload.get("message") or "").strip()
        if not message:
            self.send_json({"ok": False, "error": "empty message"}, 400)
            return
        clean = message[:4000]                      # the user's typed question, as-is
        raw_hist = payload.get("history") or []
        history = [{"role": h.get("role"), "text": str(h.get("text", ""))[:8000]}
                   for h in raw_hist[-40:] if isinstance(h, dict)]
        view = (payload.get("view") or "")[:120] or None      # report section it was asked from
        period = (payload.get("period") or "")[:120] or None
        scope = (payload.get("scope") or "")[:120] or None
        session_id = (payload.get("session_id") or "")[:120] or None
        try:
            import chat_agent
        except Exception as exc:                    # noqa: BLE001
            self.send_json({"ok": False, "error": f"chat unavailable: {exc}"}, 503)
            return

        # Resolve the viewer ONCE, server-side from the proxy headers (never the client
        # payload) — used to scope first-person questions, attribute usage, and own the
        # transcript. login is a person login or None (anon).
        login = ident = None
        try:
            import store
            _c = store.connect()
            try:
                login, ident = self._resolve_viewer(_c)
            finally:
                _c.close()
        except Exception:                           # noqa: BLE001 — attribution best-effort
            pass

        # Build the model message server-side: clean question + trusted context
        # (scope/period/view) + verified identity. The client no longer annotates the
        # text, so the CLEAN question is what we persist.
        bits = []
        bits.append(f"scope={scope}" if scope else "scope=whole org (no slice)")
        if period:
            bits.append(f"period={period}")
        if view:
            bits.append(f"view={view}")
        ctx = f"\n\n[Report context: {', '.join(bits)}]"
        if login:
            ctx += f"\n\n[Viewer (server-verified): asking_as={login}]"
        model_message = clean + ctx

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")   # tell nginx not to buffer the stream
        self.end_headers()

        answer_parts = []

        def emit(ev: dict) -> None:                 # raises on a dead socket → ends turn
            if isinstance(ev, dict) and ev.get("type") == "text":
                answer_parts.append(ev.get("text") or "")
            self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                             .encode("utf-8"))
            self.wfile.flush()

        usage = {}
        try:
            usage = chat_agent.answer(history, model_message, emit) or {}
        except (BrokenPipeError, ConnectionResetError):
            pass                                    # client navigated away mid-stream
        answer_text = "".join(answer_parts)

        # Persist analytics (usage_event) + the transcript (chat_message). Both use the
        # server-resolved identity; the transcript stores CLEAN text (no annotations).
        try:
            import store
            conn = store.connect()
            try:
                store.record_usage_events(conn, login, ident, [{
                    "kind": "chat_msg", "target": view, "period": period,
                    "tokens_in": usage.get("tokens_in"), "tokens_out": usage.get("tokens_out"),
                    "tokens_cached": usage.get("tokens_cached"),
                    "cost_usd": usage.get("cost_usd")}])
                store.record_chat_message(conn, session_id, login, ident, "user", clean,
                                          view=view, period=period)
                msg_id = None
                if answer_text:
                    msg_id = store.record_chat_message(
                        conn, session_id, login, ident, "assistant", answer_text,
                        view=view, period=period,
                        tokens_in=usage.get("tokens_in"), tokens_out=usage.get("tokens_out"),
                        tokens_cached=usage.get("tokens_cached"), cost_usd=usage.get("cost_usd"))
                store.record_chat_tool_calls(conn, session_id, login, ident, msg_id,
                                             usage.get("tool_calls") or [])
                store.prune_chat_messages(conn, CHAT_HISTORY_DAYS)
            finally:
                conn.close()
        except Exception as exc:                    # noqa: BLE001 — never break the response
            # Still a swallow: the answer is already on its way to the user and
            # losing the transcript row costs history, not correctness. But this is
            # the same class as the page-open write, which logs — and a chat log that
            # silently records nothing would look like a product nobody uses.
            log_degraded("chat transcript + token accounting write", exc)

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self.send_json({"ok": False, "error": "invalid payload size"}, 413)
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json({"ok": False, "error": "body must be UTF-8 JSON"}, 400)
            return None

    def handle_config_post(self, path: str) -> None:
        import configstore
        payload = self._read_json_body()
        if payload is None:
            return
        if path == "/api/config/collect":
            self.config_add_source(configstore, payload)
            return
        # /api/config — persist classification/element overrides to the DB + apply
        import store as _store
        _conn = _store.connect()
        cur_ver = _store.overrides_version(_conn, configstore.CONFIG_SCOPES)
        _conn.close()
        sent_ver = payload.get("_version")
        if sent_ver is not None and sent_ver != cur_ver:
            self.send_json({"ok": False, "error": "stale edit — the config changed in "
                            "another session; reload before saving"}, 409)
            return
        try:
            ov = configstore.overlay_from_post(payload)
            configstore.save_overlay(ov)
        except Exception as exc:                    # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        applied, apply_error = False, None
        try:
            import reconfig
            reconfig.apply(do_render=False)          # served live; no baked file
            applied = True                            # report_version() moved → cache busts itself
        except Exception as exc:                     # noqa: BLE001
            apply_error = str(exc)
        _conn = _store.connect()
        new_ver = _store.overrides_version(_conn, configstore.CONFIG_SCOPES)
        _conn.close()
        self.send_json({"ok": True, "applied": applied, "apply_error": apply_error,
                        "version": new_ver})

    def handle_semantic_post(self) -> None:
        """Persist the global semantic taxonomy mapping (categories/stages/ci)."""
        import store
        import semantic_editor
        payload = self._read_json_body()
        if payload is None:
            return
        conn = store.connect()
        try:
            cur_ver = store.overrides_version(conn, ("semantic",))
            if payload.get("_version") not in (None, cur_ver):
                self.send_json({"ok": False, "error": "stale edit — reload before saving"}, 409)
                return
            assignments = payload.get("assignments")
            if not isinstance(assignments, dict):
                self.send_json({"ok": False, "error": "missing assignments"}, 400)
                return
            level = (payload.get("level") or "global").strip()
            target = (payload.get("target") or "").strip()
            import re as _re
            if level not in ("global", "org", "element", "repo", "project") or \
                    not _re.match(r"^[A-Za-z0-9_./-]{0,120}$", target):
                self.send_json({"ok": False, "error": "invalid scope"}, 400)
                return
            new_ver = semantic_editor.save(conn, level, target, assignments)
        except Exception as exc:                     # noqa: BLE001
            self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        finally:
            conn.close()
        # unified taxonomy: a taxonomy edit must re-tag issues (bug/epic/story) and
        # re-render the main report, not just Delivery/Flow. reconfig recategorises
        # from the new resolver + rebuilds — same instant-recompute path as /config.
        applied, apply_error = False, None
        try:
            import reconfig
            reconfig.apply(do_render=False)          # served live; no baked file
            applied = True
        except Exception as exc:                     # noqa: BLE001
            apply_error = str(exc)
        self.send_json({"ok": True, "version": new_ver,
                        "applied": applied, "apply_error": apply_error})

    def config_add_source(self, configstore, payload: dict) -> None:
        """Add a new org or repo to the overlay and queue a collection run."""
        org = (payload.get("org") or "").strip()
        repo = (payload.get("repo") or "").strip()
        if org and not re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$", org):
            self.send_json({"ok": False, "error": "invalid org name"}, 400)
            return
        if repo and not re.match(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", repo):
            self.send_json({"ok": False, "error": "repo must be org/name"}, 400)
            return
        if not org and not repo:
            self.send_json({"ok": False, "error": "org or repo required"}, 400)
            return
        import store
        conn = store.connect()
        try:
            if org:
                store.write_override(conn, "extra_org", org, {})
            if repo:
                store.write_override(conn, "extra_repo", repo, {})
            new_ver = store.overrides_version(conn, configstore.CONFIG_SCOPES)
        finally:
            conn.close()
        if not run_job("refresh", ["refresh"]):
            self.send_json({"ok": False, "error": "a job is already running; try again shortly"}, 409)
            return
        # return the bumped version so the tab stays current (a later Save won't 409)
        self.send_json({"ok": True, "queued": org or repo, "version": new_ver}, 202)

    def handle_setup_post(self, path: str) -> None:
        import store
        if path == "/api/setup/collect":
            if not run_job("setup", ["all"]):
                self.send_json({"ok": False, "error": "a job is already running"}, 409)
                return
            self.send_json({"ok": True, "job": "setup"}, 202)
            return
        if path == "/api/setup/token/clear":
            conn = store.connect()
            try:
                store.clear_secret(conn, "gh_token")
            finally:
                conn.close()
            self.send_json({"ok": True, "token_source": token_status()})
            return
        payload = self._read_json_body()
        if payload is None:
            return
        if path == "/api/setup/token":
            import ghclient
            tok = (payload.get("token") or "").strip()
            if not tok:
                self.send_json({"ok": False, "error": "token is empty"}, 400)
                return
            res = ghclient.validate_token(tok)      # network call, never logs the token
            if not res.get("ok"):
                self.send_json({"ok": False, "error": res.get("error", "invalid token")}, 400)
                return
            conn = store.connect()
            try:
                store.set_secret(conn, "gh_token", tok)
            finally:
                conn.close()
            # response carries only non-secret metadata — never the token
            self.send_json({"ok": True, "login": res.get("login"),
                            "remaining": res.get("remaining")})
            return
        # /api/setup/save — primary org + optional extra orgs/repos
        org = (payload.get("org") or "").strip()
        if not re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$", org):
            self.send_json({"ok": False, "error": "invalid org name"}, 400)
            return
        # Syntax is not existence. Saving an org GitHub has never heard of used to
        # succeed, and the mistake only surfaced as an empty first collection — which
        # reads as "the tool doesn't work" rather than "that name is wrong". Checked
        # against the stored token; a network failure does NOT block the save.
        import ghclient as _gh
        # required=False: the wizard lets the org be saved before a token exists, and
        # token() otherwise raises SystemExit — which would kill the request, not
        # explain anything. Unauthenticated, the check still catches a plain typo.
        _org_check = _gh.check_org(org, _gh.token(required=False))
        if not _org_check.get("ok"):
            self.send_json({"ok": False, "error": _org_check.get("error", "org not found")}, 400)
            return
        orgs, repos = [], []
        for o in (payload.get("extra_orgs") or []):
            o = (o or "").strip()
            if o and re.match(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$", o):
                orgs.append(o)
        for r in (payload.get("extra_repos") or []):
            r = (r or "").strip()
            if r and re.match(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", r):
                repos.append(r)
        conn = store.connect()
        try:
            store.write_override(conn, "setting", "org", {"value": org})
            store.replace_overrides(conn, "extra_org", {o: {} for o in orgs})
            store.replace_overrides(conn, "extra_repo", {r: {} for r in repos})
        finally:
            conn.close()
        self.send_json({"ok": True})

    def do_POST(self) -> None:
        if self.require_auth():
            return
        path = urlparse(self.path).path
        if self.reject_cross_origin():
            return
        if path == "/api/dashboard/preview-panel":
            import dashboards
            payload = self._read_json_body()
            if payload is None:
                return
            panel = payload.get("panel") or {}
            scope = (payload.get("scope") or "")[:120]
            period = (payload.get("period") or "all")[:16]
            html = dashboards.render_panel(panel, scope=scope, period=period)
            # The modal preview renders outside the dashboard page's own <style>, so
            # without the chart CSS a line/area/bar/pie preview would fall back to
            # the default SVG fill:black — same bug as the dashboard view itself.
            html = "<style>%s</style>%s" % (shell.CHART_CSS, html)
            self.send_bytes(html.encode(), "text/html; charset=utf-8")
            return
        if path == "/api/dashboard/preview-panel.json":
            # Resolved-DATA twin of /api/dashboard/preview-panel (same body shape:
            # {panel, scope?, period?}) — returns {viz,title,pin,data} JSON for the
            # React preview island, where the HTML endpoint returns rendered HTML.
            import dashboards
            payload = self._read_json_body()
            if payload is None:
                return
            panel = payload.get("panel") or {}
            scope = (payload.get("scope") or "")[:120]
            period = (payload.get("period") or "all")[:16]
            resolved = dashboards.resolve_panel_data(panel, scope=scope, period=period)
            self.send_json({"ok": True, **resolved})
            return
        if path == "/api/chat":
            self.serve_chat()
            return
        if path == "/api/usage":
            # Best-effort usage beacon (tab/panel views). Identity is resolved
            # SERVER-SIDE; any identity the client puts in the body is ignored.
            # Always answers 204 — a tracking beacon must never surface an error.
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if 0 < length <= MAX_USAGE_BYTES:
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = None
                if isinstance(payload, dict) and isinstance(payload.get("events"), list):
                    sid = payload.get("session_id")
                    events = [e for e in payload["events"][:MAX_USAGE_EVENTS]
                              if isinstance(e, dict)]
                    for e in events:                    # stamp the shared session id
                        if sid and not e.get("session_id"):
                            e["session_id"] = sid
                    try:
                        import store
                        conn = store.connect()
                        try:
                            login, ident = self._resolve_viewer(conn)
                            store.record_usage_events(conn, login, ident, events)
                        finally:
                            conn.close()
                    except Exception as exc:            # noqa: BLE001 — never error
                        # Beacon endpoint: the client is gone and cannot be told, so
                        # the swallow stays. Logged for the same reason as the other
                        # adoption writes — a permanently failing one would otherwise
                        # read as "nobody used the report".
                        log_degraded("usage-event beacon write", exc)
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if path == "/api/dashboard" or path.startswith("/api/dashboard/"):
            import store, dashboards
            payload = self._read_json_body()
            if payload is None:
                return
            action = (payload.get("action") or "save")
            spec = payload.get("spec") or {}
            conn = store.connect()
            try:
                login, _ = self._resolve_viewer(conn)
                if login is None:
                    self.send_json({"ok": False, "error": "sign-in required"}, 403)
                    return
                did = path[len("/api/dashboard/"):] if path != "/api/dashboard" else ""
                if action == "delete" and did:
                    d = store.get_dashboard(conn, did)
                    if not d or d["owner_login"] != login:
                        self.send_json({"ok": False, "error": "not found"}, 404)
                        return
                    store.delete_dashboard(conn, did)
                    self.send_json({"ok": True})
                    return
                ok, err = dashboards.validate_spec(spec)
                if not ok:
                    self.send_json({"ok": False, "error": err}, 400)
                    return
                vis = payload.get("visibility") if payload.get("visibility") in ("private", "shared") else "private"
                if did:
                    d = store.get_dashboard(conn, did)
                    if not d or d["owner_login"] != login:
                        self.send_json({"ok": False, "error": "not found"}, 404)
                        return
                    store.update_dashboard(conn, did, title=spec.get("title"),
                                           spec=spec, visibility=vis)
                    self.send_json({"ok": True, "id": did})
                else:
                    new_id = store.create_dashboard(conn, login, spec.get("title"),
                                                     spec, visibility=vis)
                    self.send_json({"ok": True, "id": new_id})
            finally:
                conn.close()
            return
        if path == "/api/people-yaml":
            raw_len = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_len)
            except ValueError:
                self.send_json({"ok": False, "error": "invalid content length"}, 400)
                return
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                self.send_json({"ok": False, "error": "invalid payload size"}, 413)
                return
            try:
                text = self.rfile.read(length).decode("utf-8")
            except UnicodeDecodeError:
                self.send_json({"ok": False, "error": "body must be UTF-8"}, 400)
                return
            # optimistic concurrency: reject a stale tab's full-replace instead of
            # silently clobbering edits another session made since it loaded.
            import store as _store
            _conn = _store.connect()
            cur_ver = _store.overrides_version(_conn, ("person",))
            _conn.close()
            sent_ver = self.headers.get("X-Override-Version")
            if sent_ver is not None and sent_ver != cur_ver:
                self.send_json({"ok": False, "error": "stale edit — another session saved "
                                "changes; reload before saving"}, 409)
                return
            # deliberate roster shrink, confirmed by a client that measured it (see
            # _check_roster_drop) — never a blanket "yes", the count has to match
            try:
                allow_drop = int(self.headers.get("X-Allow-Drop") or 0)
            except ValueError:
                allow_drop = 0
            # Both editors POST application/json: the roster mapping as data, with no
            # hand-written YAML in between. A text/yaml BODY still parses, so an edit
            # from a tab on a pre-JSON bundle is not stranded, but nothing shipped
            # generates one and no people.yaml file is involved either way.
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            try:
                if ctype == "application/json":
                    try:
                        body = json.loads(text)
                    except json.JSONDecodeError as exc:
                        self.send_json({"ok": False, "error": f"invalid JSON: {exc}"}, 400)
                        return
                    if not isinstance(body, dict):
                        self.send_json({"ok": False, "error": "expected a JSON object"}, 400)
                        return
                    save_people(body.get("people"), allow_drop=allow_drop)
                else:
                    write_people_yaml(text, allow_drop=allow_drop)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            # apply the edit immediately: fold company/name/aliases into the
            # collected data + re-render (seconds, no GitHub). Saving still
            # succeeds even if the fast apply hiccups.
            applied, apply_error = False, None
            try:
                import reindex
                reindex.apply(do_render=False)  # served live; report_version() moved
                applied = True
            except Exception as exc:           # noqa: BLE001
                apply_error = str(exc)
            _conn = _store.connect()
            new_ver = _store.overrides_version(_conn, ("person",))
            _conn.close()
            self.send_json({"ok": True, "applied": applied,
                            "apply_error": apply_error, "version": new_ver})
            return

        if path in ("/api/config", "/api/config/collect"):
            self.handle_config_post(path)
            return
        if path == "/api/score-label":
            import re as _re
            payload = self._read_json_body()
            if payload is None:
                return
            subject = (payload.get("subject") or "").strip()
            if not _re.match(r"^[A-Za-z0-9-]{1,39}$", subject):
                self.send_json({"ok": False, "error": "invalid subject"}, 400)
                return
            try:
                rating = int(payload.get("rating"))
            except (TypeError, ValueError):
                self.send_json({"ok": False, "error": "rating must be 1–5"}, 400)
                return
            if not 1 <= rating <= 5:
                self.send_json({"ok": False, "error": "rating must be 1–5"}, 400)
                return
            note = (payload.get("note") or "")[:500]
            import store as _store
            _conn = _store.connect()
            try:
                _store.write_score_label(_conn, subject, self._oauth_user(), rating, note)
            finally:
                _conn.close()
            self.send_json({"ok": True})
            return
        if path == "/api/score-weights":
            payload = self._read_json_body()
            if payload is None:
                return
            import configstore as _cs
            try:
                eff = _cs.save_score_weights(payload.get("weights") or payload)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            except Exception as exc:               # noqa: BLE001 — never 500 silently
                self.send_json({"ok": False, "error": str(exc)}, 500)
                return
            self.send_json({"ok": True, "weights": eff})
            return
        if path == "/api/config/policy":
            # Deliberately its own endpoint rather than part of the config save, for
            # the same reason the score weights are: that save does whole-scope
            # replaces, and a policy block must not be wiped by an unrelated edit to
            # repo classification. One block per request, so a bad YAML in one cannot
            # lose the others.
            payload = self._read_json_body()
            if payload is None:
                return
            import configstore as _cs
            try:
                out = _cs.save_policy(payload.get("key") or "", payload.get("yaml"))
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            except Exception as exc:               # noqa: BLE001 — never 500 silently
                log_degraded(f"policy save ({payload.get('key')})", exc)
                self.send_json({"ok": False, "error": str(exc)}, 500)
                return
            self.send_json({"ok": True, **out})
            return
        if path == "/api/semantic":
            self.handle_semantic_post()
            return
        if path == "/api/semantic/coverage":
            import store
            import semantic_editor
            payload = self._read_json_body()
            if payload is None:
                return
            level = (payload.get("level") or "global").strip()
            target = (payload.get("target") or "").strip()
            import re as _re
            if level not in ("global", "org", "element", "repo", "project") or \
                    not _re.match(r"^[A-Za-z0-9_./-]{0,120}$", target):
                self.send_json({"ok": False, "error": "invalid scope"}, 400)
                return
            conn = store.connect()
            try:
                cov = semantic_editor.coverage_preview(
                    conn, level, target, payload.get("assignments") or {})
            except Exception as exc:                 # noqa: BLE001
                self.send_json({"ok": False, "error": str(exc)}, 400)
                return
            finally:
                conn.close()
            self.send_json({"ok": True, "coverage": cov})
            return
        if path == "/api/mcp/token":
            import secrets as _secrets
            import store as _store
            tok = _secrets.token_hex(24)
            conn = _store.connect()
            _store.set_secret(conn, "mcp_token", tok)
            conn.close()
            self.send_json({"ok": True, "token": tok})
            return
        if path == "/api/mcp/public-url":
            import re as _re
            import store as _store
            payload = self._read_json_body()
            if payload is None:
                return
            url = (payload.get("url") or "").strip().rstrip("/")
            if url and not _re.match(r"^https?://[A-Za-z0-9.\-]+(:\d+)?$", url):
                self.send_json({"ok": False,
                                "error": "must be http(s)://host — no path"}, 400)
                return
            conn = _store.connect()
            _store.write_override(conn, "setting", "public_url", {"value": url})
            conn.close()
            self.send_json({"ok": True, "url": url})
            return
        if path in ("/api/setup/token", "/api/setup/token/clear",
                    "/api/setup/save", "/api/setup/collect"):
            self.handle_setup_post(path)
            return

        jobs = {
            "/api/refresh": ("refresh", ["refresh"]),
            "/api/directory": ("directory", ["directory"]),
            "/api/export": ("export", ["export"]),
        }
        if path not in jobs:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        kind, args = jobs[path]
        if not run_job(kind, args):
            self.send_json({"ok": False, "error": "job already running"}, 409)
            return
        self.send_json({"ok": True, "job": kind}, 202)


class _PortalServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that treats a mid-response client disconnect as the
    normal end of a connection, not an error.

    When a browser aborts an in-flight request (switching period, navigating away)
    the next socket write raises BrokenPipeError/ConnectionResetError — inherent to
    the network, not a fault in our handlers, and unpreventable. socketserver would
    otherwise dump a full traceback per abort. We recognise exactly those two
    exceptions and log a single concise line; every other exception still gets the
    default full traceback so real bugs stay loud."""

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            print(f"{client_address[0]} - client disconnected before the response "
                  f"completed ({type(exc).__name__})")
            return
        super().handle_error(request, client_address)


def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    # Self-heal any taxonomy saved under an older bucket vocabulary (idempotent).
    try:
        import store
        import semantic_editor
        conn = store.connect()
        try:
            if semantic_editor.seed_split_categories(conn):
                print("Taxonomy: applied one-time bug/feature/epic/story split.")
            n = semantic_editor.migrate_saved_buckets(conn)
        finally:
            conn.close()
        if n:
            print(f"Taxonomy: migrated {n} saved override patch(es) to the current vocabulary.")
    except Exception as e:  # never block startup on a best-effort migration
        print(f"Taxonomy migration skipped: {e}")
    httpd = _PortalServer((host, port), Handler)
    print(f"Serving report portal at http://{host}:{port}")
    if PORTAL_PASSWORD:
        print(f"Built-in auth ON — HTTP Basic, user '{PORTAL_USER}'.")
    elif host not in ("127.0.0.1", "localhost", "::1"):
        print("\n  ⚠  SECURITY: portal is bound to a non-local address with NO built-in auth.")
        print("     Anyone who can reach it sees your data and can edit config/identity.")
        print("     Set PORTAL_PASSWORD to require a login, or put it behind an authenticating")
        print("     reverse proxy, or bind PORTAL_HOST=127.0.0.1.\n")
    httpd.serve_forever()


if __name__ == "__main__":
    serve(
        host=os.environ.get("PORTAL_HOST", "127.0.0.1"),
        port=int(os.environ.get("REPORT_PORT", "8080")),
    )
