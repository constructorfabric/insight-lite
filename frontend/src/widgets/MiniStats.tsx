// The stat-tile row shared across the report views —
//   .mini > .m[data-*?] > .mv{value} + .ml{label}
// A horizontal strip of small "big number + caption" tiles. Some tiles are
// drillable (a data-drill/data-* set spread on the .m, e.g. People's reviews
// mini) and some captions embed a `<span class="prec exact|heuristic">` badge
// or an `<span class="alltime-tag">` — so `label` (and `value`) are ReactNodes
// the caller builds, keeping the badge/spacing byte-exact. Ported verbatim from
// the in-page copies in AiTools/People/Traffic/Repositories; emits
// byte-identical DOM so the pixel gate sees no diff. Data-agnostic: the caller
// resolves the numbers/captions.
import type React from "react";

export type MiniStatItem = {
  value: React.ReactNode;          // .mv content (number or node)
  label: React.ReactNode;          // .ml caption (may embed a .prec / .alltime-tag span)
  drill?: Record<string, string>;  // data-drill/data-* spread on the .m (optional; omitted when absent)
};

export type MiniStatsProps = { items: MiniStatItem[] };

export function MiniStats({ items }: MiniStatsProps) {
  return (
    <div className="mini">
      {items.map((it, i) => (
        <div className="m" key={i} {...(it.drill ?? {})}>
          <div className="mv">{it.value}</div>
          <div className="ml">{it.label}</div>
        </div>
      ))}
    </div>
  );
}
