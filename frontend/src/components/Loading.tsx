// Skeleton placeholder for the client-fetched report views. Every report route
// fetches /api/report/<view> on mount; until this existed they rendered `null`
// meanwhile, so a cold load showed a blank content area (~1-1.5s on prod for
// delivery/flow/trend) that read as a broken page.
//
// Why a skeleton and not a spinner: it shows the page's real shape while data is
// in flight and — because it REUSES the actual layout classes (.topbar,
// .period-bar, .card) rather than inventing its own box — the placeholder blocks
// sit exactly where the content will land, so nothing jumps when it swaps in.
// That frame (filter strip → heading → first card) is common to all 10 report
// views, verified against the live DOM, which is why one component covers them
// without per-page drift.
//
// It used to open with a `.sub` line too — the per-page "Org … · generated …"
// caption. That line moved into the sidebar's brand block (shell.report_caption),
// and nothing removed it from here, so every report page grew a grey bar above the
// filters that appeared and then vanished with nothing taking its place. A skeleton
// for markup the page no longer has is worse than no skeleton: it is a promise of
// content that never arrives.
//
// The one part that is NOT a placeholder is the filter bar — see the comment on it
// below.
//
// Styling: styles/report.css (.sk*), loaded by every report entry. Leaves the DOM
// as soon as data arrives, and the pixel gate captures after network-idle, so
// screenshots never contain it. `role=status` + a visually-hidden label keeps it
// announced for screen readers; the sweep honours prefers-reduced-motion.
import FilterBar from "./FilterBar";
import useFilterModel from "../hooks/useFilterModel";

export default function Loading({ label = "Loading the report…" }: { label?: string }) {
  const filters = useFilterModel();
  return (
    <div role="status" aria-live="polite">
      <span className="sk-sr">{label}</span>

      {/* The filter strip is the REAL bar, not a placeholder: its options come from
          the `#filter-model` island the server inlines (see hooks/useFilterModel),
          so it can paint and take clicks while the payload is still on the wire —
          and it is literally the same component the page renders next, so there is
          nothing left to shift. The skeleton strip stays only for the case with no
          island at all (no report model collected yet). */}
      {filters ? <FilterBar {...filters} /> : (
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
        </div>
      )}

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
