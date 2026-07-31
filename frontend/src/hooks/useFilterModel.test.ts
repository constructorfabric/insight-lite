/**
 * The filter bar paints with the shell instead of behind a skeleton because its
 * options arrive in a `#filter-model` JSON island rather than in the payload. Two
 * things have to hold for that to be safe: a missing or broken island degrades to
 * null (the caller keeps its skeleton) rather than throwing during the first
 * render of every report page, and the ACTIVE selection comes from the URL, which
 * is where it came from in the first place.
 *
 * tests/test_spa.py pins the server half — that the island carries only the
 * request-independent keys and cannot close its own script tag.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const ISLAND = {
  periodPresets: [
    { key: "7d", label: "7 days" },
    { key: "30d", label: "30 days" },
    { key: "all", label: "All-time" },
  ],
  scopeTargets: { repo: ["o/a"], element: ["Studio"] },
};

function plant(text: string | null) {
  document.body.innerHTML = "";
  if (text === null) return;
  const el = document.createElement("script");
  el.id = "filter-model";
  el.type = "application/json";
  el.textContent = text;
  document.body.appendChild(el);
}

/** The island is parsed once per module load, so each case needs a fresh module. */
async function read(search = "") {
  window.history.replaceState({}, "", `/flow${search}`);
  vi.resetModules();
  const { default: useFilterModel } = await import("./useFilterModel");
  const { renderHook } = await import("@testing-library/react");
  return renderHook(() => useFilterModel()).result.current;
}

beforeEach(() => vi.resetModules());

describe("without a usable island", () => {
  it("returns null when there is none — a fresh install has no model yet", async () => {
    plant(null);
    expect(await read()).toBeNull();
  });

  it("returns null instead of throwing on malformed JSON", async () => {
    plant("{not json");
    expect(await read()).toBeNull();
  });
});

describe("with one", () => {
  it("takes the options from the island", async () => {
    plant(JSON.stringify(ISLAND));
    const m = (await read())!;
    expect(m.periodPresets).toEqual(ISLAND.periodPresets);
    expect(m.scopeTargets).toEqual(ISLAND.scopeTargets);
  });

  it("takes the ACTIVE period and scope from the URL, not the island", async () => {
    plant(JSON.stringify(ISLAND));
    const m = (await read("?p=30d&slice=repo:o/a"))!;
    expect(m.period).toEqual({ preset: "30d", label: "30 days", from: null, to: null });
    expect(m.scope).toBe("repo:o/a");
  });

  it("defaults to all-time when the URL names no period", async () => {
    plant(JSON.stringify(ISLAND));
    const m = (await read())!;
    expect(m.period.preset).toBe("all");
    expect(m.period.label).toBe("All-time");
    expect(m.scope).toBe("");
  });

  it("resolves a from/to pair as a custom range, the way the server does", async () => {
    plant(JSON.stringify(ISLAND));
    const m = (await read("?from=2026-03-01&to=2026-03-31"))!;
    expect(m.period.preset).toBe("custom");
    expect(m.period.from).toBe("2026-03-01");
    expect(m.period.to).toBe("2026-03-31");
  });

  it("falls back to the key as its own label for a preset it does not know", async () => {
    plant(JSON.stringify(ISLAND));
    const m = (await read("?p=42d"))!;
    expect(m.period.label).toBe("42d");
  });

  it("survives an island missing a field", async () => {
    plant(JSON.stringify({ periodPresets: ISLAND.periodPresets }));
    const m = (await read())!;
    expect(m.scopeTargets).toEqual({});
  });
});
