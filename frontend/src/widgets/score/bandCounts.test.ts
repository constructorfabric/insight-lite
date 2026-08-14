// bandCounts() feeds both the distribution strip and its key, so the thing worth testing is
// arithmetic rather than presentation: every person in the board is in exactly one bucket, and
// the buckets are the bands plus the not-banded remainder.
//
// It is tested because it was wrong in a way that was visible on screen and still shipped. The
// key read "Strong 6, Solid 17, Developing 17, Building 6, Not banded 4" for a board of 46 —
// fifty entries — because a partial-coverage row carries a band in the payload while the table
// withholds it and renders "not banded", so three thin rows were counted twice. CodeRabbit
// caught it on #7. A one-line sum would have caught it on the machine.
import { describe, expect, it } from "vitest";

import { bandCounts } from "./PersonScore";

const SCALE = [
  { min: 0, band: "Building", tone: "bad" },
  { min: 30, band: "Developing", tone: "warn" },
  { min: 50, band: "Solid", tone: "good" },
  { min: 70, band: "Strong", tone: "good" },
];

const ACTIVE = ["flow", "delivery", "craft", "engagement"];

/** A board row with only what bandCounts reads: its band and its pillar coverage. */
function row(band: string, covered: number) {
  const pillars: Record<string, number | null> = {};
  ACTIVE.forEach((p, i) => { pillars[p] = i < covered ? 50 : null; });
  return { band, pillars } as never;
}

describe("bandCounts", () => {
  it("counts each fully-covered person once, in their own band", () => {
    const counts = bandCounts(
      [row("Strong", 4), row("Strong", 4), row("Solid", 4), row("Building", 4)],
      SCALE, ACTIVE);
    expect(counts.map((c) => [c.band, c.n])).toEqual([["Strong", 2], ["Solid", 1], ["Building", 1]]);
  });

  it("leaves out partial coverage, whatever band the payload gave it", () => {
    // The exact production shape: three thin rows the payload put in Building, one in Developing.
    const board = [
      ...Array.from({ length: 6 }, () => row("Strong", 4)),
      ...Array.from({ length: 3 }, () => row("Building", 4)),
      row("Building", 3), row("Building", 1), row("Building", 1),  // thin
      row("Developing", 3),                                        // thin
    ];
    const counts = bandCounts(board, SCALE, ACTIVE);
    const byBand = Object.fromEntries(counts.map((c) => [c.band, c.n]));
    expect(byBand["Building"]).toBe(3);
    expect(byBand["Developing"]).toBeUndefined();  // its only member was thin, so the band is gone

    // The invariant. This is the assertion the shipped bug would have failed: 9 + 4 = 13.
    const banded = counts.reduce((a, c) => a + c.n, 0);
    const thin = board.length - banded;
    expect(banded + thin).toBe(board.length);
    expect(banded).toBe(9);
    expect(thin).toBe(4);
  });

  it("orders bands best first, matching the scale read right to left", () => {
    const board = SCALE.map((b) => row(b.band, 4));
    expect(bandCounts(board, SCALE, ACTIVE).map((c) => c.band))
      .toEqual(["Strong", "Solid", "Developing", "Building"]);
  });

  it("drops empty bands rather than drawing a zero-width segment", () => {
    const counts = bandCounts([row("Solid", 4)], SCALE, ACTIVE);
    expect(counts).toHaveLength(1);
    expect(counts[0].n).toBe(1);
  });

  it("counts everyone when no pillar is active, since no row can then be partial", () => {
    // nAct === 0 makes coverage 0 for every row, and the table's own `partial` check is
    // guarded on nAct > 0 — so both agree that nobody is withheld.
    const board = [row("Solid", 0), row("Strong", 0)];
    expect(bandCounts(board, SCALE, []).reduce((a, c) => a + c.n, 0)).toBe(2);
  });
});

// A pillar the score RENORMALISED AWAY for want of data is not a hole in that person's
// coverage: their score is an honest mean of everything we could measure, so withholding
// their band would report our collection gap as their thin row.
describe("a pillar left out of one person's score", () => {
  /** `scored_on` omits flow, and flow is the pillar they have no reading for. */
  function renormalised(band: string) {
    const pillars: Record<string, number | null> = { flow: null };
    ACTIVE.filter((p) => p !== "flow").forEach((p) => { pillars[p] = 50; });
    return { band, pillars, scored_on: ACTIVE.filter((p) => p !== "flow"),
             weight_gaps: ["flow"] } as never;
  }

  it("is banded, because nothing it is scored on is missing", () => {
    const counts = bandCounts([renormalised("Solid")], SCALE, ACTIVE);
    expect(counts.map((c) => [c.band, c.n])).toEqual([["Solid", 1]]);
  });

  it("still withholds the band when a pillar they ARE scored on has no data", () => {
    // craft missing means "opened no PRs" — a fact about them, still counted as zero,
    // so the row really is thin and must not be banded.
    const thin = { band: "Building",
                   pillars: { flow: null, delivery: 50, craft: null, engagement: 50 },
                   scored_on: ["delivery", "craft", "engagement"],
                   weight_gaps: ["flow"] } as never;
    expect(bandCounts([thin], SCALE, ACTIVE)).toEqual([]);
  });

  it("keeps the sum invariant with a mix of both", () => {
    const board = [renormalised("Strong"), row("Strong", 4), row("Solid", 3)];
    const banded = bandCounts(board, SCALE, ACTIVE).reduce((a, c) => a + c.n, 0);
    expect(banded).toBe(2);                       // the thin row(…, 3) is withheld
    expect(banded + (board.length - banded)).toBe(board.length);
  });

  it("falls back to the active list when the payload predates scored_on", () => {
    expect(bandCounts([row("Solid", 4)], SCALE, ACTIVE)[0].n).toBe(1);
  });
});
