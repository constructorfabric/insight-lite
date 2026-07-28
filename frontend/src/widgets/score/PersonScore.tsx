// Person's Developer-score panel (EXPERIMENTAL, behind a collapsed <details>) —
// the SVG gauge + the pillar "score chain" table + the "why you rank here"
// lines + the per-member team board (a <details> per person with a hand-rolled
// `.comp` make-up bar). Promoted verbatim from the in-page copy in
// pages/Person.tsx; emits byte-identical DOM so the pixel gate sees no diff.
// This set is Person-specific but internally cohesive — Overview's team
// scorecard TABLE is a DIFFERENT widget (widgets/score/Scorecard.tsx), NOT
// shared with this one.
//
// NOTE on the board make-up bar: the monolith paints it as a bespoke
// `<span class="comp" data-tip=…>` of flex `<i>` segments (NOT the shared
// components/SegBar, whose DOM is `<span class="segbar">` with a per-segment
// data-tip). Swapping in SegBar here would change the class + data-tip
// structure and break parity, so it is kept verbatim as `.comp`.
import { useState } from "react";

import { fmtNum, jr } from "../../lib/format";

// ---- types (mirror render.person_json's score payload) ---------------------
export type ScorePillars = Record<string, number | null>;
export type ScoreDrivers = {
  commits: number; loc: number; prs_merged: number; prs_opened: number;
  ttm: number | null; size: number | null; rounds: number | null; merge_rate: number | null;
  reviews_given: number; specs: number; flow: number | null; ai_share: number;
};
export type ScoreAbove = {
  name: string; login: string; score: number; gap_total: number; pillar: string | null;
  metric_label: string | null; lower_better: boolean | null; mine: number | null; theirs: number | null;
};
export type VsSelf = { self?: boolean; delta?: number; pillar?: string; metric_label?: string | null;
                       row_val?: number | null; anchor_val?: number | null } | null;
export type ScoreRow = {
  login: string; name: string; score: number; band: string; tone: string;
  pillars: ScorePillars; contributions: Record<string, number | null>; drivers: ScoreDrivers;
  rank: number; above?: ScoreAbove | null; vs_self?: VsSelf;
};
export type ScoreBlock = {
  self: ScoreRow | null; board: ScoreRow[]; weights: Record<string, number>;
  n_eligible: number; n_ranked: number; active_pillars: string[];
  team_medians: Record<string, number | null>; min_activity: number; is_self_view: boolean;
};

// ---- score-specific format helpers -----------------------------------------
function scol(v: number | null): string {
  if (v === null || v === undefined) return "var(--mut)";
  if (v >= 67) return "#10b981";
  if (v >= 45) return "#f59e0b";
  return "#ef4444";
}
const PLABELS: Record<string, [string, string]> = {
  engagement: ["Engagement", "output + reviews & specs"],
  delivery: ["Delivery", "time-to-merge · PR size"],
  craft: ["Craft & rework", "review rounds · merge rate"],
  flow: ["Flow", "forward-flow through stages"],
};
const PCOLOR: Record<string, string> = {
  engagement: "#5b5bf0", delivery: "#06b6d4", craft: "#10b981", flow: "#f59e0b",
};
const PILLAR_ORDER = ["engagement", "delivery", "craft", "flow"];
function nodataMetric(pillar: string): string {
  if (pillar === "flow") return "no flow data";
  if (pillar === "delivery" || pillar === "craft") return "no merged PRs";
  return "no PRs";
}
function fmtPrimary(pillar: string, v: number | null): string {
  if (v === null || v === undefined) return nodataMetric(pillar);
  if (pillar === "delivery") return `${jr(v, 1)}h to merge`;
  if (pillar === "craft") return `${jr(v, 1)} rounds/PR`;
  if (pillar === "flow") return `${jr(v, 2)} friction`;
  return `${fmtNum(v)} commits`;
}
function fmtValScore(pillar: string, v: number | null): string {
  if (v === null || v === undefined) return nodataMetric(pillar);
  if (pillar === "delivery") return `${jr(v, 1)}h`;
  if (pillar === "craft") return `${jr(v, 1)} rounds`;
  if (pillar === "flow") return `${jr(v, 2)}`;
  return `${fmtNum(v)} commits`;
}

function ScoreChain({ row, active, weights, tm, wsum }: {
  row: ScoreRow; active: string[]; weights: Record<string, number>;
  tm: Record<string, number | null>; wsum: number;
}) {
  const dv = row.drivers;
  return (
    <table className="dsc-chain">
      <thead><tr><th>Pillar</th><th>Your real work</th><th>vs team</th><th>Score → pts</th></tr></thead>
      <tbody>
        {PILLAR_ORDER.map((key) => {
          const on = active.includes(key);
          const v = row.pillars[key];
          const pts = row.contributions[key];
          const work = key === "engagement"
            ? `${fmtNum(dv.commits)} commits · ${fmtNum(dv.prs_merged)} PRs · ${fmtNum(dv.reviews_given)} reviews · ${fmtNum(dv.specs)} specs`
            : key === "delivery"
              ? (dv.ttm !== null ? `${jr(dv.ttm, 1)}h to merge · ${Math.round(dv.size ?? 0)} files/PR` : "no merged PRs")
              : key === "craft"
                ? (dv.rounds !== null ? `${jr(dv.rounds, 1)} rounds/PR · ${Math.round(100 * (dv.merge_rate ?? 0))}% merged` : "no PRs opened")
                : (dv.flow !== null ? `${jr(dv.flow, 2)} friction/item` : "no timeline data");
          return (
            <tr key={key} className={`${!on ? "off" : ""}${on && v === null ? " gap" : ""}`.trim() || undefined}>
              <td className="pil">{PLABELS[key][0]}{on && <> <span className="w">{Math.round((100 * weights[key]) / wsum)}%</span></>}</td>
              <td className="work">{work}</td>
              <td className="vs">
                {!on ? <span className="mut">not scored (team data gap)</span>
                  : key === "engagement" ? `team ${fmtNum(tm.prs_merged ?? 0)} PRs`
                    : key === "delivery" ? (tm.ttm !== null && tm.ttm !== undefined ? `team ${jr(tm.ttm, 1)}h` : "—")
                      : key === "craft" ? (tm.rounds !== null && tm.rounds !== undefined ? `team ${jr(tm.rounds, 1)} rounds` : "—")
                        : (tm.flow !== null && tm.flow !== undefined ? `team ${jr(tm.flow, 2)}` : "—")}
              </td>
              <td className="sp">{on ? <><b>{v ?? 0}</b><span className="ar">→ +{pts}</span></> : <span className="mut">—</span>}</td>
            </tr>
          );
        })}
        <tr className="tot"><td colSpan={3}>Score</td><td className="sp"><b>{row.score}</b></td></tr>
      </tbody>
    </table>
  );
}

function WhyRankAbove({ row }: { row: ScoreRow }) {
  const ab = row.above;
  if (!ab) return <p className="dsc-whyrank">Top of the board this window.</p>;
  return (
    <p className="dsc-whyrank">
      Behind <b>#{row.rank - 1} {ab.name}</b> by {ab.gap_total} pt{ab.gap_total !== 1 ? "s" : ""} — mostly{" "}
      <b>{ab.pillar ? PLABELS[ab.pillar][0] : ""}</b>
      {ab.pillar && ab.pillar !== "engagement" && (
        <>: {ab.mine === null ? `you: ${nodataMetric(ab.pillar)}` : `you ${fmtPrimary(ab.pillar, ab.mine)}`} vs{" "}
          {ab.theirs === null ? `them: ${nodataMetric(ab.pillar)}` : `their ${fmtPrimary(ab.pillar, ab.theirs)}`}</>
      )}.
    </p>
  );
}

function VsSelfLine({ v, you }: { v: VsSelf; you: string }) {
  if (!v) return null;
  if (v.self) return <p className="dsc-whyrank dsc-selfrow">This is {you}.</p>;
  const delta = v.delta ?? 0;
  const dir = delta > 0 ? "Ahead of" : delta < 0 ? "Behind" : "Level with";
  return (
    <p className="dsc-whyrank">
      {dir} {you}
      {delta ? ` by ${Math.abs(delta)} pt${Math.abs(delta) !== 1 ? "s" : ""}` : ""}
      {v.pillar && (
        <> — mostly <b>{PLABELS[v.pillar][0]}</b>
          {v.pillar !== "engagement" && v.metric_label &&
            <>: they {fmtValScore(v.pillar, v.row_val ?? null)}, {you} {fmtValScore(v.pillar, v.anchor_val ?? null)}</>}
        </>
      )}.
    </p>
  );
}

export function PersonScore({ score, login }: { score: ScoreBlock; login: string }) {
  // The board renders everyone but shows only the top 15 until this flips. The
  // monolith did it with a delegated click listener in report.j2's inline script
  // that stripped `.capped` and removed the button; that listener never came
  // across with the markup, so on the React page the button sat there inert. The
  // pixel gate could not have caught it — a screenshot does not click. Local
  // state gives the same one-way reveal without reaching into the DOM.
  const [showAll, setShowAll] = useState(false);
  const capped = score.board.length > 15 && !showAll;
  const sc = score.self;
  const active = score.active_pillars && score.active_pillars.length
    ? score.active_pillars : PILLAR_ORDER;
  const wsum = active.reduce((s, k) => s + (score.weights[k] || 0), 0) || 1;
  const you = score.is_self_view ? "you" : login;
  const C = 326.726;
  return (
    <details className="dsc">
      <summary>
        <span className="dsc-exp">Experimental</span> Developer score{" "}
        <span className="mut">— compound, org-relative · click to open</span>
      </summary>
      <div className="dsc-body">
        {sc && sc.score !== null && sc.score !== undefined ? (
          <div className="dsc-top">
            <div className="dsc-gauge">
              <svg width="128" height="128" viewBox="0 0 128 128">
                <circle cx="64" cy="64" r="52" fill="none" stroke="var(--panel2)" strokeWidth="11" />
                <circle
                  cx="64" cy="64" r="52" fill="none" stroke={scol(sc.score)} strokeWidth="11"
                  strokeLinecap="round" transform="rotate(-90 64 64)"
                  strokeDasharray={C} strokeDashoffset={jr(C * (1 - sc.score / 100), 1)}
                />
                <text x="64" y="60" textAnchor="middle" className="dsc-val">{sc.score}</text>
                <text x="64" y="78" textAnchor="middle" className="dsc-of">/ 100</text>
              </svg>
              <div className="dsc-band" style={{ color: scol(sc.score) }}>{sc.band}</div>
              <div className="mut" style={{ fontSize: "11.5px" }}>#{sc.rank} of {score.n_ranked || score.n_eligible}</div>
            </div>
            <div className="dsc-pillars">
              <WhyRankAbove row={sc} />
              <ScoreChain row={sc} active={active} weights={score.weights} tm={score.team_medians} wsum={wsum} />
              <div className="dsc-ctx">
                <span className="dsc-chip">AI leverage {sc.drivers.ai_share}%</span> share of AI-marked commits — context, not scored.
              </div>
            </div>
          </div>
        ) : (
          <p className="conc" style={{ marginTop: 0 }}>
            <b>{login}</b> had under {score.min_activity} commits+PRs in the selected period, so no individual
            score is computed for them here. The team ranking below still applies.
          </p>
        )}

        {score.board && score.board.length > 0 && (
          <div className="dsc-board">
            <div className="dsc-board-h">
              Team <span className="mut">— everyone active is ranked, and each row is measured against {you}:
                click to see how to catch up (above) or where {you} lead{!score.is_self_view ? "s" : ""} (below).
                The bar shows what each score is made of.</span>
            </div>
            <div className="dsc-leg">
              {PILLAR_ORDER.filter((k) => active.includes(k)).map((k) => (
                <span key={k} className="dsc-legi"><i style={{ background: PCOLOR[k] }} />{PLABELS[k][0]}</span>
              ))}
            </div>
            <div className={`dsc-rows${capped ? " capped" : ""}`}>
              {score.board.map((r) => (
                <details key={r.login} className={`dsc-drow${r.login === login ? " me" : ""}`}>
                  <summary>
                    <span className="rk">{r.rank}</span>
                    <span className="nm">{r.name}</span>
                    <span
                      className="comp"
                      data-tip={`score ${r.score} = ${PILLAR_ORDER
                        .filter((k) => active.includes(k) && r.contributions[k])
                        .map((k) => `${PLABELS[k][0]} ${r.contributions[k]}`).join(" + ")}`}
                    >
                      {PILLAR_ORDER.map((k) => {
                        const pts = r.contributions[k];
                        return active.includes(k) && pts
                          ? <i key={k} style={{ flex: pts, background: PCOLOR[k] }} /> : null;
                      })}
                    </span>
                    <span className="sc">{r.score}</span>
                  </summary>
                  <div className="dsc-drow-body">
                    <VsSelfLine v={r.vs_self ?? null} you={you} />
                    <ScoreChain row={r} active={active} weights={score.weights} tm={score.team_medians} wsum={wsum} />
                  </div>
                </details>
              ))}
            </div>
            {capped && (
              // data-dsc-showall is kept so the monolith's delegated listener still
              // works on the ?legacy=1 fallback, which renders the same markup.
              <button type="button" className="dsc-showall" data-dsc-showall
                      onClick={() => setShowAll(true)}>Show all {score.board.length}</button>
            )}
          </div>
        )}

        <p className="conc" style={{ marginTop: 12 }}>
          <b>Experimental v0.</b> Each signal is a percentile within the {score.n_eligible} people active this
          window (≥{score.min_activity} commits+PRs); pillars are averaged, weighted, and normalised.{" "}
          <b>Everyone active is ranked</b> — a scored pillar you have no data for (e.g. no PRs opened) counts as{" "}
          <b>0</b>, a real minus, rather than dropping you from the board. A pillar with too little data across the
          team (a collection gap, not a person's shortfall) is shown <i>not scored</i> and left out for everyone.
          It's a transparent heuristic to calibrate against outcomes — not a verdict, and no ML. Known proxies: no
          true code-complexity signal, and quality is read from review rounds / merge rate, not blame.
        </p>
      </div>
    </details>
  );
}
