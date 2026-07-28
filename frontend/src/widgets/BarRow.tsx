// The horizontal "label · bar · value" row shared across report lists —
//   .row[data-*?] > .nm[data-tip?] + .bb>.bar>i[style width/background] + .vv
// often drillable (either via data-* on the .row, or via a .dr span the caller
// embeds inside `value`), and — through BarList — optionally followed by a
// collapsible "+N more" tail (.row.more-row + .more-tail[hidden]) driven by the
// shared report JS. Ported verbatim from the in-page copies in
// People/Person/Traffic/Overview; emits byte-identical DOM so the pixel gate
// sees no diff. Data-agnostic: the caller resolves numbers/labels and passes
// already-formatted nodes.
import type React from "react";

export type BarRowProps = {
  label: React.ReactNode;          // .nm content (GhLink, text, swatch+text, …)
  tip?: string;                    // data-tip on .nm (email, repo, …)
  // Bar width, spliced straight into `width:${pct}%`. Accepts a string because
  // callers pass a pre-formatted value (People/Overview use fmtPct(), which
  // strips a trailing ".0") — forcing a number would change the rendered width
  // string and break parity. Person/Traffic pass a raw number.
  pct: number | string;
  color?: string;                  // bar fill (default var(--acc))
  value: React.ReactNode;          // .vv content (already-formatted; may embed a .dr drill span)
  drill?: Record<string, string>;  // data-drill/data-* spread on the .row (optional)
  cls?: string;                    // extra row class if a site needs it
};

export function BarRow({ label, tip, pct, color = "var(--acc)", value, drill, cls }: BarRowProps) {
  return (
    <div className={cls ? `row ${cls}` : "row"} {...(drill ?? {})}>
      <div className="nm" {...(tip ? { "data-tip": tip } : {})}>{label}</div>
      <div className="bb"><div className="bar"><i style={{ width: `${pct}%`, background: color }} /></div></div>
      <div className="vv">{value}</div>
    </div>
  );
}

// The "+N more" tail. Two shapes exist in the wild, distinguished by which
// fields are present:
//  · People categories: a full bar + value row (pct + value + color=var(--mut)).
//  · Traffic contributors: empty .bb and .vv (pct/value omitted).
export type BarListTail = {
  moreLabel: string;               // .nm text + data-more
  lessLabel: string;               // data-less
  pct?: number | string;           // present → render .bb>.bar>i; absent → empty .bb
  value?: React.ReactNode;         // present → render in .vv; absent → empty .vv
  color?: string;                  // tail bar fill (default var(--acc))
};

export type BarListProps = {
  rows: BarRowProps[];
  cap?: number;                    // visible rows before the tail (undefined → all)
  tail?: BarListTail;              // the "+N more" collapsible slice
};

export function BarList({ rows, cap, tail }: BarListProps) {
  const visible = cap == null ? rows : rows.slice(0, cap);
  const rest = cap == null ? [] : rows.slice(cap);
  return (
    <>
      {visible.map((r, i) => <BarRow key={i} {...r} />)}
      {tail && (
        <>
          <div className="row more-row" data-more={tail.moreLabel} data-less={tail.lessLabel}>
            <div className="nm">{tail.moreLabel}</div>
            {tail.pct !== undefined ? (
              <div className="bb"><div className="bar"><i style={{ width: `${tail.pct}%`, background: tail.color ?? "var(--acc)" }} /></div></div>
            ) : (
              <div className="bb" />
            )}
            {tail.value !== undefined ? (
              <div className="vv">{tail.value}</div>
            ) : (
              <div className="vv" />
            )}
          </div>
          <div className="more-tail" hidden>
            {rest.map((r, i) => <BarRow key={i} {...r} />)}
          </div>
        </>
      )}
    </>
  );
}
