# Report Usage Analytics — Design

**Date:** 2026-07-21
**Status:** Approved (design), pending implementation plan
**Approach:** A — raw event table + beacon, aggregate on read

## Problem

We have no visibility into how the Insight report itself is used. We want to know:

1. **Adoption / engagement** — who opens the report, how often, how many personas are active.
2. **Per-persona / per-team** — which people engage with which parts.
3. **Prune / prioritize widgets** — which panels nobody looks at, so we can cut them.
4. **Prove value** — a "usage of the usage report" figure for stakeholders.

All four are served by a single event stream: **who + what + when**.

## Existing plumbing we reuse (do not rebuild)

The report is served by `server.py` (`http.server`) behind:

```
browser → Cloudflare → nginx → oauth2-proxy → report portal (server.py:8080)
```

- **Identity is already solved.** oauth2-proxy forwards `X-Forwarded-User` / `-Email` /
  `-Preferred-Username`. `server.py:_oauth_idents()` reads them (GitHub username first),
  and `store.person_login_for(conn, ident)` maps an identity to a persona login. `/api/whoami`
  already returns the viewer's persona. **We never trust the client for identity.**
- **Break-glass** = htpasswd Basic-auth fallback in oauth2-proxy (no GitHub identity).
- **Granular-events → aggregate-on-read** is the report's existing idiom (`store.aggregate`,
  the `commits`/`pull_request`/`issue` tables, `/api/period`). We mirror it.
- **Manage/portal** = `portal_html()` (`server.py:314`) with link-cards (Setup, Config,
  Identity, Metrics, View catalog, MCP access). The usage surface is a new card here.
- **The report is rendered LIVE from the DB**, not from a baked file. `do_GET` serves the
  `/report` / `/report.html` branch (`server.py:~1469-1488`) which calls `report_html()`
  (`server.py:832`, cached on `report_version`) built from `templates/report.j2` +
  `templates/fragment.j2`. The standalone `report.html` file (written by `render.py main()`)
  is consumed only by `email_report.py`; `send_html_file_with_nav()` (~line 876) is **unused**.
  **Testing implication:** template/instrumentation edits are picked up by a **server restart**
  (live render) — re-running `render.py` only rewrites the email file. Guard the instrumentation
  JS to no-op outside a live browser so it ships inert in the emailed copy.

## Decisions

| Decision | Choice |
|---|---|
| Approach | A (raw events + beacon, aggregate on read). B's nightly rollups deferred; schema makes them a painless later add. |
| View granularity | Page opens **+** tab activations **+** panel scroll-into-view. |
| Identity | Resolved **server-side** from proxy headers. Client identity in payloads is ignored. |
| Storage | New `usage_event` table in `report.db` (source-of-truth vision). |
| Surface | **Manage/portal**: new "Usage insights" link-card → `/usage-insights` page + `/api/usage-summary`. Its own period selector (outside report tabs). |
| Visibility | All signed-in viewers (internal tool). |
| Disclosure | None (internal tool behind org SSO). |
| Widget ids | **Reuse existing `data-period-panel` slugs**; do NOT invent a parallel `data-widget-id` system. Add ids only to non-filterable panels that lack one. |
| Viewer buckets | Two only: resolved persona (`viewer_login`), or **unresolved** (`viewer_login IS NULL`, raw ident kept). No separate `breakglass` label — it is indistinguishable from a GitHub user with no `person` row. |
| Zero-view detection | Follow-up (needs widget-id registry via `view_registry.py`); v1 shows observed bottom widgets. |

## 1. Data model — `store.py`

```sql
CREATE TABLE IF NOT EXISTS usage_event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,   -- ISO8601 UTC ending 'Z', ALWAYS server-stamped
                                     --   (lexicographic compare == chronological)
    session_id   TEXT,               -- client UUID per page load (tab/panel events only)
    viewer_login TEXT,               -- resolved persona login; NULL if unresolved
    viewer_ident TEXT,               -- raw proxy identity, or 'anon' when unresolved
    kind         TEXT    NOT NULL,   -- 'page' | 'tab' | 'panel'
    target       TEXT,               -- tab id / widget id; NULL for 'page'
    tab          TEXT,               -- active tab context for a panel event
    dwell_ms     INTEGER,            -- panel dwell before flush (optional)
    period       TEXT                -- active period preset when fired (optional)
);
CREATE INDEX IF NOT EXISTS idx_usage_ts     ON usage_event(ts);
CREATE INDEX IF NOT EXISTS idx_usage_viewer ON usage_event(viewer_login);
CREATE INDEX IF NOT EXISTS idx_usage_kind   ON usage_event(kind, target);
```

Raw events only; aggregate on read (volume is dozens of internal viewers). Retention prune is a
later concern.

## 2. Ingest — two paths, identity always server-side

### 2a. Page-opens — server-side, JS-independent
In `do_GET`, **in the `/report` / `/report.html` branch** (`server.py:~1469-1488`, the handler —
NOT inside the `report_html()` render, which is process-cached on `report_version` and would fire
once per version), after resolving the viewer via `_oauth_idents()` → `store.person_login_for()`,
insert a `page` event (`session_id=NULL`). Cannot be blocked by ad-blockers or JS-off → the
trustworthy adoption number. The client sends **no** page event, so no double-count.

Identity mapping: `_oauth_idents()` returns `[]` (not `'anon'`) when headers are absent — the
writer maps empty/unresolved → `viewer_ident='anon'`, `viewer_login=NULL`.

Wrap the insert in a try/except that **swallows errors** (model on the `/api/whoami` handler at
`server.py:1562`): a locked DB or write failure must never break the report render.

Filter obvious non-humans (health checks / bots) by UA and request path so they don't inflate opens.

### 2b. Tab/panel — `POST /api/usage` beacon
Body: `{ "session_id": "<uuid>", "events": [ { "kind": "tab"|"panel", "target": "...", "tab": "...", "dwell_ms": N, "period": "30d" } ] }`

Server:
- Reuse the existing **cross-origin guard** `reject_cross_origin()` (`server.py:924`). Note it
  passes requests with no `Origin` header — acceptable for a best-effort beacon.
- Cap body size; cap events per request; accept only `kind ∈ {tab, panel}`; truncate `target`/`tab`.
- **Resolve `viewer_login` / `viewer_ident` from headers**; ignore any identity in the body.
  Unresolved → `viewer_login=NULL`, raw ident kept. No `breakglass` label (see decision table).
- Server-stamp authoritative `ts`.
- Batch insert, wrapped in a try/except that **swallows errors** (SQLite write-contention: a
  full-replace collect holds the write lock past `busy_timeout=5000`). Always respond `204`.

## 3. Client collector — inline JS in `templates/report.j2` (live-rendered)

No dependencies; matches existing inline-JS style. Guard the whole module to no-op when
`IntersectionObserver`/`sendBeacon` are absent (so it ships inert in the emailed report).
- `session_id = crypto.randomUUID()` once per load.
- **Tabs:** hook the existing `selectMode()` / `.tabs .tab` switcher (`templates/report.j2:~1323-1372`)
  → enqueue `{kind:'tab', target:mode}` on each activation, including the initially shown tab.
  Reuse the existing mode/tab identifiers; do not invent new ones.
- **Panels:** the widget id is the existing **`data-period-panel`** slug. The `IntersectionObserver`
  (threshold ≈0.5) MUST observe the **persistent wrapper** `<div data-period-panel>` — `/api/period`
  swaps its `innerHTML` (`report.j2:~1056-1074`), so observing inner content would detach on reslice.
  Enqueue `{kind:'panel', target:slug, tab, dwell_ms}` **once per session per widget** (dedup `Set`);
  dwell timed enter→(exit or flush).
- **Flush:** batch queue; `navigator.sendBeacon('/api/usage', blob)` on
  `visibilitychange→hidden` and `pagehide`, plus a periodic flush. Never sends identity.

**Template change (minimal):** reuse existing `data-period-panel` slugs; add an id attribute ONLY
to the handful of non-filterable panels that currently lack one. No parallel id system.

## 4. Read & surface

- `store.usage_summary(conn, since, until)` →
  `{ opens, unique_personas, by_widget:[{target, views, unique_viewers}], by_persona:[{login, opens, widgets_seen}] }`.
  - **`by_widget` excludes `tab='all'` panel events** (see §5 "All-tab inflation"), so bottom-widget /
    prune analysis reflects deliberate visits, not one scroll through the All tab.
  - **`by_persona` filters `viewer_login IS NOT NULL`** so unresolved viewers don't render as a
    phantom "null" row. `unique_personas` uses `COUNT(DISTINCT viewer_login)` (NULLs already ignored).
- `GET /api/usage-summary?days=N` (or `?from=&to=`), mirroring `/api/period`. **Pad the bounds exactly
  as `serve_custom_period` does** — `since → …T00:00:00Z`, `until → …T23:59:59Z` — and compare on the
  full ISO `ts` inclusively (`ts >= ? AND ts <= ?`). A naive `ts <= 'YYYY-MM-DD'` drops the end day.
- New **"Usage insights"** link-card in `portal_html()` → **`/usage-insights`** page: opens +
  unique personas, top/bottom widgets, per-persona breakdown, with a small built-in period selector.
- **Zero-view widgets** (true "nobody opened this") require a known-widget-id registry — tie into
  `view_registry.py` / `views_catalog.py` in a **follow-up**. v1 shows observed bottom widgets.

## 5. Edge cases, accuracy & privacy

- **Unresolved viewers** (break-glass htpasswd users AND GitHub users with no `person` row —
  indistinguishable) → `viewer_login=NULL`, raw `viewer_ident` kept for forensics. No `breakglass` label.
- **All-tab inflation.** Nearly every panel carries `data-modes="… all"`, so the "All" tab renders
  every widget at once — one All-tab scroll would fire a panel-view for essentially every widget and,
  via once-per-session dedup, mark the whole report "seen," defeating prune goal #3. Mitigation:
  panel events carry their `tab` context, and `by_widget` **excludes `tab='all'`** events. Surface a
  one-line caveat that All-tab views are tracked separately.
- **Canonical tab mode.** `selectMode()` collapses hash aliases to a canonical mode
  (`hashMode`, e.g. `categories→people`, `ai-usage→fabric`). The collector enqueues the resolved
  `data-mode`, not the raw location hash, so tab counts don't fragment across aliases.
- **Bots / health checks** filtered from page opens by UA/path.
- **Beacon is lossy** — `sendBeacon` on `pagehide` with an expired oauth2-proxy session is 302'd to
  login and silently dropped. Therefore **server-side page-opens are the authoritative adoption
  number; beacon-derived tab/panel counts are a floor, not exact.** State this on the surface.
- **Page vs tab/panel events cannot be session-joined** (page events have `session_id=NULL`; only
  the client beacon carries a `session_id`). Metrics aggregate by `viewer_login`, so this is fine —
  but "% of opens that reached widget X" is NOT computable from this schema by design.
- **No disclosure** (internal tool behind org SSO), per decision.
- **Retention**: events grow slowly; optional prune later (YAGNI now).

## 6. Testing & verification

- **Unit:** `store.usage_summary` over a seeded event set; the event writer.
- **Endpoint:** `POST /api/usage` rejects cross-origin, oversized bodies, and non-POST; attributes
  the viewer from headers (not the body); happy-path batch insert.
- **UI:** **restart the server** (live render picks up template/JS edits — re-running `render.py`
  only rewrites the email file), load the report in the local preview, confirm beacons fire on tab
  switch / panel scroll (network panel) and that `/usage-insights` renders the summary.

## Addendum — drill-down analytics (added after review)

Extended the page/tab/panel granularity with **drill-downs**, in both directions:

- **Tracking (`kind='drill'`):** the collector records every drill-down open in the report.
  User clicks go through the report's OWN local `openDrill` (not `window.openDrill`), so a
  delegated `[data-drill]` click listener is the reliable hook; `window.openDrill` is also
  wrapped to catch the programmatic reopen from a shared `?drill=…` URL. `target` = the drill
  entity plus its flag (e.g. `commit`, `prs/merged`).
- **Surfacing:** `usage_summary` gained `by_drill`; a new `store.usage_detail(conn, since, until,
  by, key)` powers row-level drill-downs ON the insights page via `GET /api/usage-detail`:
  clicking a widget/tab/drill row shows *who viewed it* (resolved personas + one `(unresolved)`
  bucket); clicking a person shows *what they viewed* (widgets, tabs, drills). Rendered in a small
  modal on `/usage-insights`.

## Out of scope (explicit)

- Nightly rollups (approach B) — deferred; raw events make it a clean later add.
- True zero-view detection via widget registry — follow-up.
- Retention/pruning job.
- Admin-only gating of the usage surface.

## Changelog / memory reminders

- Add a `changelog.py` "What's new" entry before deploy (user-facing change).
- Deploy backs up `report.db` automatically (`deploy.sh`); the new table is additive.
