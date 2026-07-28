// Schema-driven table — a React port of the data_table()/_dt_cell() Jinja
// macros (templates/panels/01_helpers.j2). Same column-kind vocabulary (text
// w/ colour-dot swatch, bar, heatmap, num/loc/pctp/raw formats), so a view can
// declare columns once and get the exact table markup/classes the monolith's
// generic table renderer produces. Sorting/cap ("+N more") interactivity is
// NOT ported (the monolith's table starts unsorted/uncapped too — see the
// `sortable` class, kept for future wiring but inert here); a column's
// `render` escape hatch covers any cell too bespoke for the kind vocabulary
// (e.g. the developer-score leaderboard's <SegBar> make-up column).
import type { ReactNode } from "react";
import { fmtLoc, fmtNum, fmtPct } from "../lib/format";

export type ColumnKind = "num" | "loc" | "pctp" | "raw" | "text" | "bar" | "heatmap" | "pair";

export type Column<R> = {
  // ReactNode (not just string) so a header can carry markup — e.g. Reviews'
  // "Median latency <span class=\"alltime-tag\">all-time</span>" — as JSX
  // directly instead of dangerouslySetInnerHTML.
  label: ReactNode;
  tip?: string;
  align?: "num";
  cls?: string;
  sortable?: boolean;
  // Raw value the click-to-sort listener (shell.SORT_JS) sorts by, emitted as the
  // cell's data-sort — port of the macro's `sort_key`/_dt_sort. Only needed to
  // override the default (key / contentKey); a `render` column with sortable text
  // (e.g. a name link) can rely on the listener's textContent fallback instead.
  sortKey?: string;
  kind?: ColumnKind;
  key?: string;
  // `key2`/`sep`: the second value field + separator for a kind="pair" cell
  // (port of _dt_cell's pair branch — e.g. "PRs (open / merged)" = opened / merged).
  key2?: string;
  sep?: string;
  widthKey?: string;
  contentKey?: string;
  contentFmt?: ColumnKind;
  alphaKey?: string;
  // "dot" = the inline 10x10 rounded swatch (colour from colorKey); "edot" = the
  // 9px round element dot (`.edot` CSS, colour from colorKey — ecolor() computed
  // server-side). Port of _dt_cell's `swatch=='dot'`/`swatch=='edot'` branches.
  swatch?: "dot" | "edot";
  colorKey?: string;
  unit?: string;
  dash?: boolean;
  drillIf?: string;
  drill?: Record<string, string>;
  tags?: { ifKey: string; text?: string; textKey?: string; prefix?: string; suffix?: string; cls?: string }[];
  render?: (row: R) => ReactNode;
};

function fmtValue(v: unknown, kind: ColumnKind | undefined): string {
  switch (kind) {
    case "loc": return fmtLoc(v);
    case "pctp": return `${fmtPct(v)}%`;
    case "raw": return v === null || v === undefined ? "" : String(v);
    default: return fmtNum(v);
  }
}

// Port of the monolith's dimZeros() (templates/report.j2's inline <script>):
// a plain-text cell whose content is exactly a zero value (0, 0%, 0/0, or the
// dash placeholder) gets faded (`td.z{color:var(--mut);opacity:.45}`) so real
// numbers stand out. The JS runs on every td with no element children —
// bar/heatmap/num/loc/pctp/raw/pair cells qualify; a 'text' cell WITH a
// swatch <span> child does not (matches `if(td.children.length) return`).
// Exported so any hand-rolled (non-DataTable) table — e.g. pages/Flow.tsx's
// by-person/dwell tables — can apply the exact same rule to its own bare-text
// `<td>`s instead of re-deriving the regex.
export function zeroClass(text: string): string {
  return /^0(\.0+)?\s*%?$/.test(text) || /^0\s*\/\s*0$/.test(text) || text === "—" ? " z" : "";
}

function drillAttrs<R extends Record<string, unknown>>(col: Column<R>, row: R): Record<string, string> {
  if (!col.drill) return {};
  if (col.drillIf && !row[col.drillIf]) return {};
  const attrs: Record<string, string> = {};
  for (const [k, v] of Object.entries(col.drill)) {
    attrs[`data-${k}`] = v.startsWith("@") ? String(row[v.slice(1)] ?? "") : v;
  }
  return attrs;
}

// Raw value emitted as the cell's data-sort, read by shell.SORT_JS — a port of
// the Jinja _dt_sort macro (templates/panels/01_helpers.j2). Returns undefined
// to omit the attribute: for a `render` column, so the sort listener falls back
// to the cell's textContent (right for a name/login link); or for a column
// explicitly opted out with sortable:false.
function sortValue<R extends Record<string, unknown>>(col: Column<R>, row: R): string | undefined {
  if (col.sortable === false) return undefined;
  if (col.sortKey) return String(row[col.sortKey] ?? "");
  if (col.render) return undefined;                               // → textContent fallback
  if (col.kind === "bar") {
    const v = col.contentKey ? row[col.contentKey] : (col.widthKey ? row[col.widthKey] : 0);
    return String(v ?? 0);
  }
  if (col.kind === "text") return String(col.key ? (row[col.key] ?? "") : "");
  if (col.dash && col.key && (row[col.key] === null || row[col.key] === undefined)) return "-1";
  return String(col.key ? (row[col.key] ?? 0) : 0);              // num/loc/pctp/raw/pair/heatmap
}

// drill data-* attributes plus the data-sort raw value, merged for one <td>.
function cellAttrs<R extends Record<string, unknown>>(col: Column<R>, row: R): Record<string, string> {
  const attrs = drillAttrs(col, row);
  const sv = sortValue(col, row);
  if (sv !== undefined) attrs["data-sort"] = sv;
  return attrs;
}

function Tags<R extends Record<string, unknown>>({ col, row }: { col: Column<R>; row: R }) {
  if (!col.tags) return null;
  return (
    <>
      {col.tags
        .filter((t) => row[t.ifKey])
        .map((t, i) => (
          <span key={i} className={`tag${t.cls ? ` ${t.cls}` : ""}`}>
            {t.prefix ?? ""}
            {t.textKey ? fmtNum(row[t.textKey]) : t.text}
            {t.suffix ?? ""}
          </span>
        ))}
    </>
  );
}

function Cell<R extends Record<string, unknown>>({ col, row }: { col: Column<R>; row: R }) {
  const kind = col.kind ?? "num";
  const cls = [col.align === "num" ? "num" : "", col.cls || ""].filter(Boolean).join(" ") || undefined;
  if (col.render) return <td className={cls} {...cellAttrs(col, row)}>{col.render(row)}</td>;

  if (kind === "bar") {
    const width = col.widthKey ? fmtPct(row[col.widthKey]) : "0";
    const content = col.contentKey !== undefined
      ? fmtValue(row[col.contentKey], col.contentFmt)
      : `${width}%`;
    return (
      <td className={`db${zeroClass(content)}${col.cls ? ` ${col.cls}` : ""}`}
          style={{ "--w": `${width}%` } as React.CSSProperties} {...cellAttrs(col, row)}>
        {content}
      </td>
    );
  }
  if (kind === "heatmap") {
    const raw = col.alphaKey ? Number(row[col.alphaKey]) || 0 : 0;
    const alpha = Math.round(Math.min(raw / 30, 1) * 0.32 * 1000) / 1000;
    const val = col.key ? row[col.key] : undefined;
    const content = fmtValue(val, "pctp");
    return (
      <td className={`hm${zeroClass(content)}${col.cls ? ` ${col.cls}` : ""}`}
          style={{ "--a": alpha } as React.CSSProperties} {...cellAttrs(col, row)}>
        {content}
      </td>
    );
  }
  // pair: two values joined by a separator (default " / ") — _dt_cell's pair
  // branch. Both format with contentFmt (default "num", matching the macro's
  // `col.fmt|default('num')`); the joined text is dim-zeroed as one string so
  // "0 / 0" fades exactly like the monolith's dimZeros() does at runtime.
  if (kind === "pair") {
    const fmt = col.contentFmt ?? "num";
    const a = fmtValue(col.key ? row[col.key] : 0, fmt);
    const b = fmtValue(col.key2 ? row[col.key2] : 0, fmt);
    const content = `${a}${col.sep ?? " / "}${b}`;
    return (
      <td className={`${cls ?? ""}${zeroClass(content)}`.trim() || undefined} {...cellAttrs(col, row)}>
        {content}
      </td>
    );
  }
  if (kind === "text") {
    const val = col.key ? row[col.key] : undefined;
    const swatched = col.swatch === "dot" || col.swatch === "edot";
    const hasChildren = swatched || !!(col.tags && col.tags.some((t) => row[t.ifKey]));
    return (
      <td className={`${cls ?? ""}${hasChildren ? "" : zeroClass(String(val ?? ""))}`.trim() || undefined}
          {...cellAttrs(col, row)}>
        {col.swatch === "dot" && col.colorKey && (
          <span
            style={{
              background: String(row[col.colorKey] ?? ""), display: "inline-block",
              width: 10, height: 10, borderRadius: 2, marginRight: 6,
            }}
          />
        )}
        {col.swatch === "edot" && col.colorKey && (
          <span className="edot" style={{ background: String(row[col.colorKey] ?? "") }} />
        )}
        {val as ReactNode}
        <Tags col={col} row={row} />
      </td>
    );
  }
  const val = col.key ? row[col.key] : undefined;
  const shown = col.dash && (val === null || val === undefined) ? "—" : `${fmtValue(val, kind)}${col.unit ?? ""}`;
  // Only dim when there's no tags/swatch sibling — mirrors the JS's
  // `if (td.children.length) return` guard (an actual DOM child disqualifies).
  const hasChildren = !!(col.tags && col.tags.some((t) => row[t.ifKey]));
  return (
    <td className={`${cls ?? ""}${hasChildren ? "" : zeroClass(shown)}`.trim() || undefined} {...cellAttrs(col, row)}>
      {shown}
      <Tags col={col} row={row} />
    </td>
  );
}

// A two-row header band above the column headers — port of data_table()'s
// `groups` param (templates/panels/01_helpers.j2): [{label, span, tip?}],
// colspan per group, every group after the first gets the 'g' separator
// (mark the matching first column with cls:'g' to carry it into the body —
// see pages/People.tsx). `label` is a ReactNode (not a string) so a group
// title carrying markup — e.g. "Surviving code · today <span
// class=\"alltime-tag\">all-time</span>" — can be written as JSX directly
// instead of dangerouslySetInnerHTML.
export type ColumnGroup = { label: ReactNode; span: number; tip?: string };

export default function DataTable<R extends Record<string, unknown>>({
  columns, rows, empty = "No data.", groups, cap, expanded,
}: {
  columns: Column<R>[];
  rows: R[];
  empty?: string;
  // `groups`: renders the two-row grouped header (switches the table into
  // "grouped" mode — sticky first column + group borders, see report.css).
  groups?: ColumnGroup[];
  // `cap`: rows beyond this index get class="extra" (hidden by CSS unless
  // `expanded`/`.expanded` is set — table.grouped/.capped tbody tr.extra{
  // display:none}, see report.css) — port of data_table()'s cap param. In
  // grouped mode the built-in "+N more" row is NOT added (an external
  // toggle reveals .extra instead — see People's tblbar); in non-grouped
  // mode a capped table gets the same "▸ Show all N" row the macro emits.
  cap?: number;
  expanded?: boolean;
}) {
  if (!rows || rows.length === 0) return <p className="hint">{empty}</p>;
  const capd = !!cap && rows.length > cap;
  const tableCls = ["dt", groups ? "grouped" : "", capd && !groups ? "capped" : "", expanded ? "expanded" : ""]
    .filter(Boolean).join(" ");
  return (
    <table className={tableCls}>
      <thead>
        {groups && (
          <tr className="grp">
            {groups.map((g, i) => (
              <th key={i} colSpan={g.span} className={i > 0 ? "g" : undefined} data-tip={g.tip}>
                {g.label}
              </th>
            ))}
          </tr>
        )}
        <tr>
          {columns.map((col, i) => (
            <th
              key={i}
              className={`${col.align === "num" ? "num " : ""}${col.sortable === false ? "" : "sortable"}${
                col.cls ? ` ${col.cls}` : ""
              }`}
              data-tip={col.tip}
            >
              {col.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, ri) => (
          <tr key={ri} className={capd && ri >= (cap as number) ? "extra" : undefined}>
            {columns.map((col, ci) => <Cell key={ci} col={col} row={row} />)}
          </tr>
        ))}
        {capd && !groups && (
          <tr className="more" data-more={`▸ Show all ${rows.length}`} data-less={`▾ Show top ${cap} only`}>
            <td colSpan={columns.length}>{`▸ Show all ${rows.length}`}</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
