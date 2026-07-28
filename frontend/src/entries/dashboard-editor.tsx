// Entry point for the /dashboard/<id>/edit route (owner-only dashboard editor).
// vega=True on the server shell provides window.vegaEmbed for the live previews.
// SSR-safe: no window/document at module top level.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/dashboard_editor.css";
import DashboardEditor from "../pages/DashboardEditor";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<DashboardEditor />);
}
