// /dashboards — the custom-dashboard list, migrated to React (Manage migration).
// Fetches GET /api/manage/dashboards.json (viewer login + own/shared dashboards)
// and reproduces server.dashboards_list_page()'s markup + classes exactly (see
// ../styles/dashboards.css) so the screenshot-diff gate sees no pixel difference.
// The New button POSTs a blank dashboard and jumps into its editor, same as the
// legacy inline JS. NOTE: distinct from Dashboard.tsx (the dashboard VIEW page).
//
// SSR-safe: no top-level window/document access — only inside effects / handlers.
import { useEffect, useState } from "react";

type Row = {
  id: string;
  title?: string;
  owner_login?: string;
  visibility?: string;
  updated_ts?: string;
};

export default function DashboardsList() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [login, setLogin] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/manage/dashboards.json")
      .then((res) => res.json())
      .then((data) => {
        if (cancelled || !data || !data.ok) return;
        setLogin(data.login ?? null);
        setRows(data.dashboards || []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  function newDashboard() {
    fetch("/api/dashboard", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec: { title: "Untitled dashboard", panels: [] } }),
    })
      .then((r) => r.json())
      .then((s) => {
        if (s.ok) window.location.href = "/dashboard/" + s.id + "/edit";
      });
  }

  return (
    <>
      <h1>Dashboards</h1>
      <p className="sub">
        Custom dashboards — your own and any shared with you. Build one from reusable panels and
        share it with the team.
      </p>
      <p>
        <button id="new-dash" className="primary" type="button" onClick={newDashboard}>
          New dashboard
        </button>
      </p>
      {rows === null ? null : rows.length ? (
        <table className="u">
          <thead>
            <tr>
              <th>Title</th>
              <th>Owner</th>
              <th>Visibility</th>
              <th>Updated</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>
                  <a href={`/dashboard/${r.id}`}>{r.title || "Untitled dashboard"}</a>
                </td>
                <td>{r.owner_login ?? ""}</td>
                <td>{r.visibility ?? ""}</td>
                <td>{(r.updated_ts || "").slice(0, 16)}</td>
                <td>
                  {r.owner_login === login ? <a href={`/dashboard/${r.id}/edit`}>Edit</a> : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="empty">No dashboards yet — click “New dashboard” to create one.</div>
      )}
    </>
  );
}
