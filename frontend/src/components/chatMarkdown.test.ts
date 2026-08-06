// The chat panel's markdown is a deliberately small, XSS-safe subset. It had two problems
// that showed up in the same production answer — a reply explaining how a metric is computed.
//
//   1. Every snake_case identifier was mangled. `_([^_\n]+)_` matched inside a word, so
//      flow_friction_per_item rendered as flow<em>friction</em>per_item. In an answer about
//      metrics, most words are identifiers.
//   2. The model reached for LaTeX, which nothing renders, so the formula arrived as source:
//      $$\text{flow\_friction\_per\_item} = \frac{2 \times (...)}{...}$$
//
// The system instruction now asks for plain text, but a prompt is advice; these tests pin the
// renderer's own behaviour so an accidental formula is readable rather than broken.
import { describe, expect, it } from "vitest";

import { mxDelatex, mxMarkdown } from "./ChatWidget";

describe("underscores", () => {
  it("leaves identifiers alone", () => {
    const html = mxMarkdown("the metric is flow_friction_per_item today");
    expect(html).toContain("flow_friction_per_item");
    expect(html).not.toContain("<em>");
  });

  it("still emphasises a whole word wrapped in underscores", () => {
    expect(mxMarkdown("this is _really_ important")).toContain("<em>really</em>");
  });

  it("does not emphasise across a trailing word character", () => {
    // `a_b_c` is three words joined, not emphasis — the CommonMark reading.
    expect(mxMarkdown("a_b_c")).not.toContain("<em>");
  });

  it("keeps bold working next to identifiers", () => {
    const html = mxMarkdown("**Your friction:** 0.43 from flow_friction_per_item");
    expect(html).toContain("<strong>Your friction:</strong>");
    expect(html).toContain("flow_friction_per_item");
  });
});

describe("mxDelatex", () => {
  it("turns a display formula into one line of code", () => {
    const out = mxDelatex(
      "$$\\text{friction} = \\frac{2 \\times (\\text{bounces} + \\text{reopens})}" +
      "{\\text{owned items}}$$");
    expect(out).toBe("`friction = (2 × (bounces + reopens)) / (owned items)`");
  });

  it("handles the inline and bracket delimiters too", () => {
    expect(mxDelatex("so \\(x \\times y\\) holds")).toBe("so `x × y` holds");
    expect(mxDelatex("\\[a \\cdot b\\]")).toBe("`a · b`");
  });

  it("unescapes the underscores LaTeX escapes", () => {
    expect(mxDelatex("$$\\text{flow\\_friction\\_per\\_item}$$"))
      .toBe("`flow_friction_per_item`");
  });

  it("leaves prose without formulas untouched", () => {
    const prose = "Your friction is 0.43 per item, against a team median of 0.17.";
    expect(mxDelatex(prose)).toBe(prose);
  });

  it("does not eat a lone dollar sign", () => {
    expect(mxDelatex("it cost $0.0021 per turn")).toBe("it cost $0.0021 per turn");
  });
});

describe("the two together, on the answer that prompted this", () => {
  it("renders a formula as code and keeps the identifier intact", () => {
    const html = mxMarkdown(
      "According to the metrics catalog, **friction** is defined as:\n" +
      "$$\\text{flow\\_friction\\_per\\_item} = \\frac{2 \\times (\\text{draft bounces} + " +
      "\\text{reopens})}{\\text{owned items}}$$");
    expect(html).toContain("<code>");
    expect(html).toContain("flow_friction_per_item");
    expect(html).not.toContain("$$");
    expect(html).not.toContain("\\frac");
    expect(html).not.toContain("<em>");
  });
});

describe("escaping is not weakened", () => {
  it("still escapes html in prose", () => {
    expect(mxMarkdown("a <script>alert(1)</script> b")).not.toContain("<script>");
  });

  it("still escapes html smuggled inside a formula", () => {
    const html = mxMarkdown("$$\\text{<img src=x onerror=1>}$$");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});
