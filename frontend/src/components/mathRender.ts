// Math for the chat panel, in its own module so it is only ever fetched by a conversation that
// actually contains a formula. The payload — 164 KB of library, a 6 KB stylesheet and a 12 KB
// woff2 covering only script capitals — arrives on the first formula a reader sees and never
// for anybody else. That is the rule this project already applies to charts: bundled rather
// than fetched from a CDN, and not downloaded by pages that do not need it.
//
// Temml rather than KaTeX because it emits MathML and lets the browser typeset, so there is
// almost no font payload; KaTeX ships ~280 KB plus twenty-odd font files, all of which would
// have to be baked in since runtime CDNs are not allowed here. The trade is that the shapes
// depend on the system math font — Cambria Math, STIX Two, Noto Sans Math — so a fraction
// looks slightly different across platforms.
//
// WHY THE LIBRARY'S OWN BUNDLE, loaded as an asset, rather than `import temml from "temml"`:
// letting Vite bundle the package produced a build in which every command failed. Measured, in
// a real browser: "a + b" typeset, while \frac, \text and \times all threw — and the identical
// code passed unminified under vitest, and the package's own temml.min.js handled all four in
// the same browser. So it is something about the re-bundling; I could not pin down what, and a
// comment claiming a cause I have not proven would be worse than this one. Using the artifact
// the library itself ships and tests sidesteps the question, still self-hosted and hashed by
// Vite's asset pipeline.
import temmlUrl from "temml/dist/temml.min.js?url";

// The Local variant: system math fonts for the bulk, the small bundled woff2 for the script
// capitals it cannot get otherwise. Imported HERE so Vite puts it in this lazy chunk.
import "temml/dist/Temml-Local.css";

type Temml = { renderToString: (tex: string, opts?: Record<string, unknown>) => string };

let pending: Promise<Temml> | null = null;

/** Fetch the library once per page, whoever asks first. */
function load(): Promise<Temml> {
  if (pending) return pending;
  pending = new Promise<Temml>((resolve, reject) => {
    const existing = (window as unknown as { temml?: Temml }).temml;
    if (existing) return resolve(existing);
    const s = document.createElement("script");
    s.src = temmlUrl;
    s.async = true;
    s.onload = () => {
      const t = (window as unknown as { temml?: Temml }).temml;
      t ? resolve(t) : reject(new Error("temml loaded but defined nothing"));
    };
    s.onerror = () => reject(new Error("temml failed to load"));
    document.head.appendChild(s);
  });
  return pending;
}

/**
 * Typeset one placeholder. Resolves false if the TeX was not usable, leaving the element
 * exactly as it was — the caller has already put a plain-text reading inside it.
 *
 * renderToString plus DOMParser, not a DOM-writing entry point: temml.render() clears the
 * target element BEFORE parsing and throws afterwards on some input, which destroys the
 * fallback and leaves a blank line — measured, on a formula containing \frac. Parsing a string
 * is what lets the swap happen only after success. DOMParser executes nothing it parses, and
 * importNode carries the <math> tree over without routing library output through the panel's
 * one dangerouslySetInnerHTML path, which escapes everything it is given.
 */
export async function renderMath(el: HTMLElement, tex: string,
                                 display: boolean): Promise<boolean> {
  try {
    const temml = await load();
    const markup = temml.renderToString(tex, { displayMode: display, throwOnError: true });
    const math = new DOMParser().parseFromString(markup, "text/html").querySelector("math");
    if (!math) return false;
    el.replaceChildren(document.importNode(math, true));
    return true;
  } catch {
    return false;
  }
}
