// /flow — the fourth migrated report view (see
// docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P4).
// Reproduces the monolith's "flow" mode-section class-for-class against
// templates/report.j2 (the `<h2 id="flow">…</h2><div data-period-panel="flow">
// {{ panel_flow(pr) }}</div>` block) + the panel_flow macro in
// templates/panels/04_flow.j2 — the friction explainer, Flow-health cards,
// cycle-time medians, the by-person friction table, and the board-movement
// views (CFD chart, time-in-stage, QA→dev rewinds) — driven by
// GET /api/report/flow (render.flow_json) instead of server-rendered HTML +
// the /api/flow fragment swap. SSR-safe: no window/document access outside
// hooks/effects.
//
// Timing note for the CFD chart's VegaChart: unlike Overview/Trend's default
// state (a build-time-cached fast path that embeds the chart immediately,
// before any font fetch has had time to complete), the monolith's Flow panel
// has NO fast path at all — templates/report.j2's initFromURL() always calls
// refreshFlow(), which live-fetches /api/flow and embeds only after that
// round-trip, EVEN for the bare default state (mirrors refreshDelivery() —
// see pages/Delivery.tsx's own docstring). So the CFD chart always uses
// waitForFonts=true here, unconditionally (no isLiveRefetch toggle needed,
// unlike pages/Trend.tsx).
import type { ReactNode } from "react";
import FilterBar from "../components/FilterBar";
import VegaChart from "../components/VegaChart";
import type { KpiDelta } from "../components/KpiTile";
import { GhLink } from "../widgets";
import DataTable, { type Column, zeroClass } from "../components/DataTable";
import { useReportData } from "../hooks/useReportData";
import { fmtNum, fmtPct } from "../lib/format";
import Loading from "../components/Loading";

// What the server attaches to any flow scalar that can move: a mini-trend over ~8
// sub-windows of the period, and a chip against the preceding equal period. Both are
// optional — a tile whose metric has neither still renders, just without them.
type Trend = { sparkPts?: string | null; sparkColor?: string; delta?: KpiDelta };

type CycleSeg = { key: string; label: string; sub: string; h: string; n: number } & Trend;
type CycleLeg = { key: string; label: string; sub: string; h: string; pct: number; color: string };
type CycleBarRepo = {
  repo: string; n: number;
  ttfrH: string; r2mH: string; totalH: string;
  // raw hours beside the formatted strings, so DataTable's data-sort is numeric
  ttfrHours: number; r2mHours: number; totalHours: number;
  widthPct: number; ttfrPct: number; r2mPct: number;
};
type CycleBar = {
  n: number; legs: CycleLeg[]; medianTotalH: string;
  p75TotalH: string; p90TotalH: string; draft: { n: number; h: string } | null;
  byRepo: CycleBarRepo[]; reposTotal: number; repoMin: number;
};
type Person = {
  login: string; name: string; items: number;
  friction: string | null; frictionColor: string | null;
  reopenPct: number; bouncePct: number;
  crRounds: number; crPrs: number; extraReqs: number;
  // …Med is the pre-formatted duration the cell shows, …MedHours the raw number
  // behind it, for the same data-sort reason as CycleBarRepo's above.
  ttmMed: string | null; ttmMedHours: number | null;
  ttfrMed: string | null; ttfrMedHours: number | null;
};
type CfdSeries = { key: string; name: string; color: string };
type Cfd = { hasData: boolean; nDates: number; firstDate: string | null; series: CfdSeries[]; spec: unknown };
type DwellStage = {
  key: string; name: string; color: string; nCurrent: number;
  ageMedianH: string | null; ageMedianHours: number | null;
  medianH: string | null; medianHours: number | null;
  n: number;
};
type Dwell = {
  hasData: boolean; ageMedianH: string | null; ageN: number; ageMaxH: string | null;
  dwellMedianH: string | null; dwellN: number; firstDate: string | null; stages: DwellStage[];
};
type Rewinds = {
  hasHistory: boolean; nDates: number; firstDate: string | null;
  hasEvents: boolean; qaToDev: number; ownerCount: number; delta?: KpiDelta;
};
type FlowBlock =
  | { hasData: false }
  | {
      hasData: true;
      nItems: number; nPrs: number; nIssues: number;
      health: {
        crRate: number; crPrs: number; crRounds: number;
        reopenRate: number; reopenedN: number;
        bounceRate: number; bouncedN: number;
        rereqRate: number; rereqN: number;
      };
      healthTrend?: Record<"crRate" | "reopenRate" | "bounceRate" | "rereqRate", Trend>;
      cycle: CycleSeg[];
      cycleBar: CycleBar | null;
      minItems: number; people: Person[];
      cfd: Cfd; dwell: Dwell; rewinds: Rewinds;
    };

type InFlightPerson = {
  login: string; n: number; drafts: number; unreviewed: number; oldestAgeD: number;
};
type StalePr = { repo: string; number: number; login: string; ageD: number; title: string };
type BigPr = { repo: string; number: number; login: string; additions: number; files: number };
type InFlight = {
  periodScoped: false;
  staleBefore: string | null; staleDays: number;
  n: number; drafts: number; unreviewed: number;
  medianAgeD: number | null; oldestAgeD: number | null;
  bands: { key: string; label: string; n: number }[];
  people: InFlightPerson[];
  staleReviewDays: number; staleUnreviewedN: number; staleUnreviewed: StalePr[];
  size: {
    medianAdditions: number | null; p90Additions: number | null;
    medianFiles: number | null; rawLines: boolean; biggest: BigPr[];
  };
};

type AbandonReason = {
  key: string; label: string; sub: string; n: number; reviews: number;
  medianLivedD: number | null; oldestLivedD: number | null;
};
type AbandonRepo = { repo: string; n: number; reviews: number; swept: number };
type SweptPr = { repo: string; number: number; login: string; livedD: number; title: string };
type Abandoned = {
  periodScoped: true;
  n: number; merged: number; closedTotal: number; ratePct: number | null;
  reviewed: number; unreviewed: number; reviewsTotal: number; drafts: number;
  reasons: AbandonReason[];
  bands: { key: string; label: string; n: number }[];
  repos: AbandonRepo[];
  swept: SweptPr[];
};

type FlowData = {
  meta: { org: string; allTime: boolean; windowStart: string; lookbackDays: number; generatedText: string };
  period: { preset: string; label: string; from: string | null; to: string | null };
  periodPresets: { key: string; label: string }[];
  scope: string;
  scopeTargets: { org?: string[]; element?: string[]; repo?: string[] };
  flow: FlowBlock;
  cycleMissing?: { key: string; label: string }[];
  inFlight?: InFlight;
  abandoned?: Abandoned;
};

// One section of the page, stating the question it answers and who asks it.
//
// The page used to be an undivided scroll of everything known about flow, which read
// as "for everybody", i.e. for nobody: a lead looking for what to unblock today and
// somebody reviewing how the process performs over a quarter were given the same wall.
// Naming the question is most of the fix; collapsing everything except the two
// sections that answer "what do I do this week" is the rest.
//
// `open` is the INITIAL state only. React writes the attribute on mount and, because
// the value never changes between renders, leaves it alone afterwards — so a reader's
// own expand/collapse survives a period or scope change.
function Section({ q, who, open, children }: {
  q: string; who: string; open?: boolean; children: ReactNode;
}) {
  return (
    <details className="flow-sec" open={open}>
      <summary>
        <span className="fs-q">{q}</span>
        <span className="fs-who">{who}</span>
      </summary>
      <div className="fs-body">{children}</div>
    </details>
  );
}

// One .fcard, with the optional delta chip and mini-trend a movable metric carries.
// Deliberately the same visual grammar as the Overview KPI tile (KpiTile.tsx): a
// right-aligned pill above the number, a sparkline under the labels. A flow number
// without either was the complaint that produced this — "15 returned to dev" is not
// something a reader can act on until they know it was 22 last month.
function FTile({ value, label, sub, trend, tip }: {
  value: ReactNode; label: string; sub?: ReactNode; trend?: Trend; tip?: string;
}) {
  const delta = trend?.delta;
  const pts = trend?.sparkPts;
  return (
    <div className="fcard">
      {delta && (
        <div className="fc-top">
          <span className={`dlt ${delta.cls}`} data-tip={delta.tip}>{delta.text}</span>
        </div>
      )}
      <div className="fc-n" data-tip={tip}>{value}</div>
      <div className="fc-l">{label}</div>
      {sub && <div className="fc-s">{sub}</div>}
      {pts && (
        <svg className="spark" viewBox="0 0 100 26" preserveAspectRatio="none" aria-hidden="true">
          <polyline
            points={pts} fill="none" stroke={trend?.sparkColor} strokeWidth={1.6}
            vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round"
          />
        </svg>
      )}
    </div>
  );
}

// The whole PR cycle as one length with its parts inside it — the shape a reader
// expects of a "cycle time" and the one thing the five median cards cannot be
// assembled into, because each of those is measured over a different set of PRs.
//
// The bar's LENGTH is the median total lead time. Its SPLIT is the mean of each PR's
// own share of its own total — never the leg medians used as widths. See
// semantic_metrics.flow_cycle_bar for why: on real data those medians summed to 4.6h
// under a total of 17.5h, so the bar contradicted the line printed beneath it.
function CycleBarPanel({ bar }: { bar: CycleBar }) {
  return (
    <>
      <h3 className="sub">
        How long a change takes end to end{" "}
        <span className="mut">
          — {fmtNum(bar.n)} pull requests that were reviewed and merged, so the parts add
          up to the whole for every one of them
        </span>
      </h3>
      <div className="cyc">
        <div className="cyc-bar">
          {bar.legs.map((l) => (
            <i key={l.key} style={{ width: `${l.pct}%`, background: l.color }}
               data-tip={`${l.label}: ${fmtPct(l.pct)}% of the wait, median ${l.h}`} />
          ))}
        </div>
        <div className="cyc-scale">
          <span>opened</span><span>merged · {bar.medianTotalH}</span>
        </div>
        <div className="cyc-legs">
          {bar.legs.map((l) => (
            <div className="cyc-leg" key={l.key}>
              <span className="sw" style={{ background: l.color }} />
              <div>
                <div className="lh">{fmtPct(l.pct)}%</div>
                <div className="ll">{l.label}</div>
                <div className="ls">{l.sub} · median {l.h}</div>
              </div>
            </div>
          ))}
        </div>
        <div className="cyc-tail">
          <span>typical total <b>{bar.medianTotalH}</b></span>
          <span>slower quarter <b>{bar.p75TotalH}</b></span>
          <span>slowest tenth <b>{bar.p90TotalH}</b></span>
          {bar.draft && (
            <span>of which in draft first <b>{bar.draft.h}</b> ({fmtNum(bar.draft.n)} PRs)</span>
          )}
        </div>
      </div>
      <p className="conc" style={{ marginTop: 8 }}>
        The bar is <b>{bar.medianTotalH}</b> long — the typical total — and the split is
        each pull request's <b>own share of its own total</b>, averaged. Those shares add
        to 100% by construction, which the two leg medians do not: pull requests tend to
        be slow in one leg <i>or</i> the other and rarely both, so each leg's median lands
        among the small values of both groups and adding them understates the journey
        badly. The medians are still printed above, as their own numbers. The{" "}
        <b>slowest tenth</b> is here because a median alone hides the wait anybody
        actually complains about, and draft time is listed rather than stacked because it
        overlaps the first leg instead of preceding it.
      </p>

      {bar.byRepo.length > 1 && (
        <>
          <h3 className="sub" style={{ marginTop: 20 }}>
            The same split per repository{" "}
            <span className="mut">
              — slowest first, {bar.byRepo.length} of {fmtNum(bar.reposTotal)} shown;
              needs ≥{bar.repoMin} reviewed-and-merged PRs
            </span>
          </h3>
          <div className="card flow-tbl" style={{ overflowX: "auto" }}>
            <DataTable<CycleBarRepo & Record<string, unknown>>
              rows={bar.byRepo}
              columns={[
                { label: "Repository", kind: "text", key: "repo" },
                {
                  // `render` rather than kind:"bar" — that kind draws ONE width from a
                  // single key, and this cell is two segments whose split differs per
                  // row. The escape hatch is what the component documents it for.
                  label: "Opened → review → merged", cls: "rb", sortKey: "totalHours",
                  render: (r) => (
                    <div className="rbar" data-tip={`${r.totalH} total`}>
                      <i style={{ width: `${r.ttfrPct}%`, background: bar.legs[0]?.color }} />
                      <i style={{ width: `${r.r2mPct}%`, background: bar.legs[1]?.color }} />
                    </div>
                  ),
                },
                { label: "To review", tip: "Median time until somebody first reviewed one",
                  kind: "raw", key: "ttfrH", sortKey: "ttfrHours" },
                { label: "In review", tip: "Median time from that first review to the merge",
                  kind: "raw", key: "r2mH", sortKey: "r2mHours" },
                { label: "Total", tip: "Median total lead time for this repository",
                  kind: "raw", key: "totalH", sortKey: "totalHours" },
                { label: "PRs", tip: "Reviewed-and-merged PRs this is measured over",
                  kind: "num", key: "n" },
              ]}
            />
          </div>
          <p className="conc">
            Each row's <b>length</b> is its median total measured against the slowest
            repository — not against its own total, or a fast repo and a slow one would
            draw the same picture — and it is divided by that repository's own average
            share, exactly as the bar above is. The two median columns beside it are
            reported, never used as widths.
          </p>
        </>
      )}
    </>
  );
}

// The dim-zero class DataTable puts on a plain-text cell itself, for the cells that
// have to ask: a `render` cell whose value is wrapped in a drill <span> has an
// element child, and an element child is what disqualifies a cell from the rule
// (see DataTable's zeroClass). Passed as a column's `clsOf` so a zero row still
// fades exactly as it did when these tables were hand-rolled.
const zcls = (text: string) => zeroClass(text).trim() || undefined;

// Three lists on this page name the worst individual pull requests — waiting on a
// first review, ignored then closed, biggest open — and all three open with the same
// two columns: the PR link and its author. Declared once, because a divergence
// between them would be an accident rather than a decision. `prLabel` differs only
// because the biggest-PRs table has no heading of its own and uses its first header
// as the title.
function prAndAuthorCols<R extends { repo: string; number: number; login: string }>(
  prLabel: string,
): Column<R>[] {
  return [
    {
      label: prLabel,
      render: (r) => (
        <a className="gh" href={`https://github.com/${r.repo}/pull/${r.number}`}
           target="_blank" rel="noopener">{r.repo}#{r.number}</a>
      ),
    },
    { label: "Author", render: (r) => <GhLink login={r.login} /> },
  ];
}

// Pull requests that were closed without merging. Reads the period, unlike InFlightPanel.
//
// It deliberately does NOT lead with review effort. The dominant reason is authors
// withdrawing their own work after feedback, which is feedback working — so a "wasted
// review" headline would be a number nobody should try to reduce. The bucket that is a
// real problem cost zero reviews: PRs nobody ever looked at before somebody else closed
// them. Hence the ordering here, and no red/waste framing. See the plan's §5.2.
function AbandonedPanel({ ab }: { ab: Abandoned }) {
  if (!ab.closedTotal) {
    return (
      <>
        <h3 className="sub" style={{ marginTop: 24 }}>
          Closed without merging <span className="mut">— nothing closed in this period</span>
        </h3>
      </>
    );
  }
  const days = (d: number | null) => (d === null ? "—" : `${fmtNum(d)}d`);
  const swept = ab.reasons.find((r) => r.key === "swept");
  const unknown = ab.reasons.find((r) => r.key === "unknown");
  return (
    <>
      <h3 className="sub" style={{ marginTop: 24 }}>
        Closed without merging{" "}
        <span className="mut">
          — {fmtNum(ab.n)} of {fmtNum(ab.closedTotal)} pull requests closed in this period ended
          without a merge
        </span>
      </h3>
      <div className="flow-cards">
        <div className="fcard">
          <div className="fc-n dr" data-drill="pr" data-abandon-reason="swept">
            {fmtNum(swept?.n || 0)}
          </div>
          <div className="fc-l">closed unreviewed by someone else</div>
          <div className="fc-s">
            {swept?.n
              ? `nobody ever looked · median ${days(swept.medianLivedD)}, worst ${days(swept.oldestLivedD)}`
              : "nobody in this period"}
          </div>
        </div>
        <div className="fcard">
          <div className="fc-n dr" data-drill="pr" data-pr-state="abandoned">{fmtPct(ab.ratePct ?? 0)}%</div>
          <div className="fc-l">of closures were abandoned</div>
          <div className="fc-s">{fmtNum(ab.n)} abandoned · {fmtNum(ab.merged)} merged</div>
        </div>
        <div className="fcard">
          <div className="fc-n">{fmtNum(ab.unreviewed)}</div>
          <div className="fc-l">closed with no review</div>
          <div className="fc-s">{fmtNum(ab.reviewed)} did get reviewed</div>
        </div>
        <div className="fcard">
          <div className="fc-n dr" data-drill="pr" data-abandon-reason="draft">{fmtNum(ab.drafts)}</div>
          <div className="fc-l">still drafts at close</div>
          <div className="fc-s">usually never meant to land</div>
        </div>
      </div>

      <h3 className="sub" style={{ marginTop: 20 }}>
        Why <span className="mut">— derived from who closed it, not guessed</span>
      </h3>
      <div className="card flow-tbl" style={{ overflowX: "auto" }}>
        <DataTable<AbandonReason>
          rows={ab.reasons.filter((r) => r.n > 0)}
          // Every bucket at zero means nothing here was abandoned at all. The
          // hand-rolled version drew a header row over an empty body, which reads
          // as a table that failed to load rather than as an answer.
          empty="Nothing closed in this period was abandoned."
          columns={[
            {
              label: "Reason",
              render: (r) => <>{r.label} <span className="mut">— {r.sub}</span></>,
            },
            {
              label: "PRs", tip: "Pull requests in this bucket", sortKey: "n",
              // The drill attributes stay on an inner <span class="dr"> rather than
              // moving to the <td> via `drill`: report.css styles the two
              // differently (`.flow-tbl .dr[data-drill]` paints an accent dashed
              // underline under the NUMBER, while `td[data-drill]` would stretch
              // that border across the whole cell and replace the row separator).
              render: (r) => (
                <span className="dr" data-drill="pr" data-abandon-reason={r.key}>{fmtNum(r.n)}</span>
              ),
            },
            {
              label: "Reviews", kind: "num", key: "reviews",
              tip: "Reviews submitted on them. Shown as context — for a withdrawal after feedback this is review working, not review lost.",
            },
            { label: "Median life", tip: "Median time from opening to being closed",
              kind: "num", key: "medianLivedD", unit: "d", dash: true },
            { label: "Longest", tip: "The longest-lived one in this bucket",
              kind: "num", key: "oldestLivedD", unit: "d", dash: true },
          ]}
        />
      </div>
      <p className="conc" style={{ marginTop: 8 }}>
        An author closing their own pull request after feedback is <b>feedback working</b>, not effort
        lost — it is the largest bucket here and is shown as context, with no target attached. The one
        to act on is <b>closed unreviewed by someone else</b>: nobody looked before it was swept up.
        Reviews are never summed across buckets as though they were waste, and none of this feeds the
        Developer score.
        {unknown?.n ? (
          <>
            {" "}
            <b>{fmtNum(unknown.n)}</b> have no reason because no closing event was collected for them —
            timeline events only cover items created since the collection window starts.
          </>
        ) : null}
      </p>

      <div className="arealeg" style={{ marginTop: 10, marginBottom: 4 }}>
        {ab.bands.map((b) => (
          <span key={b.key} className="dsc-legi">
            lived {b.label}: <b>{fmtNum(b.n)}</b>
          </span>
        ))}
      </div>

      {ab.swept.length > 0 && (
        <>
          <h3 className="sub" style={{ marginTop: 20 }}>
            Longest ignored before being closed{" "}
            <span className="mut">— never reviewed, closed by somebody else</span>
          </h3>
          <div className="card flow-tbl" style={{ overflowX: "auto" }}>
            <DataTable<SweptPr>
              rows={ab.swept}
              columns={[
                ...prAndAuthorCols<SweptPr>("PR"),
                { label: "Waited", kind: "num", key: "livedD", unit: "d" },
              ]}
            />
          </div>
        </>
      )}

      {ab.repos.length > 1 && (
        <>
          <h3 className="sub" style={{ marginTop: 20 }}>
            By repository <span className="mut">— most abandoned first</span>
          </h3>
          <div className="card flow-tbl" style={{ overflowX: "auto" }}>
            <DataTable<AbandonRepo>
              rows={ab.repos}
              columns={[
                { label: "Repository", kind: "text", key: "repo" },
                { label: "Abandoned", kind: "num", key: "n" },
                { label: "Reviews on them", kind: "num", key: "reviews" },
                { label: "Closed unreviewed", kind: "num", key: "swept" },
              ]}
            />
          </div>
        </>
      )}
    </>
  );
}

// Work currently open. Two things this panel has to be honest about, because every
// other panel on this page behaves differently:
//  · it ignores the period control entirely (the data is a point-in-time quantity),
//    which has to be said on the panel or it reads as a filtering bug;
//  · it leads with "nobody has looked at it, and for how long" rather than with the
//    total, because that is the half with an owner and an action.
function InFlightPanel({ inf }: { inf: InFlight }) {
  if (!inf.n) {
    return (
      <>
        <h3 className="sub">
          In flight <span className="mut">— open right now, not affected by the period</span>
        </h3>
        <p className="hint">No open pull requests in this scope.</p>
      </>
    );
  }
  const days = (d: number | null) => (d === null ? "—" : `${fmtNum(d)}d`);
  // Drills open an ALL-TIME window: this panel ignores the period, so a drill that
  // silently inherited it would show fewer PRs than the tile that opened it.
  const ALLTIME = "2008-01-01";
  const aging = inf.bands.filter((b) => b.key === "d90" || b.key === "d90p")
    .reduce((s, b) => s + b.n, 0);
  return (
    <>
      {/* The "ignores the period" caveat lives on the section header now, so it is not
          repeated here two lines below itself. */}
      <h3 className="sub">
        In flight{" "}
        <span className="mut">
          — {fmtNum(inf.n)} open pull requests, <b>as of the last refresh</b>
        </span>
      </h3>
      <div className="flow-cards">
        <div className="fcard">
          <div className="fc-n dr" data-drill="pr" data-pr-state="open_unreviewed" data-from={ALLTIME}>
            {fmtNum(inf.unreviewed)}
          </div>
          <div className="fc-l">not reviewed yet</div>
          <div className="fc-s">nobody has looked at these</div>
        </div>
        <div className="fcard">
          <div className="fc-n dr" data-drill="pr" data-pr-state="open" data-from={ALLTIME}>
            {days(inf.medianAgeD)}
          </div>
          <div className="fc-l">median age</div>
          <div className="fc-s">oldest {days(inf.oldestAgeD)} · {fmtNum(inf.n)} open</div>
        </div>
        <div className="fcard">
          <div
            className="fc-n dr" data-drill="pr" data-pr-state="open"
            data-from={ALLTIME} data-to={inf.staleBefore || undefined}
          >
            {fmtNum(aging)}
          </div>
          <div className="fc-l">open over {inf.staleDays} days</div>
          <div className="fc-s">of {fmtNum(inf.n)} open</div>
        </div>
        <div className="fcard">
          <div
            className="fc-n dr" data-drill="pr" data-pr-state="open"
            data-flag="is_draft" data-from={ALLTIME}
          >
            {fmtNum(inf.drafts)}
          </div>
          <div className="fc-l">still drafts</div>
          <div className="fc-s">{fmtPct((100 * inf.drafts) / inf.n)}% of open</div>
        </div>
      </div>
      <div className="arealeg" style={{ marginTop: 10, marginBottom: 4 }}>
        {inf.bands.map((b) => (
          <span key={b.key} className="dsc-legi">
            {b.label}: <b>{fmtNum(b.n)}</b>
          </span>
        ))}
      </div>
      <p className="conc" style={{ marginTop: 8 }}>
        Age is shown in <b>bands</b> rather than as one average, because a single very old PR would
        drag a mean far from where most of the work sits. Open work is <b>not</b> added to any commit,
        LOC or delivery number and does not feed the Developer score — it is work in progress, not
        output.
      </p>

      {inf.people.length > 0 && (
        <>
          <h3 className="sub" style={{ marginTop: 24 }}>
            Who is carrying it{" "}
            <span className="mut">— oldest open PR first; a separate list, not the table above</span>
          </h3>
          <div className="card flow-tbl" style={{ overflowX: "auto" }}>
            {/* Every drill cell here uses `render` rather than the column-level
                `drill`, which would put the data-* attributes on the <td>: the
                overlay binds to either, but report.css does not style them the same
                (`.flow-tbl .dr[data-drill]` underlines the number in accent, a
                `td[data-drill]` would run that dashed border the width of the cell).
                The zero cells then need `clsOf` to keep the dim-zero class the
                <span> child would otherwise cost them. */}
            <DataTable<InFlightPerson>
              rows={inf.people}
              columns={[
                { label: "Person", render: (p) => <GhLink login={p.login} /> },
                {
                  label: "Open", tip: "Pull requests this person has open right now",
                  sortKey: "n", clsOf: (p) => zcls(fmtNum(p.n)),
                  render: (p) => (
                    <span className="dr" data-drill="pr" data-pr-state="open"
                          data-author={p.login} data-from={ALLTIME}>{fmtNum(p.n)}</span>
                  ),
                },
                { label: "Oldest", tip: "Age of their oldest open pull request",
                  kind: "num", key: "oldestAgeD", unit: "d", dash: true },
                {
                  label: "Unreviewed", tip: "Of their open PRs, how many nobody has reviewed yet",
                  sortKey: "unreviewed", clsOf: (p) => zcls(fmtNum(p.unreviewed)),
                  render: (p) => (p.unreviewed ? (
                    <span className="dr" data-drill="pr" data-pr-state="open_unreviewed"
                          data-author={p.login} data-from={ALLTIME}>{fmtNum(p.unreviewed)}</span>
                  ) : fmtNum(p.unreviewed)),
                },
                {
                  label: "Drafts", tip: "Of their open PRs, how many are still drafts",
                  sortKey: "drafts", clsOf: (p) => zcls(fmtNum(p.drafts)),
                  render: (p) => (p.drafts ? (
                    <span className="dr" data-drill="pr" data-pr-state="open" data-flag="is_draft"
                          data-author={p.login} data-from={ALLTIME}>{fmtNum(p.drafts)}</span>
                  ) : fmtNum(p.drafts)),
                },
              ]}
            />
          </div>
          <p className="conc" style={{ marginTop: 8 }}>
            Deliberately its own list rather than a column on the friction table above: that table
            only includes people with items <i>created in the selected period</i>, so somebody whose
            only current activity is one long-running PR would be missing from it — which is exactly
            the case this panel exists to show.
          </p>
        </>
      )}

      {inf.staleUnreviewedN > 0 && (
        <>
          <h3 className="sub" style={{ marginTop: 24 }}>
            Waiting on a first review{" "}
            <span className="mut">
              — {fmtNum(inf.staleUnreviewedN)} open over {inf.staleReviewDays} days with no review,
              longest wait first
            </span>
          </h3>
          <div className="card flow-tbl" style={{ overflowX: "auto" }}>
            <DataTable<StalePr>
              rows={inf.staleUnreviewed}
              columns={[
                ...prAndAuthorCols<StalePr>("PR"),
                { label: "Waiting", kind: "num", key: "ageD", unit: "d" },
              ]}
            />
          </div>
          <p className="conc" style={{ marginTop: 8 }}>
            These are waiting on the team rather than on their authors. Whether a review was ever
            requested cannot be shown: the collector does not record review requests, so that column
            is empty for every pull request — rather than report it as "never asked" and always mean
            100%, it is left out.
          </p>
        </>
      )}

      <h3 className="sub" style={{ marginTop: 24 }}>
        How big the open work is{" "}
        <span className="mut">— typical size, not a total</span>
      </h3>
      <div className="flow-cards">
        <div className="fcard">
          <div className="fc-n">+{fmtNum(inf.size.medianAdditions ?? 0)}</div>
          <div className="fc-l">median PR</div>
          <div className="fc-s">{fmtNum(inf.size.medianFiles ?? 0)} files</div>
        </div>
        <div className="fcard">
          <div className="fc-n">+{fmtNum(inf.size.p90Additions ?? 0)}</div>
          <div className="fc-l">p90 PR</div>
          <div className="fc-s">9 in 10 are smaller than this</div>
        </div>
      </div>
      {inf.size.biggest.length > 0 && (
        <div className="card flow-tbl" style={{ overflowX: "auto", marginTop: 10 }}>
          <DataTable<BigPr>
            rows={inf.size.biggest}
            columns={[
              ...prAndAuthorCols<BigPr>("Biggest open PRs"),
              // `render` for the "+" — `unit` is a suffix, and a diff size reads as
              // "+9,021", not "9,021+". The number itself is still what sorts.
              { label: "Lines", sortKey: "additions", render: (b) => `+${fmtNum(b.additions)}` },
              { label: "Files", kind: "num", key: "files" },
            ]}
          />
        </div>
      )}
      <p className="conc" style={{ marginTop: 8 }}>
        Shown as a <b>median and p90 with the outliers named</b>, never as a sum. A total would
        describe the outliers instead of the work: when this was measured, a single fork-sync pull
        request accounted for 35% of all open additions and the top three for 73%. These are also{" "}
        <b>raw GitHub line counts</b> — pull-request diffs get no vendored/generated filter, unlike
        the commit numbers elsewhere in the report, so treat them as diff size rather than as
        authored code.
      </p>
    </>
  );
}

function CfdChart({ cfd }: { cfd: Cfd }) {
  if (!cfd.hasData) {
    return (
      <p className="hint">
        The board-flow chart needs at least two daily snapshots
        {cfd.firstDate && ` — ${cfd.nDates} captured so far, since ${cfd.firstDate}`}. It fills in as
        snapshots accumulate.
      </p>
    );
  }
  return (
    <>
      <div className="arealeg" style={{ marginBottom: 8 }}>
        {cfd.series.map((s) => (
          <span className="lg" key={s.key}><i style={{ background: s.color }} />{s.name}</span>
        ))}
      </div>
      <div className="areawrap"><VegaChart spec={cfd.spec} waitForFonts /></div>
      <p className="conc">
        Cumulative flow of board items by stage, from daily snapshots since {cfd.firstDate} ({cfd.nDates}{" "}
        days). A widening upper band = growing WIP / backlog; a fattening QA band = a testing bottleneck; a
        steadily rising base = throughput to Done. Forward-only and sampled once a day — GitHub keeps no
        board status history. Depends on the Status&nbsp;→&nbsp;stage mapping in the{" "}
        <a href="/semantic">Taxonomy</a>.
      </p>
    </>
  );
}

function DwellPanel({ dwell }: { dwell: Dwell }) {
  if (!dwell.hasData) {
    return (
      <p className="hint">
        No board timing yet — this reads each item's last-update time, captured from the next collection
        onward{dwell.firstDate && ` (snapshot history since ${dwell.firstDate})`}.
      </p>
    );
  }
  return (
    <>
      <div className="flow-cards" style={{ marginBottom: 12 }}>
        {dwell.ageMedianH != null && (
          <div className="fcard">
            <div className="fc-n">{dwell.ageMedianH}</div>
            <div className="fc-l">median age in current stage</div>
            <div className="fc-s">{fmtNum(dwell.ageN)} items waiting now</div>
          </div>
        )}
        {dwell.ageMaxH != null && (
          <div className="fcard">
            <div className="fc-n">{dwell.ageMaxH}</div>
            <div className="fc-l">longest waiting</div>
            <div className="fc-s">oldest item in a stage</div>
          </div>
        )}
        {dwell.dwellMedianH != null && (
          <div className="fcard">
            <div className="fc-n">{dwell.dwellMedianH}</div>
            <div className="fc-l">median completed dwell</div>
            <div className="fc-s">{fmtNum(dwell.dwellN)} observed moves</div>
          </div>
        )}
      </div>
      {/* `sticky`, not `groups`: this table has nothing to group, but it is wide
          enough to scroll sideways on a narrow screen and the stage name has to stay
          visible while it does — which is the only reason it was hand-rolled as
          <table class="grouped"> before. */}
      <DataTable<DwellStage>
        rows={dwell.stages}
        sticky
        columns={[
          {
            label: "Stage",
            // `render` rather than swatch:"dot"/"edot": the built-in swatches are a
            // 10px rounded square and a 9px round .edot, and this dot is the 8px
            // .dwdot shared with the Delivery panel's stage list. Reproducing it
            // exactly costs one line; a 1px-different dot on one table does not.
            render: (s) => <><span className="dwdot" style={{ background: s.color }} />{s.name}</>,
          },
          {
            label: "Now", align: "num", tip: "items in this stage as of the latest snapshot",
            // An empty stage reads "—", not "0": the count is a snapshot fact, and
            // `dash` cannot express it because the payload normalises the absent
            // count to 0 rather than null. `clsOf` supplies the fade `dash` would
            // have brought with it.
            sortKey: "nCurrent", clsOf: (s) => zcls(s.nCurrent ? fmtNum(s.nCurrent) : "—"),
            render: (s) => (s.nCurrent ? fmtNum(s.nCurrent) : "—"),
          },
          { label: "Median age", align: "num", kind: "raw", key: "ageMedianH", dash: true,
            sortKey: "ageMedianHours",
            tip: "median time current items have sat in this stage (now − last update)" },
          { label: "Median dwell", align: "num", kind: "raw", key: "medianH", dash: true,
            sortKey: "medianHours",
            tip: "median time items historically spent here before moving on" },
          { label: "Moves", align: "num", kind: "num", key: "n",
            tip: "observed moves out of this stage" },
        ]}
      />
      <p className="conc">
        <b>Age</b> — how long current items have sat in their stage (latest snapshot − item's last update);
        available now, no history needed. <b>Dwell</b> — how long items historically spent in a stage before
        moving on, from snapshot diffs. Both time transitions by each item's update time (≈ the actual move);
        it's a close estimate since an update can be any edit, not strictly a status change. Terminal stages
        are excluded from age.
      </p>
    </>
  );
}

function RewindsPanel({ rewinds }: { rewinds: Rewinds }) {
  if (!rewinds.hasHistory) {
    return (
      <p className="hint">
        Needs at least two board snapshots to see movement — only {rewinds.nDates || 0} captured so far
        {rewinds.firstDate && ` (since ${rewinds.firstDate})`}. Fills in as snapshots accumulate.
      </p>
    );
  }
  if (!rewinds.hasEvents) {
    return (
      <p className="conc">
        No items were sent back from QA to development in this period. Reconstructed from board snapshots
        since {rewinds.firstDate} — a same-day round-trip between snapshots can be missed.
      </p>
    );
  }
  return (
    <>
      <div className="flow-cards">
        {/* The one number on this page that says least on its own — a count with no
            denominator and no scale. The period-over-period chip is the whole answer
            to "and what do I do with 15?"; there is deliberately no sparkline, because
            eight buckets of a snapshot diff this thin would be noise wearing a trend's
            clothes (see semantic_metrics.FLOW_DELTA_KEYS). */}
        <div className="fcard fcard-drill" data-drill="rewinds" data-tip="Click for the list of items">
          {rewinds.delta && (
            <div className="fc-top">
              <span className={`dlt ${rewinds.delta.cls}`} data-tip={rewinds.delta.tip}>
                {rewinds.delta.text}
              </span>
            </div>
          )}
          <div className="fc-n">{fmtNum(rewinds.qaToDev)}</div>
          <div className="fc-l">returned to dev from QA</div>
          <div className="fc-s">across {rewinds.ownerCount} owner(s) · click for the list</div>
        </div>
      </div>
      <p className="conc">
        Items pushed backward on the board from testing to development (QA → In progress), reconstructed by
        diffing board snapshots since {rewinds.firstDate}. GitHub keeps no board status-change history, so we
        only see moves between snapshots — treat as a floor
        {rewinds.delta
          ? ", and read the count against the previous period rather than on its own"
          : ""}. Depends on the Status&nbsp;→&nbsp;stage mapping in the{" "}
        <a href="/semantic">Taxonomy</a>.
      </p>
    </>
  );
}

// The by-person friction table. Declared out here rather than inline in the view
// because it is nine columns of mostly tooltips, and because nothing in it depends
// on the render (unlike the in-flight tables, which close over ALLTIME).
const PEOPLE_COLS: Column<Person>[] = [
  { label: "Person", render: (r) => <GhLink login={r.login} /> },
  { label: "Items", kind: "num", key: "items",
    tip: "Issues + PRs this person owns that were created in the period" },
  {
    label: "Friction / item", cls: "fr", sortKey: "friction",
    tip: "2×(back-to-draft + reopened) + review-request & assignment churn, per owned item. Click to see the items.",
    // `render`, for two reasons at once: the drill attributes belong on the inner
    // <span class="dr"> (report.css styles `.flow-tbl .dr[data-drill]`, not a
    // drilled <td>, and the span also carries the per-person friction colour), and
    // the "too few items to score" case is a muted dash rather than a number.
    render: (r) => (r.friction !== null ? (
      <span
        className="dr" data-drill="flowitems" data-author={r.login} data-scope="none"
        style={{ color: r.frictionColor ?? undefined }}
      >
        {r.friction}
      </span>
    ) : (
      <span className="mut">—</span>
    )),
  },
  { label: "Reopened", kind: "pctp", key: "reopenPct",
    tip: "Share of this person's items reopened at least once" },
  { label: "Back to draft", kind: "pctp", key: "bouncePct",
    tip: "Share of this person's PRs sent back to draft at least once" },
  {
    label: "Rework rounds", kind: "num", key: "crRounds",
    tip: "Rework rounds — the count of CHANGES_REQUESTED reviews across their PRs. One PR can be sent back more than once, so this can exceed the number of PRs.",
    // The header tooltip explains the metric; this one reports the row — the same
    // count against the number of PRs it is spread over, which is the question the
    // number itself invites.
    tipOf: (r) => `${r.crRounds} rework round(s) across ${r.crPrs} PR(s) — a PR can be sent back more than once`,
  },
  { label: "Extra reviews", kind: "num", key: "extraReqs",
    tip: "Count of extra review requests across their PRs" },
  // Pre-formatted durations ("38.8h", "2.1d") — kind:"raw" so they are not
  // re-formatted, with the raw hours as the sort key so they do not sort as text.
  { label: "Med lead", kind: "raw", key: "ttmMed", dash: true, sortKey: "ttmMedHours",
    tip: "Median open→merge for this person's merged PRs" },
  { label: "Med to review", kind: "raw", key: "ttfrMed", dash: true, sortKey: "ttfrMedHours",
    tip: "Median open→first-review-request for this person's PRs" },
];

export default function Flow() {
  const { data, error } = useReportData<FlowData>("flow");

  if (error && !data) return <p className="hint" style={{ padding: 24 }}>Could not load the report ({error}).</p>;
  if (!data) return <Loading />;

  const f = data.flow;
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

      <h2 id="flow">
        Flow &amp; friction <span className="period-tag">{periodLabel}</span>
      </h2>
      <div data-period-panel="flow">
        {/* Leads the page, and sits OUTSIDE the hasData gate for two separate reasons.
            It leads because it is the only section with anything to do about it today,
            and a reader who came to unblock somebody should not have to scroll past a
            quarter of process history to find them. It is outside the gate because open
            PRs exist whether or not anything was created in the selected window, and the
            "no lifecycle events yet" hint below must not swallow a real number. */}
        {data.inFlight && (
          <Section
            open
            q="What is open right now, and who is waiting?"
            who="For whoever is running the team this week — this is the only section that ignores the period control, because open work is a point-in-time fact."
          >
            <InFlightPanel inf={data.inFlight} />
          </Section>
        )}

        {!f.hasData ? (
          <p className="hint">
            No lifecycle timeline events collected yet. Run a collection to backfill issue/PR timeline events
            (reopens, back-to-draft, review requests) — Flow metrics appear here once they land.
          </p>
        ) : (
          <>
            <Section
              open
              q="How long does a change take, and where does the time go?"
              who="For the team and whoever is trying to make delivery faster — measured over work created in the selected period."
            >
              {f.cycleBar && <CycleBarPanel bar={f.cycleBar} />}

              <h3 className="sub">
                Cycle-time <span className="mut">— median lifecycle segments, each over whatever
                items have that segment</span>
              </h3>
              <div className="flow-cards">
                {f.cycle.map((c) => (
                  <FTile
                    key={c.key} value={c.h} label={c.label}
                    sub={`${c.sub} · n=${fmtNum(c.n)}`} trend={c}
                  />
                ))}
              </div>
              {/* A segment with no data used to vanish, so a reader could not tell "nothing
                  happened in this window" from "this is never computable" — which is what
                  two of these were for months. Named, not hidden. */}
              {data.cycleMissing && data.cycleMissing.length > 0 && (
                <p className="conc" style={{ marginTop: 8 }}>
                  No data for{" "}
                  {data.cycleMissing.map((m, i) => (
                    <span key={m.key}>
                      {i > 0 ? ", " : ""}<b>{m.label}</b>
                    </span>
                  ))}{" "}
                  in this window — the lifecycle events those need were not recorded for any
                  item here.
                </p>
              )}
              <p className="conc">
                These five are each measured over whichever items happen to have that
                segment, so they belong to different populations and cannot be added
                together — which is exactly why the bar above them fixes one cohort first.
              </p>
            </Section>

            <Section
              q="How much work comes back instead of going forward?"
              who="For a retro, or for anyone asking why delivery feels slower than the throughput suggests. Starts closed — it is a diagnosis, not a daily number."
            >
              {/* A definition, not a finding: worth reading once and then in the way. */}
              <details className="card flow-explain">
                <summary>
                  What “friction” means{" "}
                  <span className="mut">— the formula behind the score, and what feeds it</span>
                </summary>
                <div>
                  <p style={{ marginTop: 0 }}>
                    Friction scores how much <b>rework and churn</b> a person's work items pick up on the way to
                    done — higher means more items bounced backward instead of flowing forward. For every issue
                    or PR they own:
                  </p>
                  <p className="flow-formula">
                    <b>friction / item</b> = 2 × (back-to-draft + reopened) + extra review requests + reassignments
                  </p>
                  <ul>
                    <li><b>back to draft</b> — a PR marked ready, then pulled back to draft (weight&nbsp;×2)</li>
                    <li><b>reopened</b> — an issue or PR closed, then reopened (weight&nbsp;×2)</li>
                    <li><b>extra review requests</b> — review re-requested on the same PR (beyond the first ask)</li>
                    <li><b>reassignments</b> — ownership handed on after the first assignee</li>
                  </ul>
                  <p className="mut" style={{ marginBottom: 0 }}>
                    Lower is smoother — it's the <b>Flow</b> pillar of the <a href="#person">Developer&nbsp;score</a>.
                    Worked example: a PR reopened once and re-reviewed twice&nbsp;= 2×1&nbsp;+&nbsp;1&nbsp;={" "}
                    <b>3</b> friction on that item. All timing here comes from real lifecycle events, not
                    board-column dwell (GitHub keeps no status-change history).
                  </p>
                </div>
              </details>

              <h3 className="sub">
                Flow health{" "}
                <span className="mut">
                  — {fmtNum(f.nItems)} items created in this period · {fmtNum(f.nPrs)} PRs, {fmtNum(f.nIssues)}{" "}
                  issues
                </span>
              </h3>
              <div className="flow-cards">
                <FTile
                  value={`${fmtPct(f.health.crRate)}%`} label="sent back for changes"
                  sub={`${fmtNum(f.health.crPrs)} PRs · ${fmtNum(f.health.crRounds)} rework rounds`}
                  trend={f.healthTrend?.crRate}
                />
                <FTile
                  value={`${fmtPct(f.health.reopenRate)}%`} label="reopened"
                  sub={`${fmtNum(f.health.reopenedN)} items came back`}
                  trend={f.healthTrend?.reopenRate}
                />
                <FTile
                  value={`${fmtPct(f.health.bounceRate)}%`} label="back to draft"
                  sub={`${fmtNum(f.health.bouncedN)} PRs pulled back`}
                  trend={f.healthTrend?.bounceRate}
                />
                <FTile
                  value={`${fmtPct(f.health.rereqRate)}%`} label="re-reviewed"
                  sub={`${fmtNum(f.health.rereqN)} PRs asked again`}
                  trend={f.healthTrend?.rereqRate}
                />
              </div>
              <p className="conc" style={{ marginTop: 8 }}>
                <b>Rework rounds</b> = the number of times a reviewer explicitly submitted a “changes
                requested” review — the closest proxy we have to review→fix cycles (we don't yet track
                the exact comment↔push cadence). A plain comment or an approval does not count. The
                chip on each card compares it with the <b>preceding equal period</b> and the line under
                it splits this period into eight, both from the same items as the number itself; on a
                short period or a thin repo slice expect a few items to move a rate visibly.
              </p>

              <h3 className="sub">
                By person{" "}
                <span className="mut">— owners with ≥{f.minItems} items this period, most friction first</span>
              </h3>
            {f.people.length ? (
              <>
                <div className="card flow-tbl" style={{ overflowX: "auto" }}>
                  {/* `sticky` for the same reason as the time-in-stage table: nine
                      columns scroll sideways, and a friction number is unreadable
                      once the person it belongs to has scrolled off the left. */}
                  <DataTable<Person> columns={PEOPLE_COLS} rows={f.people} sticky />
                </div>
                <p className="conc">
                  Friction/item matches the <b>Flow</b> pillar on each person's score. “Med lead” and “Med to
                  review” are medians over items created in this period; “—” means too few data points to be
                  meaningful.
                </p>
              </>
            ) : (
              <p className="hint">No owner has ≥{f.minItems} items with lifecycle events in this period yet.</p>
            )}
            </Section>

            <Section
              q="How does work move across the board?"
              who="For whoever owns the board and its statuses. The thinnest data on the page — it is reconstructed from daily snapshots, so it fills in over time rather than being available on day one."
            >
              <div className="dsub">Cumulative flow over time</div>
              <div className="card"><CfdChart cfd={f.cfd} /></div>
              <div className="dsub" style={{ marginTop: 14 }}>
                Time in stage <span className="alltime-tag">between statuses</span>
              </div>
              <div className="card"><DwellPanel dwell={f.dwell} /></div>
              <div className="dsub" style={{ marginTop: 14 }}>
                Returned to dev from testing <span className="alltime-tag">QA → dev</span>
              </div>
              <div className="card"><RewindsPanel rewinds={f.rewinds} /></div>
            </Section>
          </>
        )}
        {/* Also outside the hasData branch, for the same reason as the in-flight
            section above: this is windowed by closed_at, not by the created-in-window
            cohort, so it has numbers even when that cohort is empty. */}
        {data.abandoned && (
          <Section
            q="What never landed?"
            who="For anyone auditing where effort went. Read the section itself before reacting — the largest bucket is authors withdrawing their own work after feedback, which is feedback working."
          >
            <AbandonedPanel ab={data.abandoned} />
          </Section>
        )}
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
