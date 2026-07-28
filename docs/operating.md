# Operating it

Running the portal beyond the quick start: Docker, deployment, tuning, and the checks worth doing before you share a report.

[← back to the README](../README.md)

## Python local portal

If you do not want Docker, run the same portal directly with Python:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python reportctl.py serve --host 127.0.0.1 --port 8080
```

Open <http://localhost:8080>.

By default the portal binds loopback (`127.0.0.1`) and runs without auth. Set
`PORTAL_HOST` (or pass `--host`) to bind elsewhere — Docker Compose does this
inside the container while publishing the port on the host's loopback only.

**Exposing it on a network?** Set `PORTAL_PASSWORD` (and optionally
`PORTAL_USER`, default `insight`) to require HTTP Basic auth on *every* request —
pages, `/data.json`, exports, and the config/identity APIs. With no password set
and a non-loopback bind, the server prints a loud startup warning. Behind an
authenticating reverse proxy you can leave it unset. State-changing (POST)
endpoints also reject cross-origin browser requests.

The portal provides:

- **Top view switcher** — move between Update, Report, and Identity resolution
  from any served page.
- **Refresh report** — runs `collect.py` then `render.py` in the background.
- **Rebuild identity editor** — runs `directory.py`.
- **Export snapshot** — renders the current `report.html` and serialises the run
  blob into `exports/` with a timestamp.
- **Open current report** — opens the latest `report.html` in the browser.
- **Identity resolution** — opens the identity/company editor; when served by
  the portal, **Save** POSTs the roster as JSON and the server writes it to the
  `override` table. If you open `identity-editor.html` as a standalone file, the
  browser falls back to downloading `people.json` for you to POST later.

The status cards show whether the server process sees the API cache in
`.cache/` and the local git clones in `.repos/`. Refresh still needs
`GH_TOKEN` or `GITHUB_TOKEN` in the server environment because the collector
initializes GitHub auth before reading cached API responses or fetching clone
updates. If you want to bypass the GitHub API cache from the CLI, use:

```bash
NO_CACHE=1 python reportctl.py refresh
```

## Docker details

Build and run the portal in Docker:

```bash
export GH_TOKEN=...
docker compose up --build
```

Then open <http://localhost:8080>.

`docker compose` mounts the project directory into `/work`, so the container
can reuse local `.cache/` API responses and `.repos/` git clones. A plain
`docker run` of the built image will not contain those directories unless you
mount them yourself; they are intentionally excluded from the build context.

The Docker portal uses the same cache as local CLI runs. If a refresh logs
`discarded invalid clone cache` or `discarded blobless clone cache`, it is
repairing one `.repos/<repo>` entry and continuing; this is expected after
older runs that created a broken path or a blobless clone.

CLI commands are also available through the container:

```bash
docker compose run --rm report python reportctl.py refresh
docker compose run --rm report python reportctl.py export
docker compose run --rm report python reportctl.py directory
```

## Develop locally → deploy to the server

The tool runs on a server (Docker Compose portal behind nginx basic-auth, daily
cron refresh). The loop for iterating:

**1. Edit & check locally**
```bash
PYTHONPATH=. python -m unittest discover -s tests   # tests
python render.py && open report.html                # preview render-only changes
# or the full local portal:  docker compose up --build   → http://localhost:8080
```

**2. Ship it** — one command, `deploy.sh`:
```bash
./deploy.sh                 # push code+config, rebuild container, re-render (fast).
                            # Use for code / render / template / server.py changes.
./deploy.sh --refresh       # ...then run a full collect (reportctl all). Use when
                            # collect.py, config.yaml or bot_logins changed. Hits GitHub.
```
`deploy.sh` rsyncs code + `config.yaml`, rebuilds the container, and refreshes. It
**never overwrites** server-side data/secrets: `report.db` (which holds the curated
identity + config overrides), the API cache, git clones, `.env`, and generated
`report.html` / `data.json` are left alone. It does take a `report.db` backup into
`history/backups/` before the image swap (newest 10 retained) — that snapshot is the
recovery path for a roster. Host/key/dir are overridable via `DEPLOY_HOST` / `DEPLOY_KEY` /
`DEPLOY_DIR`. (There were once `--identity` / `--pull-identity` flags that rsynced a
`people.yaml` between laptop and server; the file is gone, see below.)

**Identity is server-owned, and Save applies instantly.** Edit companies/aliases in
the browser at `/identity` and **Save** — one click writes the server's `override`
table (atomically, in `report.db`) **and immediately re-applies it**: `reindex.py`
folds company / name / login-aliases into
the already-collected data (granular tables, person dim, `data.json`) and re-renders
`report.html` — seconds, no GitHub fetch (`reportctl reindex`, also run inline by the
Save endpoint). Open the Report and the change is already there. (Blame-based
surviving-LOC for merged accounts fully reconciles on the next `reportctl all`.)
A normal `./deploy.sh` will **not** clobber these edits. To bring the curated file
back into git, run `./deploy.sh --pull-identity` and commit. Push local identity to
the server only with `./deploy.sh --identity` (a deliberate override).

**Server management**
```bash
ssh -i ~/.ssh/deploy_key user@your-server
cd ~/insight-report
docker compose ps                       # container status
docker compose logs -f report           # portal logs
docker compose exec report python reportctl.py all   # manual full refresh
tail -f /var/log/insight-report.log # daily-cron output (03:30 UTC)
curl -s localhost:8080/health/data      # is the DATA still being refreshed?
docker compose exec report python alert.py check     # same check, notifies + exits 1
```

**Access:** `http://<server>:8081/` (nginx basic-auth) — the portal itself is
bound to `127.0.0.1:8080` (not public). Daily `cron.d/insight-report` runs
`reportctl all` at 03:30 UTC.

**Is it still collecting?** Two endpoints, deliberately separate:
`/health` is liveness (is the process up) and always answers `200 ok`;
`/health/data` reports the age of the newest stored run and answers **503** once it
exceeds `HEALTH_MAX_AGE_HOURS` (default 36 — a nightly run makes ~24h normal). Point
a monitor at the second one. Note that oauth2-proxy fronts the portal, so an external
checker either polls from the box or needs an nginx `location` that bypasses the gate.

Failures notify through `ALERT_WEBHOOK_URL` (any Slack/Mattermost-style incoming
webhook; set it in `.env` together with `ALERT_LABEL=prod` so the message names the
environment rather than a container id) — the cron entries call `alert.py notify` on a
non-zero exit and `alert.py check` at 06:00 for the silent case where nothing crashed
but the data stopped advancing. With the variable unset the alert still reaches the log
and the exit code, just nobody's screen. This exists because the nightly refresh once
failed ten nights in a row unnoticed: `MAILTO=""` plus a log file is not alerting.
Compose reads `.env` when it *creates* a container, so apply the edit with
`docker compose up -d report` — a plain `restart` keeps the old environment.

Manual equivalent:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export GH_TOKEN="$(gh auth token)"
python collect.py && python render.py && open report.html
```

## Tuning

- **Window**: `lookback_days` in `config.yaml` — an integer (rolling window in
  days; `7` for a weekly delta) or **`all`** (also `0`) for the **entire history**
  of both orgs. All-time relies on: full git clones (no `--since` cap on
  commits/LOC/blame) and **date-sliced search** — GitHub's search API caps each
  query at 1000 results, so PR/issue sweeps recursively halve the date range
  until every sub-range is ≤1000, keeping the all-time count complete.
- **Repo classification**: move repos between `platform` / `app` / `ignore`.
  Anything unlisted is treated as `app` and flagged under "Unclassified repos".
- **Work type** (bug / feature / epic): not a `config.yaml` key. It is resolved from
  each issue's labels and native Issue Type by the taxonomy stored in the DB — edit it
  under Manage → Semantic, not in this file. The old `labels:` block was removed after it
  silently diverged from the resolver (see `collect.py`'s note on the 2026-07-17 rename).
- **Specs**: every markdown file is counted as a spec unless excluded by the
  `specs:` denylist in `config.yaml`. Keep that denylist current when adding
  generated docs, fixtures, agent files, or vendored frameworks.
- **Identity/company**: run `python3 directory.py`, review at `/identity`, Save,
  then rerun `python3 collect.py`.
- **Orgs**: `org` is the primary (carries the full pre-migration history, so it
  is cloned for code); `extra_orgs` (e.g. `your-old-org`) add
  PRs/issues/forks/members/identity. **Legacy-only** repos in an extra org (never
  migrated, no same-named primary repo) are **also cloned** so their code history
  counts too; migrated old-org copies are skipped to avoid double-counting.
- **Meaningful LOC**: `meaningful_loc` denylist (`exclude_segments` /
  `exclude_basenames` / `exclude_name_prefixes` / `exclude_suffixes`).
- **AI-tool markers**: `ai_tools.markers` — each `{pattern, precision}`.
- **Content provenance**: `studio_provenance.markers` (regex, `paths`/`exclude`,
  `mode: content|files`) + `blame_marker` (which marker's lines get blamed).
- **Framework usage**: `gears_usage.markers` (provider repos auto-excluded in code).
- **Generic trackers**: `fabric_trackers` — add a tracker = a marker entry
  (`mode: content|files`) + optional `exclude_repos`; renders a panel automatically.
- **Companies**: `companies.domains` (email-domain → company) +
  `companies.overrides` (login → company) + `companies.default`.
- **Cache**: `cache_ttl_hours` for the API disk cache; `NO_CACHE=1` bypasses it.
- **Elements**: `elements:` maps repo name → product element (exact names, then
  trailing-`*` prefix globs, else `default`). Orthogonal to platform/app.
- **Blame cache**: surviving-LOC blame is cached per repo under
  `.cache/blame/<repo>.json`, keyed by file blob SHA; unchanged files are not
  re-blamed. Delete `.cache/blame/` to force a full re-blame.
- **API rate limit**: `api_max_wait_seconds` (default 90) caps how long a run
  waits out a primary GitHub limit before giving up. If the limit is hit, the
  run finishes with **partial** data, `data.json` carries an `api` block
  (`rate_limited`, `reset`, remaining budget), and the report shows a red
  "PARTIAL" banner. Re-run after the reset for complete data.

## Trust checklist

Before treating a report as stakeholder-ready, check:

- `identity.unresolved_human` in `data.json` is understood or resolved.
- The report has no unexpected "Unclassified repos" section.
- The clone-traffic panel has access to the repos you care about; otherwise
  usage volume is partial.
- Label mappings in `config.yaml` match current team practice.

## Pieces

| File | Role |
|------|------|
| `config.yaml` | repo classification, lookback, labels, meaningful-LOC / AI-tool / provenance / framework / tracker markers, companies, identity overrides |
| `collect.py` | collects GitHub API + git-history data → SQLite `runs`/`person_runs`/`repo_runs` (+ `data.json` export) |
| `render.py` | latest run from SQLite (→ `data.json` fallback) → self-contained `report.html` (no CDN, inline JS for tabs/tooltips, email-safe) |
| `ghclient.py` | shared GitHub client (pagination, rate-limit + transient-5xx backoff, raw-response JSON file cache) + config loader |
| `store.py` | SQLite store (`history/report.db`): runs, normalised people/repos, traffic, snapshots; exports `history/*.jsonl` (git-ignored: org data, not source) |
| `identity.py` | clone/fetch, verified pairs, PR-bridge, name-bridge, identity resolution + suggestions |
| `directory.py` | builds `identity-editor.html` from the collected run + the curated identity overrides |
| `reportctl.py` | CLI: `collect / render / directory / refresh / all / export / serve` (`--no-cache`) |
| `server.py` | local web portal (view / refresh / export / edit identity) |
| `Dockerfile` / `docker-compose.yml` | containerized portal on `:8080` (secrets via `.env`, never hardcoded) |

