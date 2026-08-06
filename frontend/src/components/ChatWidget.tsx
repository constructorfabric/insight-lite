// Metrics-assistant floating chat (#mx-fab + #mx-panel) — a React component port
// of shell.CHAT_WIDGET_JS. Rendered by report views via ReportChrome (report_chrome
// pages) into a body-level portal, so — like the vanilla original appended at
// <body> level — it sits above the page and never lands in the emailed copy. CSS
// lives in styles/report.css (#mx-fab/#mx-panel/.mx-*), already loaded by the route
// entry, so classes/markup are reproduced 1:1 for pixel parity (fab closed by
// default). Streams POST /api/chat (SSE); every number comes from the server tools.
//
// SSR-safe: window/document only in effects/handlers.
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

// ---- XSS-safe minimal markdown (verbatim from the vanilla widget) ----
function mxEsc(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
// Formulas. The panel renders a small markdown subset and no math, but a model asked how a
// metric is computed reaches for LaTeX anyway, and the answer then arrives as source:
// "$$\\text{flow\\_friction\\_per\\_item} = \\frac{2 \\times (...)}{...}$$". The system
// instruction now asks for plain text, and this is the fallback for when it does it regardless
// — not a renderer, just enough to make an accidental formula readable: the wrappers go, the
// three commands that actually turn up become their plain equivalents, and the result is shown
// as code so it reads as a formula rather than as mangled prose.
// Attribute-safe: mxEsc handles & < > but not quotes, and the TeX travels in an attribute.
function mxAttr(s: string) {
  return mxEsc(s).replace(/"/g, "&quot;");
}

// Math is pulled out BEFORE escaping and put back afterwards as a placeholder carrying the
// TeX in an attribute, with the plain-text rendering as its content. Three things fall out of
// that order: the escaper still sees every character of prose, the placeholder survives
// markdown untouched, and a reader without the math chunk — it failed to load, or the formula
// is malformed — is left with a readable line rather than a blank.
export function mxExtractMath(raw: string): [string, { tex: string; display: boolean }[]] {
  const found: { tex: string; display: boolean }[] = [];
  const take = (tex: string, display: boolean) => {
    found.push({ tex: tex.trim(), display });
    return `\u0000MATH${found.length - 1}\u0000`;      // no HTML-special characters
  };
  let s = raw;
  s = s.replace(/\$\$([\s\S]+?)\$\$/g, (_m, b) => take(b, true));
  s = s.replace(/\\\[([\s\S]+?)\\\]/g, (_m, b) => take(b, true));
  s = s.replace(/\\\(([\s\S]+?)\\\)/g, (_m, b) => take(b, false));
  // Single-dollar inline math, but only when it looks like math rather than money: a lone
  // "$0.0021 per turn" must not become a formula, so both delimiters have to be adjacent to
  // non-space and the body must not start with a digit followed by a decimal point.
  s = s.replace(/\$(?!\s)([^$\n]{1,200}?)(?<!\s)\$/g, (m, b) =>
    /^\d+[.,]\d/.test(b) ? m : take(b, false));
  return [s, found];
}

export function mxDelatex(s: string) {
  const plain = (body: string) =>
    body
      .replace(/\\text\{([^{}]*)\}/g, "$1")
      .replace(/\\mathrm\{([^{}]*)\}/g, "$1")
      .replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, "($1) / ($2)")
      .replace(/\\times/g, "\u00d7")
      .replace(/\\cdot/g, "\u00b7")
      .replace(/\\[,;!]/g, " ")
      .replace(/\\_/g, "_")
      .replace(/\s+/g, " ")
      .trim();
  s = s.replace(/\$\$([\s\S]+?)\$\$/g, (_m, b) => "`" + plain(b) + "`");
  s = s.replace(/\\\[([\s\S]+?)\\\]/g, (_m, b) => "`" + plain(b) + "`");
  s = s.replace(/\\\(([\s\S]+?)\\\)/g, (_m, b) => "`" + plain(b) + "`");
  return s;
}

function mxInline(s: string) {
  s = s.replace(/`([^`]+)`/g, (_m, c) => "<code>" + c + "</code>");
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s"'<>]+|\/[^)\s"'<>]*)\)/g, (_m, t, u) => '<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + t + "</a>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  // Only at a word boundary. Without that, flow_friction_per_item came out as
  // flow<em>friction</em>per_item — every snake_case identifier in an answer about a metric
  // was mangled, which is most of them. CommonMark treats an intra-word underscore as
  // literal for exactly this reason.
  s = s.replace(/(^|[^\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>");
  return s;
}
const mxIsSep = (s: string) => /^[\s|:-]+$/.test(s) && s.indexOf("|") >= 0 && s.indexOf("-") >= 0;
const mxCells = (s: string) => s.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => mxInline(c.trim()));
export function mxMarkdown(raw: string): string {
  const [stripped, maths] = mxExtractMath(raw);
  const lines = mxEsc(stripped).split("\n");
  let html = "",
    list: string | null = null,
    para: string[] = [];
  const flushPara = () => {
    if (para.length) {
      html += "<p>" + para.join("<br>") + "</p>";
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      html += "</" + list + ">";
      list = null;
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    if (ln.indexOf("|") >= 0 && i + 1 < lines.length && mxIsSep(lines[i + 1])) {
      flushPara();
      flushList();
      const head = mxCells(ln),
        rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].indexOf("|") >= 0 && lines[i].trim() !== "") {
        rows.push(mxCells(lines[i]));
        i++;
      }
      i--;
      html +=
        "<table><thead><tr>" +
        head.map((h) => "<th>" + h + "</th>").join("") +
        "</tr></thead><tbody>" +
        rows.map((r) => "<tr>" + r.map((c) => "<td>" + c + "</td>").join("") + "</tr>").join("") +
        "</tbody></table>";
      continue;
    }
    const ul = ln.match(/^\s*[-*]\s+(.*)$/),
      ol = ln.match(/^\s*\d+\.\s+(.*)$/),
      hd = ln.match(/^\s*#{1,6}\s+(.*)$/);
    if (ul) {
      flushPara();
      if (list !== "ul") {
        flushList();
        html += "<ul>";
        list = "ul";
      }
      html += "<li>" + mxInline(ul[1]) + "</li>";
    } else if (ol) {
      flushPara();
      if (list !== "ol") {
        flushList();
        html += "<ol>";
        list = "ol";
      }
      html += "<li>" + mxInline(ol[1]) + "</li>";
    } else if (hd) {
      flushPara();
      flushList();
      html += "<p><strong>" + mxInline(hd[1]) + "</strong></p>";
    } else if (ln.trim() === "") {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(mxInline(ln));
    }
  }
  flushPara();
  flushList();
  // Placeholders back to markup, last: the content is the plain-text reading (already
  // escaped by mxDelatex's output going through mxEsc below) and the attribute carries the
  // TeX for the lazy typesetter to pick up after mount.
  return html.replace(/\u0000MATH(\d+)\u0000/g, (_m, i) => {
    const m = maths[Number(i)];
    if (!m) return "";
    const plain = mxEsc(mxDelatex(m.display ? `$$${m.tex}$$` : `\\(${m.tex}\\)`)
      .replace(/^`|`$/g, ""));
    return `<span class="mx-math${m.display ? " mx-math-block" : ""}" `
      + `data-tex="${mxAttr(m.tex)}"${m.display ? ' data-display="1"' : ""}>`
      + `<code>${plain}</code></span>`;
  });
}

// ---- report-context helpers (verbatim) ----
function curView() {
  // The hash is where the monolith kept its deep-links (#person, #delivery). The React
  // routes have none, so this returned null on every page and the assistant was told
  // nothing about where the question came from — which is how it ended up applying an
  // element scope to a question asked on the Person page, a page that ignores scope.
  const hash = (location.hash || "").replace(/^#/, "");
  if (hash) return hash;
  const path = (location.pathname || "").replace(/^\/+|\/+$/g, "") || "overview";
  const tab = new URLSearchParams(location.search).get("view");
  return tab ? `${path}/${tab}` : path;
}
function curPeriod() {
  const c = document.querySelector(".pchip.active[data-pchip]");
  if (c) return (c.textContent || "").trim();
  const cb = document.getElementById("pcustbtn");
  if (cb && cb.textContent && cb.textContent.indexOf("Custom") === -1) return cb.textContent.trim();
  return null;
}
function curScope() {
  const s = document.getElementById("global-scope") as HTMLInputElement | null;
  return s ? s.value || "" : "";
}
function usage(ev: any) {
  const w = window as any;
  if (w.__reportUsage) {
    try {
      w.__reportUsage(ev);
    } catch {
      /* best-effort */
    }
  }
}

type Msg = { cls: "user" | "bot" | "err"; text?: string; html?: string } | { cls: "tool"; tools: string[] };

export default function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [max, setMax] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  const history = useRef<{ role: string; text: string }[]>([]);
  const convId = useRef<string>(
    (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2),
  );
  const bodyRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    // scroll to newest on any message change / expand
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [msgs, max]);

  // Typeset any formula that has arrived, fetching the math module the first time one does —
  // so a conversation without formulas never pays for it. Each placeholder is marked done so
  // a re-render (every streamed chunk causes one) does not re-render the same MathML, and a
  // formula Temml rejects keeps the plain-text reading that is already inside it.
  useEffect(() => {
    const root = bodyRef.current;
    if (!root) return;
    const todo = Array.from(
      root.querySelectorAll<HTMLElement>(".mx-math[data-tex]:not([data-typeset])"));
    if (!todo.length) return;
    let cancelled = false;
    import("./mathRender")
      .then(async ({ renderMath }) => {
        for (const el of todo) {
          if (cancelled) return;
          const tex = el.getAttribute("data-tex") || "";
          const ok = await renderMath(el, tex, el.hasAttribute("data-display"));
          el.setAttribute("data-typeset", ok ? "1" : "failed");
        }
        if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
      })
      .catch(() => {
        // Offline, or the chunk failed to build: mark them so this does not retry on every
        // keystroke. The plain-text formula stays on screen, which is the whole point of
        // putting it there rather than leaving the placeholder empty.
        for (const el of todo) el.setAttribute("data-typeset", "unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, [msgs, max]);

  function openPanel() {
    setOpen(true);
    usage({ kind: "chat_open", target: curView(), period: curPeriod() });
    setTimeout(() => taRef.current?.focus(), 50);
  }
  function closePanel() {
    setOpen(false);
  }
  function toggleMax() {
    setMax((m) => !m);
  }

  function send() {
    const ta = taRef.current;
    if (!ta) return;
    const text = ta.value.trim();
    if (!text || busy) return;
    setBusy(true);
    ta.value = "";
    ta.style.height = "auto";
    setMsgs((m) => [...m, { cls: "user", text }]);
    const view = curView(),
      period = curPeriod(),
      scope = curScope();
    const prior = history.current.slice();
    history.current.push({ role: "user", text });
    let acc = "";
    let botIdx = -1;

    const finish = () => {
      if (acc) history.current.push({ role: "assistant", text: acc });
      setBusy(false);
      taRef.current?.focus();
    };

    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: prior, view, period, scope, session_id: convId.current }),
    })
      .then((r) => {
        if (!r.ok || !r.body) throw new Error("HTTP " + r.status);
        const reader = r.body.getReader(),
          dec = new TextDecoder();
        let buf = "";
        const pump = (): Promise<void> =>
          reader.read().then((res) => {
            if (res.done) {
              finish();
              return;
            }
            buf += dec.decode(res.value, { stream: true });
            const parts = buf.split("\n\n");
            buf = parts.pop() || "";
            parts.forEach((chunk) => {
              const line = chunk.replace(/^data:\s?/, "").trim();
              if (!line) return;
              let ev: any;
              try {
                ev = JSON.parse(line);
              } catch {
                return;
              }
              if (ev.type === "tool") {
                setMsgs((m) => [...m, { cls: "tool", tools: ev.tools || [] }]);
              } else if (ev.type === "text") {
                acc += ev.text;
                const htmlStr = mxMarkdown(acc);
                setMsgs((m) => {
                  const copy = m.slice();
                  if (botIdx === -1) {
                    botIdx = copy.length;
                    copy.push({ cls: "bot", html: htmlStr });
                  } else {
                    copy[botIdx] = { cls: "bot", html: htmlStr };
                  }
                  return copy;
                });
              } else if (ev.type === "error") {
                setMsgs((m) => [...m, { cls: "err", text: ev.error || "Something went wrong." }]);
              }
            });
            return pump();
          });
        return pump();
      })
      .catch((e) => {
        setMsgs((m) => [...m, { cls: "err", text: "Could not reach the assistant (" + e.message + ")." }]);
        finish();
      });
  }

  return createPortal(
    <>
      <button
        id="mx-fab"
        title="Ask about these metrics"
        aria-label="Ask about these metrics"
        className={open ? "hide" : ""}
        onClick={openPanel}
      >
        <svg viewBox="0 0 24 24">
          <path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.5 8.5 8.5 0 0 1-3.6-.8L3 21l1.8-5.9A8.5 8.5 0 1 1 21 11.5z" />
        </svg>
      </button>
      <div id="mx-panel" className={(open ? "open" : "") + (max ? " max" : "")}>
        <div className="mx-head">
          <span className="mx-t">Metrics assistant</span>
          <span className="mx-sub">grounded in this report</span>
          <button className="mx-exp" aria-label="Expand" title={max ? "Restore" : "Expand"} onClick={toggleMax}>
            {max ? "⤡" : "⤢"}
          </button>
          <button className="mx-x" aria-label="Close" onClick={closePanel}>
            ×
          </button>
        </div>
        <div className="mx-body" ref={bodyRef}>
          {!msgs.length ? (
            <div className="mx-empty">Ask about any metric — I read the live report data to answer.</div>
          ) : (
            msgs.map((m, i) =>
              m.cls === "tool" ? (
                <div className="mx-tool" key={i}>
                  fetched
                  {m.tools.map((n, j) => (
                    <code key={j}>{String(n).replace(/[<&]/g, "")}</code>
                  ))}
                </div>
              ) : m.cls === "bot" ? (
                <div className="mx-msg bot" key={i} dangerouslySetInnerHTML={{ __html: m.html || "" }} />
              ) : (
                <div className={"mx-msg " + m.cls} key={i}>
                  {m.text}
                </div>
              ),
            )
          )}
        </div>
        <div className="mx-foot">
          <textarea
            rows={1}
            placeholder="Ask why a number moved…"
            ref={taRef}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            onInput={(e) => {
              const t = e.currentTarget;
              t.style.height = "auto";
              t.style.height = Math.min(t.scrollHeight, 120) + "px";
            }}
          />
          <button className="mx-send" disabled={busy} onClick={send}>
            Send
          </button>
        </div>
      </div>
    </>,
    document.body,
  );
}
