// /overview — the pilot React route for the report-view migration (see
// docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P1).
// Reproduces the monolith's default ("overview" mode) content — KPI tiles,
// the all-time Contributors chart, Contribution-by-company, Activity-by-week,
// Work-type, and the Developer-score panel — class-for-class against
// templates/report.j2 + templates/panels/02_overview.j2, driven by
// GET /api/report/overview (render.overview_json) instead of server-rendered
// HTML. SSR-safe: no window/document access outside hooks/effects.
import FilterBar from "../components/FilterBar";
import { type ChartData } from "../components/charts/TimeChart";
import { FilledLine, LinesChart } from "../components/charts/shapes";
import KpiTile, { type KpiTileData } from "../components/KpiTile";
import DataTable, { type Column } from "../components/DataTable";
import { BarRow, SplitBar, Scorecard, type Segment as SplitSeg, type ScorecardData } from "../widgets";
import { useReportData } from "../hooks/useReportData";
import { fmtNum, fmtPct } from "../lib/format";
import Loading from "../components/Loading";
import { css } from "../lib/tokens";

type OverviewData = {
  meta: { org: string; allTime: boolean; windowStart: string; lookbackDays: number; generatedText: string };
  period: { preset: string; label: string; from: string | null; to: string | null };
  periodPresets: { key: string; label: string }[];
  scope: string;
  scopeTargets: { org?: string[]; element?: string[]; repo?: string[] };
  dataQuality: { apiRateLimited: boolean; apiReset: string | null };
  kpis: KpiTileData[];
  contributors: {
    tiles: { value: string; label: string; sub: string; color?: string }[];
    chart: ChartData | null;
    legend: { name: string; color: string }[];
    since: string;
    points: number;
  } | null;
  companies: { rows: Record<string, unknown>[] };
  weekly: { rows: { title: string; total: string; chart: ChartData | null }[];
            weeksCount: number } | null;
  workType: {
    rows: { type: string; count: number; pct: number; color: string }[];
    total: number;
    breakdown: {
      typeCols: string[];
      byCompany: { company: string; types: Record<string, number>; total: number }[];
      byRepo: { repo: string; key: string; legacy: boolean; types: Record<string, number>; total: number }[];
    } | null;
  };
  score: ScorecardData | null;
};

const COMPANY_COLS: Column<Record<string, unknown>>[] = [
  { label: "Company", kind: "text", key: "company", swatch: "dot", colorKey: "color" },
  { label: "People", key: "people", drillIf: "people", drill: { drill: "people", company: "@company" } },
  { label: "Commits", key: "commits", drill: { drill: "commit", company: "@company" } },
  { label: "Commit %", tip: "Share of total commits", kind: "bar", widthKey: "pct" },
  {
    label: "AI%", tip: "Share of this company's commits carrying an AI-tool marker (floor)",
    kind: "heatmap", key: "ai_pct", alphaKey: "ai_pct",
    drillIf: "ai_commits", drill: { drill: "commit", company: "@company", flag: "ai_marked" },
  },
  { label: "Meaningful LOC +", kind: "loc", key: "meaningful_additions", drill: { drill: "commit", company: "@company" } },
  { label: "LOC %", tip: "Share of total meaningful LOC added", kind: "bar", widthKey: "loc_pct" },
  { label: "Specs", key: "specs", drill: { drill: "commit", company: "@company", flag: "is_spec" } },
  { label: "Bugs", key: "bugs", drill: { drill: "issue", company: "@company", flag: "is_bug" } },
  { label: "Epics", key: "epics", drill: { drill: "issue", company: "@company", flag: "is_epic" } },
  { label: "Features", key: "features", drill: { drill: "issue", company: "@company", flag: "is_feature" } },
  { label: "PRs", key: "prs", drill: { drill: "pr", company: "@company" } },
];

function Contributors({ data }: { data: NonNullable<OverviewData["contributors"]> }) {
  return (
    <>
      <h2 id="contributors">
        Contributors <span className="alltime-tag">cumulative · all-time</span>
      </h2>
      <div className="card">
        <p className="hint">
          Distinct people with any commit / PR / issue (bots &amp; migration duplicates excluded), counted
          cumulatively. Δ = change over the last 90 days (since {data.since}). Not affected by the period
          filter above.
        </p>
        <div className="kpis">
          {data.tiles.map((t, i) => (
            <div className="kpi" key={i}>
              <div className="n" style={{ color: t.color }}>{t.value}</div>
              <div className="l">{t.label}</div>
              <div className="l2">{t.sub}</div>
            </div>
          ))}
        </div>
        <div className="trendhead" style={{ marginTop: 14 }}>
          <h3 className="trend-h" style={{ margin: 0 }}>Cumulative contributors by month</h3>
          <div className="arealeg">
            {data.legend.map((s) => (
              <span className="lg" key={s.name}>
                <i style={{ background: s.color }} />{s.name}
              </span>
            ))}
          </div>
        </div>
        <div className="areawrap">
          {data.chart && <LinesChart chart={data.chart} />}
        </div>
      </div>
    </>
  );
}

function Companies({ rows, periodLabel }: { rows: Record<string, unknown>[]; periodLabel: string }) {
  return (
    <>
      <h2 id="companies">
        Contribution by company <span className="period-tag">{periodLabel}</span>
      </h2>
      <div className="card">
        <SplitBar
          className="split"
          segments={rows
            .filter((c) => (c.pct as number) > 0)
            .map((c): SplitSeg => ({
              pct: fmtPct(c.pct),
              color: String(c.color),
              tip: `${c.company}: ${fmtNum(c.commits)} commits (${fmtPct(c.pct)}%)`,
              text: (c.pct as number) >= 7 ? (c.pct as number).toFixed(1) : "",
            }))}
        />
        <div className="card" style={{ overflowX: "auto", border: "none", padding: 0, marginTop: 12 }}>
          <DataTable columns={COMPANY_COLS} rows={rows} />
        </div>
        <p className="conc">
          Affiliation = email domain (example.com / example.net → Constructor, partner.example → Example Inc) or a
          manual <code>companies.overrides</code> in config; unmapped → <b>Other</b>. People with no
          commit-email are mapped by login override only. Bar = share of commits.
        </p>
      </div>
    </>
  );
}

function Weekly({ data, periodLabel }: { data: NonNullable<OverviewData["weekly"]>; periodLabel: string }) {
  return (
    <>
      <h2 id="weekly">Activity by week <span className="period-tag">{periodLabel}</span></h2>
      <div className="card">
        <div className="wkgrid">
          {data.rows.map((r, i) => (
            <div className="wkcell" key={i}>
              <div className="wkcell-h">
                <span className="wkcell-t">{r.title}</span>
                <span className="wkcell-n">{r.total}</span>
              </div>
              {r.chart && <FilledLine chart={r.chart} />}
            </div>
          ))}
        </div>
        <p className="conc">
          Volume per week for the selected period &amp; slice ({data.weeksCount} weeks; commits/specs from
          primary-org history, PRs/issues across both orgs). Each metric has its own scale; hover a week for
          its count.
        </p>
      </div>
    </>
  );
}

function WorkTypeBreakdown({ breakdown }: { breakdown: NonNullable<OverviewData["workType"]["breakdown"]> }) {
  const companyRows = breakdown.byCompany.map((r) => ({ company: r.company, total: r.total, ...r.types }));
  const repoRows = breakdown.byRepo.map((r) => ({ repo: r.repo, legacy: r.legacy, total: r.total, ...r.types }));
  const typeCols: Column<Record<string, unknown>>[] = breakdown.typeCols.map((c) => ({
    label: c, key: c, kind: "raw" as const,
  }));
  return (
    <details className="repo-details">
      <summary>Breakdown by company &amp; by repo</summary>
      {breakdown.byCompany.length > 0 && (
        <>
          <h3 style={{ marginTop: 12 }}>By company</h3>
          <div style={{ overflowX: "auto" }}>
            <DataTable
              columns={[
                { label: "Company", kind: "text", key: "company" },
                ...typeCols,
                { label: "total", render: (r) => <b>{String(r.total)}</b> },
              ]}
              rows={companyRows}
            />
          </div>
        </>
      )}
      {breakdown.byRepo.length > 0 && (
        <>
          <h3 style={{ marginTop: 16 }}>By repo</h3>
          <div style={{ overflowX: "auto" }}>
            <DataTable
              columns={[
                {
                  label: "Repo", kind: "text", key: "repo",
                  tags: [{ ifKey: "legacy", text: "legacy-only", cls: "legacy" }],
                },
                ...typeCols,
                { label: "total", render: (r) => <b>{String(r.total)}</b> },
              ]}
              rows={repoRows}
            />
          </div>
        </>
      )}
    </details>
  );
}

function WorkType({ data, periodLabel }: { data: OverviewData["workType"]; periodLabel: string }) {
  return (
    <>
      <h2 id="commit-types">
        Work type — conventional commits <span className="prec exact">exact</span>{" "}
        <span className="period-tag">{periodLabel}</span>
      </h2>
      <div className="card">
        <p className="hint">
          Parsed from conventional-commit prefixes (feat/fix/docs/…) in commit subjects — an exact split of{" "}
          <i>what kind of work</i>. <code>other</code> = no conventional prefix (sums to 100%). Follows the
          period &amp; slice.
        </p>
        <div>
          {data.total ? (
            <>
              <SplitBar
                className="cmix-bar wtbar"
                role="img"
                ariaLabel="work-type composition"
                segments={data.rows
                  .filter((t) => t.count)
                  .map((t): SplitSeg => ({
                    pct: (100 * t.count) / data.total,
                    color: t.color,
                    tip: `${t.type}: ${fmtNum(t.count)} (${fmtPct(t.pct)}%)`,
                  }))}
              />
              <div className="wtlist">
                {data.rows.map((t) => (
                  <BarRow
                    key={t.type}
                    label={<><span className="wtsw" style={{ background: t.color }} />{t.type}</>}
                    pct={fmtPct(t.pct)}
                    color={t.color}
                    value={<>{fmtPct(t.pct)}% · {fmtNum(t.count)}</>}
                  />
                ))}
              </div>
              {data.breakdown && <WorkTypeBreakdown breakdown={data.breakdown} />}
            </>
          ) : (
            <p className="conc">No commits in this period.</p>
          )}
        </div>
      </div>
    </>
  );
}

export default function Overview() {
  const { data, error } = useReportData<OverviewData>("overview");

  if (error && !data) return <p className="hint" style={{ padding: 24 }}>Could not load the report ({error}).</p>;
  if (!data) return <Loading />;

  const periodLabel = data.period.label;

  return (
    <>

      <FilterBar
        periodPresets={data.periodPresets} period={data.period} scope={data.scope}
        scopeTargets={data.scopeTargets}
      />

      {data.dataQuality.apiRateLimited && (
        <div className="card" style={{ borderColor: "var(--bad)", background: css("bad-soft") }}>
          <p style={{ margin: 0, color: "var(--bad)", fontWeight: 600 }}>
            ⚠ GitHub API rate limit hit during collection — this report is PARTIAL.
            {data.dataQuality.apiReset && ` Quota resets at ${data.dataQuality.apiReset}.`} Re-run collection
            after the reset for complete data.
          </p>
        </div>
      )}

      <div data-period-panel="kpis">
        <div className="kpis">
          {data.kpis.map((k, i) => <KpiTile key={i} {...k} />)}
        </div>
      </div>

      <details className="readme" aria-label="Metric definitions">
        <summary>How to read these numbers</summary>
        <div>
          <div className="defs">
            <div className="def">
              <b>Meaningful LOC</b>
              <span>
                Raw git additions minus generated, vendor, dependency, fixture, lockfile, build and binary
                paths. This is a cleaner code-volume signal, not a productivity score.
              </span>
            </div>
            <div className="def">
              <b>Raw additions</b>
              <span>
                Everything reported by git across cloned repositories before filtering. Kept in tooltips for
                auditability.
              </span>
            </div>
            <div className="def">
              <b>Identity confidence</b>
              <span>
                How a person was resolved: manual review, verified email/name bridge, GitHub login, or
                unresolved.
              </span>
            </div>
          </div>
          <p className="conc" style={{ margin: "10px 0 0" }}>
            All counts are <b>created/opened in the window</b> (PRs also show how many later merged). Full
            status lifecycle — bugs resolved/verified/declined, PRs approved/rejected — is a planned next step
            (needs closed-issue/review queries).
          </p>
        </div>
      </details>

      {data.contributors && <Contributors data={data.contributors} />}
      <Companies rows={data.companies.rows} periodLabel={periodLabel} />
      {data.weekly && <Weekly data={data.weekly} periodLabel={periodLabel} />}
      <WorkType data={data.workType} periodLabel={periodLabel} />
      {data.score && <Scorecard data={data.score} />}

      <p className="foot">
        Definitions — <b>Contributing to Fabric</b>: any commit, PR, spec edit, bug or user story in{" "}
        <b>any</b> repo of the org (apps included). <b>Using, not contributing back</b>: forked an org repo
        but made zero contribution to any org repo in the window. <b>Specs</b>: commits touching markdown
        under <code>specs/</code> directories (templates &amp; vendored SDLC framework excluded — see{" "}
        <code>config.yaml</code>). Platform-vs-app below is a "where effort goes" breakdown, not the
        contribute/use line. GitHub-only data; passive consumption beyond forks/stars is not observable.
      </p>
    </>
  );
}
