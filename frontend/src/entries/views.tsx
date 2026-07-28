// Entry point for the /views route. SSR-safe: no window/document at module top
// level. base+shell+chart + the page's own views.css (from views_catalog._PAGE_CSS).
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/views.css";
import Views from "../pages/Views";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<Views />);
}
