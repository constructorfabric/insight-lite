// Entry point for the /update route. SSR-safe: no window/document at module top
// level. base+shell+chart + the page's own update.css.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/update.css";
import UpdatePortal from "../pages/UpdatePortal";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<UpdatePortal />);
}
