// A marker/provenance table — the hand-written `<table class="dt">` the
// monolith paints for studio_prov / gears_usage / fabric_trackers. Columns are
// Repo + one per content marker; each marker header carries a
// `<span class="prec exact|heuristic">` badge (part of the thead). A single
// <tbody> wraps the header + data rows so the browser-auto-tbody
// `tbody tr:last-child td{border-bottom:none}` behaviour matches. Cells are
// "<files> / <lines>", dim-zeroed via zc() to match dimZeros(). Promoted
// verbatim from the in-page copy in pages/AiTools.tsx (used for the studio /
// gears / per-tracker provenance tables); emits byte-identical DOM so the pixel
// gate sees no diff. Data-agnostic: the caller resolves the marker rollups.
import { zeroClass } from "../components/DataTable";

export type MarkerBadge = { marker: string; prec: string };
export type MarkerCell = { files: number; lines: number };
export type MarkerRow = { repo: string; cells: MarkerCell[] };
export type MarkerTableData = {
  markers: string[]; badges: MarkerBadge[]; totals: unknown; repoCount: number; rows: MarkerRow[];
};

// zeroClass() returns " z" (leading space) or ""; normalise to the DOM class —
// same helper the AiTools page uses for its own hand-rolled tables so a bare
// "0 / 0" cell fades exactly like the monolith's dimZeros() does at runtime.
function zc(text: string): string | undefined {
  return zeroClass(text) ? "z" : undefined;
}

export function MarkerTable({ table, empty, repoHead }: { table: MarkerTableData; empty: string; repoHead: string }) {
  const colspan = table.markers.length + 1;
  return (
    <table className="dt">
      <tbody>
        <tr>
          <th className="sortable">{repoHead}</th>
          {table.badges.map((b) => (
            <th className="sortable" key={b.marker}>
              {b.marker} <span className={`prec ${b.prec}`}>{b.prec}</span> (files / lines)
            </th>
          ))}
        </tr>
        {table.rows.length > 0 ? (
          table.rows.map((r) => (
            <tr key={r.repo}>
              <td className={zc(r.repo)} data-sort={r.repo}>{r.repo}</td>
              {r.cells.map((c, i) => {
                const text = `${c.files} / ${c.lines}`;
                // sort by file count (raw) — matches the monolith's data-sort on
                // these provenance cells (report.j2's studio_prov/gears rows).
                return (
                  <td className={zc(text)} key={i} data-sort={String(c.files)}>
                    {c.files} / {c.lines}
                  </td>
                );
              })}
            </tr>
          ))
        ) : (
          <tr>
            <td colSpan={colspan} className="muted">
              {empty}
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
