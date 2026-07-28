// Entry point for the /mcp-info route. SSR-safe: no window/document at module top
// level. base+shell+chart + the page's own mcp.css (from server.mcp_page()).
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/mcp.css";
import Mcp from "../pages/Mcp";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<Mcp />);
}
