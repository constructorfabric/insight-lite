// The proportion / composition bar shared across the report views — a flex row
// of <i> segments whose widths sum to ~100% (`.split2` / `.split` / `.cmix-bar
// wtbar`), optionally paired with a `.leg2` legend (swatch + label + bold % +
// optional "· value"). Ported verbatim from the in-page copies in
// Overview/Person/AiTools/Repositories; emits byte-identical DOM so the pixel
// gate sees no diff. Data-agnostic: the caller resolves widths/labels/colours
// and passes already-formatted values. The bar and its legend are SIBLINGS in
// every current site (not nested), so they are two components the caller places
// next to each other, keeping any wrapper (a `.sub` heading, a `.pcard`) around
// them in the page.
import type React from "react";

export type Segment = {
  // Bar segment width, spliced straight into `width:${pct}%`. Accepts a string
  // because callers pass pre-formatted values (fmtPct()/jr()/server strings) —
  // forcing a number would change the rendered width string and break parity;
  // number callers (Person split2, Overview work-type) pass the raw expression.
  pct: number | string;
  color: string;                   // segment fill + legend swatch background
  label?: string;                  // legend label text (omit for bars with no legend)
  value?: React.ReactNode;         // legend trailing "· value" (rendered when show includes "value")
  // Legend bold "<b>{pctText ?? pct}%</b>". Some sites show a legend % that
  // differs from the bar-segment width (Person: a rounded bar width vs the
  // server's pct string; Repositories: bar `width` vs legend `pct`), so it is a
  // separate field defaulting to `pct` when they coincide.
  pctText?: number | string;
  tip?: string;                    // data-tip on the bar segment
  text?: React.ReactNode;          // children inside the bar <i> (.split bars label their segments)
  drill?: Record<string, string>;  // data-* spread on BOTH the bar <i> and the legend <span>
};

export type SplitBarProps = {
  segments: Segment[];
  className?: string;              // "split2" (default) | "split" | "cmix-bar wtbar"
  style?: React.CSSProperties;     // e.g. { height: 16 } (Repositories) / { marginTop: 8 } (Person impact)
  role?: string;                   // "img" (Overview work-type bar)
  ariaLabel?: string;              // paired aria-label
};

export function SplitBar({ segments, className = "split2", style, role, ariaLabel }: SplitBarProps) {
  return (
    <div className={className} style={style} role={role} aria-label={ariaLabel}>
      {segments.map((s, i) => (
        <i
          key={i}
          style={{ width: `${s.pct}%`, background: s.color }}
          {...(s.drill ?? {})}
          {...(s.tip ? { "data-tip": s.tip } : {})}
        >
          {s.text}
        </i>
      ))}
    </div>
  );
}

export type LegendProps = {
  segments: Segment[];
  className?: string;              // "leg2" (default)
  // Which parts to render after the label: the bold "% " and/or the "· value".
  // Person's code-vs-specs and Repositories show both; Person's repo-types shows
  // only the pct.
  show?: ("pct" | "value")[];
  swatchClass?: string;            // swatch element class (default "sw")
};

export function Legend({ segments, className = "leg2", show = ["pct", "value"], swatchClass = "sw" }: LegendProps) {
  const showPct = show.includes("pct");
  const showValue = show.includes("value");
  return (
    <div className={className}>
      {segments.map((s, i) => (
        <span key={i} {...(s.drill ?? {})}>
          <span className={swatchClass} style={{ background: s.color }} />{s.label}
          {showPct && <> <b>{s.pctText ?? s.pct}%</b></>}
          {showValue && s.value !== undefined && <> · {s.value}</>}
        </span>
      ))}
    </div>
  );
}
