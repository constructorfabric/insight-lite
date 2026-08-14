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

import { Help } from "../../components/FilterBar";
import { Fragment, useState } from "react";

import { fmtNum, jr } from "../../lib/format";
import { PILLAR_COLORS } from "../../lib/tokens";
import { bandColor } from "./bands";

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
// store.score_delta: the move split into the part that is the person's and the part that is
// the team moving around them. The score is a percentile, so both happen, and reporting one
// number would make a claim about the wrong one.
export type ScoreDelta = {
  prev: number; now: number; total: number; team: number; you: number;
  /** The window compared against — a delta that does not name it is not a comparison. */
  since?: string; until?: string;
  pillars: Record<string, { prev: number | null; now: number | null;
                            prev_points: number | null; now_points: number | null }>;
};
export type ScoreRow = {
  login: string; name: string; score: number; band: string; tone: string;
  pillars: ScorePillars; pillar_bands?: Record<string, PillarBand>;
  contributions: Record<string, number | null>; drivers: ScoreDrivers;
  rank: number; above?: ScoreAbove | null; vs_self?: VsSelf;
  /** Total move against the previous window; null when they were not scored then. */
  delta?: number | null;
  /** The pillars THIS person's score is a mean of, and the ones dropped from it because
      we collected no data (store._SCORE_GAP_PILLARS). The weight share printed beside a
      pillar is only true against `scored_on`, and a pillar in `weight_gaps` has to read
      as "not measured" — it contributed nothing, and it cost nothing either. Optional:
      a payload from before this existed simply has every active pillar scored. */
  scored_on?: string[]; weight_gaps?: string[];
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
  signals?: ScoreSignal[]; bands_scale?: BandStop[]; delta?: ScoreDelta | null;
};

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
// Heaviest pillar first, and derived in ONE place. The change table used PILLAR_ORDER while
// the ingredients table sorted by weight, so one screen listed the same four pillars in two
// different orders — Engagement first at the top, Flow first below it.
function byWeight(weights: Record<string, number>): string[] {
  return PILLAR_ORDER.slice().sort((a, b) => (weights[b] || 0) - (weights[a] || 0));
}
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

function FactorRows({ row, tm, signals, whose }: {
  row: ScoreRow; tm: Record<string, number | null>; signals: ScoreSignal[];
  /** Whose values these are. Ingredients renders for the page's subject AND for every team
      row, so a hardcoded "You" labelled bob's numbers as yours on alice's page. */
  whose: string;
}) {
  const dv = row.drivers as unknown as Record<string, number | null>;
  return (
    <table className="dsc-fac">
      <thead>
        <tr><th>Factor</th><th>{whose}</th><th>Team</th><th>Standing</th></tr>
      </thead>
      <tbody>
        {signals.map((s) => {
          const mine = dv[s.key] ?? null;
          const med = tm[s.key] ?? null;
          const v = verdict(mine, med, s.higher_is_better);
          // --good / --bad, not the chart fills --c-story / --c-bug. tokens.json is explicit
          // that those are FILLS which measured 3.76:1 as type, which is why --c-bug-fg
          // exists at all; this column is type, and --good/--bad declare text_on panel.
          const col = v.good === null ? "var(--mut)" : v.good ? "var(--good)" : "var(--bad)";
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

// How many of the pillars THEY are scored on the person actually has data for. A missing
// one counts as zero in the score, which is deliberate — "didn't ship" is a real minus —
// but on a table a manager reads it makes a data gap look like a result, so the count is
// shown and the band is withheld.
//
// Measured against `scored_on`, not `active`: a pillar renormalised away for want of data
// is no longer a hole in their score, so counting it as one would withhold a band from
// somebody whose score is an honest mean of everything we could measure. `scored` below
// is that person's denominator, and the two have to be read as a pair.
function scored(row: ScoreRow, active: string[]): string[] {
  return row.scored_on ?? active;
}
function coverage(row: ScoreRow, active: string[]): number {
  return scored(row, active).filter((p) => row.pillars[p] !== null && row.pillars[p] !== undefined).length;
}

// How many people sit in each band, best first — the order the scale is drawn in, read right
// to left. Only rows with COMPLETE coverage: a partial row carries a band in the payload but
// the table withholds it and prints "not banded", so counting it by its payload band put it in
// two places at once. On production that read "Strong 6, Solid 17, Developing 17, Building 6,
// Not banded 4" — fifty entries for forty-six people, with three thin rows inflating Building.
// Caught by CodeRabbit on #7, and the mechanism was already written down three lines above the
// bug. Exported for the test, because the invariant worth pinning is arithmetic: the bands plus
// the not-banded must be the board, exactly once each.
export function bandCounts(board: ScoreRow[], scale: BandStop[], active: string[]) {
  const full = board.filter((r) => coverage(r, active) === scored(r, active).length);
  return scale.slice().reverse()
    .map((b) => ({ ...b, n: full.filter((r) => r.band === b.band).length,
                   col: bandColor(bandIndex(b.band, scale), scale.length) }))
    .filter((b) => b.n > 0);
}

// The pillar breakdown, led by the arithmetic rather than by prose. The previous version
// showed the same numbers, but a wide free-text "your real work" column dominated the row
// and the weight was a 10px superscript — read left to right it argued the opposite of the
// score, which is exactly how a reviewer concluded the total was unexplained. Weight,
// percentile and points now each own a column, and the total row states the sum.
function Ingredients({ row, active, weights, tm, wsum, signals, scale, whose }: {
  row: ScoreRow; active: string[]; weights: Record<string, number>;
  tm: Record<string, number | null>; wsum: number; signals: ScoreSignal[];
  scale: BandStop[]; whose: string;
}) {
  // One pillar open at a time, and the whole row is the control. <details> put the
  // toggle inside the drill, i.e. under the row it belongs to, which reads as a stray
  // link; the reference puts a chevron on the row and opens it in place.
  const [open, setOpen] = useState<string | null>(null);
  const order = byWeight(weights);
  // This person's own denominator. A pillar we have no data for is renormalised away
  // rather than scored 0, so its weight is redistributed — printing the team-wide share
  // here would describe an arithmetic the score did not use.
  const gaps = new Set(row.weight_gaps ?? []);
  const mine = row.scored_on ?? active;
  const msum = mine.reduce((s, k) => s + (weights[k] || 0), 0) || wsum;
  const parts = order.filter((k) => mine.includes(k)).map((k) => row.contributions[k] ?? 0);
  return (
    <>
    <table className="dsc-ing">
      <tbody>
        {order.map((key) => {
          const on = active.includes(key);
          const nodata = gaps.has(key);            // active for the team, no reading here
          const v = row.pillars[key];
          const pb = row.pillar_bands?.[key] ?? null;
          const sigs = signals.filter((s) => s.pillar === key);
          const canOpen = on && !nodata && sigs.length > 0;
          const isOpen = open === key;
          const Icon = PICON[key];
          return (
            <Fragment key={key}>
              <tr
                className={`${!on || nodata ? "off" : ""}${on && !nodata && v === null ? " gap" : ""}${canOpen ? " can" : ""}`.trim() || undefined}
                onClick={canOpen ? () => setOpen(isOpen ? null : key) : undefined}
              >
                <td className="pico"><span style={{ color: PCOLOR[key] }}><Icon size={17} /></span></td>
                <td className="pil">
                  <span className="w" style={{ color: PCOLOR[key] }}>
                    {!on ? "not scored"
                      : nodata ? "not measured here"
                      : `${Math.round((100 * weights[key]) / msum)}% of score`}
                  </span>
                  <span className="nm">{PLABELS[key][0]}</span>
                  <span className="mut"> · {sigs.length} factor{sigs.length === 1 ? "" : "s"}</span>
                </td>
                <td className="bd">
                  {/* Three different absences, and they must not read alike: the whole
                      team lacks it (collection gap), THIS person has no reading for a
                      pillar whose absence we treat as a gap (renormalised away, costs
                      them nothing), or they have no reading and it counts as a zero. */}
                  {pb
                    ? <span className="dsc-pill"><i style={{ background: bandColor(bandIndex(pb.band, scale), scale.length) }} />{pb.band}</span>
                    : <span className="mut">
                        {!on ? "team data gap" : nodata ? "no data for you — not counted"
                          : nodataMetric(key)}
                      </span>}
                </td>
                <td className="pt">
                  {on && !nodata
                    ? <><b>{row.contributions[key] ?? 0}</b> <span className="u">pts</span></>
                    : "—"}
                </td>
                <td className="cv">
                  {/* A real button, not just a click handler on the row. The row stays
                      clickable because that is the nicer target, but without this the whole
                      breakdown was mouse-only — and the team rows below, which are
                      <details>/<summary>, were keyboard-operable, so the same content had two
                      different answers. stopPropagation so the row's handler does not undo
                      this one's toggle. */}
                  {canOpen && (
                    <button
                      type="button" className="dsc-cvbtn" aria-expanded={isOpen}
                      aria-label={`${isOpen ? "Hide" : "Show"} what drives ${PLABELS[key][0]}`}
                      onClick={(e) => { e.stopPropagation(); setOpen(isOpen ? null : key); }}
                    >
                      {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    </button>
                  )}
                </td>
              </tr>
              {canOpen && isOpen && (
                // Its own full-width row: inside a cell the factor table inherits that
                // column's width and wraps every label onto three lines.
                <tr className="drill">
                  <td colSpan={5}>
                    <div className="dsc-drill-in">
                      <span className="dsc-drill-h">
                        {PLABELS[key][0]} sits at the <b>{ordinal(v ?? 0)} percentile</b> of the team.
                        What that is made of:
                      </span>
                      <FactorRows row={row} tm={tm} signals={sigs} whose={whose} />
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
// Why this is three numbers and not one: the score is a percentile, so it moves when the
// person moves AND when the team moves past them. On production one person fell 18 points of
// which 11 was the team, and another fell 26 of which 10 was. "You dropped 18" is not a
// rounder version of that, it is a different claim — and the wrong one.
// "61th" / "1th" / "22th" — percentiles run 0-100, so the naive suffix is wrong for most of
// them. 11/12/13 are the exceptions that make a lookup on the last digit alone insufficient.
function ordinal(n: number): string {
  const rem100 = n % 100;
  if (rem100 >= 11 && rem100 <= 13) return `${n}th`;
  return `${n}${["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
}

function fmtDay(s: string | undefined): string {
  if (!s) return "";
  // new Date("garbage") returns an Invalid Date rather than throwing, so a try/catch never
  // fires and the heading reads "vs Invalid Date". Check the timestamp and fall back to the
  // raw string, which at least says something true.
  const d = new Date(`${s}T00:00:00`);
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function WhatsChanged({ d, boardHasDeltas, weights, wsum }: {
  d: ScoreDelta | null; boardHasDeltas: boolean;
  weights: Record<string, number>; wsum: number;
}) {
  if (!d) {
    // Absent for two different reasons, and they are not the same news.
    return (
      <p className="dsc-nodelta">
        {boardHasDeltas
          ? "No comparison: this person was not scored in the window before this one."
          : "No comparison: there is no data for the window before this one."}
      </p>
    );
  }
  const sign = (v: number) => (v > 0 ? `+${v}` : `${v}`);
  const col = (v: number) => (v === 0 ? "var(--mut)" : v > 0 ? "var(--good)" : "var(--bad)");
  return (
    <div className="dsc-card">
      <div className="dsc-card-h">
        <h3>
          What&rsquo;s changed{" "}
          {/* The mechanism has to be available — "how can my score fall because of the team"
              is the first thing anyone asks — but it does not have to sit on the screen
              forever. Same "?" the period and scope controls use, so it is one affordance
              the page already teaches, and it carries the hidden text a screen reader needs. */}
          <Help
            id="dsc-delta-help" of="this split"
            text="Your score is a rank among the people scored, so it moves when their numbers move and not only when yours do. From the team is that part of the change; from you is your own."
          />{" "}
          {d.since && d.until && (
            <span className="dsc-vs">vs {fmtDay(d.since)} &ndash; {fmtDay(d.until)}</span>
          )}
        </h3>
      </div>
      <div className="dsc-split">
        {/* Each tile names a CAUSE, shows that cause's share of the total in points of your
            score, and says in one line HOW that cause reaches your score. All three are the
            same quantity, so the signs agree and -4 + -9 = -13 reads straight off the tiles.
            The captions are the mechanism, never the label restated — "FROM THE TEAM" over
            "the team improved" was that mistake — and they fill space the tiles already had
            rather than adding height.
            This took three tries and the first two were the same mistake: describing the TEAM
            while the number measured YOUR SCORE. "the part of the move that was not yours"
            restated the label; "The team improved" over a red -4 had three signals disagreeing;
            making the -4 neutral removed the colour clash but not the contradiction, because
            "improved" and "-4" cannot both be about the same thing. They were not. That the
            team moved up is still readable — a negative share from the team means exactly that
            — it just is not welded to a number measuring something else.
            Colour stays on the tiles that are a verdict on you; the team's share is not one. */}
        {([["Total", d.total, true, `${d.prev} → ${d.now}`],
           ["From the team", d.team, false, "the ranking shifted"],
           ["From you", d.you, true, "your own numbers moved"],
          ] as [string, number, boolean, string][])
          .map(([label, v, valenced, why]) => (
            <div key={label}>
              <span className="lb">{label}</span>
              <b style={{ color: valenced ? col(v) : "var(--ink)" }}>{sign(v)}</b>
              <span className="why">{why}</span>
            </div>
          ))}
      </div>
      <table className="dsc-chg">
        <thead>
          <tr><th>Pillar</th><th>Points was</th><th>now</th><th>Δ</th></tr>
        </thead>
        <tbody>
          {byWeight(weights).filter((k) => d.pillars[k]).map((k) => {
            const p = d.pillars[k];
            const dpt = (p.now_points ?? 0) - (p.prev_points ?? 0);
            return (
              <tr key={k}>
                <td>
                  {PLABELS[k][0]}
                  <span className="sh">{Math.round((100 * (weights[k] || 0)) / wsum)}% of score</span>
                </td>
                <td>{p.prev_points ?? "—"}</td>
                <td>{p.now_points ?? "—"}</td>
                <td style={{ color: col(dpt) }}>{sign(dpt)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td>Score</td>
            <td>{d.prev}</td><td>{d.now}</td>
            <td style={{ color: col(d.total) }}>{sign(d.total)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function Hero({ row, n, scale, caveat }: { row: ScoreRow; n: number; scale: BandStop[]; caveat: string }) {
  const stops = scale.length ? scale : [{ min: 0, band: row.band, tone: row.tone }];
  const col = bandColor(bandIndex(row.band, stops), stops.length);
  return (
    <div className="dsc-hero">
      <div className="dsc-hero-h">
        {/* The chip is the warning label, so it is where the warning lives. This used to be
            110 words of paragraph at the foot of the panel, about half of which the board's
            meta line and "?" now say ("46 ranked, >=5 commits+PRs", what not banded means).
            What is left is the part that stops the number being misused, and it hangs off the
            word that already says be careful. */}
        {/* Wrapped, because .dsc-hero-h is a space-between flex row and Help renders TWO
            nodes: dropped in loose they became separate flex items, so the "?" was dealt out
            to the middle of the row — 329px into a 716px header, 229px from the chip it
            belongs to. One item, and it sits against the word again. */}
        <span className="dsc-exp-w">
          <span className="dsc-exp">Experimental</span>
          <Help id="dsc-exp-help" of="this score" text={caveat} />
        </span>
        <span className="mut">org-relative · this window</span>
      </div>
      <div className="dsc-hero-n">
        <b>{row.score}</b>
        <span className="of">of 100</span>
        <span className="dsc-pill"><i style={{ background: col }} />{row.band}</span>
      </div>
      {/* The thermometer, as the reference builds it: boundaries above, a CONTINUOUS red-to-
          dark-green gradation behind, band names over it, and a tick below for position.
          Two earlier attempts got this wrong. Four flat segments are not a gradation, and
          dimming everything except the current band was worse still: it made brightness say
          "you are here" when brightness is the channel that has to say "this way is better".
          Position belongs to the tick. And the ramp is monotonic in HUE — red, amber, green —
          not in luminance, which is what makes "which end is good" readable without a key. */}
      <div className="dsc-scale">
        <span className="dsc-scale-k">
          {[...stops.map((b) => b.min), 100].map((v) => (
            <em key={v} style={{ left: `${v}%` }}>{v}</em>
          ))}
        </span>
        <span className="dsc-scale-t">
          {stops.map((b, i) => {
            const hi = i + 1 < stops.length ? stops[i + 1].min : 100;
            return (
              <em key={b.band} style={{ left: `${(b.min + hi) / 2}%` }}
                  title={`${b.band}: ${b.min}\u2013${hi === 100 ? 100 : hi - 1}`}>
                {b.band}
              </em>
            );
          })}
        </span>
        <span className="dsc-scale-m"><b style={{ left: `${row.score}%` }} /></span>
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
        {/* Two wrappers so the wide layout can put "everyone" beside "you" instead of
            under it. Below the breakpoint they are display:contents, so the DOM order
            hero -> what's changed -> ingredients -> team standing IS the reading order
            and nothing has to be re-sorted for narrow screens. */}
        <div className="dsc-mainc">
        {sc && sc.score !== null && sc.score !== undefined ? (
          <>
            <Hero row={sc} n={score.n_ranked || score.n_eligible} scale={score.bands_scale ?? []}
                  caveat={`A transparent heuristic to calibrate against outcomes — not a verdict, and no ML. A scored pillar you have no data for (e.g. no PRs opened) counts as 0, a real minus, rather than dropping you from the board. Two absences are treated differently: a pillar with too little data across the whole team is a collection gap, shown not scored and left out for everyone; and Flow, whose inputs exist per repository rather than per person, is left out of YOUR score when we have none for you — its weight going to the pillars that do have a reading — because that absence is ours, not yours. Known proxies: no true code-complexity signal, and quality is read from peer review — how much of your merged work a colleague reviewed, and how many rounds it took — plus merge rate, not blame. Review bots and your own reviews of your own work do not count as peer review. A per-item average is weighed by how much work stands behind it, so a ratio from three pull requests does not outrank the same ratio from eight hundred.`} />
            <WhatsChanged d={score.delta ?? null} weights={score.weights} wsum={wsum}
                          boardHasDeltas={(score.board ?? []).some((r) => r.delta !== null && r.delta !== undefined)} />
            <div className="dsc-card">
              <div className="dsc-card-h">
                <h3>Score ingredients</h3>
                <p>What the score is made of. Open a pillar to see its factors against the team.</p>
              </div>
              <Ingredients row={sc} active={active} weights={score.weights} tm={score.team_medians} wsum={wsum} signals={signals} scale={score.bands_scale ?? []} whose={score.is_self_view ? "You" : sc.name} />
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

        </div>

        {score.board && score.board.length > 0 && (
        <div className="dsc-sidec">{(() => {
          const nAct = active.length;
          const thin = score.board.filter((r) => coverage(r, active) < scored(r, active).length);
          // No "and N of those land in <lowest band>" any more, and not only for length: the
          // payload bands a thin row, but the TABLE prints "not banded" for it, so the claim
          // could not be checked against the rows beside it and contradicted the sentence
          // straight after ("those rows are left unbanded"). Verified on production — all four
          // thin rows render "not banded" while three carried band=Building in the payload.
          const bscale = score.bands_scale ?? [];
          const counts = bandCounts(score.board, bscale, active);
          return (
          <div className="dsc-card">
            {/* Was a heading and forty words of prose, then fifty-five more under the table.
                Everything that was a FACT is still here — the population is the meta line, the
                unbanded count is a figure in the distribution key beside the band counts — and
                what was a caveat is behind the "?" the page already teaches. "Open a row to
                see how X compares" is gone: the ingredients card teaches that gesture in the
                same panel, and teaching it twice is what made this card a wall of text. */}
            <div className="dsc-card-h">
              <h3>
                Team standing{" "}
                <Help
                  id="dsc-board-help" of="this table"
                  text={`Scores are percentiles inside this window and scope, so they compare to each other and to nothing else. Not banded means a pillar that counts as zero for want of data — a data gap rather than a result. Flow is the exception: where we have no reading for somebody it is left out of their score instead, so the Data column counts against the pillars they are actually scored on. Anyone under ${score.min_activity} commits and PRs is not scored and is absent, so a name that is not here is not a name that is fine.`}
                />{" "}
                <span className="dsc-vs">
                  {score.board.length} ranked &middot; &ge;{score.min_activity} commits+PRs
                </span>
              </h3>
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
                  {/* Not a band, so it has no place on the bar — but it is the same question
                      ("how many people are where?") and the reader is already counting here. */}
                  {thin.length > 0 && (
                    // .none, not a --line2 fill: that was 1.25:1 on the white card, i.e. no
                    // marker at all, where the band swatches run 3.87-5.24. A ring says
                    // "no band" without borrowing a band's colour.
                    <em className="none"><i />Not banded <b>{thin.length}</b></em>
                  )}
                </span>
              </div>
            )}

            {/* No pillar key here. It was a second row of coloured dots under the band
                counts, decoding a different thing in the same idiom — and three of the four
                pillar colours sit within ~60 of RGB distance of a band colour, so naming it
                only made the duplication legible, not the card simpler. The key already
                exists on this screen and in a better place: the Score ingredients card puts
                the same four colours (verified identical) beside the pillar NAMES, which is
                what a swatch row was approximating. The bars keep their own tooltip with the
                exact split, and the row's drill has the full breakdown. */}
            {/* Column labels on the same grid as the rows. Seven columns with a delta among
                them cannot go unlabelled — a bare "+7" beside a score invites being read as
                part of it. */}
            <div className="dsc-rowh">
              <span />
              <span>Person</span>
              <span>Make-up</span>
              <span className="r">Score</span>
              <span>Band</span>
              <span className="r" data-tip="against the window before this one">&Delta;</span>
              <span className="r" data-tip="pillars with data, of those scored this window">Data</span>
            </div>
            <div className={`dsc-rows${capped ? " capped" : ""}`}>
              {score.board.map((r) => {
                const nMine = scored(r, active).length;
                const cov = coverage(r, active);
                const partial = nMine > 0 && cov < nMine;
                return (
                <details key={r.login} className={`dsc-drow${r.login === login ? " me" : ""}`}>
                  <summary>
                    <span className="rk">{r.rank}</span>
                    <span className="nm">{r.name}</span>
                    {/* byWeight, not PILLAR_ORDER. This bar and its tooltip were the last two
                        places still laying pillars out in declaration order — Engagement
                        first — while the change table, the ingredients table and this row's
                        own drill all sort heaviest-first. That is the same defect Oleg
                        reported as "why is the pillar order different top and bottom",
                        surviving in the one place I had not looked: the segments read left to
                        right in one order and the drill underneath listed them in another. */}
                    <span
                      className="comp"
                      data-tip={`score ${r.score} = ${byWeight(score.weights)
                        .filter((k) => active.includes(k) && r.contributions[k])
                        .map((k) => `${PLABELS[k][0]} ${r.contributions[k]}`).join(" + ")}`}
                    >
                      {byWeight(score.weights).map((k) => {
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
                    <span className="dlt">
                      {r.delta === null || r.delta === undefined
                        ? <span className="mut" data-tip="not scored in the previous window">—</span>
                        : <span style={{ color: r.delta === 0 ? "var(--mut)"
                                                : r.delta > 0 ? "var(--good)" : "var(--bad)" }}>
                            {r.delta > 0 ? `+${r.delta}` : r.delta}
                          </span>}
                    </span>
                    <span className={`cov${partial ? " thin" : ""}`} data-tip={
                      nMine < nAct
                        ? `data for ${cov} of the ${nMine} pillars this person is scored on — `
                          + `${nAct - nMine} of the window's ${nAct} had no data for them and `
                          // _SCORE_GAP_PILLARS holds only flow today, so this is "was" in
                          // every case the backend currently produces; agreeing with the
                          // count keeps it right if a second pillar is ever added to it.
                          + `${nAct - nMine === 1 ? "was" : "were"} left out of their `
                          + `score rather than counted as zero`
                        : `data for ${cov} of the ${nAct} pillars scored this window`}>
                      {cov}/{nMine}
                    </span>
                  </summary>
                  <div className="dsc-drow-body">
                    <VsSelfLine v={r.vs_self ?? null} you={you} />
                    <Ingredients row={r} active={active} weights={score.weights} tm={score.team_medians} wsum={wsum} signals={signals} scale={score.bands_scale ?? []} whose={r.name} />
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

          </div>
          );
        })()}</div>
        )}

      </div>
    </div>
  );
}
