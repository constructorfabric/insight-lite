// /mcp-info — the MCP access page, migrated to React (Manage migration). Fetches
// GET /api/manage/mcp.json and reproduces server.mcp_page()'s markup + classes
// exactly (see ../styles/mcp.css) so the screenshot-diff gate sees no pixel
// difference. Behaviours ported from the page's inline JS: editable Public URL
// with a live "<base>/mcp" preview, Save (POST /api/mcp/public-url), and Copy token.
//
// SSR-safe: no top-level window/document access — window is read only inside the
// component body / handlers, which run in the browser (the entry mounts client-side).
import { useEffect, useState } from "react";

type Tool = { name: string; sig: string; doc: string };

// Mirrors the legacy mcpBase(): the entered URL (trimmed) or the current origin,
// with any trailing slashes stripped.
function mcpBase(value: string): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  let s = (value || "").trim() || origin;
  while (s.length && s.charAt(s.length - 1) === "/") s = s.slice(0, -1);
  return s;
}

const inputStyle: React.CSSProperties = {
  flex: 1, fontSize: "13px", padding: "7px 9px", border: "1px solid var(--line)",
  borderRadius: "6px", background: "var(--panel)", color: "var(--ink)",
};
const tokenStyle: React.CSSProperties = {
  flex: 1, fontFamily: "ui-monospace,Menlo,monospace", fontSize: "13px", padding: "7px 9px",
  border: "1px solid var(--line)", borderRadius: "6px", background: "var(--panel2)",
  color: "var(--ink)", overflowX: "auto", whiteSpace: "nowrap",
};
const rowStyle: React.CSSProperties = { display: "flex", gap: "8px", alignItems: "center", margin: "8px 0" };

export default function Mcp() {
  const [publicUrl, setPublicUrl] = useState("");
  const [token, setToken] = useState("");
  const [tools, setTools] = useState<Tool[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/manage/mcp.json")
      .then((res) => res.json())
      .then((data) => {
        if (cancelled || !data || !data.ok) return;
        setPublicUrl(data.publicUrl || "");
        setToken(data.token || "");
        setTools(data.tools || []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  async function save() {
    try {
      const r = await fetch("/api/mcp/public-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: publicUrl.trim() }),
      });
      const j = await r.json();
      if (j.ok) setPublicUrl(j.url);
      else alert(j.error || "save failed");
    } catch {
      alert("save failed");
    }
  }

  function copy() {
    const t = (token || "").trim();
    if (!t) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t).catch(() => {});
    }
  }

  const urlPreview = mcpBase(publicUrl) + "/mcp";

  return (
    <>
      <h1>MCP access</h1>
      <p className="sub">
        Connect an AI client (Claude, etc.) to this instance over the Model Context Protocol for
        read-only access to the contribution &amp; delivery data. Point the client at the URL below
        with the bearer token, then call the tools listed underneath.
      </p>

      <div className="card" style={{ marginTop: "12px" }}>
        <div className="label">
          Public URL — used in the connect instructions (set it to how clients reach this host, e.g.
          https://insight.example.com)
        </div>
        <div style={rowStyle}>
          <input
            id="mcp-baseurl"
            type="text"
            value={publicUrl}
            placeholder="https://your-host"
            style={inputStyle}
            onChange={(e) => setPublicUrl(e.target.value)}
          />
          <button onClick={save}>Save</button>
        </div>
        <div className="label" style={{ marginTop: "10px" }}>
          Bearer token — read-only data access for MCP clients
        </div>
        <div style={rowStyle}>
          <code id="mcp-token" data-token={token} style={tokenStyle}>
            {token || "(none — set MCP_TOKEN on the server)"}
          </code>
          <button onClick={copy}>Copy</button>
        </div>
        <div className="fine">
          Connect an MCP client to <code id="mcp-url">{urlPreview}</code> and send{" "}
          <code>Authorization: Bearer &lt;token&gt;</code>. Shown in the clear on purpose so you can
          copy it. Empty token = unauthenticated (only safe behind this proxy).
        </div>
        <div className="fine" style={{ marginTop: "6px" }}>
          Rotating the token would invalidate every connected MCP client at once, so it's
          intentionally not a button here — rotate it from the server (<code>MCP_TOKEN</code> env /
          secret) only when a token is leaked.
        </div>
      </div>

      <h2>Tools</h2>
      <p className="sub">
        Every tool is read-only. Descriptions are read straight from the MCP server, so this list
        always matches what a connected client sees.
      </p>
      <table className="mcp">
        <thead>
          <tr>
            <th>Tool</th>
            <th>Parameters</th>
            <th>What it does</th>
          </tr>
        </thead>
        <tbody>
          {tools.length ? (
            tools.map((t) => (
              <tr key={t.name}>
                <td>
                  <code>{t.name}</code>
                </td>
                <td>{t.sig ? <code>{t.sig}</code> : <span className="mut">—</span>}</td>
                <td>{t.doc}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={3} className="mut">
                No tools found.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
