// Entry point for the /calibrate route. SSR-safe: no window/document at module top
// level. base+shell+chart + the page's own calibrate.css.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/calibrate.css";
import CalibrateEditor from "../pages/CalibrateEditor";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<CalibrateEditor />);
}
