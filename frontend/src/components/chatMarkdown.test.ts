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

/** The text a reader sees, with markup removed. */
function visible(html: string) {
  return html.replace(/<[^>]*>/g, "");
}

describe("the placeholder contract", () => {
  const ANSWER =
    "According to the metrics catalog, **friction** is defined as:\n" +
    "$$\\text{flow\\_friction\\_per\\_item} = \\frac{2 \\times (\\text{draft bounces} + " +
    "\\text{reopens})}{\\text{owned items}}$$";

  it("keeps the TeX in an attribute for the typesetter", () => {
    const html = mxMarkdown(ANSWER);
    expect(html).toContain('class="mx-math mx-math-block"');
    expect(html).toContain('data-tex="\\text{flow\\_friction');
    expect(html).toContain('data-display="1"');
  });

  it("shows the plain reading until the typesetter arrives", () => {
    // The point of the fallback: the math chunk is lazy, so this is what a reader sees for
    // the first moment — and permanently if the chunk cannot load.
    const text = visible(mxMarkdown(ANSWER));
    expect(text).toContain(
      "flow_friction_per_item = (2 × (draft bounces + reopens)) / (owned items)");
    expect(text).not.toContain("\\frac");
    expect(text).not.toContain("$$");
  });

  it("does not mangle the identifier on the way through", () => {
    expect(mxMarkdown(ANSWER)).not.toContain("<em>");
  });

  it("marks inline math as inline", () => {
    const html = mxMarkdown("so \\(x + y\\) holds");
    expect(html).toContain('class="mx-math"');
    expect(html).not.toContain("data-display");
  });
});

describe("single dollars: math, or money", () => {
  it("treats $…$ as math", () => {
    expect(mxMarkdown("the ratio $a + b$ matters")).toContain("data-tex=\"a + b\"");
  });

  it("leaves a price alone", () => {
    for (const money of [
      "it cost $0.0021 per turn",
      "$0.03 and $0.35 for two turns",
      "costs $5-$10 per seat",        // body "5-" — this one got through every earlier guard
      "between $1,200 and $3,400",
      "$5 or $10",
    ]) {
      expect(mxMarkdown(money), money).not.toContain("mx-math");
    }
  });

  it("still recognises math that happens to be numeric", () => {
    // A hyphen does not qualify a body as math, but an operator money never uses does.
    expect(mxMarkdown("the term $2^{10}$ appears")).toContain("mx-math");
    expect(mxMarkdown("with $x_1$ and $x_2$")).toContain("mx-math");
  });

  it("leaves a lone dollar alone", () => {
    expect(mxMarkdown("about $5 total")).not.toContain("mx-math");
  });
});

describe("the seam between the markup and the effect that typesets it", () => {
  // The widget's effect queries `.mx-math[data-tex]:not([data-typeset])`. That selector and
  // the markup below are written in two different places and nothing else couples them, so a
  // rename in either one would silently stop every formula from being typeset — the fallback
  // would stay on screen and look deliberate.
  const SELECTOR = ".mx-math[data-tex]:not([data-typeset])";

  const dom = (html: string) => {
    const host = document.createElement("div");
    host.innerHTML = html;
    return host;
  };

  it("the emitted placeholder is what the effect looks for", () => {
    const host = dom(mxMarkdown("$$a + b$$ and \\(c\\)"));
    expect(host.querySelectorAll(SELECTOR).length).toBe(2);
  });

  it("a placeholder already typeset is skipped", () => {
    const host = dom(mxMarkdown("$$a + b$$"));
    const el = host.querySelector(SELECTOR) as HTMLElement;
    el.setAttribute("data-typeset", "1");
    expect(host.querySelectorAll(SELECTOR).length).toBe(0);
  });

  it("the tex survives the round trip through the attribute", () => {
    const host = dom(mxMarkdown("$$\\frac{a}{b}$$"));
    expect((host.querySelector(SELECTOR) as HTMLElement).getAttribute("data-tex"))
      .toBe("\\frac{a}{b}");
  });

  it("prose alone gives the effect nothing to do", () => {
    expect(dom(mxMarkdown("no formulas here, just words")).querySelectorAll(SELECTOR).length)
      .toBe(0);
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

  it("escapes the quote that would break out of the tex attribute", () => {
    // mxEsc handles & < > and not quotes, and the TeX now travels in an attribute.
    const html = mxMarkdown('$$\\text{a" onmouseover="alert(1)}$$');
    expect(html).not.toMatch(/data-tex="[^"]*"\s+onmouseover/);
    expect(html).toContain("&quot;");
  });
});
