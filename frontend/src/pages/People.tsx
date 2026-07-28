// /people — the fifth migrated report view (see
// docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P5).
// Reproduces the monolith's "people" mode-sections class-for-class against
// templates/report.j2 (the `<div class="mode-section" data-modes="people
// all">` blocks at lines ~996-1063: %-contribution-by-category, code review,
// per-person breakdown) + panel_categories/panel_reviews
// (templates/panels/02_overview.j2) and panel_people
// (templates/panels/05_people.j2) — driven by GET /api/report/people
// (render.people_json) instead of server-rendered HTML + the /api/period
// fragment swap. People is the one table-heavy view with NO Vega charts.
// SSR-safe: no window/document access outside hooks/effects.
import { useMemo, useState } from "react";
import FilterBar from "../components/FilterBar";
import DataTable, { type Column, type ColumnGroup } from "../components/DataTable";
import { GhLink, BarList, MiniStats, type BarRowProps } from "../widgets";
import { useReportData } from "../hooks/useReportData";
import { fmtLoc, fmtNum, fmtPct } from "../lib/format";
import Loading from "../components/Loading";

type CategoryRow = { login: string; email: string; value: number; pct: number };
type Category = {
  key: string; title: string; unit: string; total: number; valueIsLoc: boolean;
  top3Pct: number; n80: number; tailN: number; tailPct: number; tailValue: number;
  drillKind: string | null; drillFlag: string | null; rows: CategoryRow[];
};

type ReviewerRow = { login: string; reviews: number; approvals: number; latencyH: string | null; barPct: number };
type ReviewCompanyRow = {
  company: string; reviews: number; approvals: number;
  reviewLatencyH: string | null; medianTtmH: string | null; merged: number;
};
type ReviewRepoRow = { repo: string; legacy: boolean; total: number; reviewed: number;
                        coveragePct: number; medianTtmH: string | null };
type Reviews = {
  totalPrs: number; reviewedPrs: number; coveragePct: number; medianTtmH: string | null;
  merged: number; windowed: boolean; reviewers: ReviewerRow[];
  byCompany?: ReviewCompanyRow[]; byRepo?: ReviewRepoRow[];
};

type SplitType = { id: string; name: string; color: string };
type PersonRow = {
  login: string; name: string; company: string; is_member: boolean; not_member: boolean; email: string;
  commits: number; loc: number; raw_loc: number; prs: number; specs: number; bugs: number;
  epics: number; features: number; by_type: Record<string, number>;
  ai_commits: number; reviews: number; approvals: number; ttm: string | null; cpt_lines: number;
  surv_code_human: number; surv_code_ai: number; surv_spec: number; surv_win_code: number | null;
  code_commits: number; commits_pct: number; loc_pct: number; loc_tip: string;
};
type PeopleBlock = { rows: PersonRow[]; splitTypes: SplitType[]; cap: number; rankedByLabel: string };

type PeopleData = {
  meta: { org: string; allTime: boolean; windowStart: string; lookbackDays: number; generatedText: string };
  period: { preset: string; label: string; from: string | null; to: string | null };
  periodPresets: { key: string; label: string }[];
  scope: string;
  scopeTargets: { org?: string[]; element?: string[]; repo?: string[] };
  dataQuality: { apiRateLimited: boolean; apiReset: string | null };
  categories: Category[];
  reviews: Reviews | null;
  people: PeopleBlock;
};

function fmtVal(v: number, isLoc: boolean): string {
  return isLoc ? fmtLoc(v) : fmtNum(v);
}

function CategoryCard({ cat }: { cat: Category }) {
  const noun = cat.n80 === 1 ? "person" : "people";
  // Map each category row to the shared BarRow shape. The .vv keeps the exact
  // "pct% · value" markup (drillable value wrapped in a .dr span when the
  // category has a drill) — passed as `value`, not a row-level drill.
  const rows: BarRowProps[] = cat.rows.map((r) => ({
    label: <GhLink login={r.login} />,
    tip: r.email,
    pct: fmtPct(r.pct),
    color: "var(--acc)",
    value: (
      <>
        {fmtPct(r.pct)}%{" "}
        · {cat.drillKind ? (
          <span
            className="dr" data-drill={cat.drillKind} data-author={r.login}
            {...(cat.drillFlag ? { "data-flag": cat.drillFlag } : {})}
          >
            {fmtVal(r.value, cat.valueIsLoc)}
          </span>
        ) : (
          fmtVal(r.value, cat.valueIsLoc)
        )}
      </>
    ),
  }));
  return (
    <div className="card">
      <h3>{cat.title} <span className="tag">{fmtVal(cat.total, cat.valueIsLoc)} {cat.unit}</span></h3>
      {cat.rows.length > 0 && (
        <div className="conc">
          Top-3 = <b>{fmtPct(cat.top3Pct)}%</b> · <b>{cat.n80}</b> {noun} cover 80%
        </div>
      )}
      <BarList
        rows={rows}
        cap={8}
        tail={cat.tailN > 0 ? {
          moreLabel: `▸ + ${cat.tailN} more`,
          lessLabel: `▾ hide ${cat.tailN}`,
          pct: fmtPct(cat.tailPct),
          color: "var(--mut)",
          value: <>{fmtPct(cat.tailPct)}% · {fmtVal(cat.tailValue, cat.valueIsLoc)}</>,
        } : undefined}
      />
      {cat.rows.length === 0 && <p className="hint">No activity in this period.</p>}
    </div>
  );
}

function CategoriesGrid({ categories }: { categories: Category[] }) {
  return (
    <div className="grid2">
      {categories.map((cat) => <CategoryCard cat={cat} key={cat.key} />)}
    </div>
  );
}

// Reviewer columns depend on `windowed`: the monolith's panel_reviews()
// (templates/panels/02_overview.j2) appends "<span class='alltime-tag'>all-time
// </span>" to the "Median latency" header ONLY when pr.reviews.windowed — a
// reminder that latency is cumulative even under a period filter. That badge
// makes the header ~2px taller; omitting it shifted the whole reviews section
// (and everything below) up by 2px, hard-failing the size-mismatch gate. Build
// the columns per-render so the label matches the monolith exactly.
function buildReviewerCols(windowed: boolean): Column<ReviewerRow>[] {
  return [
    { label: "Reviewer", sortable: true, render: (r) => <GhLink login={r.login} /> },
    { label: "Reviews", kind: "bar", widthKey: "barPct", contentKey: "reviews", contentFmt: "num" },
    { label: "Approvals", key: "approvals" },
    {
      label: windowed
        ? <>Median latency <span className="alltime-tag">all-time</span></>
        : "Median latency",
      tip: "median time from review-requested → approved",
      kind: "raw", key: "latencyH", unit: "h", dash: true,
    },
  ];
}

const REVIEW_COMPANY_COLS: Column<ReviewCompanyRow>[] = [
  { label: "Company", kind: "text", key: "company" },
  { label: "Reviews given", key: "reviews" },
  { label: "Approvals", key: "approvals" },
  { label: "Review latency", tip: "median time review-requested → approved (as reviewers)",
    kind: "raw", key: "reviewLatencyH", unit: "h", dash: true },
  { label: "Median TTM", tip: "median time-to-merge of this company's authored PRs",
    kind: "raw", key: "medianTtmH", unit: "h", dash: true },
  { label: "Merged", kind: "raw", key: "merged" },
];

const REVIEW_REPO_COLS: Column<ReviewRepoRow>[] = [
  { label: "Repo", kind: "text", key: "repo",
    tags: [{ ifKey: "legacy", text: "legacy-only", cls: "legacy" }] },
  { label: "PRs", kind: "raw", key: "total" },
  { label: "Reviewed", kind: "raw", key: "reviewed" },
  { label: "Coverage", kind: "pctp", key: "coveragePct" },
  { label: "Median TTM", kind: "raw", key: "medianTtmH", unit: "h" },
];

function ReviewsSection({ reviews }: { reviews: Reviews }) {
  // DOM nesting mirrors the monolith EXACTLY (templates/report.j2's #reviews
  // block, lines ~1019-1052): the outer `.card` is the chrome; ONLY the mini
  // stats + reviewers table live inside `<div data-period-panel="reviews">`
  // (that's all panel_reviews() renders); the by-company/by-repo <details> and
  // the closing `.conc` are SIBLINGS of that div, still inside the card. An
  // earlier version wrapped the whole card in data-period-panel and dropped the
  // inner wrapper — a 2px margin-collapse drift vs the monolith (the full-page
  // screenshot gate hard-fails on ANY size delta), fixed by matching the nesting.
  return (
    <div className="card">
      <div data-period-panel="reviews">
        <MiniStats
          items={[
            {
              value: <>{fmtPct(reviews.coveragePct)}%</>,
              label: <>PRs with ≥1 review ({fmtNum(reviews.reviewedPrs)}/{fmtNum(reviews.totalPrs)})</>,
              drill: reviews.reviewedPrs ? { "data-drill": "pr", "data-reviewed": "1" } : undefined,
            },
            {
              value: reviews.medianTtmH != null ? `${reviews.medianTtmH}h` : "—",
              label: <>median time-to-merge ({fmtNum(reviews.merged)} merged)</>,
              drill: reviews.merged ? { "data-drill": "pr", "data-pr-state": "merged" } : undefined,
            },
            { value: reviews.reviewers.length, label: "active reviewers" },
          ]}
        />
        <div className="card" style={{ overflowX: "auto", border: "none", padding: 0, marginTop: 10 }}>
          <DataTable columns={buildReviewerCols(reviews.windowed)} rows={reviews.reviewers} empty="No reviews in this window." />
        </div>
      </div>
      {(reviews.byCompany?.length || reviews.byRepo?.length) ? (
        <details className="repo-details">
          <summary>Review load — by company &amp; by repo</summary>
          {reviews.byCompany && reviews.byCompany.length > 0 && (
            <>
              <h3 style={{ marginTop: 12 }}>Review load by company</h3>
              <div style={{ overflowX: "auto" }}>
                <DataTable columns={REVIEW_COMPANY_COLS} rows={reviews.byCompany} />
              </div>
            </>
          )}
          {reviews.byRepo && reviews.byRepo.length > 0 && (
            <>
              <h3 style={{ marginTop: 16 }}>By repo</h3>
              <div style={{ overflowX: "auto" }}>
                <DataTable columns={REVIEW_REPO_COLS} rows={reviews.byRepo.slice(0, 25)} />
              </div>
            </>
          )}
        </details>
      ) : null}
      <p className="conc">
        Reviewers &amp; approvals from PR reviews (GitHub-authenticated <span className="prec exact">exact</span>);
        time-to-merge = createdAt → mergedAt. Bots excluded.
      </p>
    </div>
  );
}

function TypeMix({ row, types }: { row: PersonRow; types: SplitType[] }) {
  const present = types.filter((t) => row.by_type[t.id]);
  const total = present.reduce((s, t) => s + row.by_type[t.id], 0);
  if (!total) return <span className="dm">—</span>;
  const tip = present.map((t) => `${t.name} ${row.by_type[t.id]}`).join(" · ");
  return (
    <span className="mixbar" data-tip={tip}>
      {present.map((t) => (
        <i key={t.id} style={{ width: `${((100 * row.by_type[t.id]) / total).toFixed(1)}%`, background: t.color }} />
      ))}
    </span>
  );
}

function CodeSpecs({ row }: { row: PersonRow }) {
  if (!row.commits) return <span style={{ color: "var(--mut)" }}>—</span>;
  return (
    <>
      <span style={{ color: "var(--acc)" }}>{fmtNum(row.code_commits)}</span>
      {" / "}
      <span style={{ color: "var(--app)" }}>{fmtNum(row.specs)}</span>
    </>
  );
}

const PEOPLE_GROUPS: ColumnGroup[] = [
  { label: "Identity", span: 3 },
  {
    label: <>Surviving code · today <span className="alltime-tag">all-time</span></>, span: 4,
    tip: "Surviving lines in today's tree (git blame, exact line counts). All-time — not affected by the period filter.",
  },
  { label: "Activity · in period", span: 7, tip: "Activity inside the selected period" },
  { label: <>Review <span className="alltime-tag">all-time</span></>, span: 3, tip: "All-time — not affected by the period filter" },
  { label: "Fabric / AI", span: 2, tip: "Fabric / AI-tool footprint (commits are period-scoped; cpt lines are all-time)" },
  { label: "Where effort goes · in period", span: 2 },
];

function buildPeopleColumns(types: SplitType[]): Column<PersonRow>[] {
  return [
    {
      label: "Person",
      render: (r) => (
        <>
          <GhLink login={r.login} />
          {r.not_member && <span className="tag ext">ext</span>}
        </>
      ),
    },
    { label: "Name", kind: "text", key: "name" },
    { label: "Co.", kind: "text", key: "company", tip: "Company affiliation (email domain / override)" },
    {
      // label_html in the monolith: "Unmarked <span class='prec heuristic'>upper
      // bound</span>" — the badge is part of the header (adds ~0.5px height that
      // propagates to the whole grouped thead), so it must render here too.
      label: <>Unmarked <span className="prec heuristic">upper bound</span></>,
      kind: "loc", key: "surv_code_human", cls: "g",
      tip: "Surviving code lines with NO AI/Studio marker. Line count is exact (blame); the 'human' label is NOT — code from tools that leave no marker (Copilot, pasted LLM output) also lands here. Treat as an UPPER BOUND on hand-written code, not proof of authorship.",
    },
    { label: <>AI-marked <span className="prec heuristic">floor</span></>, kind: "loc", key: "surv_code_ai",
      tip: "surviving code lines carrying an AI/Studio marker (@cpt / studio). A floor on AI-generated code — only marked generations are counted." },
    { label: "Spec LOC", kind: "loc", key: "surv_spec", tip: "surviving spec (markdown) lines authored in today's tree" },
    { label: "Δ window", kind: "loc", key: "surv_win_code", dash: true,
      tip: "surviving code lines whose last commit is inside the lookback window (all-time metric; shown only at all-time)" },
    { label: "Commits", kind: "bar", widthKey: "commits_pct", contentKey: "commits", contentFmt: "num", cls: "g",
      drill: { drill: "commit", author: "@login" }, drillIf: "commits" },
    { label: "LOC +", kind: "bar", widthKey: "loc_pct", contentKey: "loc", contentFmt: "loc",
      drill: { drill: "commit", author: "@login" }, drillIf: "commits",
      tip: "Meaningful LOC = raw git additions minus generated/vendor/dependency/fixture/lockfile/build/binary paths. Cleaner code-volume signal, not productivity." },
    { label: "PRs", key: "prs", drill: { drill: "pr", author: "@login" }, drillIf: "prs" },
    { label: "Specs", key: "specs", drill: { drill: "commit", flag: "is_spec", author: "@login" }, drillIf: "specs" },
    { label: "Bugs", key: "bugs", drill: { drill: "issue", flag: "is_bug", author: "@login" }, drillIf: "bugs" },
    { label: "Epics", key: "epics", drill: { drill: "issue", flag: "is_epic", author: "@login" }, drillIf: "epics" },
    { label: "Features", key: "features", drill: { drill: "issue", flag: "is_feature", author: "@login" }, drillIf: "features" },
    { label: "Rev", key: "reviews", cls: "g", tip: "PR reviews given (as reviewer) — all-time" },
    { label: "Appr", key: "approvals", tip: "approvals given (as reviewer) — all-time" },
    { label: "Med TTM", kind: "raw", key: "ttm", unit: "h", dash: true,
      tip: "median time-to-merge of this person's merged PRs — all-time" },
    { label: "AI✦", key: "ai_commits", cls: "g", drill: { drill: "commit", flag: "ai_marked", author: "@login" },
      drillIf: "ai_commits", tip: "commits carrying an AI-tool marker in the period — Platform usage" },
    { label: "cpt", kind: "loc", key: "cpt_lines", tip: "assistant-marked code lines authored (git blame) — all-time" },
    { label: "Type mix", cls: "g", tip: "Commits + PRs per repository type in the period",
      render: (r) => <TypeMix row={r} types={types} /> },
    { label: "Code / Specs", tip: "Share of this person's commits that are code vs spec docs",
      render: (r) => <CodeSpecs row={r} /> },
  ];
}

function PeopleTable({ people }: { people: PeopleBlock }) {
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(false);
  const columns = useMemo(() => buildPeopleColumns(people.splitTypes), [people.splitTypes]);
  const rows = people.rows;
  const q = query.trim().toLowerCase();
  const filtered = q
    ? rows.filter((r) => `${r.login} ${r.name} ${r.company}`.toLowerCase().includes(q))
    : rows;
  const showToggle = rows.length > people.cap;

  return (
    <>
      <div className="tblbar">
        <input
          type="search" className="people-filter" placeholder="Filter by login, name, or company…"
          aria-label="Filter people" value={query} onChange={(e) => setQuery(e.target.value)}
        />
        {showToggle && (
          <button type="button" className="people-toggle" onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Show top 40" : `Show all ${rows.length}`}
          </button>
        )}
        <span className="cnt">{q ? `${filtered.length} / ${rows.length} match` : ""}</span>
      </div>
      <div className="card" style={{ overflowX: "auto" }}>
        {/* NO `cap` on purpose — the monolith's people table renders ALL rows,
            never a capped 40. Its data_table() macro DOES try to cap (emits
            `class="extra"` on rows past 40), but render._env() runs with
            autoescape=True, so the macro's `{{ ' class="extra"' if … }}` string
            is escaped to `<tr class=&#34;extra&#34;>` — a broken attribute whose
            value is the literal `"extra"` (with quotes), which the CSS rule
            `table.grouped tbody tr.extra{display:none}` never matches. Net: the
            cap silently no-ops and all rows show. To stay pixel-identical we
            reproduce that — show every row. The Show-all/Show-top-40 toggle and
            its note still render (they match the monolith's equally-inert
            toggle, which only flips the note/button text), driven by `expanded`
            below. (Tracked as a pre-existing monolith bug to fix at R-FINAL,
            once the parity gate no longer pins us to the monolith's behaviour.) */}
        <DataTable columns={columns} rows={filtered} groups={PEOPLE_GROUPS} />
      </div>
      {showToggle && (
        <p className="conc people-note">
          {expanded ? (
            <span className="when-expanded">
              Showing <b>all {rows.length}</b> people (ranked by {people.rankedByLabel}). <b>Show top 40</b> collapses
              the list.
            </span>
          ) : (
            <span className="when-collapsed">
              Showing top <b>40</b> of <b>{rows.length}</b> people (ranked by {people.rankedByLabel}). The filter
              searches all of them; <b>Show all</b> expands the rest.
            </span>
          )}
        </p>
      )}
    </>
  );
}

export default function People() {
  const { data, error } = useReportData<PeopleData>("people");

  if (error && !data) return <p className="hint" style={{ padding: 24 }}>Could not load the report ({error}).</p>;
  if (!data) return <Loading />;

  const periodLabel = data.period.label;

  return (
    <>
      <p className="sub">
        Org <b>{data.meta.org}</b> ·{" "}
        {data.meta.allTime ? (
          <>
            <b>all-time history</b> (since {data.meta.windowStart})
          </>
        ) : (
          <>window {data.meta.windowStart} → today ({data.meta.lookbackDays} days)</>
        )}{" "}
        · generated {data.meta.generatedText} UTC
      </p>

      <FilterBar
        periodPresets={data.periodPresets} period={data.period} scope={data.scope}
        scopeTargets={data.scopeTargets}
      />

      {data.dataQuality.apiRateLimited && (
        <div className="card" style={{ borderColor: "var(--bad)", background: "#fff5f5" }}>
          <p style={{ margin: 0, color: "var(--bad)", fontWeight: 600 }}>
            ⚠ GitHub API rate limit hit during collection — this report is PARTIAL.
            {data.dataQuality.apiReset && ` Quota resets at ${data.dataQuality.apiReset}.`} Re-run collection
            after the reset for complete data.
          </p>
        </div>
      )}

      <h2 id="categories">
        % contribution by category <span className="period-tag">{periodLabel}</span>
      </h2>
      <div data-period-panel="categories">
        <CategoriesGrid categories={data.categories} />
      </div>

      {data.reviews && (
        <>
          <h2 id="reviews">
            Code review <span className="period-tag">{periodLabel}</span>
          </h2>
          <ReviewsSection reviews={data.reviews} />
        </>
      )}

      <h2 id="people">
        Per-person breakdown <span className="period-tag">{periodLabel}</span>
      </h2>
      <p className="conc">
        Activity columns (commits, LOC+, PRs, specs, bugs, features, AI✦, Fabric core / Apps)
        reflect the selected period; <b>Surviving code</b>, <b>Review</b> and <b>cpt</b> columns are{" "}
        <span className="alltime-tag">all-time</span>. Last two columns split each person's commits+PRs
        by repo type — <b>Fabric core</b> = the framework itself (gears-*, studio, DNA, example-codegen…),{" "}
        <b>Apps</b> = products built on Fabric (insight, example-wiki, example-app…). Hover a name for emails.
      </p>
      <div data-period-panel="people">
        <PeopleTable people={data.people} />
      </div>

      <p className="foot">
        Definitions — <b>Contributing to Fabric</b>: any commit, PR, spec edit, bug or user story in{" "}
        <b>any</b> repo of the org (apps included). <b>Using, not contributing back</b>: forked an org
        repo but made zero contribution to any org repo in the window. <b>Specs</b>: commits touching
        markdown under <code>specs/</code> directories (templates &amp; vendored SDLC framework
        excluded — see <code>config.yaml</code>). Platform-vs-app below is a "where effort goes"
        breakdown, not the contribute/use line. GitHub-only data; passive consumption beyond
        forks/stars is not observable.
      </p>
    </>
  );
}
