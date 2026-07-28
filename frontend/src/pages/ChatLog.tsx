// /chat-log — assistant conversation viewer (URL-only, not linked in the sidebar),
// migrated to React (Manage migration). Reproduces server.chat_log_page()'s markup
// + classes (see ../styles/chatlog.css) and ports the inline JS 1:1: period chips +
// custom date range, the session list (GET /api/chat-sessions), and the transcript
// panel with per-turn tool calls (GET /api/chat-session).
//
// SSR-safe: no top-level window/document access — only in effects / handlers.
import { useEffect, useRef, useState } from "react";

type Session = { session_id: string; who: string; last: string; questions: number; tokens: number; cost: number | null };
type Detail = { messages?: any[]; tools?: Record<string, any[]> };

const api = (p: string) => location.origin + p;
const fmtNum = (n: number) => (n || 0).toLocaleString("en-US");
const fmtCost = (c: number | null) => (c == null ? "n/a" : "$" + Number(c).toFixed(4));
const when = (ts: string) => (ts || "").replace("T", " ").slice(0, 16);

export default function ChatLog() {
  const [days, setDays] = useState(30);
  const [applied, setApplied] = useState<{ from: string; to: string } | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [listErr, setListErr] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | "loading" | "error" | null>(null);
  const reqSeq = useRef(0);

  const rangeQ = () => (applied ? `from=${applied.from}&to=${applied.to}` : `days=${days}`);

  useEffect(() => {
    let cancelled = false;
    setListErr(false);
    fetch(api("/api/chat-sessions?" + rangeQ()))
      .then((r) => r.json())
      .then((s) => {
        if (cancelled) return;
        setSessions(s.sessions || []);
      })
      .catch(() => {
        if (!cancelled) setListErr(true);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days, applied]);

  function openSession(id: string) {
    setActiveId(id);
    setDetail("loading");
    const seq = ++reqSeq.current;
    fetch(api("/api/chat-session?id=" + encodeURIComponent(id)))
      .then((r) => r.json())
      .then((s) => {
        if (seq !== reqSeq.current) return;
        setDetail(s);
      })
      .catch(() => {
        if (seq !== reqSeq.current) return;
        setDetail("error");
      });
  }

  function toolLine(t: any, i: number) {
    let a: any = {};
    try {
      a = JSON.parse(t.args || "{}");
    } catch {
      /* keep {} */
    }
    const argStr =
      a && a.sql != null ? String(a.sql) : Object.keys(a || {}).map((k) => k + "=" + a[k]).join(", ");
    return (
      <div className={"cl-tool " + (t.ok ? "ok" : "err")} key={i}>
        <span className="tn">{t.tool_name}</span>
        {t.ok ? "" : <span className="mut"> (error)</span>}
        {argStr ? <code>{argStr}</code> : null}
      </div>
    );
  }

  function panelBody() {
    if (detail == null) return <div className="empty">Pick a conversation on the left.</div>;
    if (detail === "loading") return <div className="empty">Loading…</div>;
    if (detail === "error") return <div className="empty">Failed to load.</div>;
    const tools = detail.tools || {};
    const msgs = detail.messages || [];
    if (!msgs.length) return <div className="empty">Empty conversation.</div>;
    return (
      <>
        {msgs.map((m: any, i: number) => {
          if (m.role === "user") {
            return (
              <div key={i}>
                <div className="cl-meta" style={{ textAlign: "right" }}>
                  {when(m.ts)}
                  {m.view ? " · " + m.view : ""}
                  {m.period ? " · " + m.period : ""}
                </div>
                <div className="cl-msg user">{m.text}</div>
              </div>
            );
          }
          const tl = tools[m.id] || [];
          return (
            <div key={i}>
              <div className="cl-msg bot">{m.text}</div>
              {tl.length ? <div className="cl-tools">{tl.map(toolLine)}</div> : null}
              <div className="cl-meta">
                {fmtNum((m.tokens_in || 0) + (m.tokens_out || 0))} tok · {fmtCost(m.cost_usd)}
              </div>
            </div>
          );
        })}
      </>
    );
  }

  const dayChips = [7, 30, 90, 365, 3660];
  const dayLabel: Record<number, string> = { 7: "7d", 30: "30d", 90: "90d", 365: "1y", 3660: "All" };

  return (
    <>
      <h1>Assistant conversations</h1>
      <p className="sub">
        Stored transcripts of the metrics assistant — each question, the answer, and the tools the assistant called. Not
        linked in the sidebar; this page is reachable by URL.
      </p>
      <div className="chips" id="chips">
        {dayChips.map((d) => (
          <button
            key={d}
            className={"chip" + (!applied && days === d ? " active" : "")}
            onClick={() => {
              setApplied(null);
              setFrom("");
              setTo("");
              setDays(d);
            }}
          >
            {dayLabel[d]}
          </button>
        ))}
        <span className="mut">·</span>
        <input type="date" id="from" value={from} onChange={(e) => setFrom(e.target.value)} />{" "}
        <span className="mut">→</span> <input type="date" id="to" value={to} onChange={(e) => setTo(e.target.value)} />
        <button
          className="chip"
          id="apply"
          onClick={() => {
            if (from && to) setApplied({ from, to });
          }}
        >
          Apply
        </button>
        <span className="mut" id="range">
          {listErr ? "failed to load" : ""}
        </span>
      </div>
      <div className="cl-grid">
        <div className="cl-list" id="list">
          {sessions == null ? null : !sessions.length ? (
            <div className="empty">No conversations in this period yet.</div>
          ) : (
            sessions.map((r) => (
              <button
                className={"cl-item" + (activeId === r.session_id ? " active" : "")}
                key={r.session_id}
                onClick={() => openSession(r.session_id)}
              >
                <div className="who">{r.who}</div>
                <div className="meta">
                  {when(r.last)} · {r.questions} Q · {fmtNum(r.tokens)} tok · {fmtCost(r.cost)}
                </div>
              </button>
            ))
          )}
        </div>
        <div className="cl-panel" id="panel">
          {panelBody()}
        </div>
      </div>
    </>
  );
}
