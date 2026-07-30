// The filter bar's props WITHOUT waiting for /api/report/*.
//
// The bar has four inputs (see backend/render.py's filter_model) and not one of
// them actually needs the payload:
//
//   periodPresets, scopeTargets — depend on the report version, not on what you
//     picked, so the server inlines them into the page as the `#filter-model`
//     JSON island — same mechanism as `#nav-model` for the sidebar.
//   period, scope — ARE what you picked, and they were read off the URL to build
//     the request in the first place, so the URL is the authority here, not the
//     echo that comes back in the response.
//
// Until this existed every report view rendered a skeleton strip in place of the
// bar for as long as its fetch took (1-2s on prod for delivery/flow), even though
// the bar came back byte-identical every time. Now it paints with the rest of the
// chrome and is clickable immediately: a preset click writes the URL, which is
// what triggers the fetch, so clicking during a load just redirects it.
//
// Returns null when the island is missing (no report model yet — fresh install),
// so the caller can keep its skeleton for that case.
import type { Period, PeriodPreset, ScopeTargets } from "../components/FilterBar";
import { useReportQuery } from "./useReportData";

type Island = { periodPresets?: PeriodPreset[]; scopeTargets?: ScopeTargets };

export type FilterModel = {
  periodPresets: PeriodPreset[];
  period: Period;
  scope: string;
  scopeTargets: ScopeTargets;
};

let cached: Island | null | undefined;

function island(): Island | null {
  // Parsed once: the island is static markup and several components want it.
  if (cached !== undefined) return cached;
  cached = null;
  if (typeof document !== "undefined") {
    const el = document.getElementById("filter-model");
    if (el?.textContent) {
      try {
        cached = JSON.parse(el.textContent) as Island;
      } catch {
        // Malformed island → skeleton, same as no island. Never throw from here:
        // this runs during the first render of every report page.
        cached = null;
      }
    }
  }
  return cached;
}

export default function useFilterModel(): FilterModel | null {
  // Subscribed rather than read once, so the active chip keeps up with the URL
  // while the payload is still in flight — otherwise a click during a load would
  // leave nothing highlighted.
  const q = useReportQuery();
  const data = island();
  if (!data) return null;
  const presets = data.periodPresets || [];
  // Mirrors the server's own resolution (server.py's period_kind): an explicit
  // from/to is a custom range, otherwise `p` names a preset, otherwise all-time.
  const custom = Boolean(q.from || q.to);
  const key = custom ? "custom" : (q.p || "all");
  return {
    periodPresets: presets,
    period: {
      preset: key,
      label: presets.find((p) => p.key === key)?.label || key,
      from: q.from || null,
      to: q.to || null,
    },
    scope: q.slice || "",
    scopeTargets: data.scopeTargets || {},
  };
}
