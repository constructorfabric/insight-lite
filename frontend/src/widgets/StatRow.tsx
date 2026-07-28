// A key/value stat line shared across the Person "Lasting impact" cards —
//   .statrow > .sk[data-tip?]{k} + .sv{v}
// The key carries an optional `data-tip` (the shared report tooltip JS reads
// it); omitted when there is no tip so no empty attribute is emitted. Ported
// verbatim from Person's in-page copy; emits byte-identical DOM so the pixel
// gate sees no diff.
import type React from "react";

export type StatRowProps = { k: React.ReactNode; v: React.ReactNode; tip?: string };

export function StatRow({ k, v, tip }: StatRowProps) {
  return (
    <div className="statrow">
      <span className="sk" {...(tip ? { "data-tip": tip } : {})}>{k}</span>
      <span className="sv">{v}</span>
    </div>
  );
}
