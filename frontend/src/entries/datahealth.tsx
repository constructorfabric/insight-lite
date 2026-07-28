// Entry point for the /data-health route. SSR-safe: no window/document at module
// top level. base+shell+chart + the page's own datahealth.css.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/datahealth.css";
import DataHealth from "../pages/DataHealth";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<DataHealth />);
}
