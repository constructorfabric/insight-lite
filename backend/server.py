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

# The repo root, one level up from backend/: templates/, assets/ and config.yaml
# live there, not next to the modules.
ROOT = Path(__file__).resolve().parent.parent
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
            # it used to stamp was a mirror, and reading it back corrupted the table.
            # No "identity_editor" either: the editor is the React /identity route, so
            # identity-editor.html is not written and the stamp read "missing" for ever.)
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
        # reportctl lives beside this file in backend/, while ROOT is the repo root
        cmd = [sys.executable, str(Path(__file__).resolve().parent / "reportctl.py"), *args]
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


# Shared model cache. build_model runs on the first hit after any collect/edit and
# is cached until the DB changes, feeding every /api/report/* view. Keyed on
# store.report_version() — a content token that moves the instant any run blob or
# override is written, unlike the DB file mtime, which under WAL needn't change on
# an in-process write. It used to also cache rendered HTML and the Jinja macro
# globals; both went with the monolith.
_RENDER: dict = {"version": None, "model": None}
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


def _cached_report_model():
    """The cached model if it is present AND current, else None. Never builds.

    For callers that WANT the model but must not pay for it — see the page path's
    _filter_inputs. Everything that needs a model to answer at all goes through
    _report_model, which builds and can serve stale."""
    version = _report_version()
    with _RENDER_LOCK:
        if _RENDER["version"] == version and _RENDER["model"] is not None:
            return _RENDER["model"]
    return None


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
            _RENDER.update(version=version, model=model)
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


def setup_boot() -> dict:
    """What the setup wizard is bootstrapped with.

    A function, not an inline dict in the route, because of the first field: the
    wizard must know WHETHER a token is stored without ever receiving it, and that is
    a rule worth being able to test directly rather than by scraping a rendered page
    (which is how it was tested while the wizard was server-rendered HTML).

    A config that cannot be read falls back to EMPTY fields, which would show a
    configured org as unconfigured — and saving the form as presented would write the
    blanks back over it. So the failure is logged loudly rather than swallowed."""
    cfg = {}
    try:
        import ghclient
        cfg = ghclient.load_config()
    except Exception as exc:               # noqa: BLE001 — degraded, never fatal
        log_degraded("setup wizard config load", exc)
    return {"token_status": token_status(),
            "org": str(cfg.get("org", "")),
            "extra_orgs": ", ".join(cfg.get("extra_orgs", []) or []),
            "extra_repos": ", ".join(cfg.get("extra_repos", []) or [])}


def _nav_param(path: str, name: str) -> str:
    """One query value off a request path, or "" — for threading a page's subject into
    the sidebar's view links (see shell._carry_href)."""
    from urllib.parse import parse_qs
    return (parse_qs(urlparse(path).query).get(name, [""])[0] or "").strip()


def _nav_view(path: str, default: str, allowed: tuple) -> str:
    """Which view of a broken-up page is being asked for. Unknown values fall back to
    the default rather than 404ing: `?view=` is navigation state, and a stale link
    should land on the page rather than on an error."""
    v = _nav_param(path, "view")
    return v if v in allowed else default


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

    def _report_carry(self) -> dict:
        """What travels with you across report pages: the global filters and, on a page
        that has one, its subject. Read straight off the request — a link rendered here
        should land on the next page with what you already picked still applied.

        The key list and the per-zone rule both live in shell (CARRY_KEYS / zone_carry),
        so reading and carrying cannot drift apart."""
        return {k: _nav_param(self.path, k) for k in shell.CARRY_KEYS}

    def _filter_inputs(self) -> dict | None:
        """The filter bar's query-independent inputs, for render_spa_page to inline as
        the `#filter-model` island (see render.filter_model), so the bar paints with the
        shell instead of behind a skeleton.

        PEEKS at the model cache — never _report_model(), which BUILDS on a miss. That
        build is load_data() + build_model(), measured at 3.5s against production, and
        putting it on the page path would mean the first request after every collection
        blocks the whole shell on it: a slower page, in the name of a faster filter bar.
        The /api/report/* call on that same page has to build anyway, so a cold cache
        costs one skeleton strip and warms it for every page after.

        None — the caller falls back to that skeleton — whenever there is nothing
        cached, including a fresh install with no model at all. A filter bar is not
        worth failing a page render over, so every failure mode is swallowed."""
        try:
            import render as _r
            model = _cached_report_model()
            if model is None:
                return None
            fm = _r.filter_model({"window_labels": model.get("window_labels"),
                                  "all_label": model.get("all_label"),
                                  "scope_targets": model.get("scope_targets")})
            return {"periodPresets": fm["periodPresets"], "scopeTargets": fm["scopeTargets"]}
        except Exception:                    # noqa: BLE001 — see docstring
            return None

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
            # ONE read of work_item_status for this whole request. Two things want it —
            # the rewind scan and the stage dwell — and the scan is then windowed twice,
            # for this period (the panel) and the preceding one (the tile's delta).
            # Left to themselves these would read the same 141k rows three times.
            board_rows = semantic_metrics.board_snapshot_rows(conn, repos)
            rewind_scan = semantic_metrics.board_rewind_scan(conn, repos, board_rows)
            # in_flight takes no since/until on purpose — it is a point-in-time
            # quantity that must not move with the period control (see store.in_flight).
            block = semantic_metrics.flow_report(conn, repos, since, until, rewind_scan,
                                                 board_rows)
            # period-over-period deltas vs the preceding equal window (skipped for
            # all-time / >2y spans) — same rule and same best-effort handling the
            # Delivery KPIs use, so a flow number never sits on the page with nothing
            # to compare it against.
            try:
                ds = datetime.fromisoformat(since.replace("Z", "+00:00"))
                du = datetime.fromisoformat(until.replace("Z", "+00:00"))
                span = du - ds
                if 0 < span.days <= 731:
                    p_since = (ds - span).strftime("%Y-%m-%dT%H:%M:%SZ")
                    prev = semantic_metrics.flow_kpis(conn, repos, p_since, since,
                                                      rewind_scan)
                    block["deltas"] = render.delta_map(
                        block, prev, keys=semantic_metrics.FLOW_DELTA_KEYS)
            except Exception as exc:        # noqa: BLE001 — deltas are best-effort
                log_degraded("Flow deltas (/api/report/flow)", exc)
            pr = {"flow": block,
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

    def do_GET(self) -> None:
        if self.require_auth():
            return
        path = unquote(urlparse(self.path).path)
        if path == "/setup":
            # A standalone centred page, so no sidebar.
            import render as _render
            self.send_bytes(
                _render.render_spa_page("setup", "setup", "Set up",
                                        bootstrap=setup_boot(), sidebar=False).encode(),
                "text/html; charset=utf-8")
        elif path == "/metrics":
            from urllib.parse import parse_qs as _pq
            import render as _render
            self.send_bytes(
                _render.render_spa_page("metrics", "metrics", "Metrics catalog").encode(),
                "text/html; charset=utf-8")
        elif path == "/views":
            import render as _render
            self.send_bytes(
                _render.render_spa_page("views", "views", "View catalog").encode(),
                "text/html; charset=utf-8")
        elif path == "/calibrate":
            import render as _render
            self.send_bytes(
                _render.render_spa_page("calibrate", "calibrate", "Calibrate").encode(),
                "text/html; charset=utf-8")
        elif path == "/api/manage/calibrate.json":
            import calibrate
            self.send_json({"ok": True, **calibrate.calibrate_json(self._oauth_user())})
        elif path in ("/whats-new", "/changelog"):
            # React route (see docs/superpowers/plans/2026-07-22-react-phase0.md
            # P0-T4): data comes from /api/whats-new.
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
            # The wizard lives at /semantic, the dense grid at /semantic/advanced;
            # both fetch from GET /api/semantic/{wizard,scope,effective}.
            from urllib.parse import parse_qs as _pq
            advanced = path.endswith("advanced")
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
            # The page polls GET /api/status (= snapshot_state) for live data.
            import render as _render
            self.send_bytes(
                _render.render_spa_page("update", "update", "Update").encode(),
                "text/html; charset=utf-8")
        elif path == "/mcp-info":
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
            # The Jinja monolith is gone. What is left here is the migration shim: a
            # client-side hash redirect that maps the old `#<mode>` deep-links onto
            # their per-view React routes, so bookmarks from before the cutover still
            # land somewhere real. /report/legacy and ?legacy=1 no longer mean
            # anything and fall through to the same shim.
            import render as _render
            self._log_page_open()
            self.send_bytes(_render.report_redirect_shim().encode("utf-8"),
                            "text/html; charset=utf-8")
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
                render.render_spa_page("overview", "overview", "Overview",
                                       report_chrome=True, filter_inputs=self._filter_inputs(),
                                       nav_carry=self._report_carry()).encode(),
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
                render.render_spa_page("trend", "trend", "Trend",
                                       report_chrome=True, filter_inputs=self._filter_inputs(),
                                       nav_carry=self._report_carry()).encode(),
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
                render.render_spa_page("delivery", "delivery", "Delivery",
                                       report_chrome=True, filter_inputs=self._filter_inputs(),
                                       nav_carry=self._report_carry()).encode(),
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
                render.render_spa_page("flow", "flow", "Flow",
                                       report_chrome=True, filter_inputs=self._filter_inputs(),
                                       nav_carry=self._report_carry()).encode(),
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
                render.render_spa_page(
                    "people", "people-" + _nav_view(self.path, "roster",
                                                    ("roster", "categories", "reviews")),
                    "People", report_chrome=True, filter_inputs=self._filter_inputs(),
                    nav_carry=self._report_carry()).encode(),
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
                render.render_spa_page(
                    "person", "person-" + _nav_view(
                        self.path, "overview",
                        ("overview", "activity", "work", "impact", "score")),
                    "Person", report_chrome=True, filter_inputs=self._filter_inputs(),
                    nav_carry=self._report_carry()).encode(),
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
                render.render_spa_page("repositories", "repos", "Repositories",
                                       report_chrome=True, filter_inputs=self._filter_inputs(),
                                       nav_carry=self._report_carry()).encode(),
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
                render.render_spa_page("elements", "elements", "Elements",
                                       report_chrome=True, filter_inputs=self._filter_inputs(),
                                       nav_carry=self._report_carry()).encode(),
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
                render.render_spa_page("traffic", "traffic", "Traffic",
                                       report_chrome=True, filter_inputs=self._filter_inputs(),
                                       nav_carry=self._report_carry()).encode(),
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
                render.render_spa_page("ai-tools", "fabric", "AI tools",
                                       report_chrome=True, filter_inputs=self._filter_inputs(),
                                       nav_carry=self._report_carry()).encode(),
                "text/html; charset=utf-8")
        elif path == "/identity":
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
        elif path == "/api/drill":
            self.serve_drill()
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
            import render as _render
            self.send_bytes(
                _render.render_spa_page("usage", "usage-insights", "Usage insights").encode(),
                "text/html; charset=utf-8")
        elif path == "/chat-log":
            # Assistant conversation viewer — intentionally NOT linked in the sidebar;
            # reachable by URL only. Portal auth still applies. React route (Manage
            # migration). Data comes from the existing GET /api/chat-sessions +
            # /api/chat-session endpoints.
            import render as _render
            self.send_bytes(
                _render.render_spa_page("chatlog", "", "Assistant conversations").encode(),
                "text/html; charset=utf-8")
        elif path == "/dashboards":
            # Custom-dashboard list — linked from the sidebar.
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
                # React route (Manage migration). React reads the spec via bootstrap and
                # fetches measures/catalog/preview from the existing endpoints.
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
                # <PanelRenderer> catalog.
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
