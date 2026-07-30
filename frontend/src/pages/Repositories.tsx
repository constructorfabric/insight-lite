// /repositories — the seventh migrated report view (see
// docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P7). NOTE the
// route rename repos→repositories (migration spec's redirect table); the
// monolith's mode is still "repos" and the sidebar active-key stays "repos".
// Reproduces the monolith's "repos" mode-sections class-for-class against
// templates/report.j2 (the three `<div class="mode-section" data-modes="repos
// all">` blocks: repo coverage ~712, "Where effort goes — by repository type"
// = panel_split ~991, and the "⚠ Unclassified repos" chips ~1066) — driven by
// GET /api/report/repositories (render.repositories_json) instead of
// server-rendered HTML. Repos has NO Vega charts (the split is CSS typebars,
// the inventory is a table inside a CLOSED <details>). SSR-safe: no
// window/document access outside hooks/effects.
import FilterBar from "../components/FilterBar";
import DataTable, { type Column } from "../components/DataTable";
import { SplitBar, Legend, MiniStats, Chips, type Segment } from "../widgets";
import { useReportData } from "../hooks/useReportData";
import Loading from "../components/Loading";

// ---- types (mirror render.repositories_json's payload) ---------------------
type RepoRow = {
  name: string; org: string; classification: string; element: string;
  code_loc: number | null; spec_loc: number | null;
  contributors: number; forks: number; stars: number;
  traffic_access: boolean; clones: number; uniques: number;
  unclassified: boolean; legacy_only: boolean;
  full_name?: string; total_loc?: number | null;
};

type RepoSummary = {
  distinct: number; primary: number; primaryOrg: string | null; legacyOnly: number;
  platform: number; app: number; unclassified: number; missingTraffic: number;
  legacyDup: number; total: number;
};

// One typebar's data (bars = the stacked <i> segments; legend = the .leg2 row).
// `width`/`tip` live on bar segments; `name`/`pct`/`value` on legend segments.
type RepoSeg = {
  id: string; color: string;
  width?: string; tip?: string;
  name?: string; pct?: string; value?: string;
};
type RepoSplit = { sub: string; drill: string; bars: RepoSeg[]; legend: RepoSeg[] };

type ReposData = {
  meta: { org: string; allTime: boolean; windowStart: string; lookbackDays: number; generatedText: string };
  period: { preset: string; label: string; from: string | null; to: string | null };
  periodPresets: { key: string; label: string }[];
  scope: string;
  scopeTargets: { org?: string[]; element?: string[]; repo?: string[] };
  dataQuality: { apiRateLimited: boolean; apiReset: string | null };
  repoSummary: RepoSummary;
  repoRows: RepoRow[];
  unclassified: string[];
  split: { present: boolean; bars: RepoSplit[] };
};

// The repo inventory table's columns — port of the monolith's REPO_COLS
// (templates/report.j2 ~735) fed to data_table(..., cap=60). This whole table
// lives inside a CLOSED <details>, so it never enters the pixel-parity gate;
// still reproduced faithfully. NO `cap` in React on purpose (see rule #1 in the
// task brief): the monolith's data_table macro TRIES to cap at 60, but
// render._env()'s autoescape mangles the per-row `class="extra"` into
// `class=&#34;extra&#34;`, so the CSS `tr.extra{display:none}` never matches and
// ALL rows actually render. Matching that = no cap here (the only DOM delta is
// the absent "▸ Show all N" more-row, invisible inside the closed details).
const REPO_COLS: Column<RepoRow>[] = [
  { label: "Repo", kind: "text", key: "name", tags: [
    { ifKey: "unclassified", text: "unclassified" },
    { ifKey: "legacy_only", text: "legacy-only", cls: "legacy" }] },
  { label: "Org", kind: "text", key: "org" },
  { label: "Class", kind: "text", key: "classification" },
  { label: "Element", kind: "text", key: "element" },
  { label: "Code LOC", tip: "surviving code lines in today's tree (blame); — = not cloned",
    kind: "loc", key: "code_loc", dash: true },
  { label: "Spec LOC", tip: "surviving spec lines in today's tree (blame)",
    kind: "loc", key: "spec_loc", dash: true },
  { label: "Contributors", kind: "raw", key: "contributors" },
  { label: "Forks", kind: "raw", key: "forks" },
  { label: "Stars", kind: "raw", key: "stars" },
  // kind='bool' in the monolith (bool_map yes/no access) — DataTable has no
  // bool kind, so a render escape hatch produces the same text.
  { label: "Traffic", render: (r) => <>{r.traffic_access ? "yes" : "no access"}</> },
  { label: "Clones", key: "clones" },
  { label: "Unique cloners", key: "uniques" },
];

// One typebar (typebar() macro, templates/panels/02_overview.j2): a stacked
// proportional bar + its legend. `first` drops the top margin on the leading
// sub-heading (the monolith's first `<div class="sub">` has no margin-top).
function TypeBar({ bar, first }: { bar: RepoSplit; first: boolean }) {
  return (
    <>
      <div className="sub" style={first ? undefined : { marginTop: 16 }}>{bar.sub}</div>
      <SplitBar
        className="split2"
        style={{ height: 16 }}
        segments={bar.bars.map((s): Segment => ({
          pct: s.width!, color: s.color, tip: s.tip,
          drill: { "data-drill": bar.drill, "data-classification": s.id },
        }))}
      />
      <Legend
        segments={bar.legend.map((s): Segment => ({
          pct: s.pct!, color: s.color, label: s.name, value: s.value,
          drill: { "data-drill": bar.drill, "data-classification": s.id },
        }))}
      />
    </>
  );
}

export default function Repositories() {
  const { data, error } = useReportData<ReposData>("repositories");

  if (error && !data) return <p className="hint" style={{ padding: 24 }}>Could not load the report ({error}).</p>;
  if (!data) return <Loading />;

  const s = data.repoSummary;
  const unclassified = data.unclassified || [];
  const periodLabel = data.period.label;

  const MINI = [
    [s.distinct, "distinct repos"],
    [s.primary, <>in {s.primaryOrg} (primary)</>],
    [s.legacyOnly, "legacy-only (old org)"],
    [s.platform, "platform"],
    [s.app, "app"],
    [s.unclassified, "unclassified"],
    [s.missingTraffic, "missing traffic access"],
  ] as const;

  return (
    <>

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

      {/* Repo coverage — ALL-TIME (no data-period-panel; never refetches on
          period change). Mirrors templates/report.j2's `data-modes="repos all"`
          block at ~712. */}
      <h2 id="repo-coverage">Repo coverage <span className="alltime-tag">all-time</span></h2>
      <div className="card">
        <p className="hint">
          Repository inventory and coverage health. Detailed repo rows are collapsed because this is a
          trust/context section, not the main story.
        </p>
        <MiniStats items={MINI.map(([mv, ml]) => ({ value: mv, label: ml }))} />
        <p className="conc">
          <b>{s.distinct} distinct repos</b> = {s.primary} in <code>{s.primaryOrg}</code> (primary) + {s.legacyOnly} legacy-only in the old org.
          {s.legacyDup ? (
            <>{" "}A further <b>{s.legacyDup}</b> old-org repos are pre-migration copies of primary repos (same name in both orgs) — the inventory below lists all {s.total} org/name pairs, but commits &amp; LOC are parsed from the primary org only, so migrated repos are not double-counted for code.</>
          ) : null}
        </p>
        {unclassified.length > 0 && (
          <p className="conc">
            <b>Needs classification:</b>{" "}
            {unclassified.map((r) => <span className="tag" key={r}>{r}</span>)}
          </p>
        )}
        <details className="repo-details">
          <summary>Show repo inventory table ({data.repoRows.length} repos)</summary>
          <div style={{ overflowX: "auto", marginTop: 10 }}>
            <DataTable columns={REPO_COLS} rows={data.repoRows} />
          </div>
        </details>
      </div>

      {/* Where effort goes — by repository type (panel_split). Period-scoped:
          data-period-panel wraps ONLY the card content (h2 is a sibling —
          rule #3). */}
      <h2 id="effort">Where effort goes — by repository type <span className="period-tag">{periodLabel}</span></h2>
      <div className="card" data-period-panel="split">
        {data.split.present ? (
          data.split.bars.map((bar, i) => <TypeBar key={bar.sub} bar={bar} first={i === 0} />)
        ) : (
          <p className="hint">Nothing in the selected period.</p>
        )}
      </div>

      {/* ⚠ Unclassified repos — its own mode-section, only when there are any. */}
      {unclassified.length > 0 && (
        <>
          <h2>⚠ Unclassified repos</h2>
          <div className="card">
            <p className="hint">
              Assign these a repository type in Config (treated as the <b>default type</b> for now):
            </p>
            <Chips items={unclassified.map((r) => ({ key: r, content: r }))} />
          </div>
        </>
      )}

      {/* Always-on page footer (templates/report.j2 ~1077, OUTSIDE every
          mode-section) — visible in ALL modes incl. repos, so render it
          verbatim (same as the other report views). */}
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
