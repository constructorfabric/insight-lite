// Entry for the app sidebar, loaded by render_spa_page on every shelled page —
// same mechanism as the `report-chrome` entry, but unconditional, because
// navigation is not a per-view feature.
//
// It mounts onto the <aside> the server already filled from the same model
// (backend/shell.py's NAV_ZONES). React clears the container and inserts its own
// output inside ONE commit, so the browser never paints an empty sidebar between
// the two — that is why the server keeps rendering it rather than emitting a bare
// shell for React to fill. The markup is structurally identical either way, so
// nothing moves on the page when this runs.
//
// The <aside> element itself is never replaced, only its children, so the
// mobile-drawer script in shell.SHELL_CSS's sibling <script> — which holds
// listeners on `.sidebar`, `.navburger` and `.nav-backdrop` — keeps working.
//
// SSR-safe: no window/document access at module top level.
import { createRoot } from "react-dom/client";
import Sidebar, { type NavZone } from "../components/Sidebar";

type NavModel = { active: string; zones: NavZone[]; caption?: string };

function model(): NavModel | null {
  const el = document.getElementById("nav-model");
  if (!el?.textContent) return null;
  try {
    return JSON.parse(el.textContent) as NavModel;
  } catch {
    // A malformed island leaves the server-rendered sidebar in place, which still
    // navigates — strictly better than replacing it with nothing.
    return null;
  }
}

if (typeof document !== "undefined") {
  const host = document.getElementById("app-sidebar");
  const nav = model();
  if (host && nav?.zones?.length) {
    createRoot(host).render(
      <Sidebar zones={nav.zones} active={nav.active} caption={nav.caption} />,
    );
  }
}
