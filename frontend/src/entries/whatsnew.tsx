// Entry point for the /whats-new route. Keep this SSR-safe: no window/document access
// at module top level — only inside the mount call below, which only ever runs in the
// browser (Vite build output is loaded via a <script type="module"> tag, never
// evaluated server-side). This discipline keeps a future SSR graft cheap (see the design
// spec's "Reversibility" section).
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/whatsnew.css";
import WhatsNew from "../pages/WhatsNew";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<WhatsNew />);
}
