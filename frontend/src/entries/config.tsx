// Entry point for the /config route. SSR-safe: no window/document at module top
// level. base+shell+chart + the page's own config.css.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/config.css";
import ConfigEditor from "../pages/ConfigEditor";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<ConfigEditor />);
}
