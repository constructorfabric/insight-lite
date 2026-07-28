// Entry point for the /semantic guided setup wizard.
// SSR-safe: no window/document at module top level.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/semantic_wizard.css";
import SemanticWizard from "../pages/SemanticWizard";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<SemanticWizard />);
}
