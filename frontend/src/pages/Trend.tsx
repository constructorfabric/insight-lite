// /trend — the second migrated report view (see
// docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P2).
// Reproduces the monolith's "trend" mode-section class-for-class against
// templates/report.j2 (the `<h2 id="trend">…</h2><div class="card"
// data-period-panel="trend">{{ panel_trend(pr) }}</div>` block) + the
// panel_trend macro in templates/panels/02_overview.j2 — the Granularity
// control, the Breakdown section (dim switcher + legend + two stacked-area
// charts), and the Throughput & activity section (PR throughput, median
// time-to-merge, active contributors) — driven by GET /api/report/trend
// (render.trend_json) instead of server-rendered HTML + /api/trend fragment
// swaps. SSR-safe: no window/document access outside hooks/effects.
import { useEffect, useRef, useState } from "react";
import FilterBar from "../components/FilterBar";
import VegaChart from "../components/VegaChart";
import { useReportData, useReportQuery, setReportQuery } from "../hooks/useReportData";
import Loading from "../components/Loading";

type TrendData = {
  meta: { org: string; allTime: boolean; windowStart: string; lookbackDays: number; generatedText: string };
  period: { preset: string; label: string; from: string | null; to: string | null };
  periodPresets: { key: string; label: string }[];
  scope: string;
  scopeTargets: { org?: string[]; element?: string[]; repo?: string[] };
  data: {
    dates: string[];
    dims: { key: string; label: string }[];
    dim: string;
    dimlabel: string;
    gran: string;
    granreq: string;
    points: number;
    noun: string;
    legend: { company: string; color: string }[];
    commitChartSpec: unknown;
    locChartSpec: unknown;
    throughputLineSpec: unknown;
    ttmAreaSpec: unknown;
    contributorsAreaSpec: unknown;
  } | null;
};

const GRAN_OPTIONS: [string, string][] = [
  ["auto", "Auto"], ["day", "Day"], ["week", "Week"], ["month", "Month"], ["quarter", "Quarter"],
];

export default function Trend() {
  // Granularity + breakdown-dimension are Trend-only controls, deep-linked via
  // the `tgran`/`tdim` query params (mirrors the monolith's `_urlTrend` +
  // initFromURL/_syncURL in templates/report.j2). A period/slice change resets
  // both to auto/company — FilterBar already clears tgran/tdim on those
  // actions, so this effect just follows suit for local state.
  const query = useReportQuery();
  const periodKey = `${query.p || ""}|${query.from || ""}|${query.to || ""}|${query.slice || ""}`;
  const prevPeriodKey = useRef(periodKey);
  const [gran, setGran] = useState(query.tgran || "auto");
  const [dim, setDim] = useState(query.tdim || "company");

  useEffect(() => {
    if (prevPeriodKey.current !== periodKey) {
      prevPeriodKey.current = periodKey;
      setGran("auto");
      setDim("company");
    }
  }, [periodKey]);

  const { data, error } = useReportData<TrendData>("trend", { gran, dim });

  // Whether the CURRENT state corresponds to the monolith's live-refetch path
  // rather than its build-time fast path — see VegaChart's `waitForFonts` doc.
  // The monolith's Trend panel gets replaced by a live AJAX fetch (and so
  // embeds its charts only after that round-trip, by which point the Inter
  // webfont has had time to load) whenever the period, slice, granularity or
  // breakdown dimension is anything other than the bare default; the bare
  // default takes a build-time-cached fast path that embeds immediately,
  // before any page text has triggered the font load. Mirroring that timing
  // (rather than always/never waiting) is what keeps the pixel-parity gate
  // clean across every state — see templates/report.j2's refreshMain()/
  // _refreshTrend()/_pendingTrendOnce.
  const isLiveRefetch = Boolean(query.p || query.from || query.to || query.slice || query.tgran || query.tdim);

  function chooseGran(g: string) {
    setGran(g);
    setReportQuery({ tgran: g === "auto" ? null : g });
  }
  function chooseDim(d: string) {
    setDim(d);
    setReportQuery({ tdim: d === "company" ? null : d });
  }

  if (error && !data) return <p className="hint" style={{ padding: 24 }}>Could not load the report ({error}).</p>;
  if (!data) return <Loading />;

  const t = data.data;
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

      <h2 id="trend">
        Contribution &amp; delivery trends <span className="period-tag">{periodLabel}</span>
      </h2>
      <div className="card" data-period-panel="trend">
        {t ? (
          <>
            <div className="trendhead">
              <div className="trendctl">
                <div className="granctl" role="group" aria-label="Granularity">
                  {GRAN_OPTIONS.map(([key, label]) => (
                    <button
                      key={key} type="button" className={`gtog${t.granreq === key ? " active" : ""}`}
                      data-gran={key} onClick={() => chooseGran(key)}
                    >
                      {label}
                      {key === "auto" && t.granreq === "auto" && <> <span className="gres">· {t.noun}</span></>}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <section className="trend-sec">
              <div className="trend-sechead">
                <h3 className="trend-secttl">Breakdown</h3>
                <div className="granctl" role="group" aria-label="Break down by">
                  {t.dims.map((d) => (
                    <button
                      key={d.key} type="button" className={`gtog${d.key === t.dim ? " active" : ""}`}
                      data-trenddim={d.key} onClick={() => chooseDim(d.key)}
                    >
                      {d.label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="arealeg">
                {t.legend.map((l) => (
                  <span className="lg" key={l.company}><i style={{ background: l.color }} />{l.company}</span>
                ))}
              </div>

              <h3 className="trend-h">Commits by {t.dimlabel}</h3>
              <div className="areawrap"><VegaChart spec={t.commitChartSpec} waitForFonts={isLiveRefetch} /></div>

              <h3 className="trend-h">Meaningful LOC by {t.dimlabel}</h3>
              <div className="areawrap"><VegaChart spec={t.locChartSpec} waitForFonts={isLiveRefetch} /></div>
            </section>

            <section className="trend-sec">
              <h3 className="trend-secttl">Throughput &amp; activity</h3>

              <h3 className="trend-h">
                PR throughput{" "}
                <span className="trend-leg">
                  <i style={{ background: "#2f80ed" }} />opened <i style={{ background: "#10b981" }} />merged
                </span>
              </h3>
              <div className="areawrap"><VegaChart spec={t.throughputLineSpec} waitForFonts={isLiveRefetch} /></div>

              {t.ttmAreaSpec ? (
                <>
                  <h3 className="trend-h">Median time-to-merge</h3>
                  <div className="areawrap"><VegaChart spec={t.ttmAreaSpec} waitForFonts={isLiveRefetch} /></div>
                </>
              ) : null}

              <h3 className="trend-h">Active contributors</h3>
              <div className="areawrap"><VegaChart spec={t.contributorsAreaSpec} waitForFonts={isLiveRefetch} /></div>
            </section>

            <p className="conc">
              Bucketed by {t.noun} ({t.points} {t.noun}{t.points === 1 ? "" : "s"}) for the selected period &amp;
              slice, derived live from the commit &amp; PR history. Granularity re-buckets every chart; the
              breakdown switcher re-splits only the two stacked charts (by company, work type, repo type or
              element). Hover any {t.noun} for the detail.
            </p>
          </>
        ) : (
          <p className="hint">No commit activity in this window / slice.</p>
        )}
      </div>

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
