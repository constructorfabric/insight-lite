// Entry point for the /metrics route. SSR-safe: no window/document at module top
// level — the mount below only runs in the browser. base+shell+chart + the page's
// own metrics.css (extracted from the pre-migration Jinja template).
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/metrics.css";
import Metrics from "../pages/Metrics";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<Metrics />);
}
