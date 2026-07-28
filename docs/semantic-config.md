# Semantic config schema

Status: **design draft** (2026-07-11). Defines the configurable "semantic layer"
that turns raw GitHub facts into meaningful, org-agnostic metrics. Nothing here is
Constructor-specific — every mapping is user-supplied, with discovery-suggested
defaults.

## 1. The three layers

```
   RAW facts            SEMANTIC config            METRICS (derived)
   (what GitHub says)   (what it MEANS here)       (what we show)
   ──────────────────   ────────────────────       ──────────────────
   issue.labels[]   ┐                          ┌─ work-type breakdown
   issue.state      ├─►  categories ──────────►┤   defect vs feature rate
   pr.additions     │    stages     ──────────►┤   cycle-time by stage
   ci_run.conclusion├─►  ci.roles   ──────────►┤   CI pass-rate
   status_raw       │    effort     ──────────►┤   throughput weighted
   milestone        ┘    sprints    ──────────►└─ per-sprint delivery
```

**Rule:** collectors write RAW only. All interpretation is config. Changing the
config re-derives via `reconfig.apply()` — no re-collect, no GitHub calls. This is
the existing override→reconfig pattern (today it already re-derives repo class /
elements / label categories) extended to every semantic axis.

## 2. Where it lives

- A new `semantic:` block in `config.yaml` (backup) + the `override` table (source
  of truth), same DB-first model as the rest of config.
- New override scope `semantic` added to `CONFIG_SCOPES`.
- Edited in the Config editor; discovery pre-populates it (§5).
- Derived columns (category, stage, ci-role, effort weight) are recomputed by
  `reconfig`; raw columns are only touched by `collect`.

## 2b. Scoping: the same schema at every level

The `semantic:` block is **not global-only**. The identical shape can be set at
several scopes; the effective config for any entity is the deep-merge along a
most-specific-wins chain:

```
global  <  organization  <  element  <  repository  <  project
(base)     (org-wide)       (product)   (one repo)      (one board)
```

- More specific overrides less specific. A scope only carries the axes that make
  sense there; unset axes are inherited.
- Merge is **per key**: scalars/maps deep-merge; lists **replace** by default, or
  **append** with a `+` prefix (`+labels: [...]`).

Natural homes (defaults live at `global`; this is just where overrides cluster):

| Axis | Typically scoped at | Why |
|---|---|---|
| `categories.*.issue_types` | organization | Issue Types are org-defined |
| `categories.*.labels` | repository / element | labels are per-repo |
| `stages` | **project** | each board has its own status set |
| `ci` | repository | workflows are per-repo |
| `effort`, `sprints`, `profile` | element / repository | team convention |

Note `element` and `project` are cross-cutting, not a strict tree: a repo maps to
one element, but a project can span repos. So `stages`/iteration-`sprints` resolve
against the **project** an item belongs to, while `categories`/`ci`/`effort` resolve
against the item's **repo → element → org** chain. Precedence is uniform
(more-specific wins); each axis simply ignores scopes it has no meaning at.

### Storage
One row per (scope, target, axis) in the existing `override` table:
`scope = "semantic"`, `key = "<level>:<target>:<axis>"`, `value = <json patch>` —
e.g. `global::categories`, `org:your-old-org:categories`, `element:Insight:stages`,
`repo:your-org/example-core:ci`, `project:your-old-org/12:stages`.
`reconfig` reads them all and resolves per entity. No new tables, no new machinery —
just more keys in the layer we already have.

### Keeping the configurator sane (the real worry)
A full per-scope matrix WOULD be a nightmare. It won't be one, by design:

1. **Global covers ~everything.** Most users only ever edit the global block.
   Overrides are the rare exception, not the default path.
2. **Overrides are sparse and *suggested*.** Discovery flags only where a scope
   actually diverges — "3 repos use labels your global config doesn't cover" — and
   offers a one-click override. You don't hunt for them.
3. **You never simulate the cascade in your head.** An **Effective-config inspector**
   ("show what applies to `example-core`") renders the merged result with
   provenance — which scope set each rule — like a browser's computed-styles panel.
   You reason about the *result*, not the layers.

UI shape: one **Global** screen (the base everyone edits) + one **Overrides** list
(scope → target → axis → a small patch) + one **Inspector** (pick an entity → its
effective config, read-only). Three simple surfaces, not a matrix.

## 2c. Storage representation: item → bucket maps

The categories/stages/ci mappings are stored as **item → bucket maps**, NOT
bucket → item lists. This is what makes per-scope overrides compose: because maps
deep-merge per key, a narrow scope can override a *single* item, so the same label
resolves to a different category per element/repo/project.

```yaml
# global::categories
categories:
  labels:  { bug: bug, spec: docs, "priority:P0": chore }   # label → category
  types:   { Bug: bug, Feature: story, Task: chore }         # native type → category
  prefer_source: [issue_type, label, title]                  # settings live at global
  unmatched: uncategorized
# element:Gears::categories   (a delta — overrides ONE label)
categories:
  labels:  { spec: test }        # in Gears, `spec` is test; elsewhere still docs
# global::stages
stages: { statuses: { Backlog: backlog, "In Review": review, Done: done }, ... }
# global::ci
ci: { roles: { CI: gate, "e2e-nightly": nightly }, count_events: [pull_request, push], ... }
```

Resolution: for each issue/PR, resolve the taxonomy against its `repo → element →
org` chain (and, for statuses, the item's `project`), then look up the item in the
merged map. The bucket → list form in §3 below is the human-readable view; storage is
the flat map.

## 3. The schema

The shape below is the human-readable view of the same mapping (bucket → items).
Storage is the flat item → bucket map from §2c; a patch at any scope sets only the
items that differ.

```yaml
semantic:

  # ── 3.1 Work-item taxonomy ────────────────────────────────────────────────
  # Categorize issues AND PRs from THREE independent GitHub sources, matched as a
  # UNION (any source hit → category applies), because they are used inconsistently
  # and complementarily:
  #   * native Issue Types  — org-level field (Task/Bug/Feature). Issues only.
  #   * labels              — free-form, on issues AND PRs.
  #   * title convention     — regex on the title.
  # Observed: some issues carry only a native type, some only a label, some neither.
  # `bug` and `story` are seeded defaults, not privileged — the set is arbitrary.
  categories:
    order: [bug, story, chore, docs, question]   # precedence + funnel order
    prefer_source: [issue_type, label, title]    # tie-break when sources disagree;
                                                 # null → tie-break by `order` only
    defs:
      bug:
        match:
          issue_types: [Bug]                     # native type (case-insensitive)
          labels: [bug, defect, regression]      # glob ok ("*bug*")
          title:  ["^fix:", "^bugfix"]           # optional regex on title
        role: defect                             # defect|feature|maintenance|support|other
      story:
        match:
          issue_types: [Feature]
          labels: [story, "user story", enhancement, epic, feature]
        role: feature
      chore:
        match:
          issue_types: [Task]
          labels: [chore, dependencies, ci, build, release-plz]
        role: maintenance
      docs:
        match: { labels: [documentation, docs] }
        role: maintenance
      question:
        match: { labels: [question, support] }
        role: support
    # An item may match several categories across sources. We record BOTH:
    #   primary = one winner (by `prefer_source`, then `categories.order`)
    #   all     = every category that matched any source (overlap analysis)
    # Native type vs label conflict (e.g. type=Task + label:bug) is resolved by
    # `prefer_source`. Items matching nothing → "uncategorized" (kept, never dropped).
    unmatched: uncategorized

  # ── 3.2 Flow stages ───────────────────────────────────────────────────────
  # Normalize heterogeneous Projects v2 status values into canonical stages so
  # different projects become comparable. OPT-IN: with no `map`, we show status
  # values as-is per project and skip cross-project stage metrics.
  stages:
    order: [backlog, ready, spec, in_progress, review, done]  # the funnel
    map:
      backlog:     [Backlog, Todo, "Not started"]
      ready:       [Ready]
      spec:        [Specification, Spec]
      in_progress: ["In Dev", "In progress", "In Progress", Doing]
      review:      ["In Review", "In review", "Code Review"]
      done:        [Done, Closed, Shipped]
    terminal: [done]          # entering a terminal stage stops the cycle-time clock
    unmatched: other          # unknown status value → stage "other" (kept, flagged)

  # ── 3.3 CI workflows ──────────────────────────────────────────────────────
  # Classify GitHub Actions workflows. Without this, pass-rate is meaningless
  # (scheduled/skipped runs dominate — observed 31k runs, mostly schedule/skipped).
  ci:
    roles:                                   # by workflow name; exact or glob
      gate:    [CI, Code, "Clippy*", Lint, Test]   # counts toward pass-rate
      nightly: ["*nightly*", "ClusterFuzzLite*", "*fuzz*"]
      release: ["release*", release-plz, Publish]
      ignore:  ["Cache Cleanup", pages-build-deployment, "Dependency Graph"]
    count_events:        [pull_request, push]   # which run events are meaningful
    default_branch_only: true                   # push runs: only the default branch
    success_conclusions: [success]              # what "green" means
    failure_conclusions: [failure, timed_out, startup_failure]
    ignore_conclusions:  [skipped, cancelled, neutral, action_required]

  # ── 3.4 Effort (optional) ─────────────────────────────────────────────────
  # Weight throughput by declared complexity, when the team labels it.
  effort:
    label_weights: { high-complexity: 3, med-complexity: 2, low-complexity: 1 }
    default_weight: 1

  # ── 3.5 Sprints / releases (optional) ─────────────────────────────────────
  sprints:
    source: milestone                 # milestone | project_iteration
    milestone_pattern: '^\d{2}\.\d{2}$'   # e.g. "26.07" → sprint id; null = off

  # ── 3.6 Flow profile ──────────────────────────────────────────────────────
  # High-level shape of how this org works. Auto-detected, overridable.
  profile:
    primary_unit:   pull_request      # pull_request | issue (which is "a unit of work")
    track_projects: true              # ingest Projects v2 status snapshots?
    track_ci:       true              # ingest Actions runs?
    track_deploys:  false             # GitHub Deployments (often unused; see note)
```

### Matching semantics (precise)

- **String match**: case-insensitive. A bare token is exact; a token with `*` is a
  glob (`*nightly*`, `release*`). Applied uniformly to labels, status values, and
  workflow names.
- **Category precedence**: `categories.order` gives both the funnel order and the
  tie-break — `primary` is the first category in `order` whose `match` succeeds.
- **Nothing is dropped**: unmatched labels/statuses fall into `uncategorized` /
  `other` and stay visible, so gaps are seen, not hidden.

## 4. Storage impact (raw vs derived)

| Entity | RAW columns (collect) | DERIVED (reconfig, from `semantic`) |
|---|---|---|
| `issue` | `type` (native), `labels[]`, `state`, `state_reason`, `closed_at`, `closed_by`, `assignees[]`, `milestone` | `category_primary`, `categories[]`, `stage` |
| `pull_request` | `state`, `closed_at`, `additions`, `deletions`, `changed_files`, `review_count`, `comment_count`, `author_association`, `closes_issues[]`, `is_revert` | `category_primary`, `categories[]`, `effort_weight` |
| `ci_run` *(new)* | `repo`, `workflow`, `event`, `branch`, `conclusion`, `created_at`, `duration_s`, `head_sha`, `actor` | `role`, `is_success`/`is_failure`/`counted` |
| `work_item_status` *(new)* | per-run snapshot: `date`, `project`, `item_type`, `repo`, `number`, `status_raw` | `stage` |

Notes:
- Today's `is_bug` / `is_user_story` columns become **derived** (`category_primary`
  / `categories`), not source. Migration keeps them as a compatibility view until
  panels move over.
- **Native Issue Types need GraphQL.** The REST issues/search path we use today does
  not return `issueType`; only the GraphQL `issue.issueType{name}` field does. So
  issue collection moves to GraphQL (or gains a GraphQL enrichment pass), and reading
  the type requires the token to have issue-type read permission — a fine-grained /
  org PAT scope. Orgs without Issue Types simply yield `type = null` (label/title
  matching still works).
- **Projects v2 has no field-change history in the API.** We snapshot each item's
  current status every run, so stage transitions & cycle-time build **forward** from
  first snapshot — no backfill. `work_item_status` is that snapshot log.
- **Deployments/Environments are empty for these orgs**, so DORA deploy metrics are
  out of scope from GitHub; `profile.track_deploys` stays false until a real CD
  source is wired in.

## 5. Discovery (so it's "connect org → go", not a blank form)

A Config-editor action **"Scan & suggest"** (on demand, not per-collect) reads what
actually exists and proposes a `semantic:` block for the user to confirm/edit:

- **issue types** — enumerate each org's native Issue Types (e.g. Task/Bug/Feature)
  and seed a category per type (Bug→bug, Feature→story, Task→chore); the user
  re-maps freely.
- **labels** — union of every repo's labels → bucket by keyword: `*bug*|defect` →
  bug, `story|feature|epic|enhancement` → story, `chore|dep|ci|build` → chore,
  `doc` → docs, `question|support` → question; leftovers listed for manual
  assignment. Types and labels are merged into the same category defs (union match).
- **statuses** — union of all Projects v2 single-select "Status"/"State" options →
  nearest canonical stage by keyword (`todo|backlog`→backlog, `spec`→spec,
  `dev|progress|doing`→in_progress, `review`→review, `done|closed|shipped`→done).
- **workflows** — union of Actions workflow names → `gate` if `ci|build|test|lint`,
  `nightly` if `nightly|fuzz|schedule`, `release` if `release|publish|deploy`,
  `ignore` for `cache|pages|graph`.
- **profile** — `primary_unit` guessed from the issue:PR ratio; `track_projects` /
  `track_ci` from whether any project / workflow was found.

The suggestion is a starting point the user always owns — same philosophy as the
identity editor's merge suggestions.

## 6. Open decisions

1. **Stage normalization = opt-in** (default: show raw per-project; canonical stages
   only when `stages.map` is set). Proposed: yes — less violence to the data.
2. **Discovery = on-demand** button in Config, not every collect. Proposed: yes —
   collect stays fast; re-scan when the taxonomy changes.
3. **Category multi-membership**: keep both `primary` (single-value panels) and
   `all` (overlap). Proposed: yes.
4. **Source priority on conflict**: `prefer_source: [issue_type, label, title]` —
   native type beats an ad-hoc label. Proposed: yes.
5. **Scope precedence**: `global < organization < element < repository < project`
   (more specific wins), lists replace-by-default with `+key` to append. Proposed:
   yes. The configurator stays a Global screen + sparse Overrides list + read-only
   Effective-config inspector — never a matrix.

## 7. Build order (after this schema is agreed)

1. `semantic` config scope + editor section + discovery. *(the contract first)*
2. Collectors emit RAW (issue lifecycle, PR enrichment) → `reconfig` derives.
3. `ci_run` collector + CI metrics.
4. `work_item_status` snapshotter (Projects v2) + forward cycle-time.
5. Collaboration matrix (needs no new raw — from existing review data).
