// Entry point for the /identity route. SSR-safe: no window/document at module top
// level. base+shell+chart + the page's own identity.css.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/identity.css";
import IdentityEditor from "../pages/IdentityEditor";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<IdentityEditor />);
}
