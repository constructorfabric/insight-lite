// installSort is the one click-to-sort in the app: every `table.dt` on every page shares it,
// so the two things worth pinning are the ones a table can get wrong by accident.
//
// 1. It must key off `data-sort`, not the rendered text. Numbers on this report go through
//    fmtNum (toLocaleString), so a cell reading "1,500" parses as 1 — a column of four-figure
//    values would sort as if they were ones, silently and plausibly.
// 2. Exactly one header may claim to be the sort key. It used to clear aria-sort only among
//    the clicked header's SIBLINGS, which was invisible while every dt table had a one-row
//    thead. The person weekly table groups repos above a leaf row of Commits/Lines, so sorting
//    by Week and then by Commits left two arrows showing.
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { installSort } from "./reportChromeEffects";

let uninstall: () => void;

beforeEach(() => { uninstall = installSort(); });
afterEach(() => { uninstall(); document.body.innerHTML = ""; });

/** A two-level header like the person weekly table: a group row over a leaf row. */
function groupedTable() {
  document.body.innerHTML = `
    <table class="dt grouped">
      <thead>
        <tr><th class="sortable" id="week">Week</th><th colspan="2">repo-a</th></tr>
        <tr><th></th><th class="sortable" id="commits">Commits</th><th class="sortable" id="lines">Lines</th></tr>
      </thead>
      <tbody>
        <tr><td data-sort="20260504">2026-05-04</td><td data-sort="9">9</td><td data-sort="1500">+1,000/-500</td></tr>
        <tr><td data-sort="20260511">2026-05-11</td><td data-sort="80">80</td><td data-sort="300">+200/-100</td></tr>
        <tr><td data-sort="20260518">2026-05-18</td><td data-sort="700">700</td><td data-sort="90">+50/-40</td></tr>
      </tbody>
      <tfoot><tr><td>Total</td><td data-sort="789">789</td><td data-sort="1890">+1,250/-640</td></tr></tfoot>
    </table>`;
  return {
    th: (id: string) => document.getElementById(id) as HTMLTableCellElement,
    keys: (col: number) => [...document.querySelector("tbody")!.rows]
      .map((r) => Number(r.cells[col].getAttribute("data-sort"))),
    arrows: () => [...document.querySelectorAll("th[aria-sort]")]
      .map((h) => `${h.textContent}=${h.getAttribute("aria-sort")}`),
  };
}

describe("installSort", () => {
  it("sorts numerically by data-sort, not by the rendered thousands-separated text", () => {
    const t = groupedTable();
    t.th("commits").click();
    // Lexically "700" < "80" < "9"; numerically 9 < 80 < 700. And a text-keyed sorter reading
    // "+1,000/-500" in the lines column would parse 1.
    expect(t.keys(1)).toEqual([9, 80, 700]);
    t.th("lines").click();
    expect(t.keys(2)).toEqual([90, 300, 1500]);
  });

  it("toggles ascending and descending on repeated clicks", () => {
    const t = groupedTable();
    t.th("commits").click();
    expect(t.keys(1)).toEqual([9, 80, 700]);
    t.th("commits").click();
    expect(t.keys(1)).toEqual([700, 80, 9]);
    expect(t.arrows()).toEqual(["Commits=descending"]);
  });

  it("leaves one arrow when the sort moves BETWEEN header rows", () => {
    const t = groupedTable();
    t.th("week").click();               // group row
    expect(t.arrows()).toEqual(["Week=ascending"]);
    t.th("commits").click();           // leaf row — the regression: Week kept its arrow
    expect(t.arrows()).toEqual(["Commits=ascending"]);
    t.th("lines").click();             // and within the leaf row
    expect(t.arrows()).toEqual(["Lines=ascending"]);
  });

  it("never reorders the footer, so a totals row stays a totals row", () => {
    const t = groupedTable();
    t.th("commits").click();
    t.th("commits").click();
    expect(document.querySelector("tfoot tr td")!.textContent).toBe("Total");
    expect(document.querySelectorAll("tfoot tr").length).toBe(1);
  });

  it("sorting a date column keyed as text alone would be a no-op, which is why weeks carry a key", () => {
    // Documents the trap rather than the fix: parseFloat("2026-05-04") is 2026 for every row,
    // so a date column with no data-sort passes the numeric check and then compares equal.
    document.body.innerHTML = `
      <table class="dt"><tbody>
        <tr><th class="sortable" id="d">Day</th></tr>
        <tr><td>2026-05-18</td></tr>
        <tr><td>2026-05-04</td></tr>
      </tbody></table>`;
    (document.getElementById("d") as HTMLElement).click();
    const rows = [...document.querySelector("tbody")!.rows].slice(1).map((r) => r.cells[0].textContent);
    expect(rows).toEqual(["2026-05-18", "2026-05-04"]);  // unchanged: 2026 === 2026
  });
});
