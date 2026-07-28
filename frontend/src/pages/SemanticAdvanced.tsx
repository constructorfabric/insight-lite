// /semantic/advanced — the dense per-scope taxonomy grid, migrated to React
// (Manage migration). Reproduces templates/editors/semantic.html's markup +
// classes (see ../styles/semantic.css) and ports the inline JS 1:1: edit/inspect
// modes, scope switching (GET /api/semantic/scope | /effective), filter, per-item
// bucket selects with "+ custom", reset, and Save (POST /api/semantic).
//
// Faithful to the legacy mutable-model + explicit-render pattern (refs + force()).
// SSR-safe: no top-level window/document access — only in effects / handlers.
import { useEffect, useReducer, useRef, useState, type ReactElement } from "react";

type Maps = { categories: { labels: Record<string, string>; types: Record<string, string> };
  stages: { statuses: Record<string, string> }; ci: { roles: Record<string, string> } };

export default function SemanticAdvanced() {
  const [loaded, setLoaded] = useState(false);
  const D = useRef<any>(null);
  const A = useRef<Maps>({ categories: { labels: {}, types: {} }, stages: { statuses: {} }, ci: { roles: {} } });
  const E = useRef<any>(null);
  const MODE = useRef<"edit" | "inspect">("edit");
  const q = useRef("");
  const onlyU = useRef(false);
  const [st, setSt] = useState<{ text: string; color: string }>({ text: "", color: "var(--good)" });
  const [, force] = useReducer((x: number) => x + 1, 0);

  useEffect(() => {
    fetchScope("global", "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function suggestionToA(): Maps {
    const s = D.current.suggestion;
    return {
      categories: { labels: { ...s.categories.labels }, types: { ...s.categories.types } },
      stages: { statuses: { ...s.stages.statuses } },
      ci: { roles: { ...s.ci.roles } },
    };
  }
  function emptyMaps(a: Maps) {
    return (
      !Object.keys(a.categories.labels).length &&
      !Object.keys(a.categories.types).length &&
      !Object.keys(a.stages.statuses).length &&
      !Object.keys(a.ci.roles).length
    );
  }
  function loadScopeState() {
    A.current = JSON.parse(JSON.stringify(D.current.own));
    if (D.current.is_global && emptyMaps(A.current)) A.current = suggestionToA();
    force();
  }
  async function fetchScope(level: string, target: string) {
    setSt({ text: "loading…", color: "var(--mut)" });
    try {
      const r = await fetch(`/api/semantic/scope?level=${encodeURIComponent(level)}&target=${encodeURIComponent(target)}`);
      const j = await r.json();
      if (!j.ok) {
        setSt({ text: j.error || "load failed", color: "var(--warn)" });
        return;
      }
      D.current = j.data;
      setSt({ text: "", color: "var(--good)" });
      setLoaded(true);
      loadScopeState();
    } catch {
      setSt({ text: "load failed", color: "var(--warn)" });
    }
  }
  async function fetchEffective(level: string, target: string) {
    setSt({ text: "loading…", color: "var(--mut)" });
    try {
      const r = await fetch(`/api/semantic/effective?level=${encodeURIComponent(level)}&target=${encodeURIComponent(target)}`);
      const j = await r.json();
      if (!j.ok) {
        setSt({ text: j.error || "load failed", color: "var(--warn)" });
        return;
      }
      E.current = j.data;
      setSt({ text: "", color: "var(--good)" });
      force();
    } catch {
      setSt({ text: "load failed", color: "var(--warn)" });
    }
  }
  function reload(value: string) {
    const idx = value.indexOf(":");
    const level = value.slice(0, idx);
    const target = value.slice(idx + 1);
    if (MODE.current === "edit") fetchScope(level, target || "");
    else fetchEffective(level, target || "");
  }
  function setMode(m: "edit" | "inspect") {
    MODE.current = m;
    reloadCurrent();
  }
  function reloadCurrent() {
    const scope = (MODE.current === "inspect" && E.current ? E.current.scope : D.current.scope);
    reload((scope.level || "global") + ":" + (scope.target || ""));
  }

  const inh = (axis: string, mk: string, name: string) => (D.current.inherited[axis][mk] || {})[name] || "";
  function setVal(axis: string, mk: string, name: string, val: string) {
    if (val === "__new") {
      val = (window.prompt("New " + axis + " bucket:", "") || "").trim();
      if (!val) {
        force();
        return;
      }
    }
    const m = A.current as any;
    m[axis][mk] = m[axis][mk] || {};
    if (val) m[axis][mk][name] = val;
    else delete m[axis][mk][name];
    force();
  }

  function rowMatches(name: string, own: string, effective: string) {
    if (q.current && !name.toLowerCase().includes(q.current.toLowerCase())) return false;
    if (onlyU.current && !(own || !effective)) return false;
    return true;
  }
  function optionsFor(axis: string, sel: string, inherited: string) {
    const opts = (D.current.buckets[axis] as string[]).slice();
    if (sel && !opts.includes(sel)) opts.push(sel);
    const zeroLabel = D.current.is_global ? "—" : inherited ? "inherit (" + inherited + ")" : "inherit (unset)";
    return (
      <>
        <option value="">{zeroLabel}</option>
        {opts.map((b) => (
          <option key={b} value={b}>
            {b}
          </option>
        ))}
        <option value="__new">+ custom…</option>
      </>
    );
  }
  function editRow(axis: string, mk: string, name: string, count: number) {
    const own = ((A.current as any)[axis][mk] || {})[name] || "";
    const inherited = inh(axis, mk, name);
    const effective = own || inherited;
    if (!rowMatches(name, own, effective)) return null;
    let cls = "row";
    if (own) cls += " overridden";
    else if (!effective) cls += " unassigned";
    return (
      <div className={cls} key={axis + mk + name}>
        <span className="nm" title={name}>
          {name}
        </span>
        <span className="ct">{count || ""}</span>
        <select value={own} onChange={(e) => setVal(axis, mk, name, e.target.value)}>
          {optionsFor(axis, own, inherited)}
        </select>
      </div>
    );
  }
  function editList(rows: (ReactElement | null)[]) {
    const kept = rows.filter(Boolean);
    return kept.length ? kept : <div className="empty">nothing here at this scope</div>;
  }

  // ---- inspect ----
  const fromLabel = (f: string) => (f && f !== "?" ? f : "unset");
  function inspRow(it: any) {
    const un = !it.bucket,
      ov = it.from && it.from !== "global";
    if (q.current && !it.name.toLowerCase().includes(q.current.toLowerCase())) return null;
    if (onlyU.current && !(ov || !it.bucket)) return null;
    return (
      <div className="row" key={it.name}>
        <span className="nm" title={it.name}>
          {it.name}
        </span>
        <span className={"bkt" + (un ? " un" : "")}>{it.bucket || "—"}</span>
        <span className={"frm" + (ov ? " ov" : "")}>from {fromLabel(it.from)}</span>
      </div>
    );
  }
  function inspSection(title: string, groups: [string | null, any[]][]) {
    const n = groups.reduce((a, g) => a + g[1].length, 0);
    return (
      <>
        <h2>
          {title} <span className="c">{n} items</span>
        </h2>
        {groups.map(([sub, arr], gi) => (
          <div key={gi}>
            {sub ? <h3>{sub}</h3> : null}
            {arr.length ? arr.map(inspRow) : <div className="empty">none</div>}
          </div>
        ))}
      </>
    );
  }

  function scopeSelectValue() {
    const s = MODE.current === "inspect" && E.current ? E.current.scope : D.current.scope;
    return s.level === "global" ? "global::" : s.level + ":" + s.target;
  }
  function scopeOptions() {
    const t = (MODE.current === "inspect" && E.current ? E.current.targets : D.current.targets) || {};
    const groups: [string, string][] = [
      ["org", "Organizations"],
      ["element", "Elements"],
      ["repo", "Repositories"],
      ["project", "Projects"],
    ];
    return (
      <>
        <option value="global::">Global (default)</option>
        {groups.map(([lv, label]) => {
          const items: string[] = t[lv] || [];
          if (!items.length) return null;
          return (
            <optgroup label={label} key={lv}>
              {items.map((tg) => (
                <option value={lv + ":" + tg} key={tg}>
                  {label.slice(0, -1)}: {tg}
                </option>
              ))}
            </optgroup>
          );
        })}
      </>
    );
  }

  function reset() {
    if (D.current.is_global) {
      const s = D.current.suggestion;
      A.current = {
        categories: { labels: { ...s.categories.labels }, types: { ...s.categories.types } },
        stages: { statuses: { ...s.stages.statuses } },
        ci: { roles: { ...s.ci.roles } },
      };
      setSt({ text: "reset to suggestion (not saved)", color: "var(--good)" });
    } else {
      A.current = { categories: { labels: {}, types: {} }, stages: { statuses: {} }, ci: { roles: {} } };
      setSt({ text: "cleared overrides (not saved)", color: "var(--good)" });
    }
    force();
  }
  async function save() {
    setSt({ text: "saving…", color: "var(--mut)" });
    try {
      const r = await fetch("/api/semantic", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ level: D.current.scope.level, target: D.current.scope.target, assignments: A.current, _version: D.current.version }),
      });
      const j = await r.json();
      if (r.status === 409) {
        setSt({ text: "another tab saved — reload", color: "var(--warn)" });
        return;
      }
      if (!j.ok) {
        setSt({ text: j.error || "save failed", color: "var(--warn)" });
        return;
      }
      D.current.version = j.version;
      setSt({ text: "saved ✓", color: "var(--good)" });
    } catch {
      setSt({ text: "save failed", color: "var(--warn)" });
    }
  }

  if (!loaded) {
    return (
      <>
        <h1>Taxonomy — advanced editor</h1>
      </>
    );
  }

  const isEdit = MODE.current === "edit";
  const isGlobal = D.current.is_global;
  const S = D.current.scan;
  const hint = !isEdit
    ? "resolved config — the chip shows which scope set each value"
    : (MODE.current === "edit" && D.current.scope.level === "global")
      ? "the base everyone inherits"
      : "overrides on top of the inherited value — leave “inherit” to keep the parent";

  return (
    <>
      <h1>Taxonomy — advanced editor</h1>
      <p className="sub">
        Map GitHub's Issue Types, labels, board statuses and CI workflows onto the report's categories, flow stages and
        CI roles. Pick a scope — narrower scopes override individual items on top of what they inherit, so the same
        label can mean different things in different elements or repos.&nbsp;
        <a href="/semantic" style={{ color: "var(--acc)", fontWeight: 600, textDecoration: "none" }}>
          ← Guided setup
        </a>
      </p>

      <div className="scopebar">
        <span className="seg">
          <button className={isEdit ? "on" : ""} onClick={() => isEdit || setMode("edit")}>
            Edit
          </button>
          <button className={!isEdit ? "on" : ""} onClick={() => isEdit && setMode("inspect")}>
            Inspect
          </button>
        </span>
        <b>Scope</b>
        <select
          id="scope"
          style={{ minWidth: 320 }}
          value={scopeSelectValue()}
          onChange={(e) => reload(e.target.value)}
        >
          {scopeOptions()}
        </select>
        <span className="lbl" id="scopehint">
          {hint}
        </span>
      </div>

      <div className="bar">
        <input
          type="text"
          id="q"
          placeholder="filter items…"
          autoComplete="off"
          onChange={(e) => {
            q.current = e.target.value;
            force();
          }}
        />
        <label className="lbl" id="onlyUwrap" style={{ display: isEdit ? undefined : "none" }}>
          <input
            type="checkbox"
            id="onlyU"
            onChange={(e) => {
              onlyU.current = e.target.checked;
              force();
            }}
          />{" "}
          only unset / overridden
        </label>
        <button id="reset" style={{ display: isEdit ? undefined : "none" }} onClick={reset}>
          {isGlobal ? "Reset to suggestion" : "Clear overrides"}
        </button>
        <button className="primary" id="save" style={{ display: isEdit ? undefined : "none" }} onClick={save}>
          Save
        </button>
        <span className="status" id="st" style={{ color: st.color }}>
          {st.text}
        </span>
      </div>

      <div id="body">
        {isEdit ? (
          <>
            <h2>
              Categories <span className="c">{S.issue_types.length + S.labels.length} items</span>
            </h2>
            <h3>Native issue types</h3>
            {editList(S.issue_types.map((t: any) => editRow("categories", "types", t.name, t.count)))}
            <h3>Labels</h3>
            {editList(S.labels.map((l: any) => editRow("categories", "labels", l.name, l.count)))}
            <h2>
              Flow stages <span className="c">{S.statuses.length} items</span>
            </h2>
            {editList(S.statuses.map((s: any) => editRow("stages", "statuses", s.name, s.count)))}
            <h2>
              CI workflows <span className="c">{S.workflows.length} items</span>
            </h2>
            {editList(S.workflows.map((w: any) => editRow("ci", "roles", w.name, w.count)))}
          </>
        ) : E.current ? (
          <>
            <div className="chain">
              {E.current.chain.map((c: any, i: number) => {
                const label = c.level === "global" ? "global" : c.level + ": " + c.target;
                const act = c.level === E.current.scope.level && c.target === E.current.scope.target ? " act" : "";
                return (
                  <span key={i}>
                    <span className={"pill" + act}>{label}</span>
                    {i < E.current.chain.length - 1 ? <i>›</i> : null}
                  </span>
                );
              })}
            </div>
            {inspSection("Categories", [
              ["Native issue types", E.current.categories.types],
              ["Labels", E.current.categories.labels],
            ])}
            {inspSection("Flow stages", [[null, E.current.stages.statuses]])}
            {inspSection("CI workflows", [[null, E.current.ci.roles]])}
          </>
        ) : null}
      </div>
    </>
  );
}
