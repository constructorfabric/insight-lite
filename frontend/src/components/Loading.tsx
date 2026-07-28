// Skeleton placeholder for the client-fetched report views. Every report route
// fetches /api/report/<view> on mount; until this existed they rendered `null`
// meanwhile, so a cold load showed a blank content area (~1-1.5s on prod for
// delivery/flow/trend) that read as a broken page.
//
// Why a skeleton and not a spinner: it shows the page's real shape while data is
// in flight and — because it REUSES the actual layout classes (.sub, .topbar,
// .period-bar, .card) rather than inventing its own box — the placeholder blocks
// sit exactly where the content will land, so nothing jumps when it swaps in.
// That frame (description line → filter strip → heading → first card) is common
// to all 10 report views, verified against the live DOM, which is why one
// component covers them without per-page drift.
//
// Styling: styles/report.css (.sk*), loaded by every report entry. Leaves the DOM
// as soon as data arrives, and the pixel gate captures after network-idle, so
// screenshots never contain it. `role=status` + a visually-hidden label keeps it
// announced for screen readers; the sweep honours prefers-reduced-motion.
export default function Loading({ label = "Loading the report…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite">
      <span className="sk-sr">{label}</span>

      {/* the description line under the page heading */}
      <p className="sub" style={{ height: 20, display: "flex", alignItems: "center" }}>
        <span className="sk sk-line" style={{ width: "64%" }} />
      </p>

      {/* the filter strip: period chips + custom-range button, scope select, legend */}
      <div className="topbar">
        <div className="period-bar">
          <span className="sk sk-line" style={{ width: 45, height: 17 }} />
          <span className="sk" style={{ width: 383, height: 42, borderRadius: 999 }} />
          <span className="sk" style={{ width: 86, height: 34, borderRadius: 999 }} />
        </div>
        <div className="period-bar">
          <span className="sk sk-line" style={{ width: 34, height: 17 }} />
          <span className="sk" style={{ width: 280, height: 36, borderRadius: 10 }} />
        </div>
        {/* the collapsed legend row — without it the strip would be ~40px shorter
            than the real one and the content would still shift on swap-in */}
        <div className="period-legend" style={{ width: "100%", display: "flex", flexDirection: "column", gap: 11 }}>
          <span className="sk sk-line" style={{ width: "92%", height: 12 }} />
          <span className="sk sk-line" style={{ width: "58%", height: 12 }} />
        </div>
      </div>

      {/* first section heading + panel */}
      <div className="sk sk-h2" />
      <div className="card sk-card">
        <div className="sk-tiles">
          {[0, 1, 2, 3].map((i) => (
            <span className="sk sk-tile" key={i} />
          ))}
        </div>
        {[0, 1, 2, 3, 4].map((i) => (
          <div className="sk-row" key={i}>
            <span className="sk sk-nm" />
            <span className="sk sk-bar" style={{ maxWidth: `${72 - i * 11}%` }} />
            <span className="sk sk-vv" />
          </div>
        ))}
      </div>
    </div>
  );
}
