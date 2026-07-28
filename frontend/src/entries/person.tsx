// Entry point for the /person route (see pages/Person.tsx). SSR-safe: no
// window/document access at module top level — only inside the mount call
// below, which only ever runs in the browser.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/report.css";
import Person from "../pages/Person";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<Person />);
}
