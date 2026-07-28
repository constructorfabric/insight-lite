// Entry point for the /semantic/advanced route (the dense grid editor).
// SSR-safe: no window/document at module top level.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/semantic.css";
import SemanticAdvanced from "../pages/SemanticAdvanced";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<SemanticAdvanced />);
}
