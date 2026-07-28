// /usage-insights — meta-analytics on the report itself, migrated to React (Manage
// migration). A faithful port of server.usage_page()'s client logic: the same
// /api/usage-summary (period) + /api/usage-detail (drill) endpoints, the same table
// markup + classes (see ../styles/usage.css), the same period chips / date range,
// clickable KPIs and the detail modal. Pixel-gated on the default 30d loaded state.
//
// SSR-safe: no top-level window/document access — only inside effects / handlers.
import { useCallback, useEffect, useState, type ReactNode } from "react";

type Row = Record<string, unknown>;
type Col = { h: string; k: string; n?: boolean; cls?: string };

const fmtNum = (n: unknown) => Number(n || 0).toLocaleString("en-US");
const fmtCost = (c: unknown) => (c == null ? "n/a" : "$" + Number(c).toFixed(4));
const tsMin = (ts: unknown) => String(ts || "").replace("T", " ").slice(0, 16);

// Mirrors usage_page()'s table(): class per cell is [n?, cls].filter, rows carry
// clk + a click into openDetail(by, key) when `by` is set (empty key → inert).
function Table({
  rows,
  cols,
  by,
  onRow,
}: {
  rows: Row[] | undefined;
  cols: Col[];
  by?: string;
  onRow?: (by: string, key: string) => void;
}) {
  if (!rows || !rows.length) return <div className="empty">No data in this period yet.</div>;
  const cls = (c: Col) => [c.n ? "n" : "", c.cls || ""].filter(Boolean).join(" ");
  const kf = cols[0].k;
  return (
    <table className="u">
      <thead>
        <tr>
          {cols.map((c, i) => (
            <th key={i} className={cls(c)}>
              {c.h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, ri) => {
          const key = r[kf] == null ? "" : String(r[kf]);
          const clickable = !!by && !!key;
          return (
            <tr
              key={ri}
              className={by ? "clk" : undefined}
              onClick={clickable && onRow ? () => onRow(by!, key) : undefined}
            >
              {cols.map((c, ci) => (
                <td key={ci} className={cls(c)}>
                  {r[c.k] == null ? "" : String(r[c.k])}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function UsageInsights() {
  const [s, setS] = useState<any | null>(null);
  const [range, setRange] = useState("");
  const [curDays, setCurDays] = useState(30);
  const [curFrom, setCurFrom] = useState("");
  const [curTo, setCurTo] = useState("");
  const [fromInput, setFromInput] = useState("");
  const [toInput, setToInput] = useState("");
  const [modal, setModal] = useState<{ title: string; body: ReactNode } | null>(null);

  const load = useCallback((days: number, from: string, to: string) => {
    const q = from && to ? `from=${from}&to=${to}` : `days=${days}`;
    fetch("/api/usage-summary?" + q)
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) {
          setRange(data.error || "error");
          return;
        }
        setS(data);
        setRange(`${data.since} → ${data.until}`);
      })
      .catch(() => setRange("failed to load"));
  }, []);

  useEffect(() => {
    load(30, "", "");
  }, [load]);

  const rangeQ = () => (curFrom && curTo ? `from=${curFrom}&to=${curTo}` : `days=${curDays}`);

  const openDetail = useCallback(
    (by: string, key: string) => {
      fetch(`/api/usage-detail?by=${encodeURIComponent(by)}&key=${encodeURIComponent(key)}&` + rangeQ())
        .then((r) => r.json())
        .then((d) => {
          if (!d.ok) return;
          const label =
            ({ widget: "Widget", tab: "Tab", drill: "Drill-down", chat: "Assistant · view", tool: "Tool", persona: "Person" } as Record<string, string>)[
              by
            ] || by;
          const title = by === "chatlog" ? "Assistant requests" : `${label}: ${key}`;
          let body: ReactNode;
          if (by === "persona") {
            const sec = (t: string, rows: Row[], l: string) => (
              <>
                <h3>{t}</h3>
                <Table rows={rows} cols={[{ h: l, k: "target" }, { h: "Views", k: "views", n: true }]} />
              </>
            );
            body = (
              <>
                {sec("Widgets", d.widgets, "Widget")}
                {sec("Tabs", d.tabs, "Tab")}
                {sec("Drill-downs", d.drills, "Drill-down")}
                {d.chat_log && d.chat_log.length ? (
                  <>
                    <h3>Assistant requests</h3>
                    <ChatReqTable rows={d.chat_log} />
                  </>
                ) : null}
              </>
            );
          } else if (by === "chat" || by === "chatlog") {
            body = <ChatReqTable rows={d.requests} />;
          } else if (by === "tool") {
            const fr = (d.calls || []).map((r: any) => {
              let a: any = {};
              try {
                a = JSON.parse(r.args || "{}");
              } catch {
                /* ignore */
              }
              const argStr =
                a && a.sql != null
                  ? String(a.sql)
                  : Object.keys(a || {})
                      .map((k) => k + "=" + a[k])
                      .join(", ");
              return { ts_f: tsMin(r.ts), who: r.who, status: r.ok ? "ok" : "error", args_f: argStr };
            });
            body = (
              <Table
                rows={fr}
                cols={[
                  { h: "When", k: "ts_f" },
                  { h: "Who", k: "who" },
                  { h: "Status", k: "status" },
                  { h: "Arguments", k: "args_f", cls: "code" },
                ]}
              />
            );
          } else {
            body = <Table rows={d.viewers} cols={[{ h: "Person", k: "who" }, { h: "Views", k: "views", n: true }]} />;
          }
          setModal({ title, body });
        })
        .catch(() => {});
    },
    [curDays, curFrom, curTo],
  );

  function openKpi(kind: string) {
    if (!s) return;
    let title = "";
    let rows: Row[] = [];
    let cols: Col[] = [];
    let by: string | undefined;
    const openCols: Col[] = [{ h: "Person", k: "who" }, { h: "Opens", k: "views", n: true }];
    if (kind === "opens" || kind === "personas") {
      rows = (s.by_persona || []).map((p: any) => ({ who: p.login, views: p.opens }));
      cols = openCols;
      by = "persona";
      if (kind === "opens") {
        const sum = rows.reduce((a, r) => a + (Number(r.views) || 0), 0);
        const un = (s.opens || 0) - sum;
        if (un > 0) rows = rows.concat([{ who: "(unresolved)", views: un }]);
        title = "Report opens";
      } else {
        title = "People who opened";
      }
    } else if (kind === "widgets") {
      title = "Widgets by views";
      rows = s.by_widget || [];
      by = "widget";
      cols = [{ h: "Widget", k: "target" }, { h: "Views", k: "views", n: true }, { h: "People", k: "unique_viewers", n: true }];
    } else if (kind === "tabs") {
      title = "Tabs by views";
      rows = s.by_tab || [];
      by = "tab";
      cols = [{ h: "Tab", k: "target" }, { h: "Views", k: "views", n: true }, { h: "People", k: "unique_viewers", n: true }];
    } else {
      return;
    }
    setModal({ title, body: <Table rows={rows} cols={cols} by={by} onRow={openDetail} /> });
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setModal(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const kpi = (v: ReactNode) => (s ? v : "–");
  const daysActive = (d: number) => !curFrom && !curTo && curDays === d;

  function pickDays(d: number) {
    setCurFrom("");
    setCurTo("");
    setFromInput("");
    setToInput("");
    setCurDays(d);
    load(d, "", "");
  }
  function apply() {
    if (fromInput && toInput) {
      setCurFrom(fromInput);
      setCurTo(toInput);
      load(curDays, fromInput, toInput);
    }
  }

  return (
    <>
      <h1>Usage insights</h1>
      <p className="sub">
        How this report itself is used: who opens it and which widgets they view. Opens are counted
        server-side (reliable); tab &amp; panel views come from the browser and are a floor, not exact.
        Whole-report <b>All</b>-tab scrolls are tracked separately and excluded from the per-widget
        ranking.
      </p>

      <div className="chips" id="chips">
        {[
          [7, "7d"],
          [30, "30d"],
          [90, "90d"],
          [365, "1y"],
          [3660, "All"],
        ].map(([d, label]) => (
          <button
            key={d}
            className={"chip" + (daysActive(d as number) ? " active" : "")}
            onClick={() => pickDays(d as number)}
          >
            {label}
          </button>
        ))}
        <span className="mut">·</span>
        <input type="date" id="from" value={fromInput} onChange={(e) => setFromInput(e.target.value)} />{" "}
        <span className="mut">→</span>{" "}
        <input type="date" id="to" value={toInput} onChange={(e) => setToInput(e.target.value)} />
        <button className="chip" id="apply" onClick={apply}>
          Apply
        </button>
        <span className="mut" id="range">
          {range}
        </span>
      </div>

      <div className="kpis">
        <div className="kpi clk" onClick={() => openKpi("opens")}>
          <div className="n">{kpi(s?.opens)}</div>
          <div className="l">report opens</div>
        </div>
        <div className="kpi clk" onClick={() => openKpi("personas")}>
          <div className="n">{kpi(s?.unique_personas)}</div>
          <div className="l">unique personas</div>
        </div>
        <div className="kpi clk" onClick={() => openKpi("widgets")}>
          <div className="n">{kpi(s?.by_widget?.length)}</div>
          <div className="l">widgets viewed</div>
        </div>
        <div className="kpi clk" onClick={() => openKpi("tabs")}>
          <div className="n">{kpi(s?.by_tab?.length)}</div>
          <div className="l">tabs opened</div>
        </div>
      </div>

      <p className="mut" style={{ fontSize: "12px", margin: "2px 0 0" }}>
        Tip: click any row to see who — or, for a person, what they viewed.
      </p>
      <div className="grid2">
        <div>
          <h2>Widgets by views</h2>
          <div>
            {s ? (
              <Table
                rows={s.by_widget}
                cols={[{ h: "Widget", k: "target" }, { h: "Views", k: "views", n: true }, { h: "People", k: "unique_viewers", n: true }]}
                by="widget"
                onRow={openDetail}
              />
            ) : null}
          </div>
        </div>
        <div>
          <h2>Tabs by views</h2>
          <div>
            {s ? (
              <Table
                rows={s.by_tab}
                cols={[{ h: "Tab", k: "target" }, { h: "Views", k: "views", n: true }, { h: "People", k: "unique_viewers", n: true }]}
                by="tab"
                onRow={openDetail}
              />
            ) : null}
          </div>
        </div>
      </div>
      <h2>Drill-downs by opens</h2>
      <div>
        {s ? (
          <Table
            rows={s.by_drill || []}
            cols={[{ h: "Drill-down", k: "target" }, { h: "Opens", k: "views", n: true }, { h: "People", k: "unique_viewers", n: true }]}
            by="drill"
            onRow={openDetail}
          />
        ) : null}
      </div>

      <h2>Metrics assistant</h2>
      <p className="sub" style={{ margin: "0 0 8px" }}>
        Adoption of the in-report chat. Opens = panel opened; questions = messages sent; each question
        is tagged with the report view it was asked from.
      </p>
      <div className="kpis" style={{ gridTemplateColumns: "repeat(6,minmax(0,1fr))" }}>
        <div className="kpi">
          <div className="n">{kpi(s?.chat_opens ?? 0)}</div>
          <div className="l">assistant opens</div>
        </div>
        <div className="kpi clk" onClick={() => openDetail("chatlog", "")}>
          <div className="n">{kpi(s?.chat_msgs ?? 0)}</div>
          <div className="l">questions asked</div>
        </div>
        <div className="kpi">
          <div className="n">{kpi(s?.chat_users ?? 0)}</div>
          <div className="l">unique askers</div>
        </div>
        <div className="kpi">
          <div className="n">{kpi(fmtNum(s?.chat_tokens ?? 0))}</div>
          <div className="l">tokens used</div>
        </div>
        <div className="kpi">
          <div className="n">{kpi((s?.chat_cache_hit_pct ?? 0) + "%")}</div>
          <div className="l">cache hit</div>
        </div>
        <div className="kpi">
          <div className="n">{kpi(fmtCost(s?.chat_cost_usd))}</div>
          <div className="l">est. cost</div>
        </div>
      </div>
      <div style={{ marginTop: "12px" }}>
        {s ? (
          <Table
            rows={(s.by_chat_view || []).map((r: any) =>
              Object.assign({}, r, { tokens_f: fmtNum(r.tokens || 0), cost_f: fmtCost(r.cost) }),
            )}
            cols={[
              { h: "Asked from view", k: "target" },
              { h: "Questions", k: "views", n: true },
              { h: "People", k: "unique_viewers", n: true },
              { h: "Tokens", k: "tokens_f", n: true },
              { h: "Cost", k: "cost_f", n: true },
            ]}
            by="chat"
            onRow={openDetail}
          />
        ) : null}
      </div>
      <h3 style={{ fontSize: "13px", margin: "18px 0 6px" }}>Tools called</h3>
      <p className="sub" style={{ margin: "0 0 8px" }}>
        Which read-only tools the assistant invoked. Click a row to see recent calls and their
        arguments — for <code>sql_query</code> the argument is the SQL itself, so recurring queries flag
        which raw SQL deserves its own tool.
      </p>
      <div>
        {s ? (
          <Table
            rows={s.by_chat_tool || []}
            cols={[
              { h: "Tool", k: "tool_name" },
              { h: "Calls", k: "calls", n: true },
              { h: "Callers", k: "unique_callers", n: true },
              { h: "Errors", k: "errors", n: true },
            ]}
            by="tool"
            onRow={openDetail}
          />
        ) : null}
      </div>

      <h2>People</h2>
      <div>
        {s ? (
          <Table
            rows={s.by_persona}
            cols={[
              { h: "Person", k: "login" },
              { h: "Opens", k: "opens", n: true },
              { h: "Widgets seen", k: "widgets_seen", n: true },
              { h: "Asked", k: "chat_msgs", n: true },
            ]}
            by="persona"
            onRow={openDetail}
          />
        ) : null}
      </div>

      <div className="dov" hidden={!modal} onClick={() => setModal(null)}>
        <div className="dbox" onClick={(e) => e.stopPropagation()}>
          <div className="dhead">
            <b>{modal?.title ?? ""}</b>
            <button type="button" aria-label="Close" onClick={() => setModal(null)}>
              ×
            </button>
          </div>
          <div className="dbody">{modal?.body}</div>
        </div>
      </div>
    </>
  );
}

function ChatReqTable({ rows }: { rows: Row[] | undefined }) {
  const fr = (rows || []).map((r: any) =>
    Object.assign({}, r, { ts_f: tsMin(r.ts), tokens_f: fmtNum(r.tokens || 0), cost_f: fmtCost(r.cost) }),
  );
  return (
    <Table
      rows={fr}
      cols={[
        { h: "When", k: "ts_f" },
        { h: "Who", k: "who" },
        { h: "View", k: "view" },
        { h: "Tokens", k: "tokens_f", n: true },
        { h: "Cost", k: "cost_f", n: true },
      ]}
    />
  );
}
