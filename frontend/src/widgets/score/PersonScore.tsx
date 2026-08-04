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
import { Activity, ChevronDown, ChevronRight, Timer, Waves, Wrench } from "lucide-react";
import { Fragment, useState } from "react";

import { fmtNum, jr } from "../../lib/format";
import { PILLAR_COLORS, token } from "../../lib/tokens";

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
export type PillarBand = { band: string; tone: string } | null;
export type ScoreRow = {
  login: string; name: string; score: number; band: string; tone: string;
  pillars: ScorePillars; pillar_bands?: Record<string, PillarBand>;
  contributions: Record<string, number | null>; drivers: ScoreDrivers;
  rank: number; above?: ScoreAbove | null; vs_self?: VsSelf;
};
// store.score_band_spec(): the band scale, ascending by floor. The gauge must draw its
// boundaries at exactly the thresholds the labels come from, so it reads them rather
// than repeating 45/60/75 on this side.
export type BandStop = { min: number; band: string; tone: string };
// store.score_signal_spec(): which pillar a signal feeds, which way is better, and how
// to print it. Sent by the server precisely so this file does not keep its own copy —
// the direction is not derivable from anything else the client can see.
export type ScoreSignal = {
  pillar: string; key: string; label: string; fmt: string; higher_is_better: boolean;
};
export type ScoreBlock = {
  self: ScoreRow | null; board: ScoreRow[]; weights: Record<string, number>;
  n_eligible: number; n_ranked: number; active_pillars: string[];
  team_medians: Record<string, number | null>; min_activity: number; is_self_view: boolean;
  signals?: ScoreSignal[]; bands_scale?: BandStop[];
};

// A colour per band STEP, taken from the band's position in the scale — not from its tone.
// tone says how severe a band is, and there are four bands over three tones: Strong and
// Solid are both "good". Colouring by tone therefore painted two different bands the same
// green, which put two identical dots in the distribution key and made the strip read as
// three steps instead of four. A scale needs one colour per step. All four are text-safe
// semantic tokens; --c-pr carries the step between warn and good.
const BAND_RAMP = [token["bad"], token["warn"], token["c-pr"], token["good"]];
function bandColor(i: number, n: number): string {
  if (i < 0 || n <= 0) return "var(--mut)";
  return BAND_RAMP[Math.round((i * (BAND_RAMP.length - 1)) / Math.max(1, n - 1))];
}
function bandIndex(band: string | undefined, scale: BandStop[]): number {
  return scale.findIndex((b) => b.band === band);
}

// ---- score-specific format helpers -----------------------------------------
const PLABELS: Record<string, [string, string]> = {
  engagement: ["Engagement", "output + reviews & specs"],
  delivery: ["Delivery", "time-to-merge · PR size"],
  craft: ["Craft & rework", "review rounds · merge rate"],
  flow: ["Flow", "forward-flow through stages"],
};
const PCOLOR = PILLAR_COLORS;
// An icon per pillar. In the reference layout these do most of the glanceable work — the
// eye lands on a shape before it reads a word — and they are what a row of small grey
// text cannot do.
const PICON: Record<string, typeof Activity> = {
  engagement: Activity, delivery: Timer, craft: Wrench, flow: Waves,
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

// toFixed, not the jr() used everywhere else, and deliberately. jr() exists for byte
// parity with the Jinja path: it appends ".0" to a whole number and otherwise prints
// whatever String() gives, so jr(1, 2) is "1.0" while jr(5.18, 2) is "5.18". Down a
// column of the same metric that reads as two different formats — "1.0" against "5.18",
// "0.0" against "0.175" — which is the exact sloppiness this table was rebuilt to remove.
// These factor columns have no Jinja counterpart to stay parity-identical with, so they
// pad to a fixed width instead. jr() itself must not change: every other number in the
// product is rendered through it.
function fixed(v: number, p: number): string {
  return v.toFixed(p);
}
function fmtSignal(fmt: string, v: number | null): string {
  if (v === null || v === undefined) return "—";
  if (fmt === "int") return fmtNum(v);
  if (fmt === "hours") return `${fixed(v, 1)}h`;
  if (fmt === "pct01") return `${fixed(100 * v, 1)}%`;
  if (fmt === "f1") return fixed(v, 1);
  if (fmt === "f2") return fixed(v, 2);
  if (fmt === "f3") return fixed(v, 3);
  return String(v);
}

// A factor's standing as a PHRASE, not a bare ratio. "0.4x" makes the reader do the work:
// they have to remember that lower is better here, invert it, and only then know it is
// good. "2.4x better" says it once. The factor of improvement is always stated the way
// round that makes it a number above 1, and the direction is carried by the word rather
// than by colour alone — which also stops colour being the only encoding.
export function verdict(mine: number | null, med: number | null, higherIsBetter: boolean):
  { text: string; good: boolean | null } {
  if (mine === null || med === null) return { text: "—", good: null };
  if (mine === med) return { text: "at the team median", good: true };
  const better = higherIsBetter ? mine > med : mine < med;
  // A zero has no finite ratio, and "better than the team" said nothing while looking
  // out of place beside "2.4x better". Each zero has a fact worth stating instead, and
  // none of them should read as missing data — the value is right there in the column.
  if (mine === 0) {
    return better
      ? { text: "best possible", good: true }      // e.g. zero friction, where lower is better
      : { text: "none at all", good: false };      // e.g. no reviews given
  }
  if (med === 0) return { text: "team median is 0", good: better };
  const hi = Math.max(mine, med);
  const lo = Math.min(mine, med);
  return { text: `${fixed(hi / lo, 1)}× ${better ? "better" : "worse"}`, good: better };
}

function FactorRows({ row, tm, signals }: {
  row: ScoreRow; tm: Record<string, number | null>; signals: ScoreSignal[];
}) {
  const dv = row.drivers as unknown as Record<string, number | null>;
  return (
    <table className="dsc-fac">
      <thead>
        <tr><th>Factor</th><th>You</th><th>Team</th><th>Standing</th></tr>
      </thead>
      <tbody>
        {signals.map((s) => {
          const mine = dv[s.key] ?? null;
          const med = tm[s.key] ?? null;
          const v = verdict(mine, med, s.higher_is_better);
          // --good / --bad, not the chart fills --c-story / --c-bug. tokens.json is explicit
          // that those are FILLS which measured 3.76:1 as type, which is why --c-bug-fg
          // exists at all; this column is type, and --good/--bad declare text_on panel.
          const col = v.good === null ? "var(--mut)" : v.good ? token["good"] : token["bad"];
          return (
            <tr key={s.key}>
              <td className="fn">
                {s.label}
                <span className="dir">{s.higher_is_better ? "higher is better" : "lower is better"}</span>
              </td>
              <td>{fmtSignal(s.fmt, mine)}</td>
              <td>{fmtSignal(s.fmt, med)}</td>
              <td className="fr" style={{ color: col }}>{v.text}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// How many of the pillars scored THIS WINDOW the person actually has data for. A missing
// active pillar counts as zero in the score, which is deliberate — "didn't ship" is a real
// minus — but on a table a manager reads it makes a data gap look like a result, so the
// count is shown and the band is withheld.
function coverage(row: ScoreRow, active: string[]): number {
  return active.filter((p) => row.pillars[p] !== null && row.pillars[p] !== undefined).length;
}

// The pillar breakdown, led by the arithmetic rather than by prose. The previous version
// showed the same numbers, but a wide free-text "your real work" column dominated the row
// and the weight was a 10px superscript — read left to right it argued the opposite of the
// score, which is exactly how a reviewer concluded the total was unexplained. Weight,
// percentile and points now each own a column, and the total row states the sum.
function Ingredients({ row, active, weights, tm, wsum, signals, scale }: {
  row: ScoreRow; active: string[]; weights: Record<string, number>;
  tm: Record<string, number | null>; wsum: number; signals: ScoreSignal[];
  scale: BandStop[];
}) {
  // One pillar open at a time, and the whole row is the control. <details> put the
  // toggle inside the drill, i.e. under the row it belongs to, which reads as a stray
  // link; the reference puts a chevron on the row and opens it in place.
  const [open, setOpen] = useState<string | null>(null);
  const order = PILLAR_ORDER.slice().sort((a, b) => (weights[b] || 0) - (weights[a] || 0));
  const parts = order.filter((k) => active.includes(k)).map((k) => row.contributions[k] ?? 0);
  return (
    <>
    <table className="dsc-ing">
      <tbody>
        {order.map((key) => {
          const on = active.includes(key);
          const v = row.pillars[key];
          const pb = row.pillar_bands?.[key] ?? null;
          const sigs = signals.filter((s) => s.pillar === key);
          const canOpen = on && sigs.length > 0;
          const isOpen = open === key;
          const Icon = PICON[key];
          return (
            <Fragment key={key}>
              <tr
                className={`${!on ? "off" : ""}${on && v === null ? " gap" : ""}${canOpen ? " can" : ""}`.trim() || undefined}
                onClick={canOpen ? () => setOpen(isOpen ? null : key) : undefined}
              >
                <td className="pico"><span style={{ color: PCOLOR[key] }}><Icon size={17} /></span></td>
                <td className="pil">
                  <span className="w" style={{ color: PCOLOR[key] }}>
                    {on ? `${Math.round((100 * weights[key]) / wsum)}% of score` : "not scored"}
                  </span>
                  <span className="nm">{PLABELS[key][0]}</span>
                  <span className="mut"> · {sigs.length} factor{sigs.length === 1 ? "" : "s"}</span>
                </td>
                <td className="bd">
                  {/* A pillar the whole team lacks is a collection gap, not this person's
                      shortfall, and one THEY lack is a real zero. Neither gets a band. */}
                  {pb
                    ? <span className="dsc-pill"><i style={{ background: bandColor(bandIndex(pb.band, scale), scale.length) }} />{pb.band}</span>
                    : <span className="mut">{on ? nodataMetric(key) : "team data gap"}</span>}
                </td>
                <td className="pt">{on ? <><b>{row.contributions[key] ?? 0}</b> <span className="u">pts</span></> : "—"}</td>
                <td className="cv">{canOpen && (isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />)}</td>
              </tr>
              {canOpen && isOpen && (
                // Its own full-width row: inside a cell the factor table inherits that
                // column's width and wraps every label onto three lines.
                <tr className="drill">
                  <td colSpan={5}>
                    <div className="dsc-drill-in">
                      <span className="dsc-drill-h">
                        {PLABELS[key][0]} sits at the <b>{v ?? 0}th percentile</b> of the team.
                        What that is made of:
                      </span>
                      <FactorRows row={row} tm={tm} signals={sigs} />
                    </div>
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
    <p className="dsc-sum">{parts.join(" + ")} = <b>{row.score}</b></p>
    </>
  );
}

// The headline: one number, its band, and where it sits on the scale. A horizontal scale
// rather than the donut it replaces, because the thing worth seeing is not "61 out of 100"
// — a ring shows that — but WHICH band you are in and how far the next one is. On an
// ordinary panel; see report.css for why it is not the dark card the reference uses.
// Deliberately no "what's changed" affordance yet: the delta it would open does not exist,
// and this file already carries the scar of a button that shipped inert.
function Hero({ row, n, scale }: { row: ScoreRow; n: number; scale: BandStop[] }) {
  const stops = scale.length ? scale : [{ min: 0, band: row.band, tone: row.tone }];
  const col = bandColor(bandIndex(row.band, stops), stops.length);
  return (
    <div className="dsc-hero">
      <div className="dsc-hero-h">
        <span className="dsc-exp">Experimental</span>
        <span className="mut">org-relative · this window</span>
      </div>
      <div className="dsc-hero-n">
        <b>{row.score}</b>
        <span className="of">of 100</span>
        <span className="dsc-pill"><i style={{ background: col }} />{row.band}</span>
      </div>
      <div className="dsc-scale">
        {/* Filled to the score in the band's colour, the rest left neutral. Painting all
            four bands across the whole bar made a traffic light out of a scale, and put
            three saturated colours on screen to say one thing. */}
        <span className="dsc-scale-t">
          <i style={{ width: `${row.score}%`, background: col }} />
          {stops.slice(1).map((s) => <u key={s.min} style={{ left: `${s.min}%` }} />)}
        </span>
        <span className="dsc-scale-m"><b style={{ left: `${row.score}%`, background: col }} /></span>
        <span className="dsc-scale-k">
          {[...stops.map((s) => s.min), 100].map((v) => (
            <em key={v} style={{ left: `${v}%` }}>{v}</em>
          ))}
        </span>
      </div>
      <div className="dsc-hero-f">
        <span>Rank <b>{row.rank}</b> of <b>{n}</b> scored</span>
        <span>Median of the {n} is <b>50</b></span>
      </div>
    </div>
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
  // Absent only on a payload from before the spec was sent; the pillar rows still
  // render, they just have nothing to drill into.
  const signals = score.signals ?? [];
  const you = score.is_self_view ? "you" : login;
  // No outer disclosure: the panel has its own route in the person sub-nav, so the
  // <details> was one click to reveal the only thing on the page, behind a summary that
  // repeated what the score already says. The Experimental chip and the
  // "org-relative · this window" caveat both live in the hero now.
  return (
    <div className="dsc">
      <div className="dsc-body">
        {sc && sc.score !== null && sc.score !== undefined ? (
          <>
            <Hero row={sc} n={score.n_ranked || score.n_eligible} scale={score.bands_scale ?? []} />
            <div className="dsc-card">
              <div className="dsc-card-h">
                <h3>Score ingredients</h3>
                <p>What the score is made of. Open a pillar to see its factors against the team.</p>
              </div>
              <Ingredients row={sc} active={active} weights={score.weights} tm={score.team_medians} wsum={wsum} signals={signals} scale={score.bands_scale ?? []} />
              {/* Inside the card, as its footer. Loose under it, this read as debris — and
                  .dsc-ctx is a flex row, so WhyRankAbove's <p> broke the line in the
                  middle of the AI-leverage sentence. */}
              <div className="dsc-foot">
                <WhyRankAbove row={sc} />
                <p>
                  <span className="dsc-chip">AI leverage {sc.drivers.ai_share}%</span>
                  {" "}share of AI-marked commits — context, not scored.
                </p>
              </div>
            </div>
          </>
        ) : (
          <p className="conc" style={{ marginTop: 0 }}>
            <b>{login}</b> had under {score.min_activity} commits+PRs in the selected period, so no individual
            score is computed for them here. The team ranking below still applies.
          </p>
        )}

        {score.board && score.board.length > 0 && (() => {
          const nAct = active.length;
          const thin = score.board.filter((r) => coverage(r, active) < nAct);
          const lowest = (score.bands_scale ?? [])[0]?.band;
          const thinLow = thin.filter((r) => r.band === lowest).length;
          // Band counts, best first — the same order the scale is drawn in the hero, read
          // right to left. Computed here because the board is the only place that knows.
          const bscale = score.bands_scale ?? [];
          const counts = bscale.slice().reverse().map((b) => ({
            ...b, n: score.board.filter((r) => r.band === b.band).length,
            col: bandColor(bandIndex(b.band, bscale), bscale.length),
          })).filter((b) => b.n > 0);
          return (
          <div className="dsc-card" style={{ marginTop: "var(--space-4)" }}>
            <div className="dsc-card-h">
              <h3>Team standing</h3>
              <p>
                Everyone with at least {score.min_activity} commits and PRs this window, ranked.
                A percentile only means something inside this window and scope, so these scores
                compare to each other and to nothing else. Open a row to see how {you}{" "}
                compare{score.is_self_view ? "" : "s"}.
              </p>
            </div>

            {counts.length > 0 && (
              <div className="dsc-dist">
                <span className="dsc-dist-t">
                  {counts.map((b) => (
                    <i key={b.band} style={{ width: `${(100 * b.n) / score.board.length}%`,
                                             background: b.col }} />
                  ))}
                </span>
                <span className="dsc-dist-k">
                  {counts.map((b) => (
                    <em key={b.band}><i style={{ background: b.col }} />{b.band} <b>{b.n}</b></em>
                  ))}
                </span>
              </div>
            )}

            <div className="dsc-leg">
              {PILLAR_ORDER.filter((k) => active.includes(k)).map((k) => (
                <span key={k} className="dsc-legi"><i style={{ background: PCOLOR[k] }} />{PLABELS[k][0]}</span>
              ))}
            </div>
            <div className={`dsc-rows${capped ? " capped" : ""}`}>
              {score.board.map((r) => {
                const cov = coverage(r, active);
                const partial = nAct > 0 && cov < nAct;
                return (
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
                    <span className="bnd">
                      {/* No band on partial coverage: a score built from one pillar out of
                          four is a data gap wearing the costume of a result, and this is the
                          table where that gets acted on. */}
                      {partial
                        ? <span className="mut">not banded</span>
                        : <span className="dsc-pill"><i style={{ background: bandColor(bandIndex(r.band, bscale), bscale.length) }} />{r.band}</span>}
                    </span>
                    <span className={`cov${partial ? " thin" : ""}`} data-tip={`data for ${cov} of the ${nAct} pillars scored this window`}>
                      {cov}/{nAct}
                    </span>
                  </summary>
                  <div className="dsc-drow-body">
                    <VsSelfLine v={r.vs_self ?? null} you={you} />
                    <Ingredients row={r} active={active} weights={score.weights} tm={score.team_medians} wsum={wsum} signals={signals} scale={score.bands_scale ?? []} />
                  </div>
                </details>
                );
              })}
            </div>
            {capped && (
              // data-dsc-showall is kept so the monolith's delegated listener still
              // works on the ?legacy=1 fallback, which renders the same markup.
              <button type="button" className="dsc-showall" data-dsc-showall
                      onClick={() => setShowAll(true)}>Show all {score.board.length}</button>
            )}

            <div className="dsc-gaps">
              <b>What this table does not know.</b>{" "}
              {thin.length > 0 && (
                <>
                  <b>{thin.length}</b> of the {score.board.length} have no data for at least one
                  pillar scored this window{thinLow > 0 && <> and <b>{thinLow}</b> of those land in {lowest}</>}
                  {" "}— a missing pillar counts as zero, so the score is a data gap and not a
                  result. Those rows are left unbanded.{" "}
                </>
              )}
              Anyone under <b>{score.min_activity}</b> commits and PRs is not scored at all and is
              absent from this table, so a name that is not here is not a name that is fine.
            </div>
          </div>
          );
        })()}

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
    </div>
  );
}
