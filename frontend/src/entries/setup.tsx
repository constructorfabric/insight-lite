// Entry point for the /setup first-run wizard. Rendered without the sidebar shell
// (render_spa_page(sidebar=False)) — the wizard is a standalone centred page.
// SSR-safe: no window/document at module top level.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/setup.css";
import SetupWizard from "../pages/SetupWizard";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<SetupWizard />);
}
