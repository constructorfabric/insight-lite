// /views — the reusable-component catalog, migrated to React (Manage migration).
// Fetches GET /api/manage/views.json and reproduces views_catalog.render_page()'s
// card markup + classes exactly (see ../styles/views.css) so the screenshot-diff
// gate sees no pixel difference. This page is static (no filters/interactivity) —
// just grouped cards.
//
// SSR-safe: no top-level window/document access — only inside effects.
import { Fragment, useEffect, useState } from "react";

type Param = {
  name: string;
  type: string;
  desc: string;
  required?: boolean;
  values?: string[];
};
type View = {
  name: string;
  kind: string;
  purpose: string;
  when_to_use: string;
  params: Param[];
  example: string;
  html_contract?: string;
  where: string;
};
type Group = { id: string; title: string; views: View[] };

function ParamRow({ p }: { p: Param }) {
  const vals = p.values && p.values.length ? " · " + p.values.join(", ") : "";
  return (
    <tr>
      <td>
        <code>{p.name}</code>
      </td>
      <td className="ty">
        {p.type}
        {vals}
      </td>
      <td>
        {p.desc}
        {p.required ? <span className="req"> required</span> : null}
      </td>
    </tr>
  );
}

function Card({ v }: { v: View }) {
  return (
    <div className="vc">
      <div className="vc-h">
        <span className="vname">{v.name}</span>
        <span className="kind">{v.kind}</span>
      </div>
      <p className="vp">{v.purpose}</p>
      <p className="vw">
        <b>When:</b> {v.when_to_use}
      </p>
      <table className="vparams">
        <tbody>
          <tr>
            <th>param</th>
            <th>type</th>
            <th>meaning</th>
          </tr>
          {v.params.map((p) => (
            <ParamRow key={p.name} p={p} />
          ))}
        </tbody>
      </table>
      <div className="vlbl">Example</div>
      <pre className="vex">{v.example}</pre>
      {v.html_contract ? (
        <>
          <div className="vlbl">HTML contract</div>
          <pre className="vex">{v.html_contract}</pre>
        </>
      ) : null}
      <div className="vsrc">
        component: <code>{v.where}</code>
      </div>
    </div>
  );
}

export default function Views() {
  const [groups, setGroups] = useState<Group[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/manage/views.json")
      .then((res) => res.json())
      .then((data) => {
        if (!cancelled && data && data.ok) setGroups(data.groups);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <h1>View catalog</h1>
      <p className="sub">
        Reusable visual components for building dashboards and artifacts — the same data the MCP{" "}
        <code>views_catalog</code> tool returns. Pick a display method, copy the example, or
        reproduce it from the HTML contract.
      </p>
      {(groups ?? []).map((g) => (
        <Fragment key={g.id}>
          <h2 className="vg">
            {g.title} <span className="gc">{g.views.length}</span>
          </h2>
          <div className="vgrid">
            {g.views.map((v) => (
              <Card key={v.name} v={v} />
            ))}
          </div>
        </Fragment>
      ))}
    </>
  );
}
