// Entry for the shared report-view chrome (chat widget + drill modal + sort),
// loaded by render_spa_page on report_chrome pages. Mounts a body-level React root;
// the overlays themselves portal to <body> (chat) / append to <body> (drill), so
// they float above the page like the vanilla originals. Gated to a live http(s)
// server (matches the old CHAT_WIDGET_JS/DRILL_JS gate) — a report opened as a
// local file shows no dead launcher and needs no /api. SSR-safe.
import { createRoot } from "react-dom/client";
import ReportChrome from "../components/ReportChrome";

if (typeof document !== "undefined" && /^https?:$/.test(location.protocol)) {
  const el = document.createElement("div");
  el.id = "report-chrome-root";
  document.body.appendChild(el);
  createRoot(el).render(<ReportChrome />);
}
