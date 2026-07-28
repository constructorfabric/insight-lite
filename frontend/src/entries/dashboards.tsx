// Entry point for the /dashboards (list) route. SSR-safe: no window/document at
// module top level. base+shell+chart + the page's own dashboards.css. NOTE:
// separate entry from dashboard.tsx (the dashboard VIEW page).
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/dashboards.css";
import DashboardsList from "../pages/DashboardsList";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<DashboardsList />);
}
