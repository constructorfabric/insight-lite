// Entry point for the /elements route (see pages/Elements.tsx).
// SSR-safe: no window/document access at module top level — only inside the
// mount call below, which only ever runs in the browser.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/report.css";
import Elements from "../pages/Elements";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<Elements />);
}
