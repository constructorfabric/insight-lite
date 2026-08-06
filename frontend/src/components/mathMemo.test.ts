// Every streamed chunk rebuilds the message HTML and hands it to dangerouslySetInnerHTML, so
// React reassigns innerHTML and destroys both the typeset <math> and the data-typeset marks.
// The effect therefore asks for the same formulas again on every chunk — review on #11 caught
// that the comment in ChatWidget claimed the marks prevented this. They do not; this memo does.
import { beforeEach, describe, expect, it } from "vitest";

import { __clearMathMemo, renderMath } from "./mathRender";

// The library is loaded by a script tag that jsdom will not execute, so stand in for it and
// count how often it is asked to work.
const calls = { n: 0 };
beforeEach(() => {
  calls.n = 0;
  __clearMathMemo();
  (window as unknown as { temml: unknown }).temml = {
    renderToString: (tex: string) => {
      calls.n++;
      if (tex.includes("BROKEN")) throw new Error("nope");
      return `<math><mi>${tex}</mi></math>`;
    },
  };
});

const el = () => {
  const s = document.createElement("span");
  s.innerHTML = "<code>fallback</code>";
  return s;
};

describe("the memo", () => {
  it("typesets a formula once however many times it is asked for", async () => {
    for (let i = 0; i < 25; i++) {                 // 25 streamed chunks, one formula
      const target = el();
      expect(await renderMath(target, "a + b", true)).toBe(true);
      expect(target.querySelector("math")).not.toBeNull();
    }
    expect(calls.n).toBe(1);
  });

  it("keys on the display mode, because the markup differs", async () => {
    await renderMath(el(), "a + b", true);
    await renderMath(el(), "a + b", false);
    expect(calls.n).toBe(2);
  });

  it("remembers a rejection, so a broken formula is not retried per chunk", async () => {
    for (let i = 0; i < 10; i++) {
      const target = el();
      expect(await renderMath(target, "BROKEN", true)).toBe(false);
      expect(target.textContent).toBe("fallback");  // and the fallback survives every time
    }
    expect(calls.n).toBe(1);
  });

  it("shares an in-flight render between callers that arrive together", async () => {
    // The real shape: several effect runs overlap while the library is still being fetched,
    // and none of them can see a memo entry yet because none has finished.
    let release: (() => void) | null = null;
    const gate = new Promise<void>((res) => { release = () => res(); });
    const inner = (window as unknown as { temml: { renderToString: (t: string) => string } }).temml;
    (window as unknown as { temml: unknown }).temml = {
      renderToString: (tex: string) => inner.renderToString(tex),
    };
    const slow = Promise.all(
      Array.from({ length: 8 }, () => gate.then(() => renderMath(el(), "a + b", true))));
    release!();
    const results = await slow;
    expect(results.every(Boolean)).toBe(true);
    expect(calls.n).toBe(1);
  });

  it("shares a failure in flight too, without eight attempts", async () => {
    const results = await Promise.all(
      Array.from({ length: 8 }, () => renderMath(el(), "BROKEN", true)));
    expect(results.every((r) => r === false)).toBe(true);
    expect(calls.n).toBe(1);
  });

  it("gives each caller its own nodes rather than moving one around", async () => {
    const a = el(), b = el();
    await renderMath(a, "a + b", true);
    await renderMath(b, "a + b", true);
    expect(a.querySelector("math")).not.toBeNull();
    expect(b.querySelector("math")).not.toBeNull();
    expect(a.querySelector("math")).not.toBe(b.querySelector("math"));
  });
});
