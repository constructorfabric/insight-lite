// Entry point for the /chat-log route (assistant conversation viewer, URL-only).
// SSR-safe: no window/document at module top level.
import { createRoot } from "react-dom/client";
import "../styles/base.css";
import "../styles/shell.css";
import "../styles/chart.css";
import "../styles/chatlog.css";
import ChatLog from "../pages/ChatLog";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<ChatLog />);
}
