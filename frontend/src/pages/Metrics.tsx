// /metrics — the metrics catalog, migrated to React (Manage migration). Fetches
// GET /api/manage/metrics.json and reproduces metrics_catalog.render_page()'s markup
// + classes exactly (see ../styles/metrics.css) so the screenshot-diff gate sees no
// pixel difference. The search / type-filter / expand-all / group-collapse / row-open
// behaviours mirror the inline IIFE the Jinja template shipped, reimplemented as
// React state — initial render (no filter, groups open, rows closed) matches the
// server DOM byte-for-byte.
//
// SSR-safe: no top-level window/document access — only inside effects.
import { useEffect, useMemo, useState } from "react";

type Metric = {
  name: string;
  type: string;
  desc?: string;
  unit?: string;
  formula?: string;
  where?: string;
  snippet?: string;
};
type Group = { id: string; title: string; metrics: Metric[] };
type Catalog = { groups: Group[]; total: number; direct: number; computed: number };

function MetricBody({ m }: { m: Metric }) {
  return (
    <>
      {m.formula ? (
        <div className="frow">
          <span className="k">Formula</span>
          <code className="formula">{m.formula}</code>
        </div>
      ) : null}
      {m.where ? (
        <div className="frow">
          <span className="k">Computed in</span>
          <span className="where">{m.where}</span>
        </div>
      ) : null}
      {m.snippet ? <pre className="snip">{m.snippet}</pre> : null}
    </>
  );
}

export default function Metrics() {
  const [cat, setCat] = useState<Catalog | null>(null);
  const [q, setQ] = useState("");
  const [tfilter, setTfilter] = useState("all");
  const [allOpen, setAllOpen] = useState(false);
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [openRows, setOpenRows] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    fetch("/api/manage/metrics.json")
      .then((res) => res.json())
      .then((data) => {
        if (!cancelled && data && data.ok) setCat(data);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const qn = q.toLowerCase().trim();
  // Mirrors the template IIFE apply(): a metric is visible when the type filter
  // matches AND the query hits its name or description.
  const visible = useMemo(() => {
    const fn = (m: Metric) =>
      (tfilter === "all" || m.type === tfilter) &&
      (!qn ||
        m.name.toLowerCase().indexOf(qn) >= 0 ||
        (m.desc || "").toLowerCase().indexOf(qn) >= 0);
    return fn;
  }, [tfilter, qn]);

  const sub = cat
    ? `Every number the report shows — ${cat.total} metrics ` +
      `(${cat.direct} direct, ${cat.computed} computed), declared next to the code so this page ` +
      `can’t drift from the implementation. Search or filter to find a metric; ` +
      `open a row for its formula and the exact query.`
    : "";

  const groups = cat?.groups ?? [];
  const shownCount = (g: Group) => g.metrics.filter(visible).length;
  const anyShown = groups.some((g) => shownCount(g) > 0);

  function toggleAll() {
    if (!allOpen) {
      const all = new Set<string>();
      for (const g of groups) for (const m of g.metrics) all.add(g.id + "::" + m.name);
      setOpenRows(all);
      setAllOpen(true);
    } else {
      setOpenRows(new Set());
      setAllOpen(false);
    }
  }
  function toggleGroup(id: string) {
    setClosed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function toggleRow(key: string) {
    setOpenRows((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }
  function jump(id: string) {
    setClosed((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    const el = document.querySelector(`.group[data-group="${id}"]`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <>
      <h1>Metrics catalog</h1>
      <p className="sub">{sub}</p>
      <div className="bar">
        <div className="bar-row">
          <label className="search">
            <svg viewBox="0 0 24 24">
              <circle cx="11" cy="11" r="7" />
              <path d="m21 21-4.3-4.3" />
            </svg>
            <input
              id="q"
              placeholder="Search metrics by name or meaning…"
              autoComplete="off"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </label>
          <span className="seg" id="typeSeg">
            {[
              ["all", "All"],
              ["direct", "Direct"],
              ["computed", "Computed"],
            ].map(([t, label]) => (
              <button
                key={t}
                data-t={t}
                className={t === tfilter ? "on" : undefined}
                onClick={() => setTfilter(t)}
              >
                {label}
              </button>
            ))}
          </span>
          <button className="expandbtn" id="toggleAll" onClick={toggleAll}>
            {allOpen ? "Collapse all" : "Expand all"}
          </button>
        </div>
        <div className="navchips">
          {groups.map((g) => (
            <a key={g.id} className="navchip" data-jump={g.id} onClick={() => jump(g.id)}>
              {g.title} <span className="c">{g.metrics.length}</span>
            </a>
          ))}
        </div>
      </div>
      <div id="content">
        {groups.map((g) => {
          const shown = shownCount(g);
          return (
            <div
              key={g.id}
              className={"group" + (closed.has(g.id) ? " closed" : "")}
              data-group={g.id}
              style={shown ? undefined : { display: "none" }}
            >
              <div className="group-h" data-gh onClick={() => toggleGroup(g.id)}>
                <h2>{g.title}</h2>
                <span className="gc">{g.metrics.length}</span>
                <span className="chev">&#9662;</span>
              </div>
              <div className="list">
                {g.metrics.map((m) => {
                  const key = g.id + "::" + m.name;
                  const hidden = !visible(m);
                  const open = openRows.has(key);
                  return (
                    <div
                      key={key}
                      className={"m" + (open ? " open" : "") + (hidden ? " hidden" : "")}
                      data-name={m.name.toLowerCase()}
                      data-desc={(m.desc || "").toLowerCase()}
                      data-type={m.type}
                    >
                      <div className="m-row" onClick={() => toggleRow(key)}>
                        <span className="m-name">{m.name}</span>
                        <span className={"pill " + m.type}>{m.type}</span>
                        <span className="m-desc">{m.desc || ""}</span>
                        <span className="unit">{m.unit || ""}</span>
                        <span className="m-chev">&#9656;</span>
                      </div>
                      <div className="m-body">
                        <MetricBody m={m} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
      <div className="empty" id="empty" hidden={cat === null || anyShown}>
        No metrics match your search.
      </div>
    </>
  );
}
