// Entry point for the /usage-insights route. SSR-safe: no window/document at module
// top level. base+shell+chart + the page's own usage.css.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/usage.css";
import UsageInsights from "../pages/UsageInsights";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<UsageInsights />);
}
