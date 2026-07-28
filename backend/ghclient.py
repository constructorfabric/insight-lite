#!/usr/bin/env python3
"""Shared GitHub client + config helpers (used by collect.py and identity.py).

Includes a disk cache keyed by logical request so iterating on the tool does
not burn the API rate limit. Delete .cache/ or set NO_CACHE=1 to bypass.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
import yaml

import paths

API = "https://api.github.com"
GRAPHQL = "https://api.github.com/graphql"
# The repo root, one level up from backend/: templates/, assets/ and config.yaml
# live there, not next to the modules.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = str(paths.data_path(".cache"))


def token(required: bool = True) -> str:
    """GitHub token: the UI-managed secret in the DB wins (so it can be changed from
    the interface); the GH_TOKEN/GITHUB_TOKEN env var is the bootstrap fallback.
    `required=False` returns '' when absent (for validation flows) instead of exiting."""
    tok = None
    try:
        import store
        conn = store.connect()
        tok = store.get_secret(conn, "gh_token")
        conn.close()
    except Exception:                    # noqa: BLE001 — DB optional
        tok = None
    if not tok:
        tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not tok and required:
        sys.exit("ERROR: set a GitHub token via the portal, "
                 "or GH_TOKEN/GITHUB_TOKEN in the environment.")
    return tok or ""


def validate_token(tok: str) -> dict:
    """Check a candidate GitHub token WITHOUT storing it: confirm it authenticates
    and report the login + remaining rate limit. Never logs the token value."""
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    try:
        r = requests.get(f"{API}/rate_limit", headers=h, timeout=15)
        if r.status_code == 401:
            return {"ok": False, "error": "token rejected (401 unauthorized)"}
        r.raise_for_status()
        core = (r.json().get("resources", {}) or {}).get("core", {})
        login = None
        try:
            u = requests.get(f"{API}/user", headers=h, timeout=15)
            if u.ok:
                login = u.json().get("login")
        except requests.RequestException:
            pass
        return {"ok": True, "login": login,
                "remaining": core.get("remaining"), "limit": core.get("limit")}
    except requests.RequestException as exc:
        return {"ok": False, "error": f"could not reach GitHub: {exc}"}


def check_org(org: str, tok: str = "") -> dict:
    """Does `org` exist and can this token see it? {ok, error, kind, name}.

    The setup wizard used to accept any syntactically valid org name, so a typo (or a
    private org the token cannot see) was only discovered when the first collection came
    back with nothing — by which point the wizard has said "saved" and the failure looks
    like the tool not working. `kind` distinguishes an organisation from a user account,
    which matters: a user account has no members and no org-level PR/issue metadata, so
    half the report would be silently empty.

    Network problems return ok=True: refusing to save an org because GitHub is briefly
    unreachable would be worse than saving one we could not verify.
    """
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    try:
        r = requests.get(f"{API}/orgs/{org}", headers=h, timeout=15)
        if r.ok:
            return {"ok": True, "kind": "org", "name": r.json().get("login") or org}
        if r.status_code == 404:
            # Not an org — it may still be a user, which is a different (and mostly
            # unsupported) shape rather than a typo, so say which it is.
            u = requests.get(f"{API}/users/{org}", headers=h, timeout=15)
            if u.ok and (u.json().get("type") == "User"):
                return {"ok": False, "kind": "user",
                        "error": f"“{org}” is a user account, not an organisation — "
                                 f"members, PRs and issues are collected per org, so "
                                 f"most of the report would be empty"}
            return {"ok": False, "kind": "missing",
                    "error": f"no organisation “{org}” is visible to this token "
                             f"(check the spelling, and that the token can see it)"}
        if r.status_code in (401, 403):
            return {"ok": False, "kind": "forbidden",
                    "error": f"the token cannot read “{org}” "
                             f"(HTTP {r.status_code} — needs read:org)"}
        r.raise_for_status()
        return {"ok": True, "kind": "org", "name": org}
    except requests.RequestException:
        return {"ok": True, "kind": "unverified"}


def load_config() -> dict:
    """Base config.yaml with the server-owned DB config overlay layered on
    top (portal edits: repo classification / element, new elements, orgs, repos)."""
    with open(os.path.join(ROOT, "config.yaml")) as fh:
        cfg = yaml.safe_load(fh) or {}
    try:
        import configstore
        configstore.apply_overlay(cfg, configstore.load_overlay())
    except Exception:                # noqa: BLE001 — overlay is optional, never fatal
        pass
    return cfg


class GH:
    """Thin GitHub client: pagination, rate-limit backoff, disk cache."""

    def __init__(self, tok: str, cache_ttl_hours: float = 24.0, max_wait_seconds: float = 90.0,
                 historical_cache_ttl_hours: float = 720.0):
        self.s = requests.Session()
        self.s.headers.update(
            {
                "Authorization": f"Bearer {tok}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        self.cache_on = os.environ.get("NO_CACHE", "") == ""
        self.cache_ttl = cache_ttl_hours * 3600
        # Closed historical date-ranges don't change -> a much longer TTL avoids
        # re-fetching them on every all-time run. Only the slice touching "now"
        # uses the short default TTL.
        self.historical_ttl = historical_cache_ttl_hours * 3600
        # Raw GitHub API responses are the "initial data" — kept as JSON files
        # under .cache/ (one file per request, TTL'd). Processed/historical data
        # lives in SQLite (store.py); raw stays JSON for auditability.
        if self.cache_on:
            os.makedirs(CACHE_DIR, exist_ok=True)
        self.max_wait_seconds = float(max_wait_seconds)
        self.rate_limited = False           # sticky: a primary limit we couldn't wait out
        self.rate_reset_epoch: int | None = None
        self.rate_events: list[dict] = []   # [{type: primary|secondary, reset?: epoch}]
        self.degraded: list[str] = []       # non-rate-limit failures that lost data
        self.warnings: list[str] = []       # stable caps/truncations (non-blocking)

    @property
    def partial(self) -> bool:
        """True when any failure made this run's data incomplete."""
        return self.rate_limited or bool(self.degraded)

    # --- raw-response file cache (JSON, one file per request) --------------
    def _ckey(self, *parts) -> str:
        h = hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:32]
        return os.path.join(CACHE_DIR, f"{h}.json")

    def _cread(self, key: str, ttl: float | None = None):
        if not self.cache_on or not os.path.exists(key):
            return None
        ttl = self.cache_ttl if ttl is None else ttl
        if ttl and (time.time() - os.path.getmtime(key)) > ttl:
            return None
        try:
            with open(key) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    def _cwrite(self, key: str, value) -> None:
        if not self.cache_on:
            return
        tmp = f"{key}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump(value, fh)
        os.replace(tmp, key)

    # --- requests ----------------------------------------------------------
    def _throttled(self, r: requests.Response) -> bool:
        """True = handled, retry the request. False = not a rate limit (or we
        gave up). Sets sticky self.rate_limited when a primary quota is exhausted
        and its reset is beyond max_wait_seconds."""
        if r.status_code not in (403, 429):
            return False
        # primary rate limit: quota exhausted
        if r.headers.get("X-RateLimit-Remaining") == "0":
            reset = int(r.headers.get("X-RateLimit-Reset", "0"))
            self.rate_events.append({"type": "primary", "reset": reset})
            self.rate_reset_epoch = reset
            need = reset - int(time.time()) + 2
            if 0 < need <= self.max_wait_seconds:
                print(f"  rate-limited (primary), sleeping {need}s", file=sys.stderr)
                time.sleep(need)
                return True
            # reset too far to wait out -> give up cleanly, flag partial
            self.rate_limited = True
            print(f"  rate limit exhausted; reset in ~{max(need,0)}s — stopping, data will be PARTIAL",
                  file=sys.stderr)
            return False
        # secondary rate limit / abuse detection -> short backoff
        retry_after = r.headers.get("Retry-After")
        msg = ""
        try:
            msg = (r.json() or {}).get("message", "").lower()
        except ValueError:
            pass
        if retry_after or "secondary rate limit" in msg or "abuse" in msg:
            self.rate_events.append({"type": "secondary"})
            time.sleep(int(retry_after) if (retry_after or "").isdigit() else 5)
            return True
        # any other 403 (e.g. "Must have push access" on traffic) is NOT a rate
        # limit — do not retry, let the caller handle the status.
        return False

    def _transient(self, r: requests.Response, attempt: int) -> bool:
        """True = a transient server error (502/503/504) we should retry with
        backoff. GitHub returns these intermittently; one shouldn't kill a run."""
        if r.status_code in (500, 502, 503, 504) and attempt < 4:
            wait = 2 ** attempt          # 1, 2, 4, 8s
            print(f"  {r.status_code} from GitHub; retry in {wait}s", file=sys.stderr)
            time.sleep(wait)
            return True
        return False

    def get(self, path: str, params: dict | None = None) -> requests.Response:
        url = path if path.startswith("http") else f"{API}{path}"
        r = None
        for attempt in range(6):
            r = self.s.get(url, params=params)
            if self._throttled(r) or self._transient(r, attempt):
                continue
            return r
        if r is not None and r.status_code in (403, 429):
            # secondary limit survived every retry — same sticky-partial
            # semantics as an exhausted primary quota
            self.rate_events.append({"type": "secondary-exhausted"})
            self.rate_limited = True
            print("  secondary rate limit persisted after retries — data will be PARTIAL",
                  file=sys.stderr)
        return r

    def get_json(self, path: str, params: dict | None = None, ttl: float | None = None):
        """Cached single GET -> (status_code, json|None)."""
        key = self._ckey("getjson", path, json.dumps(params or {}, sort_keys=True))
        hit = self._cread(key, ttl)
        if hit is not None:
            return hit[0], hit[1]
        r = self.get(path, params)
        body = None
        try:
            body = r.json()
        except ValueError:
            pass
        # Only cache stable outcomes. 403s are transient more often than not
        # (SAML, IP allowlists, secondary limits) — caching one bakes a silent
        # zero into every run for up to the historical TTL.
        if r.status_code in (200, 404):
            self._cwrite(key, [r.status_code, body])
        return r.status_code, body

    def paginate(self, path: str, params: dict | None = None, cap: int = 5000,
                 ttl: float | None = None):
        params = dict(params or {})
        params.setdefault("per_page", 100)
        per_page = params["per_page"]
        key = self._ckey("paginate", path, json.dumps(params, sort_keys=True), str(cap))
        hit = self._cread(key, ttl)
        if hit is not None:
            return hit
        url = f"{API}{path}"
        out = []
        partial = False
        while url and len(out) < cap:
            r = self.get(url, params=params)
            if r.status_code in (404, 409):   # 409 = empty repo (list-commits)
                break
            if r.status_code in (403, 429):
                if not self.rate_limited:
                    # permission 403 (missing scope, SAML) — skip, don't crash
                    self.degraded.append(f"GET {path} -> {r.status_code}")
                    print(f"  WARN: {path} -> {r.status_code}; skipped, data will be PARTIAL",
                          file=sys.stderr)
                partial = True
                break
            r.raise_for_status()
            batch = r.json()
            if isinstance(batch, dict) and "items" in batch:
                out.extend(batch["items"])
                if len(batch["items"]) < per_page:
                    break
            else:
                out.extend(batch)
            url = r.links.get("next", {}).get("url")
            params = None
        if not partial:
            self._cwrite(key, out)   # never cache a rate-limited partial
        return out

    _SEARCH_DT = "%Y-%m-%dT%H:%M:%SZ"

    @staticmethod
    def _next_boundary(d: datetime, unit: str) -> datetime:
        """Start of the next calendar `unit` after d."""
        if unit == "year":
            return datetime(d.year + 1, 1, 1, tzinfo=timezone.utc)
        if unit == "month":
            return (datetime(d.year + 1, 1, 1, tzinfo=timezone.utc) if d.month == 12
                    else datetime(d.year, d.month + 1, 1, tzinfo=timezone.utc))
        if unit == "day":
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1)
        if unit == "hour":
            return d.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        if unit == "minute":
            return d.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return d.replace(microsecond=0) + timedelta(seconds=1)

    @classmethod
    def _calendar_buckets(cls, lo: datetime, hi: datetime) -> list:
        """Split [lo, hi] into consecutive sub-ranges aligned to the finest
        calendar unit on which lo and hi differ. Interior buckets are CANONICAL
        (e.g. a whole year/month) so their query strings — and cache keys — are
        identical across runs regardless of 'today'; only the first/last bucket
        is clamped to lo/hi."""
        if hi.year != lo.year:
            unit = "year"
        elif hi.month != lo.month:
            unit = "month"
        elif hi.day != lo.day:
            unit = "day"
        elif hi.hour != lo.hour:
            unit = "hour"
        elif hi.minute != lo.minute:
            unit = "minute"
        else:
            unit = "second"
        out, cur = [], lo
        while cur <= hi:
            nb = cls._next_boundary(cur, unit)
            out.append((cur, min(nb - timedelta(seconds=1), hi)))
            cur = nb
        return out

    def search_all(self, q_base: str, lo, hi=None) -> list:
        """Search the issues/PRs index without the silent 1000-results-per-query
        cap: recursively split the created-date range on CALENDAR boundaries
        (year → month → day → hour → minute → second) until each sub-range has
        <=1000 hits, then concatenate. Calendar-aligned buckets keep cache keys
        stable across days, so closed historical ranges hit the long-TTL cache on
        re-runs. `q_base` has no created: filter; `lo`/`hi` are dates or ISO ts."""
        def _dt(s):
            if isinstance(s, datetime):
                return s
            return datetime.strptime(s if "T" in s else s + "T00:00:00Z",
                                     self._SEARCH_DT).replace(tzinfo=timezone.utc)

        lo_dt, hi_dt = _dt(lo), (_dt(hi) if hi else datetime.now(timezone.utc).replace(microsecond=0))
        # A range ending before the start of today is CLOSED — members never change,
        # so cache it long; only ranges touching today use the short default TTL.
        today0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        ttl = self.historical_ttl if hi_dt < today0 else None
        q = f"{q_base} created:{lo_dt.strftime(self._SEARCH_DT)}..{hi_dt.strftime(self._SEARCH_DT)}"
        st, j = self.get_json("/search/issues", {"q": q, "per_page": 1}, ttl=ttl)
        if st != 200:
            # a failed count must not read as "zero results"
            self.degraded.append(f"search [{q_base}] {lo_dt:%Y-%m-%d}.. -> HTTP {st}")
            print(f"  WARN: search HTTP {st} for [{q_base}] — bucket dropped, data will be PARTIAL",
                  file=sys.stderr)
            return []
        total = (j or {}).get("total_count", 0)
        if total == 0:
            return []
        if total <= 1000:
            return self.paginate("/search/issues", {"q": q}, cap=1000, ttl=ttl)
        if (hi_dt - lo_dt).total_seconds() <= 1:   # single second with >1000 — unsplittable
            print(f"  WARN: >1000 results in a 1s window for [{q_base}] at {lo} — capped",
                  file=sys.stderr)
            return self.paginate("/search/issues", {"q": q}, cap=1000, ttl=ttl)
        out = []
        for a, b in self._calendar_buckets(lo_dt, hi_dt):
            out += self.search_all(q_base, a, b)
        return out

    def graphql(self, query: str, variables: dict) -> dict:
        gkey = self._ckey("graphql", query, json.dumps(variables, sort_keys=True))
        hit = self._cread(gkey)
        if hit is not None:
            return hit
        for attempt in range(6):
            r = self.s.post(GRAPHQL, json={"query": query, "variables": variables})
            if self._throttled(r) or self._transient(r, attempt):
                continue
            if r.status_code in (403, 429) and self.rate_limited:
                return {}            # partial; flag already set, not cached
            r.raise_for_status()
            data = r.json()
            errors = data.get("errors") or []
            # GraphQL signals primary rate-limit exhaustion with HTTP 200 +
            # errors[].type=RATE_LIMITED and data=null — never via status code.
            if any(e.get("type") == "RATE_LIMITED" for e in errors):
                reset = int(r.headers.get("X-RateLimit-Reset", "0") or 0)
                self.rate_events.append({"type": "primary", "reset": reset})
                self.rate_reset_epoch = reset or self.rate_reset_epoch
                need = reset - int(time.time()) + 2
                if 0 < need <= self.max_wait_seconds and attempt < 5:
                    print(f"  graphql rate-limited, sleeping {need}s", file=sys.stderr)
                    time.sleep(need)
                    continue
                self.rate_limited = True
                print("  graphql rate limit exhausted — stopping, data will be PARTIAL",
                      file=sys.stderr)
                return {}
            result = data.get("data") or {}
            if errors:
                # partial GraphQL response: return what came, never cache it
                msg = errors[0].get("message", "")
                self.degraded.append(f"graphql: {msg}")
                print(f"  graphql warn: {msg}", file=sys.stderr)
                return result
            self._cwrite(gkey, result)
            return result
        # all retries exhausted on transient errors / throttling that never cleared:
        # flag degradation so the run is treated as PARTIAL (else a zero-review
        # response would be persisted as a complete run and poison the trends).
        self.degraded.append("graphql: retries exhausted (transient errors)")
        print("  WARN: graphql retries exhausted — data will be PARTIAL", file=sys.stderr)
        return {}
