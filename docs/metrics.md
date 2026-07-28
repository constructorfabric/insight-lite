# Metrics & panels

What every number on the report means, and how much to trust it.

[← back to the README](../README.md)

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

## Precision badges

Every signal is tagged **exact** (green) or **heuristic** (amber) so a reader
knows what to trust. Examples: an authenticated bot co-author trailer = exact; a bare tool name
(commit mention)` = heuristic; page views = exact-human; clone counts = heuristic
(CI-inflated). Marker precision lives in `config.yaml` (`precision:` on each
marker).

