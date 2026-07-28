// The chip row shared across the report views —
//   .chips > span[data-tip?] × items
// A flex-wrap row of small pills. The chip CONTENT differs per site (a GhLink +
// tag, a repo name, a "login ·member → forks" string, or a "path · Nv" label),
// so the caller builds each `content` node and this widget only owns the
// `.chips` wrapper + the `<span data-tip?>` shell. The optional `data-tip` is
// omitted when absent (no empty attribute). An optional `style` covers the one
// site (Traffic popular-paths) whose wrapper carries an inline margin. Ported
// verbatim from the in-page copies in Traffic/Repositories; emits byte-identical
// DOM so the pixel gate sees no diff.
import type React from "react";

export type ChipItem = {
  content: React.ReactNode;        // the chip's inner nodes (page-built)
  tip?: string;                    // data-tip on the span (optional)
  key: string;                     // React list key (not rendered)
};

export type ChipsProps = {
  items: ChipItem[];
  style?: React.CSSProperties;     // e.g. { marginTop: 6 } (Traffic popular paths)
};

export function Chips({ items, style }: ChipsProps) {
  return (
    <div className="chips" style={style}>
      {items.map((c) => (
        <span key={c.key} {...(c.tip ? { "data-tip": c.tip } : {})}>{c.content}</span>
      ))}
    </div>
  );
}
