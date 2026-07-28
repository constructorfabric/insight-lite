// Entry point for the /dashboard/<id> route (see pages/Dashboard.tsx). Ships
// base+shell+chart + the dashboard-page chrome CSS — NOT report.css, matching
// what the legacy dashboard.j2 loaded (its .kpi/.dt styling never lived there),
// so the KPI tiles / data tables render identically on both paths. SSR-safe:
// no window/document access at module top level — only inside the mount below,
// which only ever runs in the browser.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/dashboard.css";
import Dashboard from "../pages/Dashboard";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<Dashboard />);
}
