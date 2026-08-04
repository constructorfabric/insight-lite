// verdict() turns a (yours, team) pair into the phrase in the drill's Standing column.
// It is worth its own test because every interesting case is a boundary: the ratio has no
// finite value when either side is zero, and getting the DIRECTION wrong paints an
// improvement red — the failure that made the whole signal spec necessary.
import { describe, expect, it } from "vitest";

import { verdict } from "./PersonScore";

describe("verdict", () => {
  it("states the factor so it always reads above 1x", () => {
    // 12.8h against a team median of 30.6h, lower is better
    expect(verdict(12.8, 30.6, false)).toEqual({ text: "2.4× better", good: true });
    // and the same shape when you are behind
    expect(verdict(28, 6, false)).toEqual({ text: "4.7× worse", good: false });
  });

  it("reads the direction, not just the size of the gap", () => {
    // identical numbers, opposite meaning
    expect(verdict(80, 40, true).good).toBe(true);
    expect(verdict(80, 40, false).good).toBe(false);
  });

  it("names what a zero of your own means instead of comparing", () => {
    // zero friction where lower is better is the floor, not a missing value
    expect(verdict(0, 0.175, false)).toEqual({ text: "best possible", good: true });
    // zero reviews where higher is better is the opposite, and equally not missing
    expect(verdict(0, 21, true)).toEqual({ text: "none at all", good: false });
  });

  it("says so when the team median is the zero", () => {
    expect(verdict(3, 0, true)).toEqual({ text: "team median is 0", good: true });
    expect(verdict(3, 0, false)).toEqual({ text: "team median is 0", good: false });
  });

  it("treats an exact match as at the median, not as a 1.0x", () => {
    expect(verdict(5, 5, false)).toEqual({ text: "at the team median", good: true });
  });

  it("has no verdict when a value is absent, which is not the same as a zero", () => {
    expect(verdict(null, 5, true)).toEqual({ text: "—", good: null });
    expect(verdict(5, null, true)).toEqual({ text: "—", good: null });
  });
});
