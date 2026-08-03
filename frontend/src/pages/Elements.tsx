// /elements — the eighth migrated report view (see
// docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P8).
// Reproduces the monolith's "elements" mode-section class-for-class against
// templates/report.j2 (the single `<div class="mode-section"
// data-modes="elements all">` block at ~757, which calls panel_elements() in
// templates/panels/02_overview.j2) — driven by GET /api/report/elements
// (render.elements_json) instead of server-rendered HTML. Elements has NO Vega
// charts (one per-element table). SSR-safe: no window/document access outside
// hooks/effects.
import FilterBar from "../components/FilterBar";
import DataTable, { type Column } from "../components/DataTable";
import { useReportData } from "../hooks/useReportData";
import Loading from "../components/Loading";
import { css } from "../lib/tokens";

// ---- types (mirror render.elements_json's payload) -------------------------
// Row dicts pass through from store.aggregate()/build_model() plus the derived
// fields elements_json adds (_code_bar/_scope/element_color, and median_ttm_h
// pre-formatted to a string). Kept loose (Record) because DataTable reads
// fields dynamically by key — same as the other table views.
type ElementRow = Record<string, unknown>;

type ElementsData = {
  meta: { org: string; allTime: boolean; windowStart: string; lookbackDays: number; generatedText: string };
  period: { preset: string; label: string; from: string | null; to: string | null };
  periodPresets: { key: string; label: string }[];
  scope: string;
  scopeTargets: { org?: string[]; element?: string[]; repo?: string[] };
  dataQuality: { apiRateLimited: boolean; apiReset: string | null };
  elementRows: ElementRow[];
};

// Port of the monolith's ELEMENT_COLS (templates/panels/02_overview.j2's
// panel_elements macro) fed to data_table(...). No `cap` — panel_elements calls
// data_table WITHOUT cap, so every element row renders (the table also isn't
// grouped). Column kinds map 1:1 to _dt_cell's branches:
//   • Element  — text + `edot` swatch (colour = ecolor(), precomputed server-side)
//   • Code LOC — in-cell bar (width _code_bar → |pct; content code_loc → |loc)
//   • People   — count + a "+N ext" tag; drills into the members list
//   • PRs      — pair (opened / merged); drills into opened PRs
//   • Med TTM  — raw (pre-formatted median string) + "h" unit; "—" when no merges
//   • AI%      — heatmap (alpha & value both from ai_pct)
// Every drillable cell resolves `@_scope` to the row's element scope.
const ELEMENT_COLS: Column<ElementRow>[] = [
  { label: "Element", kind: "text", key: "element", swatch: "edot", colorKey: "element_color" },
  { label: "Code LOC", kind: "bar", widthKey: "_code_bar", contentKey: "code_loc", contentFmt: "loc" },
  { label: "Spec LOC", kind: "loc", key: "spec_loc" },
  { label: "Repos", key: "repos" },
  {
    label: "People", tip: "distinct contributors (members) with commits in this element",
    key: "people_members", drillIf: "people_members",
    drill: { drill: "people", scope: "@_scope", members: "1" },
    tags: [{ ifKey: "people_external", prefix: "+", textKey: "people_external", suffix: " ext", cls: "ext" }],
  },
  { label: "Commits", key: "commits_window", drill: { drill: "commit", scope: "@_scope" } },
  {
    label: "PRs (open / merged)", kind: "pair", key: "prs_opened_window", key2: "prs_merged_window",
    drill: { drill: "pr", scope: "@_scope", tip: "PRs opened in this element" },
  },
  {
    label: "Med TTM", tip: "median time-to-merge of PRs in this element", kind: "raw",
    key: "median_ttm_h", dash: true, unit: "h", drillIf: "prs_merged_window",
    drill: { drill: "pr", "pr-state": "merged", scope: "@_scope", tip: "the merged PRs behind this median" },
  },
  {
    label: "AI%", kind: "heatmap", key: "ai_pct", alphaKey: "ai_pct", drillIf: "ai_pct",
    drill: { drill: "commit", scope: "@_scope", flag: "ai_marked" },
  },
];

export default function Elements() {
  const { data, error } = useReportData<ElementsData>("elements");

  if (error && !data) return <p className="hint" style={{ padding: 24 }}>Could not load the report ({error}).</p>;
  if (!data) return <Loading />;

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

      {/* By Element (panel_elements). Period-scoped: data-period-panel wraps
          ONLY the panel content (h2 is a sibling — rule #3). */}
      <h2 id="elements">By Element</h2>
      <div className="card">
        <p className="hint">
          Product-line rollup. <b>Code/Spec LOC</b> = surviving lines in today's tree (git blame), specs
          counted separately from code; computable for cloned primary-org repos only —{" "}
          <span className="alltime-tag">all-time</span>, not period-filtered.{" "}
          <b>Commits, PRs, people and AI%</b> reflect the selected period.
        </p>
        <div data-period-panel="elements">
          <div style={{ overflowX: "auto" }}>
            <DataTable columns={ELEMENT_COLS} rows={data.elementRows} />
          </div>
        </div>
        <p className="conc">
          Element mapping lives in <code>config.yaml</code> under <code>elements:</code>. Old-org
          (<code>your-old-org</code>) repos contribute PR/people metadata to their element but no LOC (not cloned).
        </p>
      </div>

      {/* Always-on page footer (templates/report.j2 ~1077, OUTSIDE every
          mode-section) — visible in ALL modes incl. elements, so render it
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
