// /semantic — guided taxonomy setup wizard, migrated to React (Manage migration).
// Reproduces templates/editors/semantic_wizard.html's markup + classes (see
// ../styles/semantic_wizard.css) and ports the inline JS 1:1: 5-step nav, scope
// picker (GET /api/semantic/wizard), category triage (auto rows + "your call"
// decks + long tail), the drag/click flow pipeline, CI triage, the exact coverage
// ring (debounced POST /api/semantic/coverage), review, and Save (POST
// /api/semantic).
//
// Faithful to the legacy mutable-model + explicit-render pattern (refs + force()).
// SSR-safe: no top-level window/document access — only in effects / handlers.
import { Fragment, useEffect, useReducer, useRef, useState } from "react";

const STEPS = ["Scope", "Categories", "Flow", "CI", "Review"];
const CATBK: Record<string, string> = { bug: "--cat-bug", feature: "--cat-feature", task: "--cat-task", epic: "--cat-epic", spec: "--cat-spec", docs: "--cat-docs", test: "--cat-test" };
const CIBK: Record<string, string> = { gate: "--cat-feature", release: "--cat-task", nightly: "--cat-spec", ignore: "--cat-other" };
const LANEBK: Record<string, string> = { backlog: "--cat-other", ready: "--cat-docs", in_progress: "--cat-task", review: "--cat-spec", qa: "--cat-test", done: "--cat-feature", released: "--cat-epic" };
const CAP = 8;
const cap = (b: string) => (b ? b.charAt(0).toUpperCase() + b.slice(1).replace(/_/g, " ") : b);

type Maps = { categories: { labels: Record<string, string>; types: Record<string, string> };
  stages: { statuses: Record<string, string> }; ci: { roles: Record<string, string> } };

export default function SemanticWizard() {
  const [loaded, setLoaded] = useState(false);
  const D = useRef<any>(null);
  const A = useRef<Maps>({ categories: { labels: {}, types: {} }, stages: { statuses: {} }, ci: { roles: {} } });
  const cur = useRef(0);
  const covData = useRef<{ covered: number; total: number }>({ covered: 0, total: 0 });
  const sel = useRef<Record<string, string>>({}); // auto/tail select values, keyed axis|mk|name
  const placement = useRef<Record<string, string>>({}); // status -> lane key | "__tray"
  const lane0 = useRef<Record<string, { own: boolean; lane0: string }>>({});
  const decidedCat = useRef<Set<string>>(new Set());
  const decidedCi = useRef<Set<string>>(new Set());
  const openZones = useRef<Set<string>>(new Set(["types", "decide", "ciauto", "cidecide"]));
  const expanded = useRef<Set<string>>(new Set());
  const selChip = useRef<string | null>(null);
  const dragChip = useRef<string | null>(null);
  const overLane = useRef<string | null>(null);
  const covTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [stMsg, setStMsg] = useState<{ html?: string; text?: string }>({ text: "" });
  const [, force] = useReducer((x: number) => x + 1, 0);

  useEffect(() => {
    loadScope("global", "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function initState() {
    A.current = { categories: { labels: {}, types: {} }, stages: { statuses: {} }, ci: { roles: {} } };
    covData.current = { covered: D.current.categories.coverage.covered, total: D.current.categories.coverage.total };
    const g = D.current.is_global;
    sel.current = {};
    decidedCat.current = new Set();
    decidedCi.current = new Set();
    D.current.categories.types.forEach((t: any) => {
      sel.current["cat|types|" + t.name] = t.current || "";
      if (t.current && (g || t.own)) A.current.categories.types[t.name] = t.current;
    });
    D.current.categories.auto.forEach((l: any) => {
      sel.current["cat|labels|" + l.name] = l.current || "";
      if (l.current && (g || l.own)) A.current.categories.labels[l.name] = l.current;
    });
    D.current.categories.tail.forEach((l: any) => (sel.current["cat|labels|" + l.name] = ""));
    D.current.ci.auto.forEach((w: any) => {
      sel.current["ci|roles|" + w.name] = w.current || "";
      if (w.current && (g || w.own)) A.current.ci.roles[w.name] = w.current;
    });
    D.current.ci.tail.forEach((w: any) => (sel.current["ci|roles|" + w.name] = ""));
    // pipeline placement
    placement.current = {};
    lane0.current = {};
    D.current.stages.lanes.forEach((l: any) => {
      (D.current.stages.placed[l.key] || []).forEach((s: any) => {
        placement.current[s.name] = l.key;
        lane0.current[s.name] = { own: !!s.own, lane0: l.key };
        if (g || s.own) A.current.stages.statuses[s.name] = l.key;
      });
    });
    (D.current.stages.tray || []).forEach((s: any) => {
      placement.current[s.name] = "__tray";
      lane0.current[s.name] = { own: !!s.own, lane0: "" };
    });
  }

  function loadScope(level: string, target: string) {
    setStMsg({ text: "loading…" });
    fetch("/api/semantic/wizard?level=" + encodeURIComponent(level) + "&target=" + encodeURIComponent(target || ""))
      .then((r) => r.json())
      .then((j) => {
        if (!j.ok) {
          setStMsg({ text: j.error || "load failed" });
          return;
        }
        D.current = j.data;
        setStMsg({ text: "" });
        initState();
        setLoaded(true);
        force();
      })
      .catch(() => setStMsg({ text: "load failed" }));
  }

  const pct = () => (covData.current.total ? Math.round((covData.current.covered / covData.current.total) * 100) : 0);
  const covMsg = () => {
    const p = pct();
    return p >= 80 ? "Great — most issues now have a category." : p >= 55 ? "Good progress — a couple more will do it." : "Confirm the high-volume labels to push this up.";
  };
  function requestCoverage() {
    if (covTimer.current) clearTimeout(covTimer.current);
    covTimer.current = setTimeout(() => {
      fetch("/api/semantic/coverage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ level: D.current.scope.level, target: D.current.scope.target, assignments: A.current }),
      })
        .then((r) => r.json())
        .then((j) => {
          if (j && j.ok) {
            covData.current = j.coverage;
            force();
          }
        })
        .catch(() => {});
    }, 350);
  }

  const scopeLabel = () => (D.current.is_global ? "Everyone · global base" : D.current.scope.level + " · " + D.current.scope.target + " · overrides");

  function pickScope(level: string, target: string) {
    loadScope(level, target);
  }
  function toggleZone(id: string) {
    if (openZones.current.has(id)) openZones.current.delete(id);
    else openZones.current.add(id);
    force();
  }
  function go(n: number) {
    n = Math.max(0, Math.min(STEPS.length - 1, n));
    cur.current = n;
    force();
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ---- category / ci item selects (auto + tail) ----
  function onItemSelect(axis: "cat" | "ci", mk: string, name: string, val: string) {
    sel.current[axis + "|" + mk + "|" + name] = val;
    if (axis === "ci") {
      if (val) A.current.ci.roles[name] = val;
      else delete A.current.ci.roles[name];
    } else {
      const map = mk === "types" ? A.current.categories.types : A.current.categories.labels;
      if (val) map[name] = val;
      else delete map[name];
      requestCoverage();
    }
    force();
  }
  function onDecide(axis: "cat" | "ci", name: string, bucket: string) {
    if (axis === "ci") {
      if (bucket) A.current.ci.roles[name] = bucket;
      else delete A.current.ci.roles[name];
      decidedCi.current.add(name);
      sel.current["ci|decide|" + name] = bucket;
    } else {
      if (bucket) A.current.categories.labels[name] = bucket;
      else delete A.current.categories.labels[name];
      decidedCat.current.add(name);
      sel.current["cat|decide|" + name] = bucket;
      requestCoverage();
    }
    force();
  }

  // ---- pipeline ----
  function moveTo(status: string, lane: string) {
    placement.current[status] = lane;
    syncStages();
    force();
  }
  function syncStages() {
    A.current.stages.statuses = {};
    D.current.stages.lanes.forEach((l: any) => {
      Object.keys(placement.current).forEach((s) => {
        if (placement.current[s] !== l.key) return;
        const info = lane0.current[s] || { own: false, lane0: "" };
        if (D.current.is_global || info.own || info.lane0 !== l.key) A.current.stages.statuses[s] = l.key;
      });
    });
  }

  async function save() {
    setStMsg({ text: "saving…" });
    try {
      const r = await fetch("/api/semantic", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ level: D.current.scope.level, target: D.current.scope.target, assignments: A.current, _version: D.current.version }),
      });
      const res = { status: r.status, j: await r.json() };
      if (res.status === 409) {
        setStMsg({ text: "another tab saved — reload the page" });
        return;
      }
      if (!res.j.ok) {
        setStMsg({ text: res.j.error || "save failed" });
        return;
      }
      D.current.version = res.j.version;
      setStMsg({ html: 'saved ✓ — <a href="/report" style="color:var(--acc-ink);font-weight:700">view report →</a>' });
    } catch {
      setStMsg({ text: "save failed" });
    }
  }

  if (!loaded) return <div className="steps" id="steps"></div>;

  const isGlobal = D.current.is_global;
  const p = pct();

  // ---- helpers for JSX ----
  const dotSpan = (v: string | undefined) => (v ? <span className="dot" style={{ background: "var(" + v + ")" }}></span> : null);
  function pillsel(name: string, mk: string, axis: "cat" | "ci", includeLeave: boolean) {
    const keys: string[] = axis === "ci" ? D.current.buckets.ci : D.current.buckets.categories;
    const col = axis === "ci" ? CIBK : CATBK;
    const key = axis + "|" + mk + "|" + name;
    const current = sel.current[key] || "";
    return (
      <span className="pillsel">
        <span className="dot" style={{ background: "var(" + (col[current] || "--cat-other") + ")" }}></span>
        <select value={current} onChange={(e) => onItemSelect(axis, mk, name, e.target.value)}>
          {includeLeave ? <option value="">— leave —</option> : null}
          {keys.map((b) => (
            <option key={b} value={b}>
              {cap(b)}
            </option>
          ))}
        </select>
      </span>
    );
  }
  function autoRow(l: any, axis: "cat" | "ci") {
    const mk = axis === "ci" ? "roles" : l.__types ? "types" : "labels";
    const key = axis + "|" + mk + "|" + l.name;
    const val = sel.current[key] || "";
    const guess = l.current || "";
    const edited = val !== guess;
    return (
      <div className={"arow" + (edited ? " overridden" : "")} key={l.name}>
        <span className="lname mono" title={l.name}>
          {l.name}
        </span>
        <span className="cnt num">{l.count}</span>
        <span className="edited" hidden={!edited}>
          edited
        </span>
        {pillsel(l.name, mk, axis, false)}
      </div>
    );
  }
  function tailRows(arr: any[], axis: "cat" | "ci") {
    if (!arr.length) return <div className="cnt" style={{ padding: "8px 4px" }}>No long tail.</div>;
    const mk = axis === "ci" ? "roles" : "labels";
    return arr.map((l) => (
      <div className="arow" key={l.name}>
        <span className="lname mono">{l.name}</span>
        <span className="cnt num">{l.count}</span>
        {pillsel(l.name, mk, axis, true)}
      </div>
    ));
  }
  function decideCards(arr: any[], axis: "cat" | "ci", deckId: string) {
    if (!arr.length) return <div className="cnt" style={{ padding: "8px 4px" }}>Nothing to decide — nice.</div>;
    const keys: string[] = axis === "ci" ? D.current.buckets.ci : D.current.buckets.categories;
    const col = axis === "ci" ? CIBK : CATBK;
    const decidedSet = axis === "ci" ? decidedCi.current : decidedCat.current;
    const isExpanded = expanded.current.has(deckId);
    const cards = arr.map((l, i) => {
      const decided = decidedSet.has(l.name);
      const chosen = sel.current[axis + "|decide|" + l.name];
      const hidden = !isExpanded && i >= CAP;
      const msg = decided
        ? axis === "ci"
          ? chosen
            ? "Tagged as “" + cap(chosen) + "”."
            : "Ignored."
          : chosen
            ? "Counted as “" + cap(chosen) + "”."
            : "Left uncategorized."
        : "";
      return (
        <div className={"dcard" + (decided ? " decided" : "")} key={l.name} style={hidden ? { display: "none" } : undefined}>
          <div className="dh">
            <span className="lname mono">{l.name}</span>
            <span className="vol num">
              {l.count.toLocaleString()} {axis === "ci" ? "runs" : "issues"}
            </span>
          </div>
          <div className="buckets">
            {keys.map((b) => {
              const s = l.suggest === b ? " sug" : "";
              return (
                <button key={b} className={"bk" + s + (chosen === b ? " sel" : "")} onClick={() => onDecide(axis, l.name, b)}>
                  {dotSpan(col[b])}
                  {cap(b)}
                  {s ? " · guess" : ""}
                </button>
              );
            })}
            <button className={"bk none" + (decided && !chosen ? " sel" : "")} onClick={() => onDecide(axis, l.name, "")}>
              {axis === "ci" ? "Ignore" : "Not a work-type"}
            </button>
          </div>
          <div className="msg">{msg}</div>
        </div>
      );
    });
    return (
      <>
        {cards}
        {arr.length > CAP && !isExpanded ? (
          <button
            className="btn showmore"
            style={{ marginTop: 10 }}
            onClick={() => {
              expanded.current.add(deckId);
              force();
            }}
          >
            Show {arr.length - CAP} more
          </button>
        ) : null}
      </>
    );
  }

  const c = D.current.categories;
  const ci = D.current.ci;
  const editedAuto = c.auto.filter((l: any) => (sel.current["cat|labels|" + l.name] || "") !== (l.current || "")).length;
  const decideLeft = c.decide.filter((l: any) => !decidedCat.current.has(l.name)).length;

  // pipeline lane counts + tray
  const laneCount = (k: string) => Object.keys(placement.current).filter((s) => placement.current[s] === k).length;
  const trayStatuses = Object.keys(placement.current).filter((s) => placement.current[s] === "__tray");
  // status meta lookup (count) from D
  const statusMeta: Record<string, any> = {};
  D.current.stages.lanes.forEach((l: any) => (D.current.stages.placed[l.key] || []).forEach((s: any) => (statusMeta[s.name] = s)));
  (D.current.stages.tray || []).forEach((s: any) => (statusMeta[s.name] = s));

  function chip(name: string) {
    const s = statusMeta[name] || { count: 0 };
    return (
      <span
        className={"schip" + (selChip.current === name ? " sel" : "") + (dragChip.current === name ? " dragging" : "")}
        key={name}
        draggable
        onDragStart={() => {
          dragChip.current = name;
          force();
        }}
        onDragEnd={() => {
          dragChip.current = null;
          overLane.current = null;
          force();
        }}
        onClick={() => {
          selChip.current = selChip.current === name ? null : name;
          force();
        }}
      >
        <span className="sname mono">{name}</span>
        <em>{s.count}</em>
      </span>
    );
  }
  function laneDropProps(lane: string) {
    return {
      onDragOver: (e: React.DragEvent) => {
        e.preventDefault();
        if (overLane.current !== lane) {
          overLane.current = lane;
          force();
        }
      },
      onDragLeave: () => {
        if (overLane.current === lane) {
          overLane.current = null;
          force();
        }
      },
      onDrop: (e: React.DragEvent) => {
        e.preventDefault();
        if (dragChip.current) moveTo(dragChip.current, lane);
        overLane.current = null;
        dragChip.current = null;
      },
      onClick: () => {
        if (selChip.current) {
          moveTo(selChip.current, lane);
          selChip.current = null;
        }
      },
    };
  }

  const scopeChip = (
    <span className="scopechip">
      <span className="k">Scope</span> {scopeLabel()}
    </span>
  );
  const scopeGroups: [string, string, string][] = [
    ["org", "A specific organization", "Override the base for one org."],
    ["element", "A product element", "A group of repos you treat as one product area."],
    ["repo", "A single repository", "The narrowest reach for code & issues."],
    ["project", "A project board", "For flow stages that live in one board."],
  ];
  const chainOrder = ["global", "org", "element", "repo", "project"];
  const chainIdx = chainOrder.indexOf(D.current.scope.level);

  const nCat = Object.keys(A.current.categories.labels).length + Object.keys(A.current.categories.types).length;
  const nStg = Object.keys(A.current.stages.statuses).length;
  const nCi = Object.keys(A.current.ci.roles).length;

  return (
    <>
      <div className="steps" id="steps">
        {STEPS.map((n, i) => {
          const cls = "step" + (i === cur.current ? " active" : "") + (i < cur.current ? " done" : "");
          return (
            <div className={cls} key={i} onClick={() => go(i)}>
              <span className="idx">{i < cur.current ? "✓" : i + 1}</span>
              <span className="nm">{n}</span>
            </div>
          );
        })}
      </div>

      {/* page 0 — scope */}
      <div className={"page" + (cur.current === 0 ? " on" : "")}>
        <div className="wz-card">
          <p className="eyebrow">Start here</p>
          <h1 className="wz">Who is this taxonomy for?</h1>
          <p className="lead">
            GitHub gives us raw labels, issue types, board columns and CI workflows. We group them into a handful of
            categories so the report's numbers mean something. First, pick the reach: set the base everyone inherits, or
            override it for one org, element, repo or project.
          </p>
          <div className="scopelist" id="scopelist">
            <div className={"scopecard" + (isGlobal ? " sel" : "")} onClick={() => pickScope("global", "")}>
              <span className="radio"></span>
              <span className="sc">
                <b>Everyone</b>
                <span>The base every org, team and repo inherits. Start here.</span>
              </span>
              <span className="rec">Recommended</span>
            </div>
            {scopeGroups.map((g) => {
              const opts: string[] = D.current.targets[g[0]] || [];
              const on = !isGlobal && D.current.scope.level === g[0];
              const dis = opts.length ? undefined : { opacity: 0.5, pointerEvents: "none" as const };
              return (
                <div
                  className={"scopecard" + (on ? " sel" : "")}
                  key={g[0]}
                  style={dis}
                  onClick={(e) => {
                    if ((e.target as HTMLElement).closest("select")) return;
                    if (opts.length) pickScope(g[0], opts[0]);
                  }}
                >
                  <span className="radio"></span>
                  <span className="sc">
                    <b>{g[1]}</b>
                    <span>
                      {g[2]}
                      {opts.length ? "" : " (none found)"}
                    </span>
                    {opts.length ? (
                      <select
                        hidden={!on}
                        value={on ? D.current.scope.target : undefined}
                        onChange={(e) => pickScope(g[0], e.target.value)}
                      >
                        {opts.map((o) => (
                          <option key={o}>{o}</option>
                        ))}
                      </select>
                    ) : null}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="chain" id="chain">
            <span className="k" style={{ color: "var(--mut)", marginRight: 4 }}>
              Inheritance:
            </span>
            {chainOrder.map((o, i) => (
              <Fragment key={o}>
                <span className={"pill" + (i === chainIdx ? " act" : "")}>{o}</span>
                {i < chainOrder.length - 1 ? <i style={{ fontStyle: "normal", color: "var(--mut)" }}>›</i> : null}
              </Fragment>
            ))}
            {isGlobal ? "  — you're setting the base everyone starts from." : "  — you'll override just what differs here; the rest inherits."}
          </div>
        </div>
      </div>

      {/* page 1 — categories */}
      <div className={"page" + (cur.current === 1 ? " on" : "")}>
        <div className="wz-card">
          <div className="titlerow">
            <div>
              <p className="eyebrow">Step 2 of 5 · the big one</p>
              <h2 className="wz">Categories — what kind of work is this?</h2>
            </div>
            {scopeChip}
          </div>
          <p className="lead" style={{ marginTop: 14 }}>
            Native issue types map straight through; labels are sorted by volume, so your first few decisions cover the
            most issues. Confirm the guesses, decide the ambiguous few, and leave the rare long tail uncategorized.
          </p>
          <div className="cov">
            <div className="ring">
              <svg width="60" height="60" viewBox="0 0 60 60">
                <circle cx="30" cy="30" r="25" fill="none" stroke="var(--line2)" strokeWidth="7" />
                <circle
                  cx="30"
                  cy="30"
                  r="25"
                  fill="none"
                  stroke="var(--acc)"
                  strokeWidth="7"
                  strokeLinecap="round"
                  strokeDasharray="157.1"
                  strokeDashoffset={(157.1 * (1 - p / 100)).toFixed(1)}
                  style={{ transition: "stroke-dashoffset .4s" }}
                />
              </svg>
              <span className="pc num">{p}%</span>
            </div>
            <div className="txt">
              <b>Issue coverage</b>
              <p>{covMsg()}</p>
            </div>
          </div>
          <div className={"zone" + (openZones.current.has("types") ? " open" : "")}>
            <div className="zone-h" onClick={() => toggleZone("types")}>
              <span className="zi a">✓</span>
              <span className="zt">
                <b>Native issue types</b>
                <span>
                  {c.types.length} of {c.types.length} · GitHub's own types
                </span>
              </span>
              <span className="chev">›</span>
            </div>
            <div className="zone-b">
              {c.types.length ? c.types.map((t: any) => autoRow({ ...t, __types: true }, "cat")) : <div className="cnt" style={{ padding: "8px 4px" }}>No native issue types in scope.</div>}
            </div>
          </div>
          <div className={"zone" + (openZones.current.has("auto") ? " open" : "")}>
            <div className="zone-h" onClick={() => toggleZone("auto")}>
              <span className="zi a">✓</span>
              <span className="zt">
                <b>Auto-matched from their names</b>
                <span>
                  {c.auto.length} labels · confirm or override{editedAuto ? " · " + editedAuto + " edited" : ""}
                </span>
              </span>
              <span className="chev">›</span>
            </div>
            <div className="zone-b">{c.auto.length ? c.auto.map((l: any) => autoRow(l, "cat")) : <div className="cnt" style={{ padding: "8px 4px" }}>Nothing auto-matched.</div>}</div>
          </div>
          <div className={"zone" + (openZones.current.has("decide") ? " open" : "")}>
            <div className="zone-h" onClick={() => toggleZone("decide")}>
              <span className="zi d">?</span>
              <span className="zt">
                <b>Your call</b>
                <span>{decideLeft ? decideLeft + " labels still need your call" : "all sorted"}</span>
              </span>
              <span className="chev">›</span>
            </div>
            <div className="zone-b">
              <div className="deck">{decideCards(c.decide, "cat", "catdeck")}</div>
            </div>
          </div>
          <div className={"zone" + (openZones.current.has("tail") ? " open" : "")}>
            <div className="zone-h" onClick={() => toggleZone("tail")}>
              <span className="zi t">≡</span>
              <span className="zt">
                <b>The long tail</b>
                <span>{c.tail.length} rare labels (&lt;30 issues) — left uncategorized unless you map them</span>
              </span>
              <span className="chev">›</span>
            </div>
            <div className="zone-b">{tailRows(c.tail, "cat")}</div>
          </div>
          <div className="adv">
            <span>Need a label to mean something different for one team or repo?</span>
            <a href="/semantic/advanced">Per-scope overrides — Advanced editor →</a>
          </div>
        </div>
      </div>

      {/* page 2 — flow */}
      <div className={"page" + (cur.current === 2 ? " on" : "")}>
        <div className="wz-card">
          <div className="titlerow">
            <div>
              <p className="eyebrow">Step 3 of 5</p>
              <h2 className="wz">Flow stages — the delivery pipeline</h2>
            </div>
            {scopeChip}
          </div>
          <p className="lead" style={{ marginTop: 14 }}>
            Drag each board status into the stage it represents. Left to right is the flow, from backlog to release. A
            status that fits no stage can stay unplaced — it's ignored.
          </p>
          <div className="tray-h" id="trayH">
            {trayStatuses.length ? "Unplaced (" + trayStatuses.length + " left) — drag into a stage, or leave to ignore" : "Unplaced — all statuses placed"}
          </div>
          <div className={"lane-body tray" + (trayStatuses.length ? "" : " empty") + (overLane.current === "__tray" ? " over" : "")} {...laneDropProps("__tray")}>
            {trayStatuses.map((s) => chip(s))}
          </div>
          <div className="pipeline">
            {D.current.stages.lanes.map((l: any, i: number) => (
              <span key={l.key} style={{ display: "contents" }}>
                {i > 0 ? <div className="lane-arrow">→</div> : null}
                <div className="lane">
                  <div className="lane-h">
                    <span className="ldot" style={{ background: "var(" + (LANEBK[l.key] || "--cat-other") + ")" }}></span>
                    {l.name}
                    <span className="lc">{laneCount(l.key)}</span>
                  </div>
                  <div className={"lane-body" + (overLane.current === l.key ? " over" : "")} {...laneDropProps(l.key)}>
                    {Object.keys(placement.current)
                      .filter((s) => placement.current[s] === l.key)
                      .map((s) => chip(s))}
                  </div>
                </div>
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* page 3 — CI */}
      <div className={"page" + (cur.current === 3 ? " on" : "")}>
        <div className="wz-card">
          <div className="titlerow">
            <div>
              <p className="eyebrow">Step 4 of 5</p>
              <h2 className="wz">CI workflows — which ones are quality gates?</h2>
            </div>
            {scopeChip}
          </div>
          <p className="lead" style={{ marginTop: 14 }}>
            Only “gate” workflows count toward pass-rate. Deploys, nightlies and scheduled jobs shouldn't drag the
            number down.
          </p>
          <div className={"zone" + (openZones.current.has("ciauto") ? " open" : "")}>
            <div className="zone-h" onClick={() => toggleZone("ciauto")}>
              <span className="zi a">✓</span>
              <span className="zt">
                <b>Pre-tagged</b>
                <span>{ci.auto.length} workflows · confirm or override</span>
              </span>
              <span className="chev">›</span>
            </div>
            <div className="zone-b">{ci.auto.length ? ci.auto.map((w: any) => autoRow(w, "ci")) : <div className="cnt" style={{ padding: "8px 4px" }}>Nothing pre-tagged.</div>}</div>
          </div>
          <div className={"zone" + (openZones.current.has("cidecide") ? " open" : "")}>
            <div className="zone-h" onClick={() => toggleZone("cidecide")}>
              <span className="zi d">?</span>
              <span className="zt">
                <b>Your call</b>
                <span>{ci.decide.length ? ci.decide.length + " higher-volume workflows to classify" : "nothing to decide"}</span>
              </span>
              <span className="chev">›</span>
            </div>
            <div className="zone-b">
              <div className="deck">{decideCards(ci.decide, "ci", "cideck")}</div>
            </div>
          </div>
          <div className={"zone" + (openZones.current.has("citail") ? " open" : "")}>
            <div className="zone-h" onClick={() => toggleZone("citail")}>
              <span className="zi t">≡</span>
              <span className="zt">
                <b>The long tail</b>
                <span>{ci.tail.length} low-volume workflows — left out of pass-rate unless you tag them</span>
              </span>
              <span className="chev">›</span>
            </div>
            <div className="zone-b">{tailRows(ci.tail, "ci")}</div>
          </div>
        </div>
      </div>

      {/* page 4 — review */}
      <div className={"page" + (cur.current === 4 ? " on" : "")}>
        <div className="wz-card">
          <p className="eyebrow">Last step</p>
          <h2 className="wz" style={{ marginBottom: 8 }}>
            Review &amp; save
          </h2>
          <p className="lead">Here's what your taxonomy will do. Saving applies it to the report immediately.</p>
          <div className="kudos">
            <div>
              <b>
                Issue coverage <span className="num">{p}%</span>
              </b>
              <p>
                {isGlobal
                  ? "Saving writes this as the base taxonomy every scope inherits."
                  : "Saving writes these as overrides for " + D.current.scope.level + " · " + D.current.scope.target + "."}
              </p>
            </div>
          </div>
          <div className="summary">
            <div className="sm">
              <div className="v num">{nCat}</div>
              <div className="l">categories mapped (labels + types)</div>
            </div>
            <div className="sm">
              <div className="v num">{nStg}</div>
              <div className="l">board statuses placed in the pipeline</div>
            </div>
            <div className="sm">
              <div className="v num">{nCi}</div>
              <div className="l">CI workflows tagged</div>
            </div>
          </div>
          <div className="adv">
            <span>Fine-tune per team, element or repo later, or map a few rare labels.</span>
            <a href="/semantic/advanced">Open the Advanced editor →</a>
          </div>
        </div>
      </div>

      <div className="footbar">
        <div className="foot-cov">
          <span className="lab">
            <b className="num">{p}%</b> issue coverage
          </span>
          <div className="bar">
            <i style={{ width: p + "%" }}></i>
          </div>
        </div>
        <span className="st-msg" id="stMsg">
          {stMsg.html ? <span dangerouslySetInnerHTML={{ __html: stMsg.html }} /> : stMsg.text}
        </span>
        <span className="skip st-msg" id="skip">
          Step {cur.current + 1} of {STEPS.length}
        </span>
        <button className="btn ghost" id="back" disabled={cur.current === 0} onClick={() => go(cur.current - 1)}>
          Back
        </button>
        <button className="btn primary" id="next" onClick={() => (cur.current < STEPS.length - 1 ? go(cur.current + 1) : save())}>
          {cur.current === STEPS.length - 1 ? "Save taxonomy ✓" : "Next →"}
        </button>
      </div>
    </>
  );
}
