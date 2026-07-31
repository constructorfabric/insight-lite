/**
 * The charting surface. Every chart in the app imports from HERE, never from
 * `recharts` directly — the same convention insight-front's components/ui/chart.tsx
 * establishes, and the reason this file exists at all.
 *
 * WHAT IS SHARED with the big Insight is the vocabulary: ChartContainer, ChartLine /
 * ChartArea / ChartBar, ChartTooltip / ChartTooltipContent, ChartLegend /
 * ChartLegendContent, ChartConfig, DOT_DENSITY_LIMIT — same names, same prop shapes,
 * same defaults (no entry animation, dots that collapse to hover-only past
 * DOT_DENSITY_LIMIT buckets). A chart written against one reads on the other, which
 * is the whole point of converging the stacks.
 *
 * WHAT IS NOT shared is the substrate. Theirs is shadcn on Tailwind with `cn()` and
 * utility classes; lite has three runtime dependencies and hand-written CSS on
 * variables, so this implements the same interface over `.ch-*` classes in
 * styles/chart.css. Swapping to Tailwind later changes this file's insides, not one
 * call site — which is exactly why the interface converges first and the substrate
 * can wait for a reason.
 *
 * The visual contract it has to hit is the retired Vega-Lite theme (vega_spec.py's
 * vega_config), because these charts are replacing those and the screenshot harness
 * compares them: 10px muted axis labels, a 0.5px --line grid, no axis titles, a
 * bottom legend with circular swatches, 1.6px strokes, and a shared nearest-x
 * tooltip listing every series at the hovered column.
 */
import {
  createContext, useContext, useId, useMemo,
  type ComponentProps, type ReactNode,
} from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend,
  Line, LineChart, Pie, PieChart, ReferenceLine, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from "recharts";

/** Above this many buckets per-point dots stop being readable and turn the line
 *  into noise, so they collapse to hover-only. Same threshold as insight-front. */
export const DOT_DENSITY_LIMIT = 31;

export type ChartConfig = Record<string, { label?: ReactNode; color?: string }>;

const ChartContext = createContext<ChartConfig | null>(null);

function useChart(): ChartConfig {
  const ctx = useContext(ChartContext);
  if (!ctx) throw new Error("useChart must be used within a <ChartContainer />");
  return ctx;
}

/** SI-compact, the `~s` d3 format the Vega axes used: 2.6M, 12k, 840. */
export function compactTick(v: number | string): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return String(v);
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${trim(n / 1_000_000)}M`;
  if (abs >= 1_000) return `${trim(n / 1_000)}k`;
  return trim(n);
}
const trim = (n: number) => String(Math.round(n * 10) / 10);
/** `.2~f` — hours, two decimals, trailing zeros trimmed. */
export const hoursTick = (v: number | string) => {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? String(Math.round(n * 100) / 100) : String(v);
};

/** Axis props shared by every chart, so a page never restates the theme. */
export const AXIS = {
  tick: { fontSize: 10, fill: "var(--mut)" },
  tickLine: { stroke: "var(--line2)" },
  axisLine: { stroke: "var(--line2)" },
} as const;

export function ChartContainer({
  config, children, height = 240, className, ...rest
}: ComponentProps<"div"> & {
  config: ChartConfig;
  children: ComponentProps<typeof ResponsiveContainer>["children"];
  height?: number;
}) {
  const id = useId();
  const value = useMemo(() => config, [config]);
  return (
    <ChartContext.Provider value={value}>
      <div
        data-chart={id}
        className={className ? `ch ${className}` : "ch"}
        style={{ height }}
        {...rest}
      >
        <ResponsiveContainer width="100%" height="100%">{children}</ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  );
}

/** A dot per point while they are legible, hover-only once they are not. */
function adaptiveDot(count: number) {
  return count > DOT_DENSITY_LIMIT ? false : { r: 2.4, strokeWidth: 0 };
}

export function ChartLine({
  points = 0, isAnimationActive = false, type = "linear", strokeWidth = 1.6, ...props
}: ComponentProps<typeof Line> & { points?: number }) {
  return (
    <Line
      isAnimationActive={isAnimationActive}
      type={type}
      strokeWidth={strokeWidth}
      dot={adaptiveDot(points)}
      activeDot={{ r: 3.5 }}
      {...props}
    />
  );
}

export function ChartArea({
  points = 0, isAnimationActive = false, type = "linear", ...props
}: ComponentProps<typeof Area> & { points?: number }) {
  return (
    <Area
      isAnimationActive={isAnimationActive}
      type={type}
      dot={adaptiveDot(points)}
      activeDot={{ r: 3.5 }}
      {...props}
    />
  );
}

export function ChartBar({ isAnimationActive = false, ...props }: ComponentProps<typeof Bar>) {
  return <Bar isAnimationActive={isAnimationActive} radius={[3, 3, 0, 0]} {...props} />;
}

/** Recharts' Tooltip is already shared across series at the hovered x; `cursor`
 *  draws the faint vertical marker the Vega hover layer used to. */
export const ChartTooltip = Tooltip;
export const CURSOR = { stroke: "var(--ink)", strokeWidth: 1, strokeOpacity: 0.18 };

type TipEntry = { name?: string | number; dataKey?: string | number;
                  value?: number | string; color?: string };

export function ChartTooltipContent({
  active, payload, label, format = compactTick, total = false, labelName = "Period",
}: {
  active?: boolean; payload?: TipEntry[]; label?: string | number;
  format?: (v: number | string) => string;
  /** Prepend the stack sum, the way the stacked-area tooltip always led with it. */
  total?: boolean;
  labelName?: string;
}) {
  const config = useChart();
  if (!active || !payload?.length) return null;
  const rows = payload.filter((p) => p.value !== undefined && p.value !== null);
  if (!rows.length) return null;
  const sum = rows.reduce((a, p) => a + (Number(p.value) || 0), 0);
  const nameOf = (p: TipEntry) => {
    const key = String(p.dataKey ?? p.name ?? "");
    return config[key]?.label ?? p.name ?? key;
  };
  return (
    <div className="ch-tip">
      {/* `labelName` names the x dimension ("Period: Q3 25"). The categorical
          panels pass "" because a bar is its own label, and a bare ": Acronis"
          is worse than no header at all. */}
      <div className="ch-tip-h">{labelName ? `${labelName}: ${label}` : label}</div>
      {total && (
        <div className="ch-tip-r ch-tip-total"><span>Total</span><b>{format(sum)}</b></div>
      )}
      {rows.map((p, i) => (
        <div className="ch-tip-r" key={`${p.dataKey}-${i}`}>
          <span className="ch-sw" style={{ background: p.color }} aria-hidden />
          <span>{nameOf(p)}</span>
          <b>{format(p.value as number)}</b>
        </div>
      ))}
    </div>
  );
}

export const ChartLegend = Legend;

export function ChartLegendContent({ payload }: { payload?: TipEntry[] }) {
  const config = useChart();
  if (!payload?.length) return null;
  return (
    <div className="ch-legend">
      {payload.map((p, i) => {
        const key = String(p.dataKey ?? p.value ?? "");
        return (
          <span className="ch-legend-i" key={`${key}-${i}`}>
            <span className="ch-sw" style={{ background: p.color }} aria-hidden />
            {config[key]?.label ?? key}
          </span>
        );
      })}
    </div>
  );
}

export {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart,
  Line, LineChart, Pie, PieChart, ReferenceLine, ResponsiveContainer, XAxis, YAxis,
};
