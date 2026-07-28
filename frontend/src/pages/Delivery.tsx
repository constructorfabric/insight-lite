// /delivery — the third migrated report view (see
// docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P3).
// Reproduces the monolith's "delivery" mode-section class-for-class against
// templates/report.j2 (the `<h2 id="delivery">…</h2>` block, lines ~686-705)
// + the panel_delivery_kpis/ci/pr/mix/flow macros in
// templates/panels/03_delivery.j2 — Issues KPIs, the issues-by-category mix
// table, Pull requests KPIs, CI & gates KPIs, and the Workflow flow-pipe —
// driven by GET /api/report/delivery (render.delivery_json) instead of
// server-rendered HTML + the /api/delivery fragment swap. Delivery has no
// Vega charts (unlike Overview/Trend) — every number here is a KPI tile, a
// plain table, or the hand-rolled flow-pipe. SSR-safe: no window/document
// access outside hooks/effects.
import FilterBar from "../components/FilterBar";
import KpiTile, { type KpiTileData } from "../components/KpiTile";
import { FlowPipe, type FlowPipeData } from "../widgets";
import { useReportData } from "../hooks/useReportData";
import { fmtPct } from "../lib/format";
import Loading from "../components/Loading";

type MixRow = { label: string; value: number; pct: number; drill: Record<string, string> };

type DeliveryData = {
  meta: { org: string; allTime: boolean; windowStart: string; lookbackDays: number; generatedText: string };
  period: { preset: string; label: string; from: string | null; to: string | null };
  periodPresets: { key: string; label: string }[];
  scope: string;
  scopeTargets: { org?: string[]; element?: string[]; repo?: string[] };
  kpis: KpiTileData[];
  ci: KpiTileData[];
  pr: KpiTileData[];
  mix: { rows: MixRow[] };
  // `stages[].pct` arrives PRE-FORMATTED as a one-decimal string (e.g. "55.0")
  // — see render.py's delivery_json() comment: Jinja's `|round` filter (no
  // precision arg) still returns a float, which the macro stringifies with a
  // trailing ".0" that a JSON number would lose once parsed back into a JS
  // number.
  flow: FlowPipeData;
};

function Mix({ rows }: { rows: MixRow[] }) {
  if (!rows.length) return <p className="hint">No issues opened in this period.</p>;
  return (
    <table style={{ width: "100%" }}>
      <tbody>
        <tr>
          <th>Category</th>
          <th className="num">Issues</th>
          <th data-tip="share of issues opened in the period">Share</th>
        </tr>
        {rows.map((r, i) => (
          <tr key={i}>
            <td><code>{r.label}</code></td>
            <td className="num" data-drill={r.drill.drill} data-category={r.drill.category}>{r.value}</td>
            <td className="db" style={{ "--w": `${fmtPct(r.pct)}%` } as React.CSSProperties}>
              {fmtPct(r.pct)}%
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Delivery() {
  const { data, error } = useReportData<DeliveryData>("delivery");

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

      <h2 id="delivery">
        Delivery — issues, PRs, CI &amp; workflow <span className="period-tag">{periodLabel}</span>
      </h2>

      <h3 className="psec">Issues</h3>
      <div data-period-panel="delivery-kpis">
        <div className="kpis">
          {data.kpis.map((k, i) => <KpiTile key={i} {...k} />)}
        </div>
      </div>
      <div className="dsub">By category</div>
      <div className="card" data-period-panel="delivery-mix">
        <Mix rows={data.mix.rows} />
      </div>

      <h3 className="psec" style={{ marginTop: 26 }}>Pull requests</h3>
      <div data-period-panel="delivery-pr">
        <div className="kpis">
          {data.pr.map((k, i) => <KpiTile key={i} {...k} />)}
        </div>
      </div>

      <h3 className="psec" style={{ marginTop: 26 }}>CI &amp; gates</h3>
      <div data-period-panel="delivery-ci">
        <div className="kpis">
          {data.ci.map((k, i) => <KpiTile key={i} {...k} />)}
        </div>
      </div>

      <h3 className="psec" style={{ marginTop: 26 }}>
        Workflow — current board state <span className="alltime-tag">now</span>
      </h3>
      <div className="card" data-period-panel="delivery-flow">
        <FlowPipe flow={data.flow} />
      </div>
      <p className="conc" style={{ marginTop: 8 }}>
        How work <b>moves</b> and how long it takes — cumulative flow, time in stage, QA→dev returns —
        lives on the <a href="#flow">Flow</a> tab.
      </p>

      <p className="conc" style={{ marginTop: 16 }}>
        Categories, stages and CI roles come from the <a href="/semantic">Taxonomy</a> resolved per
        item's element/repo. Unmapped items fall into <code>uncategorized</code> — refine the mapping
        to sharpen these.
      </p>

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
