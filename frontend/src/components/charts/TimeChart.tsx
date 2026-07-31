/**
 * The shell every time-series chart on the report shares: the container, the grid,
 * both axes and the shared-x tooltip, themed once. What gets DRAWN is composed by
 * the caller as children.
 *
 * This started as one `SeriesChart` that took the payload and switched on two
 * booleans — `stacked` and `areaFirst`. It rendered all seven charts in fewer lines
 * and it was the wrong shape, for the reason this migration exists: in the big
 * Insight a chart is composed at the call site (`<LineChart><ChartLine/>…`), so a
 * chart hidden behind flags here would not transfer, and every new kind of chart
 * would have added another flag rather than another child.
 *
 * So the theme is shared and the composition is not:
 *
 *   <TimeChart data={rows} config={cfg} unit="hours">
 *     {series.map(s => <ChartArea key={s.key} dataKey={s.key} … />)}
 *   </TimeChart>
 *
 * `rows` is one row per x with a column per series — `toRows` transposes the
 * report's payload, which is one row per series, into that.
 */
import type { ReactNode } from "react";
import {
  AXIS, AreaChart, CartesianGrid, ChartContainer, ChartTooltip, ChartTooltipContent,
  CURSOR, LineChart, XAxis, YAxis, compactTick, hoursTick, type ChartConfig,
} from "../ui/chart";

export type ChartSeries = { name: string; key: string; color: string; vals: (number | null)[] };
/** render.py's chart_data envelope. */
export type ChartData = {
  dates: string[];
  series: ChartSeries[];
  unit: string;
  areaFirst: boolean;
  stacked: boolean;
};

/** Payload (a row per series) → Recharts rows (a row per x). */
export function toRows(data: ChartData): Record<string, string | number | null>[] {
  return data.dates.map((d, i) => {
    const row: Record<string, string | number | null> = { x: d };
    for (const s of data.series) row[s.key] = s.vals[i] ?? null;
    return row;
  });
}

export function configOf(data: ChartData): ChartConfig {
  return Object.fromEntries(data.series.map((s) => [s.key, { label: s.name, color: s.color }]));
}

/** What the retired Vega envelope gave every chart on the page, minis included —
 *  their spec carried `height: 220` too, and forcing them smaller crushed the plot
 *  area into the axis labels. */
export const CHART_HEIGHT = 220;

export const tickFor = (unit: string) => (unit === "hours" ? hoursTick : compactTick);

export default function TimeChart({
  data, config, children, height = CHART_HEIGHT, unit = "", total = false,
  kind = "area", className,
}: {
  data: Record<string, string | number | null>[];
  config: ChartConfig;
  children: ReactNode;
  height?: number;
  unit?: string;
  /** Lead the tooltip with the sum of the hovered column — what a stack needs. */
  total?: boolean;
  kind?: "area" | "line";
  className?: string;
}) {
  const Chart = kind === "line" ? LineChart : AreaChart;
  const fmt = tickFor(unit);
  return (
    <ChartContainer config={config} height={height} className={className}>
      <Chart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="x" {...AXIS} interval="preserveStartEnd" minTickGap={24} />
        <YAxis {...AXIS} width={38} tickFormatter={fmt} />
        <ChartTooltip cursor={CURSOR} content={<ChartTooltipContent total={total} format={fmt} />} />
        {children}
      </Chart>
    </ChartContainer>
  );
}
