/**
 * Nav links carry the report query, and getting that wrong is invisible: a link
 * that quietly drops `?person=` still navigates, still highlights, and lands you
 * on somebody else's page. That is the bug this behaviour exists for — switching
 * Person views dropped the person, because the server built those hrefs from the
 * query it saw on ITS request and a person picked afterwards never reached it.
 *
 * The server and this component must agree on which params travel; the rule lives
 * once, in backend/shell.py's zone_carry, and reaches here as each zone's `carry`
 * list. tests/test_spa.py pins the server half.
 */
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Sidebar, { type NavZone } from "./Sidebar";

const REPORT_CARRY = ["from", "p", "slice", "to"];

const ZONES: NavZone[] = [
  {
    key: "overview", label: "Overview", icon: "LayoutGrid", carry: REPORT_CARRY,
    items: [{ key: "overview", label: "Overview", href: "/overview", icon: "LayoutGrid" }],
  },
  {
    key: "person", label: "Person", icon: "User", carry: [...REPORT_CARRY, "person"],
    items: [
      { key: "person-overview", label: "Overview", href: "/person?view=overview", icon: "LayoutGrid" },
      { key: "person-activity", label: "Activity", href: "/person?view=activity", icon: "Activity" },
    ],
  },
  {
    key: "manage", label: "Manage", icon: "Settings2", carry: [],
    items: [{ key: "update", label: "Update", href: "/update", icon: "RefreshCw" }],
  },
];

function at(search: string) {
  window.history.replaceState({}, "", `/person${search}`);
}
const hrefs = (sel: string) =>
  Array.from(document.querySelectorAll<HTMLAnchorElement>(sel)).map((a) => a.getAttribute("href"));

describe("nav links and the live query", () => {
  it("keeps the subject when switching views inside its own zone", () => {
    at("?person=ainetx&p=30d");
    render(<Sidebar zones={ZONES} active="person-overview" />);
    expect(hrefs(".sb-pane .tab")).toEqual([
      "/person?view=overview&p=30d&person=ainetx",
      "/person?view=activity&p=30d&person=ainetx",
    ]);
  });

  it("carries the global filters into other report zones but not the subject", () => {
    at("?person=ainetx&p=30d&slice=repo:o/a");
    render(<Sidebar zones={ZONES} active="person-overview" />);
    const rail = hrefs(".sb-rail .rz");
    expect(rail[0]).toContain("p=30d");
    expect(rail[0]).toContain("slice=repo%3Ao%2Fa");
    expect(rail[0]).not.toContain("person=");
  });

  it("gives Manage none of it — a period means nothing to /config", () => {
    at("?person=ainetx&p=30d&slice=repo:o/a");
    render(<Sidebar zones={ZONES} active="person-overview" />);
    expect(hrefs(".sb-rail .rz")[2]).toBe("/update");
  });

  it("lets the href win where it names a carry key itself", () => {
    // A link that already pins the subject must keep the one it names. Merging the
    // live value on top would silently retarget it — and it has to be a key the zone
    // actually CARRIES, or the merge never considers it and the test proves nothing.
    at("?person=ainetx&p=30d");
    const zones: NavZone[] = [{
      ...ZONES[1],
      items: [{ key: "person-work", label: "Composition",
                href: "/person?view=work&person=bob", icon: "Layers" }],
    }];
    render(<Sidebar zones={zones} active="person-work" />);
    const href = hrefs(".sb-pane .tab")[0]!;
    expect(href).toContain("person=bob");
    expect(href).not.toContain("ainetx");
    expect(href).toContain("p=30d");        // the key it does NOT name still merges
  });

  it("does not pin you to the view you are leaving", () => {
    // `view` is not a carry key precisely so a pane link can name its own.
    at("?view=overview&person=ainetx");
    render(<Sidebar zones={ZONES} active="person-overview" />);
    expect(hrefs(".sb-pane .tab")[1]).toContain("view=activity");
    expect(hrefs(".sb-pane .tab")[1]).not.toContain("view=overview");
  });

  it("ignores a param no zone declares", () => {
    at("?person=ainetx&utm_source=slack");
    render(<Sidebar zones={ZONES} active="person-overview" />);
    expect(hrefs(".sb-pane .tab").join(" ")).not.toContain("utm_source");
  });

  it("leaves a fragment a fragment", () => {
    // Splitting on "?" would put the query AFTER the hash on an href that has one
    // and no query — /report#flow?p=30d, which is a different address. No nav href
    // carries a fragment today; this is what keeps that from mattering when one does.
    at("?p=30d");
    const zones: NavZone[] = [{
      ...ZONES[0],
      items: [{ key: "overview", label: "Overview", href: "/report#flow", icon: "LayoutGrid" }],
    }];
    render(<Sidebar zones={zones} active="overview" />);
    expect(hrefs(".sb-pane .tab")[0]).toBe("/report?p=30d#flow");
  });
});

describe("rail and pane", () => {
  it("heads a pane that holds more than one row", () => {
    render(<Sidebar zones={ZONES} active="person-overview" />);
    expect(document.querySelector(".navgroup")).toHaveTextContent("Person");
  });

  it("does not head a pane holding a single row — that duplicates the lit rail icon", () => {
    render(<Sidebar zones={ZONES} active="overview" />);
    expect(document.querySelectorAll(".navgroup")).toHaveLength(0);
    expect(hrefs(".sb-pane .tab")).toHaveLength(1);
  });

  it("falls back to the first zone for a page outside the nav", () => {
    // e.g. /calibrate — a rail with nothing lit beside an empty pane reads as broken
    render(<Sidebar zones={ZONES} active="" />);
    expect(document.querySelector(".sb-rail .rz")).toHaveClass("active");
    expect(hrefs(".sb-pane .tab")).toHaveLength(1);
  });

  it("points a rail entry at its zone's first item", () => {
    render(<Sidebar zones={ZONES} active="overview" />);
    expect(hrefs(".sb-rail .rz")[1]).toContain("/person?view=overview");
  });
});
