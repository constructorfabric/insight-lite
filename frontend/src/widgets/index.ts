// Widget catalog — the shared, data-agnostic UI components the report views compose
// (and, from Phase 2, the dashboard PanelRenderer). One import surface:
//   import { GhLink } from "../widgets";
// See docs/superpowers/specs/2026-07-23-unified-widget-system-design.md.
//
// New widgets are added here as Phase 1 extracts them (BarRow, BarList, SplitBar,
// Legend, MiniStats, StatRow, HeatStrip, Chips, MarkerTable, GroupedTable, FlowPipe,
// PersonScore, Scorecard). The existing primitives under components/ (DataTable,
// KpiTile, SegBar, FilterBar) are intentionally NOT moved yet (churn +
// CSS-import diff-risk — see the spec); re-export them here if a single path is wanted.
export { default as GhLink } from "./GhLink";
export { BarRow, BarList } from "./BarRow";
export type { BarRowProps, BarListProps, BarListTail } from "./BarRow";
export { SplitBar, Legend } from "./SplitBar";
export type { Segment, SplitBarProps, LegendProps } from "./SplitBar";
export { MiniStats } from "./MiniStats";
export type { MiniStatItem, MiniStatsProps } from "./MiniStats";
export { StatRow } from "./StatRow";
export type { StatRowProps } from "./StatRow";
export { HeatStrip } from "./HeatStrip";
export type { HeatWeek, HeatStripProps } from "./HeatStrip";
export { Chips } from "./Chips";
export type { ChipItem, ChipsProps } from "./Chips";
export { MarkerTable } from "./MarkerTable";
export type { MarkerTableData, MarkerBadge, MarkerCell, MarkerRow } from "./MarkerTable";
export { FlowPipe } from "./FlowPipe";
export type { FlowPipeData, FlowStage } from "./FlowPipe";
// Dev-score: TWO separate widgets (NOT merged) — Person's gauge+chain+board vs
// Overview's team scorecard TABLE. See widgets/score/.
export { PersonScore } from "./score/PersonScore";
export type { ScoreBlock, ScoreRow, ScoreAbove, VsSelf, ScorePillars, ScoreDrivers } from "./score/PersonScore";
export { Scorecard } from "./score/Scorecard";
export type { ScorecardData } from "./score/Scorecard";
// NOTE: no GroupedTable — Person weekly is a single-use bespoke multi-header +
// tfoot table and the Flow "dwell"/"by-person" tables are flat tables merely
// wearing the .grouped CSS class (no column groups); they share no reusable
// grouped shape, so extracting one would be a forced abstraction. Left as page
// components (see the T5 decision in the batch report).
