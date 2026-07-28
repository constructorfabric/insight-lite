// Renders one Vega-Lite spec (as produced server-side by vega_spec.py) via the
// vendored vega-embed global — the React equivalent of report.j2's hydrateVega()
// (see templates/report.j2's inline <script>): idempotent, width-guarded (embeds
// once the container has a real clientWidth, retrying via ResizeObserver if it
// doesn't yet — e.g. a chart inside a still-hidden tab), same options
// ({actions:false, renderer:'svg', tooltip:true}) so the rendered SVG marks are
// pixel-identical to the server-hydrated `.vl-panel` the monolith uses.
//
// SSR-safe: only touches window/document inside the effect.
import { useEffect, useRef } from "react";

declare global {
  interface Window {
    vegaEmbed?: (el: Element, spec: unknown, opts: Record<string, unknown>) => Promise<unknown>;
  }
}

export default function VegaChart({ spec, waitForFonts }: { spec: unknown; waitForFonts?: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || !spec) return;
    let done = false;
    let cancelled = false;
    let ro: ResizeObserver | null = null;

    // Vega's canvas-based text measurement (used to auto-size the axis-label
    // margin) reads whatever face is CURRENTLY registered for the config's
    // font string (vega_spec.py's `_FONT`, "Inter, ...") at the instant it
    // runs — silently falling back to a system font, with no error, if the
    // Inter webfont hasn't finished loading yet, a few-px auto-margin drift
    // invisible on a coarse chart but large enough to matter on a fine-
    // grained one (many x-axis ticks amplify a 2-3px shift). Whether the
    // monolith's OWN embed happens to race ahead of or behind that font load
    // depends on which of ITS code paths renders that specific panel (a
    // build-time/cached fast path embeds immediately, before ANY page text
    // has triggered the font fetch; a live AJAX-replaced panel embeds after
    // the fetch, by which point the font has had time to load) — so there's
    // no single "always wait" answer that matches the monolith everywhere.
    // `waitForFonts` lets the CALLER (which knows whether ITS view's current
    // state corresponds to the monolith's fast path or its live-refetch path
    // — see pages/Trend.tsx) opt in per-chart; default false preserves the
    // original immediate-embed behaviour (matches Overview/WhatsNew, whose
    // charts are never touched by a live refetch).
    async function withFontsReady() {
      if (!waitForFonts) return;
      try {
        if (document.fonts) {
          await Promise.all([document.fonts.load("10px Inter"), document.fonts.load("11px Inter")]);
          await document.fonts.ready;
        }
      } catch {
        // best-effort — embed anyway rather than block the chart on a font failure
      }
    }

    async function embed() {
      await withFontsReady();
      if (cancelled || !el) return;
      window.vegaEmbed!(el, spec, { actions: false, renderer: "svg", tooltip: true }).catch(() => {
        if (el) el.innerHTML = '<div class="dp-err">chart failed</div>';
      });
    }

    function tryEmbed(): boolean {
      if (done) return true;
      if (!el || !el.clientWidth) return false;   // not laid out yet — wait for width
      if (!window.vegaEmbed) return false;
      done = true;
      embed();
      return true;
    }

    if (!tryEmbed() && typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(() => {
        if (tryEmbed() && ro) ro.disconnect();
      });
      ro.observe(el);
    }
    return () => {
      cancelled = true;
      if (ro) ro.disconnect();
    };
  }, [spec, waitForFonts]);

  if (!spec) return null;
  return <div className="vl-panel" ref={ref} />;
}
