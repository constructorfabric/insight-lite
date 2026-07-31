/**
 * The URL is the app's state. Every filter writes it with history.replaceState and
 * every reader parses it back, so a key that can be READ but not WRITTEN is a state
 * that silently resets — which is what `view` was: clicking a period on
 * Person > Activity rewrote the address without it and landed you on Overview,
 * while the sidebar pane still highlighted Activity.
 *
 * tests/test_page_scripts.py greps the source to check every QUERY_KEYS entry
 * appears in toSearchString. That catches a key added to one list and not the
 * other; it cannot catch the round trip actually working. This does.
 */
import { describe, expect, it } from "vitest";

import { setReportQuery } from "./useReportData";

function at(search: string) {
  window.history.replaceState({}, "", `/person${search}`);
}
const q = () => new URLSearchParams(window.location.search);

describe("the query round trip", () => {
  it("keeps the sub-view when a filter changes", () => {
    at("?view=activity&person=ainetx");
    setReportQuery({ p: "30d" });
    expect(q().get("view")).toBe("activity");
    expect(q().get("person")).toBe("ainetx");
    expect(q().get("p")).toBe("30d");
  });

  it("keeps it through a scope change and a person pick too", () => {
    at("?view=score&person=ainetx&p=30d");
    setReportQuery({ slice: "repo:o/a" });
    expect(q().get("view")).toBe("score");
    setReportQuery({ person: "brad" });
    expect(q().get("view")).toBe("score");
    expect(q().get("person")).toBe("brad");
  });

  it("survives every key at once, so nothing is dropped by ordering", () => {
    at("");
    setReportQuery({ p: "90d", slice: "element:Studio", person: "ainetx", view: "impact" });
    expect(Object.fromEntries(q())).toEqual({
      p: "90d", slice: "element:Studio", person: "ainetx", view: "impact",
    });
  });
});

describe("clearing", () => {
  it("removes a key given null rather than writing an empty value", () => {
    at("?p=30d&person=ainetx&view=activity");
    setReportQuery({ person: null });
    expect(q().has("person")).toBe(false);
    expect(q().get("view")).toBe("activity");
  });

  it("a custom range replaces the preset, and a preset replaces the range", () => {
    at("?p=30d");
    setReportQuery({ from: "2026-03-01", to: "2026-03-31", p: null });
    expect(q().has("p")).toBe(false);
    expect(q().get("from")).toBe("2026-03-01");
    setReportQuery({ p: "7d", from: null, to: null });
    expect(q().get("p")).toBe("7d");
    expect(q().has("from")).toBe(false);
  });

  it("never leaves the two period forms in the URL together", () => {
    // toSearchString writes from/to OR p, never both — the server resolves a
    // custom range when from/to are present, so keeping a stale p would be a
    // period the page does not honour sitting in a shared link.
    at("?p=30d");
    setReportQuery({ from: "2026-03-01" });
    expect(q().has("p")).toBe(false);
  });
});

describe("history", () => {
  it("replaces rather than pushes, so filtering does not fill the back button", () => {
    at("?p=7d");
    const before = window.history.length;
    setReportQuery({ p: "30d" });
    setReportQuery({ p: "90d" });
    expect(window.history.length).toBe(before);
  });
});
