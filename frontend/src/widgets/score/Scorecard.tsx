// Overview's team Developer-score scorecard — the `.card.scorecard` with the
// band SegBar, the "Top N by score" table, the "by company · median" table, and
// the team-median KPI tiles. Promoted verbatim from the in-page `Score`
// component in pages/Overview.tsx; emits byte-identical DOM so the pixel gate
// sees no diff. This is a DIFFERENT widget from Person's PersonScore (the gauge
// + chain + board) — they are NOT merged. It reuses the existing
// components/{DataTable,SegBar,KpiTile} primitives exactly as the page did.
import SegBar, { type Segment } from "../../components/SegBar";
import DataTable, { type Column } from "../../components/DataTable";
import KpiTile from "../../components/KpiTile";

export type ScorecardData = {
  n: number; median: number; activePillars: string[];
  pillarColors: Record<string, string>;
  bands: { band: string; n: number; color: string }[];
  top: { rank: number; login: string; name: string; score: number; contributions: Record<string, number> }[];
  byCompany: { company: string; median: number; n: number }[];
  teamMedians: { commits: string | null; ttm: string | null; rounds: string | null; flow: string | null };
};

const PILLAR_LABELS: Record<string, string> = {
  engagement: "Engagement", delivery: "Delivery", craft: "Craft", flow: "Flow",
};

export function Scorecard({ data }: { data: ScorecardData }) {
  const bandSegs: Segment[] = data.bands.map((b) => ({ value: b.n, color: b.color, label: b.band }));
  const topCols: Column<ScorecardData["top"][number]>[] = [
    { label: "#", key: "rank", align: "num", sortable: false },
    {
      label: "Person", sortable: false,
      render: (r) => (
        <a className="gh" href="#person" data-person={r.login}>{r.name}</a>
      ),
    },
    {
      label: "Make-up", sortable: false, cls: "sc-mk",
      render: (r) => (
        <SegBar
          height={12}
          segments={Object.entries(r.contributions).map(([k, v]) => ({
            value: v, color: data.pillarColors[k], label: PILLAR_LABELS[k],
          }))}
        />
      ),
    },
    { label: "Score", key: "score", align: "num" },
  ];
  const companyCols: Column<ScorecardData["byCompany"][number]>[] = [
    { label: "Company", kind: "text", key: "company" },
    { label: "Median", kind: "bar", widthKey: "median", contentKey: "median", contentFmt: "raw", cls: "sc-mk" },
    { label: "People", key: "n", align: "num" },
  ];
  const tm = data.teamMedians;
  return (
    <div className="card scorecard">
      <p className="eyebrow">Experimental</p>
      <h2>Developer score — team</h2>
      <p className="h-sub">
        {data.n} people scored · org-relative composite (median {data.median}). Open any name for the full
        breakdown on their Person page.
      </p>
      <SegBar segments={bandSegs} height={16} legend />
      <div className="sc-grid">
        <div>
          <div className="sc-h">Top {data.top.length} by score</div>
          <DataTable columns={topCols} rows={data.top} />
        </div>
        <div>
          <div className="sc-h">By company · median score</div>
          <DataTable columns={companyCols} rows={data.byCompany} />
          <div className="sc-h" style={{ marginTop: 14 }}>
            Team medians{" "}
            <span className="mut" style={{ fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>
              — real numbers behind the pillars
            </span>
          </div>
          <div className="kpis sc-kpis">
            {tm.commits !== null && <KpiTile value={tm.commits} label="median commits" sub="per person" />}
            {tm.ttm !== null && <KpiTile value={tm.ttm} label="median time-to-merge" />}
            {tm.rounds !== null && <KpiTile value={tm.rounds} label="median review rounds" />}
            {tm.flow !== null && <KpiTile value={tm.flow} label="median friction" />}
          </div>
        </div>
      </div>
      <p className="conc" style={{ marginTop: 12 }}>
        Experimental · org-relative. Because the score ranks people against each other, its median sits near
        the middle by design — the band split and per-company view show the spread, and the team medians are
        the absolute numbers. Not a ranking of worth.
      </p>
    </div>
  );
}
