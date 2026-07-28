# Widget System — Phase 1 (Catalog) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development
> to execute this plan task-by-task (one implementer per task + gate + commit), same as the
> R-P* report migration this session. Steps use checkbox (`- [ ]`) syntax.
> Design spec: `docs/superpowers/specs/2026-07-23-unified-widget-system-design.md`.
> Branch `feat/react-migration`.

**Goal:** Extract the report views' duplicated/bespoke UI into a single reusable widget
catalog under `frontend/src/widgets/`, and adopt it across every report view — deleting
the in-page copies — with each step pixel-diff clean under the existing gate. This is the
"maximum components" deliverable and the foundation the later dashboard-on-React phase
composes. Behaviour and pixels are unchanged; this is a pure componentization refactor.

**Architecture:** New components live in `frontend/src/widgets/`. The EXISTING
`frontend/src/components/` (DataTable, KpiTile, VegaChart, SegBar, FilterBar) stay put
(moving them is optional churn with CSS-import diff-risk — see spec); `widgets/` may
re-export them via an index so pages have one import path. Each widget is data-agnostic
(resolved data as props) and emits the exact same DOM/classes the in-page version does
today, so the pixel gate sees no diff.

**Tech stack:** React 19 + TypeScript + Vite (under `frontend/`); Playwright pixel-diff
harness (`frontend/visual/`); Python stdlib server for serving (`reportctl.py serve`).

**Gate protocol (every task):** local server on **port 8090** with `REPORT_DB=history/
report.db`; `cd frontend && npm run build`; capture candidate for the affected React
routes (env `OVERVIEW_ROUTE=/overview … PERSON_ROUTE=/person …`) vs the existing
`visual/baseline` (monolith via `/report/legacy`); `node visual/diff.mjs` must pass
(≤0.1%, chart states ≤1.5%, `elements-slice` ≤0.2% as documented). The monolith is
untouched, so baselines stay valid. `npx tsc --noEmit` clean; `python -m pytest -q` green.

---

## Task 0: `widgets/` scaffold + dedup `GhLink`

**Files:**
- Create: `frontend/src/widgets/GhLink.tsx`, `frontend/src/widgets/index.ts`
- Modify: `frontend/src/pages/{Flow,People,Traffic}.tsx` (remove local `GhLink`, import from widgets)

`GhLink` is copy-pasted identically in Flow.tsx:75, People.tsx:61, Traffic.tsx:56.

- [ ] **Step 1 — create the widget** (port the current identical impl verbatim):

```tsx
// frontend/src/widgets/GhLink.tsx
// The person link used across report tables/lists — an in-app link to the /person
// view (href="#person" + data-person, picked up by the person route/drill). Ported
// verbatim from the three identical in-page copies (Flow/People/Traffic).
export default function GhLink({ login }: { login: string }) {
  return (
    <a className="gh" href="#person" data-person={login} title={`Open ${login}'s Person page`}>
      {login}
    </a>
  );
}
```

- [ ] **Step 2 — index re-export** so pages import from one place:

```ts
// frontend/src/widgets/index.ts
export { default as GhLink } from "./GhLink";
// (later tasks append: BarRow, BarList, SplitBar, Legend, MiniStats, StatRow, HeatStrip,
//  Chips, MarkerTable, GroupedTable, FlowPipe, ScoreGauge, ScoreChain, ScoreBoard, Scorecard)
// Optionally also re-export the existing components/ ones for a single import path:
// export { default as DataTable } from "../components/DataTable";  // etc.
```

- [ ] **Step 3 — adopt.** In Flow.tsx, People.tsx, Traffic.tsx: delete the local
  `function GhLink(...)` and add `import { GhLink } from "../widgets";`. Confirm the
  rendered `<a class="gh" …>` is byte-identical (People's version differs only if it
  had extra props — check each; they are identical per the inventory).
- [ ] **Step 4 — verify:** `npx tsc --noEmit`; `npm run build`; gate the affected routes
  (people, person — Person also uses GhLink in the picker/board; flow, traffic). Must be 0-diff.
- [ ] **Step 5 — commit:** `git commit -m "widgets: extract shared GhLink (dedup 3 copies)"`

---

## Task 1: `BarRow` + `BarList`

The horizontal "label · bar · value" row (`.row > .nm + .bb>.bar>i + .vv`), optionally
drillable, with an optional "+N more" collapsible tail (`.more-row` + `.more-tail`).

**Current sites (port from these, keep DOM identical):**
- People `CategoryCard` rows (People.tsx:73 — the per-category rows incl. the `.more-row`
  tail and `.more-tail`).
- Person "Top repositories" / "By Element" / "Work type" rows (Person.tsx, inside
  `PersonDashboard` "Where the work goes").
- Traffic `ContribRow` (Traffic.tsx:69) + the contributor "more" tail.
- Overview bar-rows (Overview.tsx — the `.bb`/`.nm`/`.vv` rows the review flagged).

**Files:** Create `frontend/src/widgets/BarRow.tsx` (exports `BarRow` and `BarList`);
modify the four pages.

- [ ] **Step 1 — design the props** to cover every current variant without changing DOM:

```tsx
export type BarRowProps = {
  label: React.ReactNode;          // .nm content (often a GhLink or text)
  tip?: string;                    // data-tip on .nm (email, repo, …)
  pct: number;                     // bar width %
  color?: string;                  // bar fill (default var(--acc))
  value: React.ReactNode;          // .vv content (already-formatted "62% · 7,426")
  drill?: Record<string, string>;  // data-drill/... on the row (optional)
};
// BarList wraps rows + the "+N more" tail (data-more/data-less + hidden .more-tail),
// matching People CategoryCard / Traffic contributors exactly.
export type BarListProps = {
  rows: BarRowProps[];
  cap?: number;                    // visible before the tail (e.g. 8 for categories)
  tail?: { pct: number; value: React.ReactNode; moreLabel: string; lessLabel: string };
};
```

- [ ] **Step 2 — implement** `BarRow`/`BarList` reproducing the exact markup: `.row`
  (+ optional `data-drill`/`data-*`), `.nm` (+ `data-tip`), `.bb > .bar > i[style=width;background]`,
  `.vv`. For the tail: the `.row.more-row[data-more][data-less]` + `.more-tail[hidden]`
  slice, byte-identical to People.tsx:103-114 / Traffic contributors.
- [ ] **Step 3 — adopt** in People `CategoryCard`, Person's three bar-lists, Traffic
  `ContribRow`/list, Overview bar-rows. Delete the inline row markup. Keep the
  surrounding card/heading in the page.
- [ ] **Step 4 — verify + gate** the affected routes: overview(+30d+slice), people(+slice),
  person(+selected), traffic(+30d+slice). 0-diff (these are non-chart states → strict 0.1%).
- [ ] **Step 5 — commit** `git commit -m "widgets: BarRow/BarList (dedup category/repo/contributor rows)"`

---

## Task 2: `SplitBar` + `Legend`

The proportion bar (`.split2` / `.cmix-bar` — segments summing to 100%) plus its legend
(`.leg2` — swatch + label + bold % + count).

**Current sites:** Person `Split2Card` (Person.tsx:282), Overview `WorkType` (Overview.tsx:218
— `.cmix-bar`/`wtbar` + `.wtlist`), AiTools `AiUsagePanel` split (AiTools.tsx:85), Repositories
`TypeBar` (Repositories.tsx:88), Delivery `Mix` if it uses a split bar.

**Files:** Create `frontend/src/widgets/SplitBar.tsx` (`SplitBar` + `Legend`); modify the pages.

- [ ] **Step 1 — props:**

```tsx
export type Segment = { pct: number; color: string; label: string; value?: React.ReactNode;
                        tip?: string; drill?: Record<string, string> };
export type SplitBarProps = { segments: Segment[]; className?: string; /* "split2" | "cmix-bar wtbar" */ };
export type LegendProps = { segments: Segment[]; className?: string; /* "leg2" | "wtlist" */
                            show?: ("pct" | "value")[] };
```

- [ ] **Step 2 — implement** both to emit each current variant's exact classes/markup.
  NOTE the variants differ (`.split2`+`.leg2` vs `.cmix-bar`+`.wtlist` vs Repositories
  `typebar`): parameterise via `className`/`show` so each caller reproduces its own DOM
  byte-for-byte. Verify the swatch (`.sw`), bold `%`, and `·count` ordering per site.
- [ ] **Step 3 — adopt** in the five sites; delete inline split/legend markup.
- [ ] **Step 4 — verify + gate:** overview, people, person, repositories, delivery,
  ai-tools (all + 30d + slice as applicable). Overview/Delivery/etc. are non-chart → 0.1%.
- [ ] **Step 5 — commit** `git commit -m "widgets: SplitBar + Legend (dedup proportion bars)"`

---

## Task 3: `MiniStats`, `StatRow`, `HeatStrip`, `Chips`

Small shared primitives.

**Current sites:**
- MiniStats (`.mini > .m > .mv/.ml`): AiTools `MiniStat` (AiTools.tsx:257), People
  `ReviewsSection` mini (People.tsx), Traffic mini, Repositories mini.
- StatRow (`.statrow > .sk/.sv`): Person `StatRow` (Person.tsx:258).
- HeatStrip (weekly commit intensity `.heat > .hc`): Person `HeatStrip` (Person.tsx:267).
- Chips (`.chips > span`): Traffic external-contributors + non-contributors chips;
  Repositories "Unclassified" chips.

**Files:** Create `frontend/src/widgets/{MiniStats,StatRow,HeatStrip,Chips}.tsx`; modify pages.

- [ ] **Step 1 — implement** each, porting the exact markup from the cited in-page copies
  (MiniStats must keep the optional `data-drill` on a `.m`; Chips keeps optional `data-tip`).
- [ ] **Step 2 — adopt** across the sites; delete local copies (AiTools `MiniStat`,
  Person `StatRow`/`HeatStrip`).
- [ ] **Step 3 — verify + gate:** ai-tools, people, traffic, repositories, person. 0-diff.
- [ ] **Step 4 — commit** `git commit -m "widgets: MiniStats/StatRow/HeatStrip/Chips primitives"`

---

## Task 4: `MarkerTable`

Promote AiTools' local `MarkerTable` (AiTools.tsx:171) — the provenance/gears/tracker
table (Repo + per-marker "files / lines" cells, `prec` header badges, single-tbody
last-border rule, `data-sort` per cell) — to `frontend/src/widgets/MarkerTable.tsx`.

**Files:** Create `frontend/src/widgets/MarkerTable.tsx`; modify AiTools.tsx (import it,
delete the local one). Its data types (`MarkerTableData`, badges) move with it or into a
shared types module.

- [ ] **Step 1 — move** the component verbatim (it's already self-contained), keeping
  `data-sort={c.files}` and the badge/tbody structure exactly.
- [ ] **Step 2 — adopt** in AiTools (used for studio/gears/tracker tables).
- [ ] **Step 3 — verify + gate:** ai-tools (+30d+slice). 0-diff.
- [ ] **Step 4 — commit** `git commit -m "widgets: promote MarkerTable to the catalog"`

---

## Task 5: `GroupedTable`

The multi-header grouped table used by Person weekly (Person.tsx:304 `WeeklyTable` —
per-repo commit/lines column groups + tfoot totals) and Flow dwell/by-person
(Flow.tsx:112 `DwellPanel`, and the Flow.tsx:341 grouped table). These share the
"group band row + column-header row + grouped body + optional tfoot" shape.

**Files:** Create `frontend/src/widgets/GroupedTable.tsx`; modify Person.tsx, Flow.tsx.

- [ ] **Step 1 — assess** whether one `GroupedTable` cleanly covers both, or whether
  DataTable's existing `groups` prop can absorb them. If the two are too structurally
  different (Person weekly has 2-cell-per-repo column groups + a tfoot; Flow's are
  simpler), implement `GroupedTable` to cover the common case and leave the genuinely
  bespoke one as a page component — DO NOT force a bad abstraction (record the decision).
- [ ] **Step 2 — implement + adopt** wherever it fits; keep `data-drill`/`data-sort` and
  the tfoot markup exact.
- [ ] **Step 3 — verify + gate:** person(+selected), flow(+30d+slice). Flow has a Vega
  chart → its states use the 1.5% chart threshold; person is non-chart → 0.1%.
- [ ] **Step 4 — commit** `git commit -m "widgets: GroupedTable (Person weekly / Flow dwell)"`

---

## Task 6: `FlowPipe`

The delivery/flow pipeline visual (`Delivery.tsx:64 FlowPipe`). Promote to
`frontend/src/widgets/FlowPipe.tsx`.

**Files:** Create `frontend/src/widgets/FlowPipe.tsx`; modify Delivery.tsx (and Flow.tsx if
it renders the same pipe).

- [ ] **Step 1 — move** verbatim (hand-rolled pipe markup; keep any `data-drill`).
- [ ] **Step 2 — adopt** in Delivery (and Flow if shared).
- [ ] **Step 3 — verify + gate:** delivery(+30d+slice), flow if touched.
- [ ] **Step 4 — commit** `git commit -m "widgets: promote FlowPipe to the catalog"`

---

## Task 7: dev-score widgets (TWO components, per review)

Person and Overview do NOT share one dev-score widget. Split:
- `frontend/src/widgets/score/PersonScore.tsx` — Person's `ScoreGauge` (SVG gauge) +
  `ScoreChain` (Person.tsx:398) + `WhyRankAbove`/`VsSelfLine`/`ScoreBoard`
  (Person.tsx:439/454/489/505/518). These are Person-specific but internally cohesive.
- `frontend/src/widgets/score/Scorecard.tsx` — Overview's `Score` (Overview.tsx:271) —
  the team scorecard TABLE (top devs + by-company). Different widget.

**Files:** Create the two (a `score/` subdir); modify Person.tsx + Overview.tsx.

- [ ] **Step 1 — extract Person score** set verbatim (gauge SVG stroke-dasharray, chain
  table, board `<details>` rows). Reuse `SegBar` where the board make-up bar uses it.
- [ ] **Step 2 — extract Overview scorecard** table verbatim (reuses `DataTable`/`SegBar`
  as it does today).
- [ ] **Step 3 — adopt** in both pages; delete the local copies.
- [ ] **Step 4 — verify + gate:** person(+selected) — the score `<details>` is collapsed
  in the baseline, so mostly structural; overview(+30d+slice). 0-diff.
- [ ] **Step 5 — commit** `git commit -m "widgets: dev-score (PersonScore + Overview Scorecard)"`

---

## Task 8: catalog index + report changelog + final gate

- [ ] **Step 1 — finalize `widgets/index.ts`** exporting every new widget (and optionally
  re-exporting the `components/` primitives) so there is one documented import surface —
  the seed of the dashboard widget registry (Phase 2).
- [ ] **Step 2 — sweep** the pages for any remaining inline duplication the tasks missed
  (grep for `className="row"`, `.mini`, `.split2`, `<a className="gh"`). Fold stragglers in.
- [ ] **Step 3 — changelog** entry (changelog.py) — internal/no-user-visible-change note
  is optional since pixels are identical; add a short "under the hood: shared UI
  components" line only if desired.
- [ ] **Step 4 — FULL gate** (all report routes, baseline unchanged) + `pytest -q` +
  `tsc --noEmit` + `npm run build` + `docker build` sanity. All green.
- [ ] **Step 5 — commit** `git commit -m "widgets: catalog index + final Phase-1 sweep"`

---

## Notes / guardrails
- Pixel-parity is non-negotiable per task — 0-diff on non-chart states, ≤1.5% on the
  Vega states (trend/flow), ≤0.2% on `elements-slice` (documented). Investigate any
  diff PNG before proceeding; a component extraction that changes pixels is not done.
- Do NOT physically move the existing `components/` files unless a task explicitly opts
  in (churn + CSS-import diff-risk — see spec). Default: add `widgets/`, re-export.
- Keep `data-drill`/`data-sort`/`data-tip` attributes intact when porting — the shared
  drill (`shell.DRILL_JS`) and sort (`shell.SORT_JS`) listeners depend on them.
- After Phase 1 lands: write the Phase 2–3 plan (dashboards-on-React) — its prerequisites
  (seed multi-viz dashboards + gate-as-owner + resolved-data JSON endpoints + E1 editor
  preview-island) are captured in the spec; do those first.
