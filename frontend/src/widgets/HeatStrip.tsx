// The weekly commit-intensity strip on the Person dashboard —
//   .heat[role=img][aria-label] > .hc[style="--a:…"][data-tip] × weeks
// Each cell's `--a` CSS var is the week's commits normalised to the busiest
// week (rounded to 3 dp), driving the fill opacity; the data-tip carries the
// week/commits/issues label the shared report tooltip JS reads. Ported verbatim
// from Person's in-page copy — the exact `--a` computation and data-tip text are
// preserved so the pixel gate sees no diff.
import type React from "react";

export type HeatWeek = { week: string; commits: number; issues: number };
export type HeatStripProps = { heat: HeatWeek[] };

export function HeatStrip({ heat }: HeatStripProps) {
  const mx = Math.max(1, ...heat.map((h) => h.commits));
  return (
    <div className="heat" role="img" aria-label="weekly commit activity">
      {heat.map((h, i) => (
        <span
          key={i} className="hc"
          style={{ "--a": Math.round((h.commits / mx) * 1000) / 1000 } as React.CSSProperties}
          data-tip={`${h.week} — ${h.commits} commits, ${h.issues} issues`}
        />
      ))}
    </div>
  );
}
