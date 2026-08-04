// /config — repository types, repo→type/element sorting, sources and company-
// domain rules, migrated to React (Manage migration). Fetches GET
// /api/manage/config.json and reproduces templates/editors/config.html's markup +
// classes (see ../styles/config.css). Behaviours ported 1:1 from the inline JS:
// type add/rename/delete/default + split preview, element/org/repo chips, the
// grouped repo list with search + inline type/element selects + bulk assign,
// domain rules, Save → POST /api/config (optimistic-concurrency _version).
//
// Faithful to the legacy mutable-model + explicit-render pattern: data lives in
// refs and each legacy render() call becomes a `force()` bump.
//
// SSR-safe: no top-level window/document access — only inside effects / handlers.
import { useEffect, useReducer, useRef, useState } from "react";
import { CATEGORY_SWATCHES, css, token } from "../lib/tokens";

type RepoType = { id: string; name: string; color?: string; default: boolean };
type Repo = { name: string; org: string; commits: number; type: string; element: string };
type Domain = { domain: string; company: string; source: string; edited?: boolean };

const COLORS = CATEGORY_SWATCHES;
const IGNORE = "ignore";

export default function ConfigEditor() {
  const [, setLoaded] = useState(false);
  const D = useRef<any>(null);
  const TYPES = useRef<RepoType[]>([]);
  const REPOS = useRef<Repo[]>([]);
  const ELEMENTS = useRef<string[]>([]);
  const ELEMS_EXTRA = useRef<Record<string, number>>({});
  const ORGS = useRef<string[]>([]);
  const REPOSRC = useRef<string[]>([]);
  const DOMAINS = useRef<Domain[]>([]);
  const COMPANIES = useRef<string[]>([]);
  // Company colours. CO_PINS holds only PINS — a company absent from it takes the
  // name-derived colour the server computed in CO_GEN. Keeping them apart is what
  // lets the UI say "generated" instead of showing every company as a deliberate choice.
  const CO_PINS = useRef<Record<string, string>>({});
  const CO_GEN = useRef<Record<string, string>>({});
  // Entries that come from config.yaml, not the database. The overlay APPENDS to
  // extra_orgs/extra_repos and merges company domains, so these cannot be removed
  // here — the UI has to say so rather than offer an × that does nothing.
  const ORGS_FILE = useRef<Set<string>>(new Set());
  const REPOSRC_FILE = useRef<Set<string>>(new Set());
  // Policy blocks (configstore.BLOB_KEYS). Saved ONE AT A TIME through their own
  // endpoint, not through save() below: that posts whole-scope replaces, and a
  // policy must not ride along with — or be wiped by — a repo-classification edit.
  const POLICIES = useRef<Record<string, any>>({});
  const polDraft = useRef<Record<string, string>>({});
  const [polStatus, setPolStatus] = useState<Record<string, { text: string; color: string }>>({});
  const VERSION = useRef<any>(null);
  const groupBy = useRef<string>("type");
  const selected = useRef<Record<string, boolean>>({});
  const query = useRef("");
  const closed = useRef<Set<string>>(new Set());
  const changed = useRef<Set<string>>(new Set());
  const [status, setStatusState] = useState<{ text: string; color: string }>({
    text: "Edits apply to the report instantly. Adding a new org/repo queues a collection.",
    color: "var(--mut)",
  });
  const [saving, setSaving] = useState(false);
  const [, force] = useReducer((x: number) => x + 1, 0);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/manage/config.json")
      .then((r) => r.json())
      .then((d) => {
        if (cancelled || !d.ok) return;
        D.current = d;
        TYPES.current = (d.repo_types || []).map((t: any) => ({
          id: t.id,
          name: t.name || t.id,
          color: t.color,
          default: !!t.default,
        }));
        REPOS.current = (d.repos || []).map((r: any) => ({
          name: r.name,
          org: r.org,
          commits: r.commits || 0,
          type: r.classification || defId(d),
          element: r.element || "Other",
        }));
        ELEMENTS.current = (d.elements || []).slice();
        const ex: Record<string, number> = {};
        (d.elements_extra || []).forEach((e: string) => (ex[e] = 1));
        ELEMS_EXTRA.current = ex;
        ORGS.current = (d.extra_orgs || []).slice();
        REPOSRC.current = (d.extra_repos || []).slice();
        ORGS_FILE.current = new Set(d.extra_orgs_from_file || []);
        REPOSRC_FILE.current = new Set(d.extra_repos_from_file || []);
        DOMAINS.current = (d.domains || []).map((x: any) => ({ domain: x.domain, company: x.company, source: x.source }));
        COMPANIES.current = (d.companies || []).slice();
        CO_PINS.current = { ...(d.company_colors || {}) };
        CO_GEN.current = { ...(d.company_colors_generated || {}) };
        POLICIES.current = d.policies || {};
        polDraft.current = Object.fromEntries(
          Object.entries(POLICIES.current).map(([k, v]: [string, any]) => [k, v.yaml || ""]),
        );
        VERSION.current = d.version;
        setLoaded(true);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // ---- pure helpers (mirror the legacy closures) ----------------------------
  function defId(d?: any) {
    const src = d || D.current || {};
    const def = (src.repo_types || []).filter((t: any) => t.default)[0];
    return def ? def.id : ((src.repo_types || [])[0] || { id: "app" }).id;
  }
  const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
  const typeById = (id: string) => TYPES.current.filter((t) => t.id === id)[0];
  const colorOf = (id: string) => {
    if (id === IGNORE) return "var(--c-other)";
    const t = typeById(id);
    return t && t.color ? t.color : "var(--c-other)";
  };
  const nameOf = (id: string) => {
    if (id === IGNORE) return "Ignore";
    const t = typeById(id);
    return t ? t.name : cap(id);
  };
  const defaultType = () => {
    const d = TYPES.current.filter((t) => t.default)[0];
    return d ? d.id : (TYPES.current[0] || { id: "app" }).id;
  };
  const elemColor = (n: string) => {
    if (!n || n === "Other") return "var(--c-other)";
    let h = 0;
    for (let i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) >>> 0;
    return COLORS[h % COLORS.length];
  };

  // ---- mutations ------------------------------------------------------------
  function setStatus(text: string, color?: string) {
    setStatusState({ text, color: color || "var(--mut)" });
  }
  function selCount() {
    return Object.keys(selected.current).filter((k) => selected.current[k]).length;
  }
  function applyBulk(field: "type" | "element", val: string) {
    Object.keys(selected.current).forEach((name) => {
      if (!selected.current[name]) return;
      const r = REPOS.current.filter((x) => x.name === name)[0];
      if (r) (r as any)[field] = val;
    });
    selected.current = {};
    force();
  }
  function addType() {
    const n = (window.prompt("New repository type (e.g. SDK, Docs, Infra):") || "").trim();
    if (!n) return;
    const id = n.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "type" + TYPES.current.length;
    if (typeById(id)) {
      window.alert("A type with that name already exists.");
      return;
    }
    TYPES.current.push({ id, name: n, color: COLORS[TYPES.current.length % COLORS.length], default: false });
    force();
  }
  function delType(id: string) {
    if (TYPES.current.length <= 1) {
      window.alert("Keep at least one type.");
      return;
    }
    const t = typeById(id);
    if (t && t.default) {
      window.alert("Pick another default type first.");
      return;
    }
    const fb = defaultType();
    REPOS.current.forEach((r) => {
      if (r.type === id) r.type = fb;
    });
    TYPES.current = TYPES.current.filter((x) => x.id !== id);
    force();
  }
  function savePolicy(key: string) {
    setPolStatus((s) => ({ ...s, [key]: { text: "saving…", color: "var(--mut)" } }));
    fetch("/api/config/policy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, yaml: polDraft.current[key] ?? "" }),
    })
      .then((r) => r.json().then((j: any) => ({ s: r.status, j })))
      .then((res) => {
        if (!res.j.ok) {
          // The server's message names the problem (bad YAML, wrong shape) — show it
          // verbatim rather than a generic "save failed", since the whole point of a
          // YAML field is that you can be told what you typed wrong.
          setPolStatus((s) => ({ ...s, [key]: { text: res.j.error || "save failed", color: "var(--bad)" } }));
          return;
        }
        POLICIES.current[key] = { ...POLICIES.current[key], overridden: res.j.overridden, yaml: res.j.yaml };
        polDraft.current[key] = res.j.yaml || "";
        setPolStatus((s) => ({
          ...s,
          [key]: res.j.overridden
            ? { text: "saved ✓ — this deployment now owns it", color: "var(--good)" }
            : { text: "cleared ✓ — back to the config.yaml default", color: "var(--good)" },
        }));
        force();
      })
      .catch(() => setPolStatus((s) => ({ ...s, [key]: { text: "save failed", color: "var(--bad)" } })));
  }

  function save() {
    setSaving(true);
    setStatus("saving...", "var(--mut)");
    const repo_class: Record<string, string> = {},
      repo_element: Record<string, string> = {};
    REPOS.current.forEach((r) => {
      repo_class[r.name] = r.type;
      repo_element[r.name] = r.element;
    });
    const company_domains: Record<string, string> = {};
    DOMAINS.current.forEach((d) => (company_domains[d.domain] = d.company));
    const payload = {
      repo_class,
      repo_element,
      repo_types: TYPES.current,
      elements_extra: Object.keys(ELEMS_EXTRA.current),
      extra_orgs: ORGS.current,
      extra_repos: REPOSRC.current,
      company_domains,
      company_colors: CO_PINS.current,
      _version: VERSION.current,
    };
    fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then((r) => r.json().then((j: any) => ({ s: r.status, j })))
      .then((res) => {
        setSaving(false);
        if (res.s === 409) {
          setStatus("another tab saved — reload the page", "var(--warn)");
          return;
        }
        if (!res.j.ok) {
          setStatus(res.j.error || "save failed", "var(--bad)");
          return;
        }
        VERSION.current = res.j.version;
        setStatus("saved ✓ — report updated" + (res.j.queued ? " · collection queued" : ""), "var(--good)");
      })
      .catch(() => {
        setSaving(false);
        setStatus("save failed", "var(--bad)");
      });
  }

  // ---- render pieces --------------------------------------------------------
  function typesNode() {
    return (
      <>
        {TYPES.current.map((t) => {
          const n = REPOS.current.filter((r) => r.type === t.id).length;
          return (
            <div className="tcard" data-type={t.id} key={t.id}>
              <span className="sw" style={{ background: t.color || css("swatch-empty") }}></span>
              <span>
                <span className="tn">{t.name}</span>
                {t.default ? <span className="df">default</span> : null}
                <span className="tc">{n} repos</span>
              </span>
              <span className="acts">
                {t.default ? null : (
                  <button
                    className="iconbtn"
                    title="Set as the default type (fallback for unlisted repos)"
                    onClick={() => {
                      TYPES.current.forEach((x) => (x.default = x.id === t.id));
                      force();
                    }}
                  >
                    &#9734;
                  </button>
                )}
                <button
                  className="iconbtn"
                  title="Rename"
                  onClick={() => {
                    const v = (window.prompt("Rename type:", t.name) || "").trim();
                    if (v) {
                      t.name = v;
                      force();
                    }
                  }}
                >
                  &#9998;
                </button>
                <button className="iconbtn" title="Delete" onClick={() => delType(t.id)}>
                  &#10005;
                </button>
              </span>
            </div>
          );
        })}
        <div className="tcard ignore">
          <span className="sw" style={{ background: "var(--c-other)" }}></span>
          <span>
            <span className="tn">Ignore</span>
            <span className="tc">{REPOS.current.filter((r) => r.type === IGNORE).length} repos · excluded</span>
          </span>
        </div>
        <button className="tadd" id="addType" onClick={addType}>
          + Add type
        </button>
      </>
    );
  }
  function previewNode() {
    let tot = 0;
    const by: Record<string, number> = {};
    REPOS.current.forEach((r) => {
      if (r.type === IGNORE) return;
      by[r.type] = (by[r.type] || 0) + r.commits;
      tot += r.commits;
    });
    return {
      bar: TYPES.current.map((t) => {
        const v = by[t.id] || 0;
        return v ? (
          <i key={t.id} style={{ width: ((100 * v) / tot).toFixed(1) + "%", background: t.color || css("swatch-empty") }} title={t.name}></i>
        ) : null;
      }),
      leg: TYPES.current.map((t) => {
        const v = by[t.id] || 0;
        if (!v) return null;
        return (
          <span key={t.id}>
            <span className="sw" style={{ background: t.color || css("swatch-empty") }}></span>
            {t.name} <b>{Math.round((100 * v) / tot)}%</b> · {v.toLocaleString()}
          </span>
        );
      }),
    };
  }
  function typeOptions() {
    return (
      <>
        {TYPES.current.map((t) => (
          <option value={t.id} key={t.id}>
            {t.name}
          </option>
        ))}
        <option value={IGNORE}>Ignore</option>
      </>
    );
  }
  function elemOptions() {
    return ELEMENTS.current.map((e) => (
      <option key={e} value={e}>
        {e}
      </option>
    ));
  }
  function repoRow(r: Repo) {
    return (
      <div className={"rrow" + (changed.current.has(r.name) ? " changed" : "")} data-repo={r.name} key={r.name}>
        <input
          type="checkbox"
          className="cb"
          checked={!!selected.current[r.name]}
          onChange={(e) => {
            selected.current[r.name] = e.target.checked;
            force();
          }}
        />
        <span className="rn mono" title={r.name}>
          {r.name}
          {r.org ? (
            <>
              {" "}
              <small>{r.org}</small>
            </>
          ) : null}
        </span>
        <span className="rc num">{r.commits.toLocaleString()}</span>
        <span className="pillsel">
          <span className="dot" style={{ background: colorOf(r.type) }}></span>
          <select
            data-field="type"
            value={r.type}
            onChange={(e) => {
              r.type = e.target.value;
              changed.current.add(r.name);
              force();
            }}
          >
            {typeOptions()}
          </select>
        </span>
        <span className="pillsel el">
          <span className="dot" style={{ background: elemColor(r.element) }}></span>
          <select
            data-field="element"
            value={ELEMENTS.current.includes(r.element) ? r.element : r.element}
            onChange={(e) => {
              r.element = e.target.value;
              changed.current.add(r.name);
              force();
            }}
          >
            {ELEMENTS.current.includes(r.element) ? null : <option value={r.element}>{r.element}</option>}
            {elemOptions()}
          </select>
        </span>
      </div>
    );
  }
  function repoListNode() {
    const q = (query.current || "").toLowerCase();
    const list = REPOS.current.filter(
      (r) =>
        !q ||
        r.name.toLowerCase().indexOf(q) >= 0 ||
        (r.element || "").toLowerCase().indexOf(q) >= 0 ||
        (r.org || "").toLowerCase().indexOf(q) >= 0,
    );
    if (groupBy.current === "none") {
      return (
        <div className="grp">
          <div className="rows">{list.map(repoRow)}</div>
        </div>
      );
    }
    const groups: Record<string, Repo[]> = {},
      order: string[] = [];
    if (groupBy.current === "type") {
      TYPES.current.forEach((t) => {
        groups[t.id] = [];
        order.push(t.id);
      });
      groups[IGNORE] = [];
      order.push(IGNORE);
    }
    list.forEach((r) => {
      const k = groupBy.current === "type" ? r.type : r.element || "Other";
      if (!groups[k]) {
        groups[k] = [];
        order.push(k);
      }
      groups[k].push(r);
    });
    return (
      <>
        {order
          .filter((k) => (groups[k] || []).length)
          .map((k) => {
            const isClosed = closed.current.has(k);
            return (
              <div className={"grp" + (isClosed ? " closed" : "")} key={k}>
                <div
                  className="grp-h"
                  data-grp={k}
                  onClick={() => {
                    if (closed.current.has(k)) closed.current.delete(k);
                    else closed.current.add(k);
                    force();
                  }}
                >
                  <span className="sw" style={{ background: groupBy.current === "type" ? colorOf(k) : elemColor(k) }}></span>
                  <b>{groupBy.current === "type" ? nameOf(k) : k}</b>
                  <span className="gc">{groups[k].length}</span>
                  <span className="chev">&#9662;</span>
                </div>
                <div className="rows">{groups[k].map(repoRow)}</div>
              </div>
            );
          })}
      </>
    );
  }
  function elemChipsNode() {
    return (
      <>
        {ELEMENTS.current.map((e) => {
          const n = REPOS.current.filter((r) => r.element === e).length;
          return (
            <span className="chip" data-kind="elem" data-val={e} key={e}>
              <span className="cdot" style={{ background: elemColor(e) }}></span>
              {e} <span className="n">{n}</span>{" "}
              <span
                className="x"
                onClick={() => {
                  ELEMENTS.current = ELEMENTS.current.filter((x) => x !== e);
                  delete ELEMS_EXTRA.current[e];
                  force();
                }}
              >
                &#10005;
              </span>
            </span>
          );
        })}
        <span
          className="chip add"
          data-kind="elem"
          onClick={() => {
            const v = (window.prompt("New element name:") || "").trim();
            if (!v) return;
            if (ELEMENTS.current.indexOf(v) < 0) {
              ELEMENTS.current.push(v);
              ELEMS_EXTRA.current[v] = 1;
            }
            force();
          }}
        >
          + Element
        </span>
      </>
    );
  }
  function srcChipsNode(
    kind: "org" | "src",
    listRef: React.MutableRefObject<string[]>,
    prompt: string,
    label: string,
    fromFile?: React.MutableRefObject<Set<string>>,
  ) {
    return (
      <>
        {listRef.current.map((o) => {
          // A file-listed entry has no × on purpose: this list is appended to, never
          // replaced, so removing it here would look like it worked and come back on
          // the next render. Say where it lives instead.
          const locked = !!fromFile?.current.has(o);
          return (
            <span
              className="chip"
              data-kind={kind}
              data-val={o}
              key={o}
              title={locked ? "Listed in config.yaml — remove it there, not here" : undefined}
              style={locked ? { opacity: 0.75 } : undefined}
            >
              {o}{" "}
              {locked ? (
                <span className="tag" style={{ marginLeft: 2 }}>config.yaml</span>
              ) : (
                <span
                  className="x"
                  onClick={() => {
                    listRef.current = listRef.current.filter((x) => x !== o);
                    force();
                  }}
                >
                  &#10005;
                </span>
              )}
            </span>
          );
        })}
        <span
          className="chip add"
          data-kind={kind}
          onClick={() => {
            const v = (window.prompt(prompt) || "").trim();
            if (!v) return;
            if (listRef.current.indexOf(v) < 0) listRef.current.push(v);
            force();
          }}
        >
          {label}
        </span>
      </>
    );
  }
  function domRowsNode() {
    return DOMAINS.current.map((d, i) => {
      const inList = COMPANIES.current.indexOf(d.company) >= 0;
      return (
        <tr data-dom={i} key={i}>
          <td className="mono">
            {d.domain}{" "}
            {d.source === "override" ? (
              <span className="tag">database</span>
            ) : (
              <span className="tag" title="Listed in config.yaml — you can change its company here, but removing the row only takes effect until the next render">
                config.yaml
              </span>
            )}
          </td>
          <td>
            <select
              data-domsel={i}
              value={d.company}
              onChange={(e) => {
                DOMAINS.current[i].company = e.target.value;
                DOMAINS.current[i].edited = true;
                force();
              }}
            >
              {inList ? null : <option value={d.company}>{d.company}</option>}
              {COMPANIES.current.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </td>
          <td>
            <button
              className="iconbtn"
              title="Remove"
              onClick={() => {
                DOMAINS.current.splice(i, 1);
                force();
              }}
            >
              &#10005;
            </button>
          </td>
        </tr>
      );
    });
  }

  const prev = previewNode();
  const n = selCount();

  return (
    <>
      <div className="savebar">
        <button className="btn primary" id="save" disabled={saving} onClick={save}>
          Save &amp; apply
        </button>
        <span className="status" id="status" style={{ color: status.color }}>
          {status.text}
        </span>
      </div>
      <h1>Configuration</h1>
      <p className="lead">
        Define your repository types, sort repos into them and into product elements, and connect new orgs or
        repositories. Types drive the “where effort goes” split across the report.
      </p>

      <div className="cols">
        <div className="card">
          <p className="eyebrow">Repository types</p>
          <h2>Repository types</h2>
          <p className="h-sub">
            Add your own — each becomes a colour in the report's split. The <b>default</b> type is the fallback for
            unlisted repos; “ignore” drops a repo from all metrics.
          </p>
          <p className="h-sub">
            <b>Replaces</b> the set in <code>config.yaml</code> as soon as you save one — the file's
            types stop being consulted, so a type you delete here is really gone.
          </p>
          <div className="types" id="types">
            {typesNode()}
          </div>
          <div className="prev">
            <div className="pl">Report split preview — commits by type</div>
            <div className="splitbar" id="splitbar">
              {prev.bar}
            </div>
            <div className="splitleg" id="splitleg">
              {prev.leg}
            </div>
          </div>
        </div>
        <div className="card">
          <p className="eyebrow">Product elements</p>
          <h2>Elements</h2>
          <p className="h-sub">The other axis each repo is sorted into.</p>
          <div className="chips" id="elemChips">
            {elemChipsNode()}
          </div>
        </div>
      </div>

      <div className="card">
        <p className="eyebrow">Repositories</p>
        <h2>Sort repos into types &amp; elements</h2>
        <p className="h-sub">
          <span id="repoCount">{REPOS.current.length} repositories</span> · select rows to bulk-assign, or set each
          inline.
        </p>
        <p className="h-sub">
          <b>Overrides per repository.</b> A repo you move is pinned here and stops following{" "}
          <code>config.yaml</code>; every repo you leave alone keeps following it, including new ones
          the next collection finds.
        </p>
        <div className="toolbar">
          <label className="search">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              style={{ color: "var(--mut)" }}
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m21 21-4.3-4.3" />
            </svg>
            <input
              id="q"
              placeholder="Filter by repo, org or element..."
              autoComplete="off"
              onChange={(e) => {
                query.current = e.target.value;
                force();
              }}
            />
          </label>
          <span className="seg-label">Group by</span>
          <span className="seg" id="groupSeg">
            {[
              ["type", "Type"],
              ["element", "Element"],
              ["none", "None"],
            ].map(([g, lbl]) => (
              <button
                key={g}
                data-g={g}
                className={groupBy.current === g ? "on" : ""}
                onClick={() => {
                  groupBy.current = g;
                  force();
                }}
              >
                {lbl}
              </button>
            ))}
          </span>
        </div>
        <div className="rhead">
          <span className="cbsp"></span>
          <span className="rn">Repository</span>
          <span className="rc">Commits</span>
          <span className="hp">Type</span>
          <span className="hp el">Element</span>
        </div>
        <div id="repolist">{repoListNode()}</div>
        <div className={"bulk" + (n > 0 ? " on" : "")} id="bulk">
          <b id="bulkN">{n} selected</b>
          <div className="sp"></div>
          <select
            id="bulkType"
            value=""
            onChange={(e) => {
              if (e.target.value) applyBulk("type", e.target.value);
            }}
          >
            <option value="">Set type...</option>
            {typeOptions()}
          </select>
          <select
            id="bulkElem"
            value=""
            onChange={(e) => {
              if (e.target.value) applyBulk("element", e.target.value);
            }}
          >
            <option value="">Set element...</option>
            {elemOptions()}
          </select>
          <button
            className="link"
            id="bulkClear"
            onClick={() => {
              selected.current = {};
              force();
            }}
          >
            Clear
          </button>
        </div>
      </div>

      <div className="cols">
        <div className="card">
          <p className="eyebrow">Sources</p>
          <h2>Orgs &amp; extra repos</h2>
          <p className="h-sub">Add a GitHub org or a single org/repo — a collection is queued on save.</p>
          <p className="h-sub">
            <b>Adds to</b> what <code>config.yaml</code> lists. Entries marked{" "}
            <span className="tag">config.yaml</span> come from that file and can only be removed
            there — this page can add sources, not take the file's away.
          </p>
          <div className="chips" id="orgChips" style={{ marginBottom: 10 }}>
            {srcChipsNode("org", ORGS, "GitHub org:", "+ Org", ORGS_FILE)}
          </div>
          <div className="chips" id="repoSrcChips">
            {srcChipsNode("src", REPOSRC, "org/repo:", "+ Repo", REPOSRC_FILE)}
          </div>
        </div>
        <div className="card">
          <p className="eyebrow">Companies</p>
          <h2>Email-domain to company</h2>
          <p className="h-sub">Maps a contributor's email domain to a company for the by-company breakdown.</p>
          <p className="h-sub">
            <b>Adds to and edits</b> what <code>config.yaml</code> lists. You can retarget a{" "}
            <span className="tag">config.yaml</span> row to a different company, but removing it
            here does not stick — delete it from the file instead.
          </p>
          <table className="dom">
            <thead>
              <tr>
                <th>Domain</th>
                <th>Company</th>
                <th></th>
              </tr>
            </thead>
            <tbody id="domBody">{domRowsNode()}</tbody>
          </table>
          <div style={{ marginTop: 10 }}>
            <span
              className="chip add"
              id="addDom"
              onClick={() => {
                const dn = (window.prompt("Email domain (e.g. acme.com):") || "").trim().toLowerCase();
                if (dn) {
                  DOMAINS.current.push({ domain: dn, company: COMPANIES.current[0] || "Other", source: "override", edited: true });
                  force();
                }
              }}
            >
              + Domain rule
            </span>
          </div>

          <h2 style={{ marginTop: 22 }}>Chart colour</h2>
          <p className="h-sub">
            Each company's colour is derived from its <b>name</b>, so it stays the same from one
            report to the next — these charts get read week over week, and a colour that followed
            commit volume swapped two companies the moment they swapped places. Pin one here to
            override that; it is stored in the database, so it survives a deployment whose{" "}
            <code>config.yaml</code> comes from git.
          </p>
          <table className="dom">
            <thead>
              <tr>
                <th>Company</th>
                <th>Colour</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {COMPANIES.current.map((co) => {
                const pinned = CO_PINS.current[co];
                // A literal, not css(): `effective` is also the <input type="color"> value
                // below and is printed as text — neither accepts var(--x).
                const effective = pinned || CO_GEN.current[co] || token["company-empty"];
                return (
                  <tr key={co}>
                    <td>
                      <span
                        style={{
                          display: "inline-block", width: 10, height: 10, borderRadius: "50%",
                          background: effective, marginRight: 8, verticalAlign: "middle",
                        }}
                      />
                      {co}
                    </td>
                    <td>
                      <input
                        type="color"
                        aria-label={`Colour for ${co}`}
                        value={effective}
                        style={{ width: 44, height: 24, padding: 0, border: "none", background: "none" }}
                        onChange={(e) => {
                          CO_PINS.current[co] = e.target.value.toLowerCase();
                          force();
                        }}
                      />
                      <code style={{ marginLeft: 8 }}>{effective}</code>
                    </td>
                    <td>
                      {pinned ? (
                        <span
                          className="chip"
                          title="Go back to the colour derived from the name"
                          onClick={() => {
                            delete CO_PINS.current[co];
                            force();
                          }}
                        >
                          reset
                        </span>
                      ) : (
                        <span className="tag" title="Derived from the company name">
                          generated
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {COMPANIES.current.length ? null : (
                <tr>
                  <td colSpan={3} className="h-sub">
                    No companies yet — add a domain rule above and they appear here.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {/* Spans both columns of .cols: a 365px column is unusable for editing YAML,
            which is what every block in here is. */}
        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <p className="eyebrow">Policies</p>
          <h2>Detection rules &amp; exclusions</h2>
          <p className="h-sub">
            The blocks that decide what counts: which commits are AI-marked, which accounts are bots,
            what a spec is, which lines are meaningful. Edited as YAML because they are nested and
            rarely touched. Each saves on its own — <b>stored in the database</b>, so they survive a
            deployment whose <code>config.yaml</code> comes from git. Empty the field to go back to
            the file default.
          </p>
          {Object.keys(POLICIES.current).length === 0 ? (
            <p className="h-sub" style={{ opacity: 0.7 }}>No policy blocks in this config.</p>
          ) : (
            Object.entries(POLICIES.current).map(([key, p]: [string, any]) => {
              const st = polStatus[key];
              const dirty = (polDraft.current[key] ?? "") !== (p.yaml ?? "");
              return (
                <details key={key} style={{ marginTop: 12 }}>
                  <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                    {p.label}{" "}
                    <span
                      className="chip"
                      style={{
                        fontWeight: 500,
                        background: p.overridden ? "var(--good-bg)" : "var(--panel2)",
                        color: p.overridden ? "var(--good)" : "var(--mut)",
                      }}
                    >
                      {p.overridden ? "database" : "config.yaml"}
                    </span>{" "}
                    <code style={{ opacity: 0.6, fontWeight: 400 }}>{key}</code>
                  </summary>
                  <p className="h-sub" style={{ marginTop: 8 }}>{p.blurb}</p>
                  <textarea
                    /* Remount when the SERVER's value changes (a save that normalised
                       the YAML, or a reset back to the file default). Without this the
                       field is uncontrolled — React leaves defaultValue alone on
                       re-render — so "Reset to config.yaml" flipped the badge while
                       the textarea kept showing the text you had just discarded. */
                    key={`${key}:${p.yaml}`}
                    spellCheck={false}
                    defaultValue={polDraft.current[key] ?? ""}
                    onChange={(e) => {
                      polDraft.current[key] = e.currentTarget.value;
                      force();
                    }}
                    style={{
                      width: "100%", minHeight: 180, fontFamily: "'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace",
                      fontSize: 12.5, lineHeight: 1.5, padding: 10,
                      border: "1px solid var(--line2)", borderRadius: "var(--r-sm)",
                      background: "var(--panel)", color: "var(--ink)", resize: "vertical",
                    }}
                  />
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8 }}>
                    <span className="chip add" onClick={() => savePolicy(key)}>
                      {dirty ? "Save" : "Save (unchanged)"}
                    </span>
                    {p.overridden ? (
                      <span
                        className="chip"
                        style={{ cursor: "pointer" }}
                        onClick={() => {
                          polDraft.current[key] = "";
                          savePolicy(key);
                        }}
                      >
                        Reset to config.yaml
                      </span>
                    ) : null}
                    {st ? <small style={{ color: st.color }}>{st.text}</small> : null}
                  </div>
                </details>
              );
            })
          )}
        </div>
      </div>
    </>
  );
}
