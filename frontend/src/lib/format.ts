// Ports of render.py's number-format filters (_num/_loc/_pct) — MUST stay
// byte-identical to the Python originals, since the same numbers render via
// both paths (the monolith's Jinja filters vs these, for the React views) and
// any drift is a pixel diff. See render.py for the Python source of truth.

function toNumber(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** A plain count: grouped integer with thousands separators — "5,848". */
export function fmtNum(v: unknown): string {
  const n = toNumber(v);
  return n === null ? "0" : Math.round(n).toLocaleString("en-US");
}

/** A volume (lines of code): compact K/M — "3.49M", "25.7K", "812". */
export function fmtLoc(v: unknown): string {
  const n = toNumber(v);
  if (n === null) return "0";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return `${Math.round(n)}`;
}

/** A percentage number (no % sign): one decimal, trailing ".0" stripped — "50", "72.1". */
export function fmtPct(v: unknown): string {
  const n = toNumber(v);
  if (n === null) return "0";
  const s = n.toFixed(1);
  return s.endsWith(".0") ? s.slice(0, -2) : s;
}

// jr(x,p): JS twin of Jinja's `|round(p)` string — Number formatting matches
// Python str(float) EXCEPT integer-valued floats, which Python prints with a
// trailing ".0" (JS drops it); re-add it so "4" → "4.0". Shared by pages/Person
// and widgets/score/PersonScore (both must stay byte-identical to the monolith).
export function jr(x: number, p = 0): string {
  const f = 10 ** p;
  const r = Math.round(x * f) / f;
  const s = String(r);
  return Number.isInteger(r) && p > 0 ? `${s}.0` : s;
}
