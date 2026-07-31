/**
 * The five pictures a user-built dashboard panel can be.
 *
 * A panel stores a `viz` — line, area, column, bar or pie — and the server resolves
 * it to data (dashboards.chart_panel_data), never to a chart spec. Which means the
 * five live here as five compositions rather than five spec branches, and a panel is
 * drawn by the same primitives the report pages use.
 *
 * The looks are the retired vega_spec.build_spec's, because these replace it and
 * saved dashboards should not change appearance under their owners: an area panel is
 * a .5 wash under its line (the report's own areas are fainter, at .12 — a different
 * chart for a different place, not a drift), bars and columns carry no legend because
 * every bar is its own label, and pie is a donut with a 40px hole.
 */
import TimeChart, {
  CHART_HEIGHT, configOf, toRows, type ChartData,
} from "./TimeChart";
import {
  AXIS, BarChart, Cell, ChartArea, ChartBar, ChartContainer, ChartLegend,
  ChartLegendContent, ChartLine, ChartTooltip, ChartTooltipContent, CURSOR, Pie,
  PieChart, XAxis, YAxis, compactTick, type ChartConfig,
} from "../ui/chart";

export type PanelRow = { label: string; value: number; color: string };
export type TimePanel = ChartData & { kind: "line" | "area" };
export type CategoricalPanel = { kind: "column" | "bar" | "pie"; rows: PanelRow[] };
export type PanelChartData = TimePanel | CategoricalPanel;

/** A guard rather than a bare `kind` check: TimePanel is an INTERSECTION with
 *  ChartData, which the compiler will not narrow out of the union on its own. */
const isTimePanel = (d: PanelChartData): d is TimePanel =>
  d.kind === "line" || d.kind === "area";

export default function PanelChart({ data }: { data: PanelChartData }) {
  return isTimePanel(data) ? <TimeSeriesPanel data={data} /> : <RowsPanel data={data} />;
}

function TimeSeriesPanel({ data }: { data: TimePanel }) {
  const line = data.kind === "line";
  return (
    <TimeChart data={toRows(data)} config={configOf(data)} unit={data.unit}
               kind={line ? "line" : "area"} total={data.stacked}>
      {data.series.map((s) =>
        data.stacked ? (
          // the catalogued `stacked_area` view resolves here
          <ChartArea key={s.key} dataKey={s.key} name={s.name} stackId="s"
                     dot={false}
                     stroke="var(--panel)" strokeWidth={0.4}
                     fill={s.color} fillOpacity={0.82} />
        ) : line ? (
          <ChartLine key={s.key} dataKey={s.key} name={s.name}
                     points={data.dates.length} connectNulls
                     stroke={s.color} fill={s.color} />
        ) : (
          <ChartArea key={s.key} dataKey={s.key} name={s.name}
                     points={data.dates.length} connectNulls
                     stroke={s.color} strokeWidth={1.6}
                     fill={s.color} fillOpacity={0.5} />
        ),
      )}
    </TimeChart>
  );
}

function RowsPanel({ data }: { data: CategoricalPanel }) {
  const { rows } = data;
  const config: ChartConfig = Object.fromEntries(
    rows.map((r) => [r.label, { label: r.label, color: r.color }]),
  );

  if (data.kind === "pie") {
    return (
      <ChartContainer config={config} height={CHART_HEIGHT}>
        <PieChart>
          <ChartTooltip content={<ChartTooltipContent labelName="Slice" />} />
          <Pie data={rows} dataKey="value" nameKey="label" innerRadius={40}
               isAnimationActive={false} stroke="var(--panel)" strokeWidth={1}>
            {rows.map((r) => <Cell key={r.label} fill={r.color} />)}
          </Pie>
          <ChartLegend content={<ChartLegendContent />} verticalAlign="bottom" />
        </PieChart>
      </ChartContainer>
    );
  }

  // column = vertical bars labelled on x; bar = horizontal, already sorted descending
  const horizontal = data.kind === "bar";
  return (
    <ChartContainer config={config} height={CHART_HEIGHT}>
      <BarChart data={rows} layout={horizontal ? "vertical" : "horizontal"}
                margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        {horizontal ? (
          <>
            <XAxis type="number" {...AXIS} tickFormatter={compactTick} />
            <YAxis type="category" dataKey="label" {...AXIS} width={110} />
          </>
        ) : (
          <>
            <XAxis dataKey="label" type="category" {...AXIS} interval={0} />
            <YAxis type="number" {...AXIS} width={38} tickFormatter={compactTick} />
          </>
        )}
        <ChartTooltip cursor={CURSOR} content={<ChartTooltipContent labelName="" />} />
        {/* one series, coloured per row — every bar IS its own label, which is why
            these carry no legend */}
        <ChartBar dataKey="value" name="value">
          {rows.map((r) => <Cell key={r.label} fill={r.color} />)}
        </ChartBar>
      </BarChart>
    </ChartContainer>
  );
}
