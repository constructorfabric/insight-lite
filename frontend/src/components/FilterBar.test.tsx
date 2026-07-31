/**
 * The filter bar is rendered from TWO places on one page — components/Loading
 * while the payload is in flight, then the page itself once it lands. Those are
 * different parent trees, so React unmounts one bar and mounts the other, and
 * anything the component held in useState goes with it.
 *
 * That swap lands ~1.9s into a Delivery load, which is long enough to have opened
 * the custom range and typed a date into it. The draft is module state for exactly
 * that reason, and this file is the test that was missing when it was fixed —
 * until now the only evidence it worked was me driving a browser by hand.
 *
 * Each test re-imports the module (vi.resetModules) because the draft is scoped to
 * a PAGE, and a page is what a module instance stands for here: an MPA navigation
 * reloads the module and forgets the draft. Sharing one module across tests would
 * be sharing one page, and the second test would start on the first one's draft.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Period } from "./FilterBar";

const PRESETS = [
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
  { key: "all", label: "All-time" },
];
const PERIOD: Period = { preset: "all", label: "All-time", from: null, to: null };

/** A freshly loaded FilterBar module — i.e. a fresh page. */
async function freshBar() {
  const { default: FilterBar } = await import("./FilterBar");
  return (period: Period = PERIOD) => (
    <FilterBar
      periodPresets={PRESETS}
      period={period}
      scope=""
      scopeTargets={{ repo: ["o/a", "o/b"], element: ["Studio"] }}
    />
  );
}

beforeEach(() => {
  vi.resetModules();
});

describe("the custom-range draft", () => {
  it("survives the bar being unmounted and mounted again", async () => {
    const user = userEvent.setup();
    const bar = await freshBar();
    const first = render(bar());
    await user.click(screen.getByRole("button", { name: /custom/i }));
    await user.type(screen.getByLabelText("from date"), "2026-03-01");
    expect(screen.getByLabelText("from date")).toHaveValue("2026-03-01");

    first.unmount();                       // Loading hands over to the page
    render(bar());

    expect(screen.getByLabelText("from date")).toHaveValue("2026-03-01");
  });

  it("keeps the popover open across the swap", async () => {
    const user = userEvent.setup();
    const bar = await freshBar();
    const first = render(bar());
    expect(screen.getByLabelText("from date")).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: /custom/i }));
    expect(screen.getByLabelText("from date")).toBeVisible();

    first.unmount();
    render(bar());
    expect(screen.getByLabelText("from date")).toBeVisible();
  });

  it("starts closed and empty on a fresh page", async () => {
    const bar = await freshBar();
    render(bar());
    expect(screen.getByLabelText("from date")).not.toBeVisible();
    expect(screen.getByLabelText("from date")).toHaveValue("");
  });

  it("is forgotten once Apply has put the dates in the URL", async () => {
    const user = userEvent.setup();
    const bar = await freshBar();
    const first = render(bar());
    await user.click(screen.getByRole("button", { name: /custom/i }));
    await user.type(screen.getByLabelText("from date"), "2026-03-01");
    await user.click(screen.getByRole("button", { name: "Apply" }));

    expect(window.location.search).toContain("from=2026-03-01");
    first.unmount();

    // Applied — the draft has nothing left to protect, so the field goes back to
    // reflecting whatever period the server resolved.
    render(bar({ preset: "custom", label: "custom", from: "2026-02-01", to: null }));
    expect(screen.getByLabelText("from date")).toHaveValue("2026-02-01");
  });
});

describe("the two help buttons", () => {
  it("announce a NAME and point at the prose, rather than reading it as a label", async () => {
    render((await freshBar())());
    const helps = screen.getAllByRole("button", { name: /^What .* covers$/ });
    expect(helps).toHaveLength(2);
    for (const b of helps) {
      // Three sentences in aria-label means a screen reader reads the whole
      // explanation where it should read "help", with no way past it.
      expect(b.getAttribute("aria-label")!.length).toBeLessThan(40);
      const described = document.getElementById(b.getAttribute("aria-describedby")!);
      expect(described).toBeInTheDocument();
      expect(described!.textContent!.length).toBeGreaterThan(100);
      expect(described).toHaveClass("vh");
      // the tooltip and the description are one string, so they cannot drift
      expect(b.getAttribute("data-tip")).toBe(described!.textContent);
    }
  });

  it("sits between the label and the control it explains", async () => {
    render((await freshBar())());
    const scope = screen.getByLabelText("Repository scope");
    // label, the "?", its visually-hidden description, then the control
    const kids = Array.from(scope.children);
    expect(kids[0]).toHaveTextContent("Scope");
    expect(kids[1].tagName.toLowerCase()).toBe("button");
    expect(kids[1]).toHaveClass("legend-help");
    expect(kids[2]).toHaveClass("vh");
    expect(kids[3].tagName.toLowerCase()).toBe("select");
  });
});

describe("the controls write the query", () => {
  it("a preset click puts the period in the URL", async () => {
    const user = userEvent.setup();
    render((await freshBar())());
    await user.click(screen.getByRole("button", { name: "30 days" }));
    expect(window.location.search).toContain("p=30d");
  });

  it("the scope select writes slice, and groups its targets", async () => {
    const user = userEvent.setup();
    render((await freshBar())());
    const select = screen.getByRole("combobox");
    expect(select).toHaveAttribute("id", "global-scope");
    expect(Array.from(select.querySelectorAll("optgroup")).map((g) => g.label))
      .toEqual(["Elements", "Repositories"]);
    await user.selectOptions(select, "repo:o/a");
    expect(window.location.search).toContain("slice=repo%3Ao%2Fa");
  });

  it("the whole-org option clears the scope rather than naming one", async () => {
    render((await freshBar())());
    const first = screen.getByRole("combobox").querySelector("option")!;
    expect(first).toHaveValue("");
    expect(first).toHaveTextContent(/whole org/i);
  });
});
