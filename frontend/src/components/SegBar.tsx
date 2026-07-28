// A horizontal stacked proportional bar — port of the segbar() Jinja macro
// (templates/panels/01_helpers.j2): one primitive for band splits, the
// developer-score make-up chips, or any part-of-whole distribution. Needs the
// shared .segbar/.segleg CSS (frontend/src/styles/report.css).
export type Segment = { value: number; color: string; label?: string; tip?: string };

export default function SegBar({
  segments, height = 14, legend = false,
}: {
  segments: Segment[];
  height?: number;
  legend?: boolean;
}) {
  return (
    <>
      <span className="segbar" style={{ height }} role="img">
        {segments
          .filter((s) => s.value)
          .map((s, i) => (
            <i
              key={i}
              style={{ flex: s.value, background: s.color }}
              data-tip={s.tip || `${s.label ?? ""}: ${s.value}`}
            />
          ))}
      </span>
      {legend && (
        <span className="segleg">
          {segments.map((s, i) => (
            <span key={i}>
              <i style={{ background: s.color }} />
              {s.label} <b>{s.value}</b>
            </span>
          ))}
        </span>
      )}
    </>
  );
}
