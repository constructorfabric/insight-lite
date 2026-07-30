// The app sidebar, rendered from the nav model the server inlines as JSON.
//
// The model lives in Python (backend/shell.py's NAV_ZONES) and is rendered TWICE:
// once to HTML there, once here. That is deliberate and not duplication of the
// model — only of its presentation. It buys two things:
//
//  · the server-rendered markup is already correct before any JS runs, so the
//    sidebar never appears empty and never shifts when this mounts;
//  · a page still navigates with the bundle absent or broken.
//
// The shape mirrors insight-front's src/lib/portal/nav-model.ts (zones holding
// items), and `icon` is a lucide component NAME — the vocabulary that file already
// declares — so the two products name the same glyph the same way. This is the
// convergence: the model and the words, not shared components. See the discussion
// in backend/shell.py's header.
import { useEffect, useState, type ReactNode } from "react";
import { useReportQuery } from "../hooks/useReportData";
import {
  Activity,
  Bell,
  ChartColumn,
  Contact,
  Folder,
  Gauge,
  GitPullRequest,
  Layers,
  LayoutDashboard,
  LayoutGrid,
  Network,
  Package,
  Plug,
  RefreshCw,
  Settings,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  TrendingUp,
  User,
  Users,
  type LucideIcon,
} from "lucide-react";

// Set on a rail click, read once by the next page's mount — see `dismissed` below.
const RAIL_DISMISSED = "insight.rail.dismissed";

export type NavItem = { key: string; label: string; href: string; icon: string };
export type NavZone = {
  key: string; label: string; icon: string; items: NavItem[];
  /** Which report-query params links into this zone keep — the server's own rule
      (backend/shell.py's zone_carry), sent along so this renderer applies exactly
      the same one instead of a second guess at it. */
  carry?: string[];
};

// Named imports into an explicit map, NOT `import * as icons` with a dynamic lookup:
// the namespace import defeats tree-shaking and pulls lucide's whole catalogue in,
// which took this bundle to 636 kB — on every page, for twenty glyphs. Adding a nav
// entry with a new icon means adding it here too, and that is the point: the cost of
// an icon is visible at the place you pay it.
//
// An unknown name renders nothing rather than throwing: a typo in the model should
// lose one glyph, not take the sidebar down with it.
const ICONS: Record<string, LucideIcon> = {
  Activity,
  Bell,
  ChartColumn,
  Contact,
  Folder,
  Gauge,
  GitPullRequest,
  Layers,
  LayoutDashboard,
  LayoutGrid,
  Network,
  Package,
  Plug,
  RefreshCw,
  Settings,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  TrendingUp,
  User,
  Users,
};

function Icon({ name }: { name: string }): ReactNode {
  const C = ICONS[name];
  return C ? <C className="i" aria-hidden="true" /> : null;
}

// The server built each href with the query it saw on THIS request. Anything picked
// after that — a person chosen in the combo, a period clicked — only ever reached the
// URL, via history.replaceState, so those hrefs are already stale by the time you use
// them. That is what made switching Person views drop the person.
//
// So merge the live query on top, per the zone's own `carry` list. The href wins where
// it says something itself: a pane link's `?view=activity` must not be overwritten by
// the `view` you are leaving.
function carried(href: string, allow: string[] | undefined, q: Record<string, string>): string {
  if (!allow?.length) return href;
  // Parsed with URL rather than split("?"), so a fragment stays a fragment. Hand-rolled
  // splitting puts the query AFTER the hash on an href that has one and no "?" —
  // /report#flow?p=30d, which is not the same address. No nav href carries a fragment
  // today (see shell.sidebar_html on why /report#<mode> is gone), and this is what
  // keeps that from mattering if one comes back.
  // Fixed base, not window.location: these hrefs are all site-relative, and the module
  // stays free of window access so it can be rendered outside a browser.
  const u = new URL(href, "http://nav.local");
  for (const k of allow) {
    const v = q[k];
    if (v && !u.searchParams.has(k)) u.searchParams.set(k, v);
  }
  return u.pathname + u.search + u.hash;
}

// Rail + pane, byte-for-byte the shape backend/shell.py's sidebar_html emits — that
// identity is the whole no-jump property, so the two must be changed together.
//
// The rail's entries are links, not zone-pinning state as in insight-front's portal:
// lite navigates per page, so a zone points at its first item and the pane shows
// whichever zone the current page belongs to. A single-item zone still gets a pane
// holding that one item — dropping the pane would change the sidebar's width by page,
// and a layout that resizes as you navigate is worse than a one-row pane. The group
// HEADING is what a single item does not earn.
export default function Sidebar(
  { zones, active, caption }: { zones: NavZone[]; active: string; caption?: string },
) {
  // Falls back to the first zone: `active` is "" or unknown on a page outside the nav
  // (e.g. /calibrate), and a rail with nothing lit beside an empty pane reads as
  // broken rather than as "this page is not in the menu".
  const current = zones.find((z) => z.items.some((i) => i.key === active)) ?? zones[0];
  // Subscribed, not read once: the person is picked without leaving the page, and the
  // links have to be right by the time the pointer reaches them.
  const q = useReportQuery() as Record<string, string>;
  // A rail click navigates, so the destination loads with the cursor still on the
  // rail — and a plain :hover would reopen it over the pane the click was aimed at.
  // The flag therefore has to survive the page load, hence sessionStorage; it is
  // consumed on read so it applies to exactly the one navigation that set it, and
  // cleared when the pointer leaves so the next hover expands normally.
  const [dismissed, setDismissed] = useState(() => {
    try {
      return !!sessionStorage.getItem(RAIL_DISMISSED);
    } catch {
      // storage can be denied (private mode, embedded); losing the flag only means
      // the rail behaves as it did before this existed
      return false;
    }
  });
  // Cleared in an effect, not in the initializer above: StrictMode dev builds invoke
  // an initializer twice, and a side effect in there means the second call sees state
  // the first one already consumed.
  useEffect(() => {
    try {
      sessionStorage.removeItem(RAIL_DISMISSED);
    } catch { /* see the read side above */ }
  }, []);
  return (
    <>
      <div className="brand">
        <div>
          Constructor&nbsp;Insight
          {/* Org + how old the numbers are. Mirrors shell.report_caption(), and falls
              back to the tagline the same way — see that function for why the line it
              replaces (per page, with "since 2008-01-01" in it) had to go. */}
          <span>{caption || "Contribution & Usage"}</span>
        </div>
      </div>
      <div className="sb-cols">
        <nav
          className={`sb-rail${dismissed ? " rail-dismissed" : ""}`}
          aria-label="Sections"
          onMouseLeave={() => setDismissed(false)}
        >
          {/* The inner wrapper is what hover widens; the <nav> keeps its 44px slot in
              the flow so the pane never moves. Mirrors shell.sidebar_html exactly. */}
          <div className="sb-rail-inner">
          {zones.map((z) => (
            <a
              key={z.key}
              className={`rz${z === current ? " active" : ""}${z.key === "manage" ? " rz-bottom" : ""}`}
              href={carried(z.items[0].href, z.carry, q)}
              aria-label={z.label}
              aria-current={z === current ? "true" : undefined}
              onClick={() => {
                // Not preventDefault: the click navigates to the zone's first item.
                // This only tells the next page to load with the rail collapsed.
                try {
                  sessionStorage.setItem(RAIL_DISMISSED, "1");
                } catch { /* see the read side above */ }
              }}
            >
              <Icon name={z.icon} />
              <span className="rz-l">{z.label}</span>
            </a>
          ))}
          </div>
        </nav>
        <nav className="sb-pane" aria-label={current.label}>
          {current.items.length > 1 && <div className="navgroup">{current.label}</div>}
          {current.items.map((it) => (
            <a
              key={it.key}
              className={`tab${it.key === active ? " active" : ""}`}
              href={carried(it.href, current.carry, q)}
            >
              <Icon name={it.icon} />
              {/* Own span so a long label ellipsises rather than being cut mid-glyph —
                  mirrors shell.sidebar_html; see .sb-pane .tab>span in SHELL_CSS. */}
              <span>{it.label}</span>
            </a>
          ))}
        </nav>
      </div>
    </>
  );
}
