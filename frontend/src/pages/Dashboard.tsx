// /dashboard/<id> — the dashboard VIEW page, rendered with the shared widget
// catalog via <PanelRenderer>, replacing templates/dashboard.j2's server Jinja
// + .vl-panel path (WS2-T3/T4). Reproduces dashboard.j2's markup exactly: the
// title <h1>, the scope/period control row, and the 6-column .grid of panel
// .cell wrappers. The dashboard id/title/panels/scopeTargets are embedded by
// the server (render_spa_page bootstrap → <script id="spa-bootstrap">); each
// panel's data is fetched from GET /api/dashboard/panel.json (WS2-T2), and the
// whole grid refetches on a scope/period change — parity with dashboard.j2's
// load(). SSR-safe: document is only touched inside the state initializer /
// effects, never at module scope.
import { useEffect, useState } from "react";
import PanelRenderer, { type PanelSpec, type ResolvedPanel } from "../widgets/PanelRenderer";

type Bootstrap = {
  id: string;
  title: string;
  panels: PanelSpec[];
  scopeTargets: Record<string, string[]>;
};

// The scope <optgroup>s render.py's _scope_options() emits — same labels/keys,
// same org/element/repo order (project is intentionally excluded there too).
const SCOPE_GROUPS: [string, string][] = [
  ["Organizations", "org"],
  ["Elements", "element"],
  ["Repositories", "repo"],
];

// The period presets dashboard.j2 hard-codes (value → label), default "all".
const PERIODS: [string, string][] = [
  ["7d", "7 days"],
  ["30d", "30 days"],
  ["90d", "90 days"],
  ["365d", "1 year"],
  ["all", "All-time"],
];

function readBootstrap(): Bootstrap | null {
  if (typeof document === "undefined") return null;
  const el = document.getElementById("spa-bootstrap");
  if (!el || !el.textContent) return null;
  try {
    return JSON.parse(el.textContent) as Bootstrap;
  } catch {
    return null;
  }
}

export default function Dashboard() {
  const [boot] = useState<Bootstrap | null>(() => readBootstrap());
  const [scope, setScope] = useState("");
  const [period, setPeriod] = useState("all");
  const [panelData, setPanelData] = useState<Record<string, ResolvedPanel>>({});

  const id = boot?.id ?? "";
  const panels = boot?.panels ?? [];

  useEffect(() => {
    if (!boot || typeof window === "undefined") return;
    let cancelled = false;
    for (const p of boot.panels) {
      const pid = p.id;
      if (!pid) continue;
      const qs =
        `id=${encodeURIComponent(id)}&panel=${encodeURIComponent(pid)}` +
        `&scope=${encodeURIComponent(scope)}&period=${encodeURIComponent(period)}`;
      fetch(`/api/dashboard/panel.json?${qs}`)
        .then((r) => r.json())
        .then((json) => {
          if (cancelled) return;
          const resolved: ResolvedPanel =
            json && json.ok
              ? { viz: json.viz, title: json.title, pin: json.pin, data: json.data }
              : { viz: "", title: pid, data: { error: "failed" } };
          setPanelData((prev) => ({ ...prev, [pid]: resolved }));
        })
        .catch(() => {
          if (cancelled) return;
          setPanelData((prev) => ({ ...prev, [pid]: { viz: "", title: pid, data: { error: "failed" } } }));
        });
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, scope, period]);

  if (!boot) return null;
  const scopeTargets = boot.scopeTargets || {};

  return (
    <>
      <h1>{boot.title}</h1>
      <div className="ctrls">
        <select id="scope" value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value="">Whole org</option>
          {SCOPE_GROUPS.map(([label, key]) => {
            const vals = scopeTargets[key] || [];
            if (!vals.length) return null;
            return (
              <optgroup key={key} label={label}>
                {vals.map((v) => (
                  <option key={v} value={`${key}:${v}`}>
                    {v}
                  </option>
                ))}
              </optgroup>
            );
          })}
        </select>
        <select id="period" value={period} onChange={(e) => setPeriod(e.target.value)}>
          {PERIODS.map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="grid" id="grid">
        {panels.map((p) => (
          <PanelRenderer key={p.id} spec={p} data={panelData[p.id ?? ""] ?? null} />
        ))}
      </div>
    </>
  );
}
