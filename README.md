# Insight Lite — Contribution & Usage Report

A self-hosted report on how people **contribute to** vs **merely use** a shared
internal platform, broken down across **code, specs, bugs, features, and people**.
It reads one or more GitHub organisations and renders a portal you host yourself;
nothing leaves your machine except GitHub API calls.

Point it at your own org in [`config.yaml`](config.yaml) — every org, repository,
element and company in the shipped config is invented, and so is every example in
this README.

## The question it answers

If your company builds a shared platform and other teams build products on top of
it, the interesting split is not "who commits the most" but:

1. **Contributing to the platform** — anyone with **any** contribution (commit, PR,
   spec edit, bug, or feature) to **any** repo in the org, split into org members
   vs external.
2. **Using the platform without contributing back** — accounts that **forked** an
   org repo (= using it) but made **zero** contribution to any org repo in the
   window.

*(Platform-vs-app is a separate "where effort goes" breakdown, configured per repo
in `config.yaml` → `repos:`. In the shipped example `example-core`, `example-sdk`
and `example-cli` are the platform; `example-web`, `example-api` and `example-docs`
are apps built on it. That axis is independent of the contribute/use line.)*

> GitHub-only data: passive consumption beyond forks/stars, and anonymous clone
> traffic, are not observable — the numbers here are a floor, not a census.
> Repo classification lives in [`config.yaml`](config.yaml); label→category mapping
> lives in the database-backed semantic taxonomy (see [`docs/semantic-config.md`](docs/semantic-config.md)).

> **A note on measuring people.** This tool aggregates identifiable per-person
> activity, which is personal data in most jurisdictions and a management artefact
> everywhere. The per-person Developer score in particular is explicitly
> experimental and is not a performance rating. Decide who may see it before you
> deploy it — the portal supports OAuth so that decision is enforceable.

## Report views (tabs)

`report.html` has a tab switcher (inline JS, no dependencies) that filters
sections by mode. Every section also carries `all`, so **All** shows everything.

- **Overview** — KPIs, commit mix, work type, contribution by category, scenarios.
- **Trend** — contribution over time, built from accumulated daily snapshots.
- **Repos** — repo coverage/inventory, platform-vs-app effort.
- **Elements** — per-Element rollup: repos, people, code/spec LOC, review TTM.
- **Usage** — traffic (clones + page views), external contributors.
- **People** — per-person table, code review, categories.
- **AI Tools Usage** — AI tools, content provenance, framework usage, generic
  trackers, platform-usage rollup by company/person, and **Bots & automation**.
- **All** — every section.

### Period filter (presets + custom)

A period selector (chips: 7d / 30d / 90d / 1 year / All-time + a custom from/to
range) re-slices **every panel that can be windowed**.

- **Portal-driven.** Each filterable panel is a `[data-period-panel]` region.
  Changing period — preset *or* custom — issues one request to
  **`/api/period?days=N`** (or `?from=YYYY-MM-DD&to=YYYY-MM-DD`); the server runs a
  single `store.aggregate(since, until)` over the granular tables and returns all
  filterable panels as an HTML fragment, which the page swaps in. Results are
  cached per period (re-selecting is instant); **All-time** restores the
  build-time snapshot without a request.
- **One source of truth.** The filterable panels are Jinja macros in the `panels`
  template, imported by both the report (build-time, all-time) and the
  `/api/period` fragment (windowed) — so they never drift.
- **Standalone file** (opened without the portal): shows the all-time state and a
  note that live filtering needs the portal.

**What the period covers** is spelled out in the UI legend under the bar:
period-filtered = **KPIs, contribution by company, %-by-category, work type,
commit mix, By Element, platform/app split, per-person activity columns, traffic**
(summed from the accumulated daily `traffic` table), **code review** (reviewers /
coverage / TTM from the granular `review` table), **bot activity**, and the
**AI-marked commit share**. Always **all-time** (by nature) = contributors
(cumulative), trend (the time axis), surviving-LOC and cpt lines (git-blame of
today's tree), repo coverage (inventory), content markers (provenance / framework
usage — a `git grep` over the current tree), and the per-tool AI split. In mixed panels the
windowed metrics and the all-time ones each carry the matching tag (a live period
tag or an `all-time` badge).

## Storage (SQLite)

The **SQLite** database `history/report.db` (override with `REPORT_DB`), managed
by `store.py`, is the **source of truth**. `collect.py` writes it; `render.py`
and `directory.py` read it (falling back to the `data.json` export only if the
DB is empty). Tables:

**Granular event tables (the source for period queries):**
- **`commits`** — one row per commit (date, author, repo, LOC, type, AI/bot flags).
- **`pull_request`** / **`issue`** — one row per PR / issue (created/merged dates,
  author, classification, bug/story + migration/bot flags).
- These make **any period a date-range query** — `store.aggregate(since, until)`
  powers both the presets and the custom-days endpoint. e.g. *Example Inc commits in a
  window*: `SELECT SUM(...) FROM commits c JOIN person p ON p.login=c.author_login
  WHERE p.company='Example Inc' AND c.committed_at BETWEEN ? AND ?`. Dates are stored
  UTC (`...Z`) so ranges compare cleanly.

**Dimensions:** **`person`** (login → name/company/emails/surviving-LOC) and
**`repo`** (key → classification/element/legacy/size), joined for company rollups.

**Other tables:**
- **`runs`** — the full `data.json` blob, one row per day (non-period report parts:
  repos inventory, traffic, provenance, bots, elements, identity). `render`/`directory`
  load the latest.
- **`traffic`** — raw per-repo/per-day clones & views (see below).
- **`snapshots`** — compact per-day aggregate behind the Trend tab.
- `person_runs` / `repo_runs` — legacy per-run aggregates, retained for cross-run
  SQL (superseded by the granular tables for period work).

The DB sits on the docker bind-mount (`.:/work`), so it persists across
container restarts; it is **git-ignored** (binary). Each run also exports the
durable history tables to **`history/*.jsonl`** and the latest payload to
**`data.json`** — text and diffable, so they are easy to back up and to inspect
(regenerated every run, so no drift). On first run an empty DB is **seeded** from any
existing `data.json` + `history/*.jsonl`.

> These exports are **git-ignored on purpose.** They describe your organisation:
> `snapshots.jsonl` carries per-company commit / LOC / headcount figures and
> `traffic.jsonl` carries repository names. An earlier internal version of this repo
> tracked them as a "text backup of the DB", which is exactly how internal metrics end
> up in a public git history. Back them up outside git.

### Layering: raw stays JSON, processed goes to SQLite

The split is deliberate. **Raw GitHub API responses — the "initial data" — stay
as JSON files** under `.cache/` (one file per request, TTL'd by
`cache_ttl_hours`; `NO_CACHE=1` bypasses). They are the auditable, untouched
source. Everything **processed or accumulated** — the run payload, normalised
people/repos, traffic series, trend snapshots — lives in SQLite. So `.cache/`
answers *"what did GitHub return"*, the DB answers *"what did we compute and how
did it change over time."*

## History & trend (snapshots)

The lookback window is a single point in time and can't show whether
contribution is rising or falling. To add the time dimension, every `collect.py`
run appends one compact dated row to **`history/snapshots.jsonl`** (per-company
commits/LOC/PRs/people + org totals). It is **idempotent per calendar day** — a
same-day re-run replaces that day's row — and only **complete** runs are
recorded (a rate-limited partial is skipped so it can't poison the trend). The
**Trend** tab plots these points; movement appears once ≥2 days accumulate.
Run the collector on a schedule (e.g. the weekly Action) and commit
`history/snapshots.jsonl` so the series persists across machines/runs.

### Raw traffic accumulation (perishable!)

GitHub's **Traffic API only returns the trailing 14 days** — older clone/view
counts are **unrecoverable** once they age out (unlike commits/LOC, which always
live in the clones). To preserve them, every run merges the raw per-repo/per-day
clone & view records into **`history/traffic.jsonl`**, keyed by `(repo, date)`
with the latest run winning (a day seen mid-progress is partial; a later run
completes it). **Run the collector at least once every 14 days** or there will
be permanent gaps. Because the data is genuinely unrecoverable, back this file up —
but **not into a public git repository**: it names your repositories. Both
`history/*.jsonl` files are git-ignored for that reason.

## Precision badges

Every signal is tagged **exact** (green) or **heuristic** (amber) so a reader
knows what to trust. Examples: an authenticated bot co-author trailer = exact; a bare tool name
(commit mention)` = heuristic; page views = exact-human; clone counts = heuristic
(CI-inflated). Marker precision lives in `config.yaml` (`precision:` on each
marker).

## Metrics & panels

All metrics are computed for the lookback window and, where shown, broken down
**by person, company, and repo**.

- **Code / LOC** — commits and `meaningful LOC` (raw git additions minus
  generated / vendor / dependency / fixture / lockfile / build / binary paths;
  filter in `config.meaningful_loc`). Raw additions are kept too.
- **Surviving LOC (contribution headline)** — `git blame` over each cloned
  repo's current tree attributes every still-existing line to its last author,
  split **code vs spec** and **AI-marked vs unmarked** (`@cpt` regions,
  a frontmatter flag, a generator stamp). The line counts are
  exact, but the human/AI split is **not**: **AI-marked** is a *floor* (only
  generations that leave a marker are caught), so **Unmarked** is an *upper
  bound* on hand-written code — tools that leave no marker (Copilot, pasted LLM
  output) also land in Unmarked. It's not proof of authorship. The metric still
  measures the final code that survives to today, so regenerating the same lines
  doesn't inflate it and commit count doesn't distort it. Commits and raw windowed additions are kept as secondary
  columns. A windowed "Δ window" column shows surviving lines whose last commit
  is inside `lookback_days`. Only computable for cloned primary-org repos.
- **By Element** — every repo maps to a product element (`config.elements`);
  the Elements tab rolls up Code/Spec KLOC, people, repos, windowed commits,
  PRs (opened/merged), median time-to-merge, and AI% per element.
- **Repo size** — the repo inventory shows each repo's surviving Code LOC and
  Spec LOC (blame totals; "—" for non-cloned old-org repos).
- **Specs** — commits touching a spec markdown file (`config.specs` denylist).
- **Bugs / user stories** — issues by label across both orgs.
- **Work type (conventional commits)** — exact split from `feat/fix/docs/…`
  commit-subject prefixes, by person / company / repo.
- **Contribution by company** — commits, AI%, meaningful LOC per company;
  affiliation via `companies.domains` + `companies.overrides` + the curated
  identity overrides.
- **Code review** (GitHub PR reviews, exact):
  - reviewer leaderboard — reviews given, approvals, **median review latency**
    (time from review-requested → approved; if review requests in your org are
    team-level, this is request→approval latency, not personal-assignment).
  - **time-to-merge** — median `createdAt → mergedAt`, per person / company / repo.
  - review coverage — % of PRs with ≥1 review, per repo.
- **AI tools** — commits carrying an AI-tool marker (`config.ai_tools.markers`):
  Claude Code, Devin, Copilot out of the box, plus any in-house assistant you add.
  Per-tool exact/heuristic badges (see *Precision badges* above).
- **Content provenance** (`config.studio_provenance`) — full-content `git grep` over
  the default-branch tree rather than commit messages, which makes it the strongest
  available signal: a spec frontmatter flag, traceability annotations, a generator
  stamp. Lines matching `blame_marker` are **blame-attributed** to their authors, so
  a marker becomes lines-per-person / per-company.
- **Framework usage** (`config.gears_usage`) — which repos DEPEND ON your shared
  framework, as opposed to the framework's own repos, matched by package or crate
  name. Provider repos are excluded so the framework does not count as its own user.
- **Generic trackers** (`config.fabric_trackers`) — the same idea, open-ended: add a
  marker entry and a tracker appears with no code change. Each marker runs in
  `content` (git grep) or `files` (path) mode, with `exclude_repos` for providers and
  meta-repos (a bulk-migration repo references everything and would match every
  tracker).
- **Platform usage by company & person** — marked commits and marked code lines
  rolled up per company and per person.
- **Activity by week** — org-wide commits/specs/PRs/issues per ISO week.
- **Traffic** — see below.

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
| `email_report.py` | sends `report.html` via SMTP (no-op until enabled) |
| `Dockerfile` / `docker-compose.yml` | containerized portal on `:8080` (secrets via `.env`, never hardcoded) |
| `.github/workflows/weekly-report.yml` | Mondays 06:00 UTC → collect, render, publish to Pages, email |

## Before you start

- **A GitHub token** with `read:org` + `repo`. The setup wizard takes it in the browser
  and stores it in the database; `GH_TOKEN` / `GITHUB_TOKEN` also work. Everything else
  that can be configured by environment is documented in [`.env.example`](.env.example)
  — copy it to `.env` when you need one, it is optional.
- **`git`** on PATH. Commits, surviving-LOC and content markers come from real clones,
  not the API, so the collector shells out to `git`.
- **Docker** for the compose path — this is the recommended one, and it builds the
  React bundle for you.
- To run it **from source** instead: **Python 3.9+** (CI and the image use 3.12) *and*
  **Node 20+**. The UI is a React app whose bundle is not committed, so it has to be
  built once with `cd frontend && npm install && npm run build`; the pages say so if you
  skip it. The collector, the API and the MCP server are pure Python — only the rendered
  pages need the bundle.
- Disk for the clones: the collector keeps full clones under `.repos/`.
- Nothing else. No database to provision — SQLite is created on first use, and so is
  every directory under `DATA_DIR`.

## Quick start (Docker Compose)

Recommended local mode is the Docker Compose portal:

```bash
docker compose up --build
```

Open <http://localhost:8080> — the setup wizard asks for the token and the org, and
stores both in the database. Nothing else to prepare.

If you would rather configure by file, copy the template and fill in what you need:

```bash
cp .env.example .env
```

`.env` is git-ignored and every setting in it is optional — the compose file treats it
as `required: false`, so a fresh clone starts without one. It is where the things that
are NOT part of the wizard live: `PORTAL_PASSWORD` for the built-in login, `MCP_TOKEN`,
`ALERT_WEBHOOK_URL` for refresh alerting, the Gemini keys for the metrics assistant, and
the OAuth settings. `GH_TOKEN` / `GITHUB_TOKEN` work there too if you prefer the
environment over the wizard.

> Compose reads `.env` when it **creates** a container, so after editing it run
> `docker compose up -d report` — a plain `restart` will not pick the change up.

The portal lets you refresh data, view the current report, edit identity
resolution, and export a timestamped HTML snapshot without downloading files
manually. The project directory is mounted into the container, so `.cache/`,
`.repos/`, `history/report.db`, `data.json`, `report.html`, and `exports/` stay
on your machine and are reused across runs.

## Run locally

One command — creates the venv, installs deps, collects, renders, opens the report:

```bash
./run.sh                 # collect + render + open report.html
./run.sh --no-open       # skip opening the browser
./run.sh --email         # also run the email step (honours config.yaml + SMTP_*)
```

Token: uses `$GH_TOKEN`/`$GITHUB_TOKEN` if set, else falls back to `gh auth token`
(needs `read:org` + `repo`). Same code path as the Action, just on your machine.

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
recovery path for a roster. Host/key/dir are overridable via `CT_HOST` / `CT_KEY` /
`CT_DIR`. (There were once `--identity` / `--pull-identity` flags that rsynced a
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
ssh -i ~/.ssh/ct_server user@your-server
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

## Test locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m py_compile collect.py render.py ghclient.py identity.py directory.py email_report.py reportctl.py server.py tests/test_rules.py
```

## Deploy (GitHub Actions only — no server)

1. Push this folder to a repo (suggest a private repo in the org, e.g.
   `your-org/insight`).
2. Repo **Settings → Pages → Source: GitHub Actions**.
3. Add secrets (**Settings → Secrets and variables → Actions**):
   - `INSIGHT_REPORT_TOKEN` — PAT with `read:org` + `repo` (the default
     `GITHUB_TOKEN` only sees its own repo, not the whole org).
   - Email (later): `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`.
4. The report publishes to the repo's GitHub Pages URL each Monday;
   trigger manually any time from the **Actions** tab → *Run workflow*.

## Enable email

1. In `config.yaml` set `email.enabled: true` and add `email.recipients`.
2. Add the four `SMTP_*` secrets.

Until then `email_report.py` is a safe no-op and only the web report is produced.

## MCP server (read-only data access)

`mcp_server.py` exposes the report's data to MCP clients (e.g. Claude) over
streamable HTTP — every tool is **read-only**. Docker Compose runs it as the `mcp`
service on `127.0.0.1:8082` (front it with the same reverse proxy as the portal).

Set `MCP_TOKEN` in `.env`; clients must then send `Authorization: Bearer <token>`.
Unset = unauthenticated (only safe on localhost / behind an authenticating proxy) —
the server warns loudly at startup. Connect a client to `https://<host>/mcp`.

Tools: `describe_schema`, `sql_query` (single SELECT/WITH, `PRAGMA query_only`),
`contribution(since, until, scope, member_only)`, `delivery(since, until, scope)`,
`person(login)`, `list_dimension(kind)`, `taxonomy(level, target)`. `scope` slices to
a repo subset, e.g. `element:Insight`, `org:your-old-org`, `repo:org/name`.

```bash
MCP_TOKEN=… python mcp_server.py         # local, serves /mcp on :8082
```

## Traffic — clones & page views

The **Traffic** panel shows GitHub traffic (last 14 days), with two distinct
signals badged by precision:

- **Page views / unique visitors** (`exact-human`) — people browsing the web UI.
  CI does not browse, so this is the cleaner human-usage signal. The panel also
  lists **popular paths** (what's viewed: PRs, issues, docs) and a **daily clone
  chart**.
- **Clones / unique cloners** (`heuristic`) — dominated by **CI/automation**:
  E2E and image-build workflows clone the repo per job, so each "unique cloner"
  is an ephemeral runner cloning ~14×. A daily spike means heavy CI that day,
  **not** more people. Read as volume/automation, never headcount. (An earlier
  "Use ÷ contrib" ratio was removed as misleading.)

Caveats common to all traffic:

- **Anonymous** — GitHub gives *how many*, never *who*. Forks & stars are the
  only per-person usage signals.
- **14-day window** — GitHub does not retain traffic longer; it's a snapshot,
  not windowed to `lookback_days`.
- **Needs push/admin** — `/traffic/*` returns `403` on repos the token can't
  push to. With the default token only insight/example-web-front resolve; grant the
  `INSIGHT_REPORT_TOKEN` org-wide push (or an org GitHub App with the traffic
  permission) to cover every repo. Repos without access are surfaced as "no
  traffic access yet" rather than silently dropped.

## Data sources

Collection uses two source paths deliberately:

- **Git history clones** for commits, lines changed, spec-document edits, commit
  types, AI-tool markers, and content markers. This counts corporate commit
  emails that GitHub does not link to a login. Repos are cloned once into
  `.repos/`; each run refreshes the stable local ref `refs/report/head`. Clones
  are **full history** (no `--shallow-since`) and **with blobs** (not
  `--filter=blob:none`): full history is needed for accurate `git blame`
  attribution of `@cpt` code markers, and blobs are needed for `git log
  --numstat` (LOC) and `git grep` over file content. An older shallow clone is
  auto-deepened (`--unshallow`) on the next run. Git transfers only new objects
  after the first clone; metrics are recomputed from the lookback window each run.
- **GitHub API** for org membership, repo inventory, verified commit authors,
  PRs, issues, forks, stars, and clone/view traffic.

### Clone cache behavior

`.repos/` is a persistent implementation cache, not report output. It is safe
to delete a repo directory there when you want to force a clean clone; the next
refresh will recreate it.

During refresh the log has two git phases per primary-org repo:

- `Clone+log: <repo>` — refreshes the local clone and parses git history for
  commits, LOC, contributors, and spec edits.
- `Verify authors: <repo>` — asks the GitHub commits API for verified
  email-to-login evidence for the same lookback window.

The collector also self-heals stale cache shapes:

- `discarded invalid clone cache: <repo>` means `.repos/<repo>` existed but was
  not a valid git checkout, for example a leftover symlink or partial failed
  clone.
- `discarded blobless clone cache: <repo>` means the old checkout used
  `remote.origin.partialclonefilter` and is being replaced with a clone that
  supports fast `git log --numstat`.
- `clone failed: <org>/<repo>: ...` and `fetch failed: <org>/<repo>: ...`
  include the last git error with the token redacted.

## Identity resolution

The report attributes git-history activity by commit-author email, but the
tables are grouped by GitHub login. That mapping is the most sensitive part of
the report: corporate emails, personal emails, renamed accounts, imported
history, and fork-based PR workflows can all make the same person appear as
multiple identities unless we resolve them deliberately.

The resolver builds `email -> GitHub login` from the evidence available in the
repos. It never asks contributors to change their Git config, and it does not
drop unresolved human emails silently.

### Inputs

- The **curated identity overrides** (`override` table, scope `person`, in
  `history/report.db`): reviewed canonical logins, display names, company
  affiliation, and known commit-email aliases. Edited at `/identity`. This is the
  preferred long-lived source of truth.
- `config.identity_overrides`: smaller manual override map for known
  `email-or-name -> login` cases.
- Git history from `.repos/`: commit author emails and commit author names
  observed in the lookback window.
- GitHub commits API: verified `email -> login` pairs where GitHub itself links
  a commit author to an account.
- GitHub PR API: authenticated PR authors and the commit emails inside their
  PRs, used for the PR-bridge layer.
- `config.bot_logins`: substring list used to exclude bot/service accounts from
  people metrics and from human-review suggestions.

### Resolution order

For every commit-author email seen in git history, the resolver applies these
layers in priority order:

1. **Reviewed directory / override**. The curated overrides' emails are merged on
   top of `config.identity_overrides`, so reviewed entries win. Use this for
   confirmed aliases, company corrections, renamed accounts, or any case where
   automatic evidence is insufficient.
2. **GitHub-verified author**. If the commits API returns a non-null GitHub
   `author` for a commit, GitHub has linked that commit email to a login. This
   is treated as ground truth unless a reviewed override exists.
3. **PR bridge**. For each PR author, the collector samples recent PR commits
   and maps the commit emails in those PRs back to the authenticated PR author.
   This catches common fork workflows where the commit email is not linked to
   GitHub, but the PR author is known.
4. **Name bridge**. If an unresolved email uses the same git author name as an
   already verified or overridden email, it inherits that login. Example: a
   personal email and a corporate email both commit as the same full name.
5. **Suggestion only**. If no layer resolves the email, the resolver computes a
   fuzzy suggestion from the email local-part and author names against known
   login/name tokens. Suggestions are written to `identity_suggestions.yaml` for
   human review; they are not treated as confirmed identity.

The `data.json` identity block summarizes how many emails were resolved by each
layer:

```json
{
  "identity": {
    "verified": 4,
    "pr_bridge": 0,
    "name_bridge": 0,
    "override": 58,
    "unresolved_human": 2
  }
}
```

`unresolved_human` should be read as a trust gap. The affected activity is not
silently assigned to a guessed person; review `identity_suggestions.yaml` or
`identity-editor.html`, confirm the correct mapping, and rerun collection.

### Manual review workflow

1. Run `python3 collect.py`. This writes `data.json` and may write
   `identity_suggestions.yaml`.
2. Run `python3 directory.py`. This writes `identity-editor.html` from the
   collected run plus the existing reviewed entries.
3. Open <http://localhost:8080/identity> from the local portal.
4. Review people in `Other`, duplicate suggestions, and any unresolved email
   aliases. Assign company, merge identities, and add/remove email aliases.
5. Click **Save**. The roster is POSTed as JSON and the local server writes it to
   the `override` table. If you are using the standalone HTML file instead of the
   portal, it downloads `people.json` for you to POST to `/api/people-yaml`.
6. Run `python3 collect.py` again so the reviewed directory overrides the
   automatic resolver.
7. Run `python3 render.py` to rebuild `report.html`.

Prefer curating confirmed long-lived mappings at `/identity`. Keep
`config.identity_overrides` for small exceptional mappings that are easier to
document next to the config.

There is deliberately **no YAML mirror** of the roster. A `people.yaml` was written
after every save as a "human-readable backup" and read back to seed an empty
override scope. Both roles were removed on 2026-07-28: when a roster actually had to
be restored, all 50 dated copies under `history/people/` turned out to be test-fixture
output, and the read path had already imported one of those fixtures into the prod
override table as curated data. Recovery came from a `report.db` snapshot, which is
what `deploy.sh` writes and what a restore should use.

### Trust rules

- A GitHub login is the canonical person key in the report.
- A commit email can belong to only one canonical login after resolution.
- A curated override wins over all automatic evidence.
- Bot/service accounts matched by `config.bot_logins` are excluded from people
  metrics.
- Fuzzy suggestions are review aids, not facts.
- Company attribution uses the curated overrides first, then
  `companies.overrides`, then email-domain mapping, then `companies.default`.

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

## Licence

Apache License 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
