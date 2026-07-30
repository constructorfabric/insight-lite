// Shared data hook for every migrated report view (see
// docs/superpowers/plans/2026-07-22-react-phaseR-report.md, "Shared scaffold").
// Reads the SAME query params the monolith's filter bar writes (p / from / to /
// slice / person), fetches GET /api/report/<view>?<same params>, and refetches
// whenever those params change. SSR-safe: no window/document access at module
// scope — only inside the effect, which never runs outside a browser.
import { useEffect, useRef, useState } from "react";

export type ReportDataState<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
};

// The subset of query params every report view's filter bar can write. A view
// that doesn't use a control (e.g. Overview has no person picker) simply never
// sets that param, so it's absent from the querystring — same as today.
// `tgran`/`tdim` are Trend-only (granularity/breakdown-dimension deep-link,
// matching the monolith's own `tgran`/`tdim` query params — see
// templates/report.j2's _syncURL/initFromURL) — harmless on every other view,
// which never reads or writes them.
export type ReportQuery = {
  p?: string | null;
  from?: string | null;
  to?: string | null;
  slice?: string | null;
  person?: string | null;
  tgran?: string | null;
  tdim?: string | null;
  /** Which VIEW of a page broken into several (Person, People) — the sidebar's pane
      links carry it, and the route reads it too so the pane can highlight one. Not
      sent to /api/report/*: the payload is the same for every view of a page, only
      what is rendered differs. */
  view?: string | null;
};

const QUERY_KEYS = ["p", "from", "to", "slice", "person", "tgran", "tdim", "view"] as const;

function readQuery(): ReportQuery {
  if (typeof window === "undefined") return {};
  const params = new URLSearchParams(window.location.search);
  const out: ReportQuery = {};
  for (const key of QUERY_KEYS) {
    const v = params.get(key);
    if (v) out[key] = v;
  }
  return out;
}

function toSearchString(q: ReportQuery): string {
  const parts: string[] = [];
  if (q.from || q.to) {
    if (q.from) parts.push(`from=${encodeURIComponent(q.from)}`);
    if (q.to) parts.push(`to=${encodeURIComponent(q.to)}`);
  } else if (q.p) {
    parts.push(`p=${encodeURIComponent(q.p)}`);
  }
  if (q.slice) parts.push(`slice=${encodeURIComponent(q.slice)}`);
  if (q.person) parts.push(`person=${encodeURIComponent(q.person)}`);
  if (q.tgran) parts.push(`tgran=${encodeURIComponent(q.tgran)}`);
  if (q.tdim) parts.push(`tdim=${encodeURIComponent(q.tdim)}`);
  return parts.join("&");
}

/** Reads/writes the URL's querystring (history.replaceState — no new entries,
 * mirroring the monolith's _syncURL) and notifies subscribers on change so
 * every hook instance on the page (and the FilterBar driving them) stays in
 * sync without a shared store. */
const listeners = new Set<() => void>();

export function setReportQuery(patch: ReportQuery) {
  if (typeof window === "undefined") return;
  const next = { ...readQuery(), ...patch };
  // Clearing a param: an explicit `null`/`""` in `patch` removes the key
  // rather than writing an empty value into the querystring.
  for (const key of Object.keys(patch) as (keyof ReportQuery)[]) {
    if (!patch[key]) delete next[key];
  }
  const qs = toSearchString(next);
  const url = window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash;
  window.history.replaceState(null, "", url);
  listeners.forEach((fn) => fn());
}

export function useReportQuery(): ReportQuery {
  const [query, setQuery] = useState<ReportQuery>(() => readQuery());
  useEffect(() => {
    const onChange = () => setQuery(readQuery());
    listeners.add(onChange);
    window.addEventListener("popstate", onChange);
    return () => {
      listeners.delete(onChange);
      window.removeEventListener("popstate", onChange);
    };
  }, []);
  return query;
}

/** Fetches `/api/report/<view>` with the current query params, refetching
 * whenever they change. `{data, loading, error}` — `data` keeps the last
 * successful payload while a refetch is in flight (no flash-to-empty on a
 * period/scope change), matching the monolith's cache-then-swap behaviour.
 *
 * `extra` carries view-specific params that aren't part of the shared
 * ReportQuery/URL contract — e.g. Trend's `gran`/`dim` (the endpoint's own
 * query params; distinct from the `tgran`/`tdim` deep-link keys the page
 * reads from the URL — see pages/Trend.tsx). Passed by value each render;
 * only its serialised contents (not identity) trigger a refetch. */
export function useReportData<T = unknown>(
  view: string,
  extra?: Record<string, string | undefined | null>,
): ReportDataState<T> {
  const query = useReportQuery();
  const [state, setState] = useState<ReportDataState<T>>({ data: null, loading: true, error: null });
  const seq = useRef(0);
  const extraKey = extra ? JSON.stringify(extra) : "";

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mySeq = ++seq.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    // `view` stays in the browser URL (so changing the period keeps the view) but is
    // stripped from the request: every view of a page reads the SAME payload, only the
    // rendering differs, so sending it would key the cache on something the server
    // does not vary by.
    const { view: _view, ...forApi } = query;
    let qs = toSearchString(forApi);
    if (extra) {
      for (const [k, v] of Object.entries(extra)) {
        if (v) qs += (qs ? "&" : "") + `${k}=${encodeURIComponent(v)}`;
      }
    }
    fetch(`/api/report/${view}${qs ? `?${qs}` : ""}`)
      .then((res) => res.json())
      .then((json) => {
        if (mySeq !== seq.current) return;   // a newer request already landed
        if (json && json.ok) {
          setState({ data: json as T, loading: false, error: null });
        } else {
          setState((s) => ({ ...s, loading: false, error: (json && json.error) || "request failed" }));
        }
      })
      .catch((err) => {
        if (mySeq !== seq.current) return;
        setState((s) => ({ ...s, loading: false, error: String(err) }));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, query.p, query.from, query.to, query.slice, query.person, extraKey]);

  return state;
}
