// The report's shared filter bar — period presets + custom range + repo-scope
// slice. Markup/classes are IDENTICAL to today's `.topbar` (templates/report.j2,
// the `.period-bar`/`.pseg`/`.pchip`/`.period-custom`/`.period-legend` block) so
// the pixel-parity gate sees no diff in the chrome every report view shares.
// Reads/writes the same query params (p/from/to/slice) via useReportData's
// setReportQuery — every view's useReportData(view) call picks up the change
// and refetches, exactly like the monolith's setPeriod()/setScope() do today.
import { useState } from "react";
import { setReportQuery } from "../hooks/useReportData";

export type PeriodPreset = { key: string; label: string };
export type ScopeTargets = { org?: string[]; element?: string[]; repo?: string[] };
export type Period = { preset: string; label: string; from: string | null; to: string | null };

function fmtDay(s: string | null | undefined): string {
  if (!s) return "";
  try {
    return new Date(`${s}T00:00:00`).toLocaleDateString(undefined, {
      day: "numeric", month: "short", year: "numeric",
    });
  } catch {
    return s;
  }
}

export default function FilterBar({
  periodPresets, period, scope, scopeTargets,
}: {
  periodPresets: PeriodPreset[];
  period: Period;
  scope: string;
  scopeTargets: ScopeTargets;
}) {
  const [customOpen, setCustomOpen] = useState(false);
  const [fromVal, setFromVal] = useState(period.from || "");
  const [toVal, setToVal] = useState(period.to || "");

  const isCustom = period.preset === "custom";

  return (
    <div className="topbar">
      <div className="period-bar" aria-label="Period filter">
        <span className="period-lbl">Period</span>
        <div className="pseg" role="group" aria-label="Preset windows">
          {periodPresets.map((p) => (
            <button
              key={p.key}
              type="button"
              className={`pchip${!isCustom && period.preset === p.key ? " active" : ""}`}
              data-pchip={p.key}
              onClick={() => setReportQuery({ p: p.key === "all" ? null : p.key, from: null, to: null,
                                              tgran: null, tdim: null })}
            >
              {p.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className={`pcustbtn${isCustom ? " on" : ""}`}
          id="pcustbtn"
          onClick={() => setCustomOpen((v) => !v)}
        >
          {isCustom ? `${fmtDay(period.from) || "…"} → ${fmtDay(period.to) || "today"}` : "Custom…"}
        </button>
        <span className="period-custom" id="period-custom" hidden={!customOpen}>
          <input
            id="pfrom" type="date" aria-label="from date" value={fromVal}
            onChange={(e) => setFromVal(e.target.value)}
          />{" "}
          –
          <input
            id="pto" type="date" aria-label="to date" value={toVal}
            onChange={(e) => setToVal(e.target.value)}
          />
          <button
            type="button"
            className="papply"
            onClick={() => {
              setReportQuery({ from: fromVal || null, to: toVal || null, p: null,
                               tgran: null, tdim: null });
              setCustomOpen(false);
            }}
          >
            Apply
          </button>
        </span>
        <span id="period-msg" className="period-note" />
      </div>
      <div className="period-bar" aria-label="Repository slice">
        <span className="period-lbl">Slice</span>
        <select
          id="global-scope"
          value={scope}
          onChange={(e) => setReportQuery({ slice: e.target.value || null, tgran: null, tdim: null })}
          style={{
            padding: "8px 14px", border: "1px solid var(--line2)", borderRadius: 999,
            background: "var(--panel)", color: "var(--ink2)", font: "inherit", fontSize: 13,
            fontWeight: 600, maxWidth: 280, boxShadow: "var(--sh)", cursor: "pointer",
          }}
        >
          <option value="">Whole org — all repositories</option>
          {scopeTargets.org && scopeTargets.org.length > 0 && (
            <optgroup label="Organizations">
              {scopeTargets.org.map((o) => (
                <option key={o} value={`org:${o}`}>{o}</option>
              ))}
            </optgroup>
          )}
          {scopeTargets.element && scopeTargets.element.length > 0 && (
            <optgroup label="Elements">
              {scopeTargets.element.map((el) => (
                <option key={el} value={`element:${el}`}>{el}</option>
              ))}
            </optgroup>
          )}
          {scopeTargets.repo && scopeTargets.repo.length > 0 && (
            <optgroup label="Repositories">
              {scopeTargets.repo.map((r) => (
                <option key={r} value={`repo:${r}`}>{r}</option>
              ))}
            </optgroup>
          )}
        </select>
        <span className="period-note" id="slice-msg">
          scopes every windowed panel; all-time panels (Contributors, Surviving-LOC) stay org-wide
        </span>
      </div>
      <details className="period-legend" id="legendDisc" open>
        <summary>What the period &amp; slice affect</summary>
        <div className="legend-body">
          <b>Period-filtered:</b> KPIs · by company · % by category · work type · By Element ·
          platform/app · weekly activity · contribution trend · per-person activity · traffic ·
          code review · bot activity · AI-marked share · per-tool AI split.{" "}
          <b>Always all-time</b> (by nature): contributors (cumulative) · surviving-LOC · repo
          coverage · content markers (Studio/Gears) · cpt lines. <b>Slice</b> scopes every windowed
          panel; all-time panels stay org-wide.
        </div>
      </details>
    </div>
  );
}
