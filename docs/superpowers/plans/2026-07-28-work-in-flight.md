# Work in flight — plan & rationale

**Problem as reported:** a PR can stay open for months, so the work in it is invisible in the
metrics until it merges. People doing long-lived work look idle.

**Decision: do NOT collect commits from PR branches.** Show work-in-flight as its own
explicitly-labelled dimension, built from PR data already in the DB, never summed into the
commit/LOC counters. Abandoned PRs get their own separate treatment (§5) — a maintainer-facing
signal in its own right, not a variant of in-flight. Written 2026-07-28 after measuring prod.

**Period scoping, decided:** in-flight is **not** period-scoped — it is a *now* quantity and does
not change with the period control. Abandoned PRs **are** period-scoped, because `closed_at` makes
"abandoned in this window" an honest question (verified: 0 of 269 closed-unmerged PRs are missing
`closed_at`). That asymmetry is intentional and must be visible in the UI, not inferred — the
in-flight panel needs an explicit "as of now" marker, since every neighbouring Flow panel does
follow the period and a silently unscoped one would read as a bug.

---

## 0. Why not just collect the branch commits

Because the work is not actually lost, and de-duplicating it is not solvable in general.

**It is not lost — it arrives back-dated.** Only default-branch commits are collected
(`identity.py:clone()` refreshes `REPORT_REF` to the remote default-branch tip; `log_ref()`
walks it), and the walk records the **author** date (`%aI`, `collect.py:995`). Squash-merging is
the minority here — commits titled `… (#N)` are 7% of the last 90 days, and per repo it ranges
from 0% (example-app 1607 commits, studio 572, example-core 528) to 24–30% (insight 1186,
example-legacy-web 375). With merge-commit and rebase strategies the branch commits *do* land on the
default branch at merge, carrying their original author dates.

**De-dup by sha only works for one of the three merge strategies:**

| strategy | what happens to branch shas | `commits` PK `(repo, sha)` |
|---|---|---|
| merge commit | preserved | de-dups correctly |
| rebase merge | **rewritten** | misses → double count |
| squash merge | **N commits → 1 new sha** | misses → double count |

`git patch-id` is stable across rebase and cherry-pick and would cover the second row, but squash
combines the diffs, so patch-id does not match either. No single key covers all three, and this
org uses at least two. Any "collect PR commits, then de-dup" scheme would therefore be wrong in a
way that varies by repository — the worst kind of wrong for a metric people are compared on.

## 1. The real defect this exposes: windows are not stable

Because commits enter the window by author date, a 157-day-old PR merging today injects commits
into windows five months back. "Last 30 days" is not reproducible: the number someone screenshots
today can differ tomorrow with no new work done.

This is worth fixing regardless of anything below, and is **not started**. Options, cheapest first:
1. Document it (a note on the period control) — honest, no code.
2. Record `first_seen` on commit rows at write time, so "was in the window when the window closed"
   is distinguishable from "arrived later". Enables a stable-by-default view and an
   as-of-then/as-of-now comparison.
3. Window PR-derived work by merge date instead of author date — changes existing numbers, so it
   needs its own decision.

## 2. What in-flight data already exists

`pull_request` already carries `state`, `created_at`, `closed_at`, `additions`, `deletions`,
`changed_files`, `is_draft`, `review_count` — **including for OPEN PRs**. No new collection is
needed for counts, ages or draft status.

Measured on prod 2026-07-28: 144 open non-bot PRs. Oldest are 237d, 194d, 194d, 182d.

## 3. Why volume-in-lines is deliberately NOT phase 1

The same 144 open PRs sum to +1,655,168 raw additions, and that total is meaningless:

- top 1 PR = **35%** of it, top 3 = **73%**, top 10 = **84%**
- the largest are fork/branch syncs, not authored work — `example-app#68 "Sync origin main into
  upstream main"` (+585,157 / 3,763 files), `example-app#67` (+541,080), `example-app#2 "Weekend Update"`
- `pull_request.additions` is GitHub's raw count with **no meaningful-LOC filter**; the commit path
  strips ~9% as vendored/generated (3,321,772 → 3,049,107 over 90 days) and this does not

Feeding that into per-person metrics would make whoever synced a fork the top contributor.
Filtering it properly needs per-PR file lists (API cost) or branch-side computation — so lines are
a later phase, gated on that work, not an opening move.

## 4. Phases

### Phase 1 — in-flight as counts and ages, in the Flow view — **DONE** (2026-07-28)
Flow is the right home: it is already about how work moves (`Flow health`, `Cycle-time`,
`By person`), not about delivered volume. Delivery and the commit/LOC counters stay untouched.

- A new `In flight` `.fcard` row next to `Flow health`, reusing the existing card idiom
  (`fc-n` / `fc-l` / `fc-s`): open PR count, median age, count over an age threshold, draft share.
- Age **bands** rather than a single mean, so a 237-day draft reads as stale instead of inflating
  a healthy-looking average.
- Per-person: an `In flight` column in Flow's existing `By person` table — owned open PRs and the
  age of the oldest. This is the answer to the original complaint: someone deep in a long PR shows
  visible in-flight work instead of looking idle.
- Drill-through to the PR list, consistent with the existing `data-drill` contract.

### Phase 2 — abandoned PRs — **DONE** (2026-07-28) — see §5 for the data and the shape

### Phase 3 — make staleness actionable — **DONE** (2026-07-28), with one signal dropped
Aging WIP is a flow problem, so surface it as one: shipped as a "Waiting on a first review"
list — open PRs with no review after 7 days, longest wait first, named rather than counted.

**`review_requested_at` turned out to be unusable.** The plan assumed it was "already stored";
it is in the schema, but `collect.py:1569` hardcodes it to `None`, so it is empty on all 2,244
rows. Separating "asked but ignored" from "never asked" is therefore not derivable, and reporting
it anyway would have meant a metric that always reads 100% — a misleading number rather than a
missing one. Left out, and said so on the panel. The same column silently breaks two existing
cycle-time segments (`ttfr`, `review_to_merge` both return h=None, n=0); that is a separate bug,
filed on its own.

### Phase 4 — in-flight volume — **DONE** (2026-07-28), reshaped rather than unblocked
The blocker in §3 was never removed — PR diffs still have no meaningful-LOC filter, because
the DB stores only `additions` / `changed_files` and no file paths. But re-reading §3, the thing
that made lines useless was the **sum** being dominated by outliers, not lines as such. A median
and a p90 are immune to exactly that, so the panel reports the size *shape* — median PR, p90 PR,
median files — with the five biggest named individually instead of averaged in. No sum is shown
anywhere, and the panel states that these are raw GitHub counts including vendored/generated
files, unlike the commit numbers.

Measured while building it, which is the argument for the reshape: median +1,079 · p90 +17,794 ·
largest single open PR +541,080. The median is a usable number; a total would have described that
one fork-sync PR.

Still open if a *true* per-PR meaningful-LOC figure is ever wanted: it needs per-PR file lists
(GitHub `pulls/{n}/files`, roughly one request per open PR) plus sync/merge-forward exclusion.
Not done, and not needed for the shape above.

## 5. Abandoned PRs — a maintainer metric in its own right

Closed-unmerged PRs are **not** in-flight work (counting them as WIP would overstate it), but they
are not noise either: for a maintainer they are where effort went that did not end in a merge. Note
"where effort went", not "waste" — §5.2 is why that wording matters. Measured on prod 2026-07-28,
all-time unless stated:

| | |
|---|---|
| closed-unmerged, non-bot | **269** (`closed_at` present on all of them) |
| abandon rate | **12%** all-time · 13% last 90d · 11% last 30d — stable enough to trend |
| reviews spent on PRs that never landed | **1,669** across 174 PRs |
| abandoned with **zero** reviews | **95** |
| still drafts at close | 49 of 269 |
| age at close | 93 <1d · 76 1–7d · 66 8–30d · 22 31–90d · 12 >90d |

**The load-bearing point: reviewed and unreviewed abandonment are different things and must not be
one number.** Which of them is a *problem* is settled in §5.2 — and the answer is not the obvious one.

- **Reviewed then abandoned** — 174 PRs, 1,669 reviews. Review happened and the work still did not
  land. Descriptive, not a fault (§5.2).
- **Abandoned with no review at all** — 95 PRs. Nobody ever looked. A different failure needing the
  opposite response. Averaging the two into one "abandoned" tile hides both.

Shape:
- Abandon rate as a Flow tile, period-scoped by `closed_at`, next to the existing flow signals
  (`sent back for changes`, `reopened`, `back to draft`) — it belongs to that family, not to
  Delivery, which is about what *did* land.
- **Lead with unreviewed-and-aging, not with review volume** — see §5.2. The headline is
  "PRs nobody looked at, and for how long", because that is the bucket with an owner and an action.
- Per repo, since the concentration is real: last 90d, example-core 42 abandoned / 514 reviews,
  insight 38 / 239 — those two dominate and deserve to be nameable.
- Age-at-close bands: most die inside a week (169 of 269), which is healthy churn — superseded or
  mistaken PRs. The 34 that lived past 30 days are the ones worth surfacing by name.
- Drafts-at-close reported separately: a closed draft is often "never intended to land".
- **Not in lines.** +764,737 additions across the 269, with the same fork-sync distortion as §3.

### 5.1 The reason is computable today — no new collection

`ClosedEvent` is already collected with its actor: `_TL_EVENT` maps it to `closed`, `_TL_PR_QUERY`
already requests `CLOSED_EVENT`, and `timeline_event.actor_login` is written. Coverage on prod is
complete — every one of the 269 abandoned PRs has a known closing actor. Comparing that actor to
`pull_request.author_login`, plus `review_count` and `is_draft` already stored, yields:

| reason | PRs | reviews spent | avg age | oldest |
|---|---|---|---|---|
| withdrawn after review | 103 | 940 | 8d | 148d |
| withdrawn, never reviewed | 58 | 0 | 3d | 100d |
| never finished (draft at close) | 49 | 334 | 18d | 116d |
| rejected after review | 40 | 395 | 26d | 95d |
| closed by another, unreviewed | 19 | 0 | **77d** | 203d |
| | **269** | | | |

Two findings worth keeping even if the panel changes shape:

- **The dominant story is authors withdrawing, not maintainers rejecting** — 103 withdrawals after
  review against 40 rejections, and 940 of the 1,669 reviews on abandoned PRs sit in that top row. "Reviewers
  keep reviewing work its own author then pulls" is a different conversation from "we reject a lot",
  and a single "abandoned" number tells neither.
- **`closed by another, unreviewed` is the worst experience and the smallest bucket** — 19 PRs,
  averaging 77 days and reaching 203 before somebody swept them up. Meanwhile
  `withdrawn, never reviewed` dies in 3 days, which is healthy self-correction (wrong branch,
  quickly superseded), not waste. Age is what separates them; count alone would not.

**Implementation requirements (both found by getting them wrong first):**
1. Join on the **last** `closed` event, not any of them. 20 PRs here were closed, reopened and
   closed again; a naive join reports 274 rows for 269 PRs and inflates every bucket.
2. `timeline_event`'s PK is `(repo, number, event, created_at)` — it excludes `item_type`, so issue
   #N and PR #N closed in the same second would collide and one would be silently dropped. Zero
   occurrences in the current data, but any query joining this table by number needs
   `item_type='pull_request'` in the predicate (as the queries above do), and the key itself is
   worth widening.

### 5.2 "Withdrawn after review" is a signal, not a problem — and that inverts the headline

Decided 2026-07-28: an author dropping their own PR after feedback is **feedback working**, not
waste. Useful to see, wrong to minimise.

That decision disqualifies the framing this section originally had. Walk the buckets with it applied:

| bucket | reviews | verdict |
|---|---|---|
| withdrawn after review | 940 | feedback worked — healthy |
| rejected after review | 395 | a maintainer decided — healthy |
| never finished (draft at close) | 334 | ambiguous, usually self-cancelled |
| withdrawn, never reviewed | 0 | dies in 3d — self-correction |
| **closed by another, unreviewed** | **0** | **77d avg, 203d worst — the actual problem** |

So **almost none of the 1,669 reviews is waste**, and "wasted review effort" must not be a tile: it
would be a number nobody should try to reduce. The real maintainer pain is the bucket that cost zero
reviews — 19 PRs left untouched for an average of 77 days before someone else swept them up. Small,
invisible in any count-based ranking, and the only bucket with a clear owner and a clear action.

Consequences for the build:
- No "waste" or "wasted" label anywhere in this panel, and no total that sums review effort across
  buckets as if it were loss.
- Rank and colour by **unreviewed age**, not by review count or by abandoned count.
- Reviewed-then-abandoned is shown as context — plain, uncoloured, no target attached.
- This is also why none of it feeds the Developer score (§6): the largest bucket is a behaviour we
  have just decided is *good*, and scoring it would push people the wrong way.

### 5.3 What a reason still cannot tell us

- **"Superseded by #M"** is not derivable from what is stored. It is cheap to add, though:
  `ClosedEvent.closer` (a Commit or PullRequest) is one extra field on a fragment that already
  fetches `ClosedEvent` — an edit to an existing query, not a new pass.
- **"The work landed anyway via another PR"** would need the abandoned branch's commits matched by
  `git patch-id` against the default branch. That means fetching `refs/pull/*/head` for closed PRs
  and is the expensive end of this; it is also the only way to distinguish "wasted" from "re-routed",
  so it may be worth it later. Not phase 1.

## 6. Explicitly out of scope

- **Neither in-flight nor abandoned work feeds the Developer score.** Scoring in-flight rewards
  opening PRs and not merging them, and the score already has a flow/friction pillar pulling the
  other way. Scoring abandonment punishes people for a maintainer's decision to close. Both are
  displayed signals only.
- Drafts are included in in-flight but labelled, because a long-lived draft is exactly the invisible
  work the request is about. Age bands keep an ancient draft from reading as healthy WIP.

## 7. Integration points

- Metrics must be registered, not just computed: `metrics_registry.metric()` + `register_for(fn, …)`
  as in `store.py`'s `_m(...)` block, or they will not appear in the catalogue or in dashboards.
- Any new report surface needs its React view plus the `?legacy=1` Jinja parity path, per the
  React migration's standing rule.
- Abandoned-PR numbers must honour the existing period/slice contract the other Flow panels use.
  The in-flight panel deliberately does not (§ header) and has to say so on the panel itself.

## 8. Verification

- The commit/LOC/score numbers must be **byte-identical** before and after Phase 1 — nothing about
  in-flight or abandoned work may change a delivered-work number. Diff a full report render either
  side.
- Cross-check the open-PR count against GitHub's own `is:open is:pr` count for one repo, and the
  abandoned count against `is:pr is:unmerged is:closed`.
- A person with a known long-lived PR shows non-zero in-flight and unchanged delivered metrics.
- WIP counts must not move when the period changes (that is the §header decision, so it is a test).
- WIP counts must not move when an old PR merges — only delivered numbers do (and see §1).
- Abandon rate must be computed against PRs *closed* in the window (abandoned ÷ abandoned+merged),
  not against PRs opened in it, or a window with a merge backlog will read as a quality collapse.

## 9. Open questions

*(the squash-strategy question moved to §10, answered by measurement)*

## 10. Resolved

- **Collect PR-branch commits?** No — §0. De-dup is unsolvable across the merge strategies in use.
- **Does the period scope in-flight?** No, in-flight is "as of now"; abandoned is windowed by
  `closed_at`. See the header.
- **Do abandoned PRs deserve their own metric?** Yes — §5, split by whether review happened at all (reviewed 174 / unreviewed 95).
- **Can the abandonment reason be computed?** Yes, today, with no new collection — §5.1. The closing
  actor is already stored; author-vs-other plus `review_count` and `is_draft` gives five buckets that
  account for all 269. "Superseded by #M" is the one worthwhile addition and is a one-field query
  edit (§5.3).
- **Do squash-heavy repos need different treatment?** Half the earlier assumption was wrong.
  Irrelevant to in-flight and abandoned, as assumed — an open PR is open and a closed-unmerged PR is
  abandoned regardless of how it would have merged. But NOT irrelevant to §1: measured on prod,
  **95% of squash commits carry an author date within an hour of the merge** (p50 = 0.0h over 246
  matched commits), so a squash merge dates the work at merge time and does **not** back-date.
  Merge and rebase repos do. That means §1's window instability is concentrated in the repos with
  little or no squashing — example-app (0% squash, 1607 commits), studio (0%), example-core (0%) — while
  insight (24%) and example-legacy-web (30%) are partly protected. §1 is therefore a per-repo problem of
  very uneven size, which also means `first_seen` would let it be *measured* instead of argued about.
- **Is "withdrawn after review" a problem?** No — a useful signal, not a fault (§5.2). This is the
  decision that removes "wasted review" from the design: with it applied, almost none of the 1,669
  reviews is loss, and the panel leads with unreviewed-and-aging instead.
