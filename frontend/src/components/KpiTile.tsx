// The KPI tile — a React port of kpi_tile()/kchip()/sparkline()/deltachip()
// (templates/panels/01_helpers.j2). `value`/`sub`/delta text are passed
// PRE-FORMATTED by the server (render.py's _kpi_tiles_json / _num / _loc /
// _pct) so this stays a pure render, never a re-format — the one thing that
// must never drift between the Jinja and React paths.
import type { ReactNode } from "react";

// kchip()'s per-category icon paths, verbatim — one visual language kept in
// sync with the Jinja macro by hand (see that macro's own `ic` dict).
const ICONS: Record<string, string> = {
  commit: '<circle cx="12" cy="12" r="3.2"/><path d="M3 12h5.6M15.4 12H21"/>',
  loc: '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
  pr: '<circle cx="6" cy="6" r="2.6"/><circle cx="6" cy="18" r="2.6"/><path d="M6 8.6v6.8"/><circle cx="18" cy="18" r="2.6"/><path d="M18 15.4V11a2.5 2.5 0 0 0-2.5-2.5H10"/><path d="M12.5 6 10 8.5 12.5 11"/>',
  spec: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6"/><path d="M8.5 13h7M8.5 17h7"/>',
  bug: '<rect x="8" y="8" width="8" height="11" rx="4"/><path d="M12 8V6"/><path d="m9.5 6.5-1.5-1.5M14.5 6.5 16 5"/><path d="M8 11H4.5M16 11h3.5M8 15H4.5M16 15h3.5"/>',
  story: '<path d="M2 4h6a3 3 0 0 1 3 3v13a2.5 2.5 0 0 0-2.5-2.5H2Z"/><path d="M22 4h-6a3 3 0 0 0-3 3v13a2.5 2.5 0 0 1 2.5-2.5H22Z"/>',
  epic: '<path d="m12 2 3 6 6 .9-4.5 4.3 1 6-5.5-2.8L6.5 19l1-6L3 8.9 9 8Z"/>',
  feature: '<path d="M12 3v18M3 12h18"/><path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
  people: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
};

export type KpiDelta = { cls: string; text: string; tip: string } | null;

export type KpiTileData = {
  icon?: string | null;
  value: string;
  label: string;
  sub?: string;
  tip?: string | null;
  delta?: KpiDelta;
  sparkPts?: string | null;
  sparkColor?: string;
  drill?: Record<string, string> | null;
};

function svg(html: string): ReactNode {
  return <svg className="i" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: html }} />;
}

function Sparkline({ pts, color }: { pts?: string | null; color?: string }) {
  if (!pts) return null;
  return (
    <svg className="spark" viewBox="0 0 100 26" preserveAspectRatio="none" aria-hidden="true">
      <polyline
        points={pts} fill="none" stroke={color} strokeWidth={1.6}
        vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round"
      />
    </svg>
  );
}

export default function KpiTile({ icon, value, label, sub, tip, delta, sparkPts, sparkColor, drill }: KpiTileData) {
  const drillAttrs: Record<string, string> = {};
  if (drill) for (const [k, v] of Object.entries(drill)) drillAttrs[`data-${k}`] = v;
  return (
    <div className="kpi" {...drillAttrs}>
      {(icon || delta) && (
        <div className="ktop" style={icon ? undefined : { justifyContent: "flex-end" }}>
          {icon && (
            <span className="ico" style={{ background: `var(--c-${icon})` }}>
              {svg(ICONS[icon] ?? "")}
            </span>
          )}
          {delta && (
            <span className={`dlt ${delta.cls}`} data-tip={delta.tip}>{delta.text}</span>
          )}
        </div>
      )}
      <div className="n num" data-tip={tip ?? undefined}>{value}</div>
      <div className="l">{label}</div>
      {sub && <div className="l2">{sub}</div>}
      <Sparkline pts={sparkPts} color={sparkColor} />
    </div>
  );
}
