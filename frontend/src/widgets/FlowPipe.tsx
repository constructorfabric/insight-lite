// The delivery workflow pipeline visual — the hand-rolled `.flowpipe` of
// `.flow-stage` cards joined by `→` arrows, each card a head (colour dot +
// name) + count + pct + mini bar, drillable when non-empty (data-drill/…).
// Promoted verbatim from the in-page copy in pages/Delivery.tsx (the only site
// that renders this pipe — Flow.tsx does not); emits byte-identical DOM so the
// pixel gate sees no diff. Data-agnostic: the caller resolves the stage
// counts/percentages.
import { Fragment } from "react";
import { fmtNum } from "../lib/format";

export type FlowStage = { key: string; name: string; color: string; count: number; pct: string; barPct: number };
export type FlowPipeData = { hasData: boolean; stages: FlowStage[]; total: number; unmapped: number };

export function FlowPipe({ flow }: { flow: FlowPipeData }) {
  if (!flow.hasData) return <p className="hint">No Projects&nbsp;v2 board statuses collected yet.</p>;
  return (
    <>
      <div className="flowpipe">
        {flow.stages.map((s, i) => (
          <Fragment key={s.key}>
            {i > 0 && <div className="flow-arrow">→</div>}
            <div
              className={`flow-stage${s.count ? "" : " zero"}`}
              {...(s.count
                ? {
                    "data-drill": "flow", "data-stage": s.key,
                    "data-tip": `${s.name}: ${s.count} of ${flow.total} (${s.pct}%) — click for the items`,
                  }
                : {})}
            >
              <div className="flow-head"><span className="dot" style={{ background: s.color }} />{s.name}</div>
              <div className="flow-n num">{fmtNum(s.count)}</div>
              <div className="flow-pct">{s.pct}%</div>
              <div className="flow-bar"><i style={{ width: `${s.barPct}%`, background: s.color }} /></div>
            </div>
          </Fragment>
        ))}
      </div>
      <p className="conc" style={{ margin: "12px 0 0" }}>
        Current board state — the latest Projects&nbsp;v2 status of each work item, mapped to a stage
        via the <a href="/semantic">Taxonomy</a>. Not window-scoped (status history isn't in GitHub's
        API).{flow.unmapped > 0 &&
          ` ${fmtNum(flow.unmapped)} item(s) sit in a status not yet mapped to a stage — refine the mapping to place them.`}
      </p>
    </>
  );
}
