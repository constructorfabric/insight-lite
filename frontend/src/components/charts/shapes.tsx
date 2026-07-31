/**
 * The three shapes the report's time-series charts actually take, each a thin
 * COMPOSITION over TimeChart rather than a mode of it.
 *
 * They exist because seven charts across two pages draw the same three pictures,
 * not because a chart must go through them: Flow composes its cumulative flow
 * inline (see CfdArea) and reads no worse for it. A page that needs a fourth
 * picture writes it, instead of a fourth flag arriving here.
 *
 * The numbers are the retired Vega theme's: .12 wash under a filled line, .82 bands
 * with a hairline panel-coloured separator when stacked.
 */
import TimeChart, { configOf, toRows, type ChartData } from "./TimeChart";
import { ChartArea, ChartLine } from "../ui/chart";

type Props = { chart: ChartData; height?: number };

/** Plain lines, one per series — Overview's contributors, Trend's Opened/Merged. */
export function LinesChart({ chart, height }: Props) {
  return (
    <TimeChart data={toRows(chart)} config={configOf(chart)} height={height}
               unit={chart.unit} kind="line">
      {chart.series.map((s) => (
        <ChartLine key={s.key} dataKey={s.key} name={s.name}
                   points={chart.dates.length} connectNulls
                   stroke={s.color} fill={s.color} />
      ))}
    </TimeChart>
  );
}

/** A solid line over a faint wash — the old .lline/.lfill pair. Single series in
 *  every current use (time-to-merge, contributors, the weekly minis). */
export function FilledLine({ chart, height }: Props) {
  return (
    <TimeChart data={toRows(chart)} config={configOf(chart)} height={height} unit={chart.unit}>
      {chart.series.map((s) => (
        <ChartArea key={s.key} dataKey={s.key} name={s.name}
                   points={chart.dates.length} connectNulls
                   stroke={s.color} strokeWidth={1.6}
                   fill={s.color} fillOpacity={0.12} />
      ))}
    </TimeChart>
  );
}

/** Stacked bands with the column total leading the tooltip — the per-company trends. */
export function StackChart({ chart, height }: Props) {
  return (
    <TimeChart data={toRows(chart)} config={configOf(chart)} height={height}
               unit={chart.unit} total>
      {chart.series.map((s) => (
        <ChartArea key={s.key} dataKey={s.key} name={s.name} stackId="s"
                   dot={false}
                   stroke="var(--panel)" strokeWidth={0.4}
                   fill={s.color} fillOpacity={0.82} />
      ))}
    </TimeChart>
  );
}
