// Dashboard widget registry — the single `viz → { component, adapt(data) }`
// binding the dashboard <PanelRenderer> looks panels up in. It mirrors
// dashboards._render_panel + resolve_panel_data (dashboards.py) and the
// kpi_tile/data_table Jinja macros (templates/panels/01_helpers.j2) so the
// React dashboard reproduces the exact panel-body DOM the server HTML path
// emitted, from the WS2-T2 resolved-data JSON boundary:
//
//   number             → KpiTile   (data {value}      → {value, label})
//   table              → DataTable (data {columns,rows}→ Column[]/rows)
//   line|area|column|
//   bar|pie            → VegaChart (data IS the Vega-Lite spec → <VegaChart spec>)
//   {error}            → the .dp-err element (any resolve failure, any viz)
//
// build_spec stays server-side (a chart's `data` already IS its Vega-Lite spec).
import type { ComponentType } from "react";
import KpiTile from "../components/KpiTile";
import DataTable, { type Column } from "../components/DataTable";
import VegaChart from "../components/VegaChart";

// The chart vizzes (dashboards._CHART_VIZ) — their resolved `data` is a spec.
export const CHART_VIZ = ["line", "area", "column", "bar", "pie"] as const;

// Resolved-data shapes resolve_panel_data returns (dashboards.py).
export type NumberData = { value: number | null };
export type DashColumn = { label: string; key: string; kind?: string; align?: string | null };
export type TableData = { columns: DashColumn[]; rows: Record<string, unknown>[] };
export type ErrorData = { error: string };
// A chart's data is an opaque Vega-Lite spec object.
export type PanelData = NumberData | TableData | ErrorData | Record<string, unknown>;

/** Any resolve failure sets `data` to {error} (dashboards.py) — for number,
 * table AND charts (build_spec returning empty). The HTML path renders it as
 * <div class="dp-err">; so does <PanelRenderer>. A live Vega-Lite spec never
 * carries an `error` key, so this cleanly separates the two. */
export function hasError(data: PanelData): data is ErrorData {
  return (
    !!data && typeof data === "object" && "error" in data
    && typeof (data as ErrorData).error === "string"
  );
}

// Port of dashboards._render_panel's `f"{value:,}"`: grouped thousands with the
// fractional part kept verbatim. Integers use toLocaleString; a fractional value
// groups the integer part and appends the JS decimal repr unchanged.
// EDGE CASE (flagged for the gate): an integer-valued float such as 1234.0 loses
// its ".0" across the JSON boundary (json.dumps(1234.0) → "1234.0", but
// JSON.parse yields the integer 1234), so it renders "1,234" here where the
// server's Python f-string on the live float would show "1,234.0". Dashboard
// `number` panels resolve integer counts in practice, so this is latent.
function fmtComma(v: number): string {
  if (!Number.isFinite(v)) return String(v);
  if (Number.isInteger(v)) return v.toLocaleString("en-US");
  const neg = v < 0;
  const [intPart, frac] = Math.abs(v).toString().split(".");
  const grouped = Number(intPart).toLocaleString("en-US");
  return `${neg ? "-" : ""}${grouped}${frac !== undefined ? `.${frac}` : ""}`;
}

// dashboards._auto_columns → DataTable Column[]. `_auto_columns` emits only
// kind "num"/"text" with align "num"/null; DataTable's num cell right-aligns
// and formats with fmtNum (== the |num Jinja filter), text renders the raw
// value — matching _dt_cell's else/text branches for these two kinds.
function adaptColumns(cols: DashColumn[]): Column<Record<string, unknown>>[] {
  return (cols || []).map((c) => ({
    label: c.label,
    key: c.key,
    kind: (c.kind as Column<Record<string, unknown>>["kind"]) || "num",
    align: c.align === "num" ? "num" : undefined,
  }));
}

export type RegistryEntry = {
  component: ComponentType<Record<string, unknown>>;
  adapt: (data: PanelData, title: string) => Record<string, unknown>;
};

const chartEntry: RegistryEntry = {
  // VegaChart takes the server-built Vega-Lite spec as its `spec` prop.
  component: VegaChart as ComponentType<Record<string, unknown>>,
  adapt: (data) => ({ spec: data }),
};

export const registry: Record<string, RegistryEntry> = {
  number: {
    component: KpiTile as ComponentType<Record<string, unknown>>,
    adapt: (data, title) => {
      const v = (data as NumberData).value;
      // Matches _render_panel: numeric → f"{value:,}"; missing/non-numeric → "n/a".
      return { value: typeof v === "number" ? fmtComma(v) : "n/a", label: title };
    },
  },
  table: {
    component: DataTable as unknown as ComponentType<Record<string, unknown>>,
    adapt: (data) => {
      const t = data as TableData;
      return { columns: adaptColumns(t.columns), rows: t.rows };
    },
  },
  line: chartEntry,
  area: chartEntry,
  column: chartEntry,
  bar: chartEntry,
  pie: chartEntry,
};

// The .dp-err tile the server HTML path emits for any resolve failure — the
// error string already carries the panel title (dashboards.py builds it).
export function ErrorTile({ message }: { message: string }) {
  return <div className="dp-err">{message}</div>;
}
