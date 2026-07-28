// /dashboard/<id>/edit — owner-only dashboard editor, migrated to React (Manage
// migration, WS2-T5 full port). Reproduces templates/dashboard_editor.j2's markup +
// classes (see ../styles/dashboard_editor.css) and ports the inline JS 1:1: the
// panel list with drag-reorder / width / remove / live preview, the measure-first
// widget-picker modal (viz gating, measures shelf, advanced tool/field, live
// preview), and Save. Reads the spec from render_spa_page bootstrap; measures/
// catalog/preview come from the existing /api/dashboard/* endpoints; Save POSTs to
// /api/dashboard/<id>. Previews are injected imperatively into ref'd divs (then
// hydrateVega) so vegaEmbed's SVG isn't clobbered by React re-renders.
//
// SSR-safe: no top-level window/document access — only in effects / handlers.
import { useEffect, useReducer, useRef, type ReactElement } from "react";

type Panel = any;
type Boot = { id: string; title: string; visibility: string; spec: { title?: string; panels?: Panel[] } };

const SHAPE_ICON: Record<string, string> = { scalar: "#", table: "▦", series: "∿" };
const VIZ_TYPES = [
  { viz: "number", glyph: "123", label: "Number" },
  { viz: "line", glyph: "∿", label: "Line" },
  { viz: "area", glyph: "◺", label: "Area" },
  { viz: "column", glyph: "▮", label: "Column" },
  { viz: "bar", glyph: "▬", label: "Bar" },
  { viz: "pie", glyph: "◔", label: "Pie" },
  { viz: "table", glyph: "▦", label: "Table" },
];
const SHAPE_VIZ: Record<string, string[]> = {
  scalar: ["number", "column", "bar", "pie", "table"],
  series: ["line", "area"],
  table: ["bar", "column", "pie", "table"],
};
const MULTI_FIELD_VIZ = ["line", "area", "column", "bar", "pie", "table"];
const DEFAULT_VIZ_FOR_SHAPE: Record<string, string> = { series: "line", table: "table", scalar: "number" };
const ADV_CUSTOM = "__custom__";

function readBootstrap(): Boot {
  const fb: Boot = { id: "", title: "Untitled dashboard", visibility: "private", spec: { title: "", panels: [] } };
  if (typeof document === "undefined") return fb;
  const el = document.getElementById("spa-bootstrap");
  if (!el || !el.textContent) return fb;
  try {
    return { ...fb, ...JSON.parse(el.textContent) };
  } catch {
    return fb;
  }
}

function hydrateVega(root: HTMLElement) {
  root.querySelectorAll<HTMLElement>(".vl-panel").forEach((el) => {
    if (el.dataset.done) return;
    const s = el.querySelector("script.vl-spec");
    const w = window as any;
    if (!s || !w.vegaEmbed) return;
    let spec: any;
    try {
      spec = JSON.parse(s.textContent || "");
    } catch {
      return;
    }
    const tryEmbed = () => {
      if (el.dataset.done) return true;
      if (!el.clientWidth) return false;
      el.dataset.done = "1";
      w.vegaEmbed(el, spec, { actions: false, renderer: "svg", tooltip: true }).catch(() => {
        el.innerHTML = '<div class="dp-err">chart failed</div>';
      });
      return true;
    };
    if (!tryEmbed() && w.ResizeObserver) {
      const ro = new w.ResizeObserver(() => {
        if (tryEmbed()) ro.disconnect();
      });
      ro.observe(el);
    }
  });
}

export default function DashboardEditor() {
  const boot = useRef<Boot>(readBootstrap()).current;
  const DID = boot.id;

  const spec = useRef<any>(boot.spec || { title: "", panels: [] });
  const panels = useRef<Panel[]>(spec.current.panels || []);
  const title = useRef<string>(boot.title || "");
  const vis = useRef<string>(boot.visibility || "private");
  const status = useRef<{ text: string; cls: string }>({ text: "", cls: "" });
  const listVersion = useRef(0);
  const counter = useRef(0);
  const dragIndex = useRef<number | null>(null);
  const previewRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  // picker state
  const pickerOpen = useRef(false);
  const measuresCache = useRef<any[] | null>(null);
  const toolFieldsCache = useRef<Record<string, any[]> | null>(null);
  const catalogCache = useRef<any>(null);
  const shelf = useRef<any[]>([]);
  const activeViz = useRef<string | null>(null);
  const search = useRef("");
  const wpTitle = useRef("");
  const wpWidth = useRef("2");
  const advOpen = useRef(false);
  const advTool = useRef("");
  const advFieldSel = useRef("");
  const advFieldCustom = useRef("");
  const note = useRef("");
  const noteTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const previewSeq = useRef(0);
  const wpPreviewRef = useRef<HTMLDivElement>(null);

  const [, force] = useReducer((x: number) => x + 1, 0);

  useEffect(() => {
    // init id counter from existing panel ids (p<N>) — like the legacy script
    let c = panels.current.length;
    panels.current.forEach((p) => {
      const m = /(\d+)$/.exec((p && p.id) || "");
      if (m) c = Math.max(c, parseInt(m[1], 10));
    });
    counter.current = c;
    listVersion.current++;
    force();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch + inject each panel's preview whenever the list changes (add/remove/reorder).
  useEffect(() => {
    panels.current.forEach((p) => {
      const el = previewRefs.current.get(p.id);
      if (!el) return;
      fetch("/api/dashboard/preview-panel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ panel: p, period: "all" }),
      })
        .then((r) => r.text())
        .then((html) => {
          el.innerHTML = html;
          hydrateVega(el);
        })
        .catch(() => {
          el.innerHTML = "<div class='dp-err'>preview failed</div>";
        });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listVersion.current]);

  // Escape closes the picker (parity with the legacy keydown listener).
  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      if (ev.key === "Escape" && pickerOpen.current) closePicker();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function setStatus(text: string, cls = "") {
    status.current = { text, cls };
    force();
  }

  // ---- picker helpers (mirror legacy) ----
  function isInShelf(m: any) {
    const tool = (m.source && m.source.tool) || "";
    const field = (m.source && m.source.field) || "";
    return shelf.current.some((c) => c.tool === tool && c.field === field);
  }
  function rowEnabled(m: any) {
    if (shelf.current.length) return m.shape === shelf.current[0].shape;
    if (!activeViz.current) return true;
    return (SHAPE_VIZ[m.shape] || []).indexOf(activeViz.current) !== -1;
  }
  function compatibleViz() {
    if (!shelf.current.length) return VIZ_TYPES.map((v) => v.viz);
    let set = (SHAPE_VIZ[shelf.current[0].shape] || []).slice();
    if (shelf.current.length > 1) set = set.filter((v) => v !== "number");
    return set;
  }
  function showNote(msg: string) {
    note.current = msg;
    if (noteTimer.current) clearTimeout(noteTimer.current);
    noteTimer.current = setTimeout(() => {
      note.current = "";
      force();
    }, 3000);
    force();
  }
  function clearNote() {
    note.current = "";
  }
  function selectViz(viz: string) {
    if (compatibleViz().indexOf(viz) === -1) return;
    activeViz.current = viz;
    refreshPreview();
    force();
  }
  function tryAddChip(chip: any) {
    if (!chip || !chip.tool || !chip.field) return;
    if (shelf.current.length && shelf.current[0].tool !== chip.tool) {
      showNote("Measures on one widget must come from the same tool.");
      return;
    }
    clearNote();
    const canAppend =
      shelf.current.length > 0 &&
      activeViz.current &&
      MULTI_FIELD_VIZ.indexOf(activeViz.current) !== -1 &&
      chip.shape === shelf.current[0].shape &&
      chip.shape !== "table";
    if (canAppend) {
      if (!shelf.current.some((c) => c.field === chip.field)) shelf.current.push(chip);
    } else {
      shelf.current = [chip];
    }
    if (!wpTitle.current.trim()) wpTitle.current = shelf.current[0].label;
    const compat = compatibleViz();
    if (!activeViz.current || compat.indexOf(activeViz.current) === -1) {
      activeViz.current = DEFAULT_VIZ_FOR_SHAPE[shelf.current[0].shape] || compat[0] || null;
    }
    refreshPreview();
    force();
  }
  function removeChip(idx: number) {
    shelf.current.splice(idx, 1);
    clearNote();
    const compat = compatibleViz();
    if (activeViz.current && compat.indexOf(activeViz.current) === -1) {
      activeViz.current = shelf.current.length ? DEFAULT_VIZ_FOR_SHAPE[shelf.current[0].shape] || compat[0] || null : activeViz.current;
    }
    refreshPreview();
    force();
  }
  function currentPanel(): Panel | null {
    const width = Number(wpWidth.current) || 2;
    const t = wpTitle.current.trim();
    if (!shelf.current.length || !activeViz.current) return null;
    const fields = shelf.current.map((c) => c.field);
    return { title: t, width, viz: activeViz.current, data: { tool: shelf.current[0].tool, fields } };
  }
  function refreshPreview() {
    const panel = currentPanel();
    if (!panel) {
      if (wpPreviewRef.current) wpPreviewRef.current.innerHTML = "<div class='wp-empty'>Pick a measure to preview.</div>";
      return;
    }
    const seq = ++previewSeq.current;
    fetch("/api/dashboard/preview-panel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ panel, period: "all" }),
    })
      .then((r) => r.text())
      .then((html) => {
        if (seq !== previewSeq.current || !wpPreviewRef.current) return;
        wpPreviewRef.current.innerHTML = html;
        hydrateVega(wpPreviewRef.current);
      })
      .catch(() => {
        if (seq !== previewSeq.current || !wpPreviewRef.current) return;
        wpPreviewRef.current.innerHTML = "<div class='dp-err'>preview failed</div>";
      });
  }
  function resetPicker() {
    shelf.current = [];
    activeViz.current = null;
    search.current = "";
    wpTitle.current = "";
    wpWidth.current = "2";
    advOpen.current = false;
    advTool.current = "";
    advFieldSel.current = "";
    advFieldCustom.current = "";
    clearNote();
  }
  function openPicker() {
    resetPicker();
    pickerOpen.current = true;
    force();
    if (!measuresCache.current) {
      fetch("/api/dashboard/measures")
        .then((r) => r.json())
        .then((d) => {
          measuresCache.current = (d && d.ok && d.measures) || [];
          toolFieldsCache.current = (d && d.ok && d.tool_fields) || {};
          force();
        })
        .catch(() => {
          measuresCache.current = [];
          toolFieldsCache.current = {};
          force();
        });
    }
    if (!catalogCache.current) {
      fetch("/api/dashboard/catalog")
        .then((r) => r.json())
        .then((d) => {
          if (!d || !d.ok) return;
          catalogCache.current = d;
          force();
        })
        .catch(() => {});
    }
  }
  function closePicker() {
    pickerOpen.current = false;
    force();
  }
  function addPanel() {
    const panel = currentPanel();
    if (!panel) return;
    counter.current += 1;
    panel.id = "p" + counter.current;
    panels.current.push(panel);
    pickerOpen.current = false;
    listVersion.current++;
    force();
  }
  function populateAdvFieldsInitial(tool: string) {
    const fields = (toolFieldsCache.current && toolFieldsCache.current[tool]) || [];
    advFieldSel.current = fields.length ? fields[0].field : ADV_CUSTOM;
    advFieldCustom.current = "";
  }
  function addAdvancedField() {
    const tool = advTool.current;
    if (!tool) return;
    const isCustom = advFieldSel.current === ADV_CUSTOM;
    const field = isCustom ? advFieldCustom.current.trim() : advFieldSel.current;
    if (!field) return;
    const fields = (toolFieldsCache.current && toolFieldsCache.current[tool]) || [];
    const opt = !isCustom ? fields.find((f) => f.field === advFieldSel.current) : null;
    const shape = (opt && opt.shape) || (tool === "trend" ? "series" : "table");
    const label = (opt && opt.label) || field;
    tryAddChip({ tool, field, label, shape });
  }

  function save() {
    setStatus("Saving…", "");
    fetch("/api/dashboard/" + DID, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec: { title: title.current, panels: panels.current }, visibility: vis.current }),
    })
      .then((r) => r.json())
      .then((d) => {
        if (d && d.ok) {
          spec.current.title = title.current;
          setStatus("Saved", "ok");
        } else {
          setStatus("Failed" + (d && d.error ? ": " + d.error : ""), "err");
        }
      })
      .catch(() => setStatus("Failed", "err"));
  }

  // ---- measure list (picker) ----
  function measureList() {
    const q = (search.current || "").trim().toLowerCase();
    const list = measuresCache.current || [];
    if (!list.length) {
      return <div className="wp-empty">{measuresCache.current ? "No measures found." : "Loading measures…"}</div>;
    }
    const filtered = list.filter((m: any) => {
      if (!q) return true;
      const tool = (m.source && m.source.tool) || "";
      const field = (m.source && m.source.field) || "";
      return [m.label, m.category, tool, field].join(" ").toLowerCase().indexOf(q) !== -1;
    });
    if (!filtered.length) return <div className="wp-empty">No measures match “{search.current}”.</div>;
    const out: ReactElement[] = [];
    let lastCategory: string | null = null;
    filtered.forEach((m: any, idx: number) => {
      if (m.category !== lastCategory) {
        lastCategory = m.category;
        out.push(
          <div className="wp-group" key={"g" + idx}>
            {m.category}
          </div>,
        );
      }
      const disabled = !rowEnabled(m);
      const tool = (m.source && m.source.tool) || "";
      const field = (m.source && m.source.field) || "";
      out.push(
        <div
          className={"wp-row" + (disabled ? " disabled" : "") + (isInShelf(m) ? " sel" : "")}
          key={"r" + idx}
          onClick={() => {
            if (disabled) return;
            tryAddChip({ tool, field, label: m.label, shape: m.shape });
          }}
        >
          <div className="wi">{SHAPE_ICON[m.shape] || "?"}</div>
          <div className="wt">
            <div className="l">{m.label}</div>
            <div className="h">{tool + (field ? " · " + field : "")}</div>
          </div>
        </div>,
      );
    });
    return out;
  }

  const compat = compatibleViz();
  const advFields = (toolFieldsCache.current && advTool.current && toolFieldsCache.current[advTool.current]) || [];

  return (
    <>
      <div className="row top">
        <div className="row">
          <input
            id="dash-title"
            defaultValue={title.current}
            onChange={(e) => (title.current = e.target.value)}
          />
          <select id="dash-vis" defaultValue={vis.current} onChange={(e) => (vis.current = e.target.value)}>
            <option value="private">Private</option>
            <option value="shared">Shared</option>
          </select>
        </div>
        <div className="row">
          <span id="status" className={"status" + (status.current.cls ? " " + status.current.cls : "")}>
            {status.current.text}
          </span>
          <button id="save" className="primary" type="button" onClick={save}>
            Save
          </button>
          <a href={"/dashboard/" + DID}>Open view →</a>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Panels</h2>
          <button id="open-picker" className="primary" type="button" onClick={openPicker}>
            + Add widget
          </button>
        </div>
        <div id="panels">
          {!panels.current.length ? (
            <div className="empty">No panels yet — click + Add widget above.</div>
          ) : (
            panels.current.map((p, i) => {
              const fieldsTxt = p.viz && p.data ? (p.data.fields || []).join(" + ") : "";
              const sub = p.viz && p.data
                ? p.viz + " · " + (p.data.tool || "?") + (fieldsTxt ? " (" + fieldsTxt + ")" : "")
                : (p.component || "?") + " / " + ((p.source && p.source.tool) || "?") + ((p.source && p.source.field) ? " (" + p.source.field + ")" : "");
              return (
                <div
                  className="panel-row"
                  key={p.id}
                  draggable
                  onDragStart={(e) => {
                    dragIndex.current = i;
                    e.dataTransfer.effectAllowed = "move";
                  }}
                  onDragOver={(e) => {
                    e.preventDefault();
                    (e.currentTarget as HTMLElement).classList.add("drag-over");
                  }}
                  onDragLeave={(e) => (e.currentTarget as HTMLElement).classList.remove("drag-over")}
                  onDrop={(e) => {
                    e.preventDefault();
                    (e.currentTarget as HTMLElement).classList.remove("drag-over");
                    if (dragIndex.current === null || dragIndex.current === i) return;
                    const moved = panels.current.splice(dragIndex.current, 1)[0];
                    panels.current.splice(i, 0, moved);
                    dragIndex.current = null;
                    listVersion.current++;
                    force();
                  }}
                >
                  <div className="handle">☰</div>
                  <div className="panel-meta">
                    <div className="t">{p.title || p.id}</div>
                    <div className="s">{sub}</div>
                  </div>
                  <div className="panel-ctrls">
                    <label>Width</label>
                    <input
                      type="number"
                      min="1"
                      max="6"
                      defaultValue={p.width || 2}
                      onChange={(e) => (panels.current[i].width = Number(e.target.value) || 2)}
                    />
                    <button
                      type="button"
                      onClick={() => {
                        panels.current.splice(i, 1);
                        listVersion.current++;
                        force();
                      }}
                    >
                      Remove
                    </button>
                  </div>
                  <div
                    className="preview"
                    ref={(el) => {
                      if (el) previewRefs.current.set(p.id, el);
                      else previewRefs.current.delete(p.id);
                    }}
                  ></div>
                </div>
              );
            })
          )}
        </div>
      </div>

      <div id="picker" className="dov" hidden={!pickerOpen.current} aria-hidden={!pickerOpen.current} onClick={(e) => { if (e.target === e.currentTarget) closePicker(); }}>
        <div className="dbox">
          <div className="dhead">
            <b>Add widget</b>
            <button id="picker-close" type="button" aria-label="Close" onClick={closePicker}>
              ×
            </button>
          </div>
          <div className="wp-body">
            <div className="wp-col wp-list">
              <input
                id="wp-search"
                type="text"
                placeholder="Search measures…"
                value={search.current}
                onChange={(e) => {
                  search.current = e.target.value;
                  force();
                }}
              />
              <div id="wp-measures">{measureList()}</div>
            </div>
            <div className="wp-col wp-detail">
              <div className="wp-field">
                <label>Show as</label>
                <div id="wp-viz" className="viz-row">
                  {VIZ_TYPES.map((vt) => (
                    <button
                      key={vt.viz}
                      type="button"
                      className={"pchip viz-btn" + (vt.viz === activeViz.current ? " active" : "")}
                      disabled={compat.indexOf(vt.viz) === -1}
                      title={vt.label}
                      onClick={() => selectViz(vt.viz)}
                    >
                      <span className="vg">{vt.glyph}</span>
                      <span className="vl">{vt.label}</span>
                    </button>
                  ))}
                </div>
              </div>
              <div className="wp-field">
                <label>Measures</label>
                <div id="wp-series" className="series-shelf">
                  {!shelf.current.length ? (
                    <span className="wp-empty">No measures yet — pick one from the list at left.</span>
                  ) : (
                    shelf.current.map((c, idx) => (
                      <span className="chip" key={idx}>
                        <span>{c.label}</span>
                        <button
                          type="button"
                          className="chip-x"
                          aria-label={"Remove " + c.label}
                          onClick={(ev) => {
                            ev.stopPropagation();
                            removeChip(idx);
                          }}
                        >
                          ×
                        </button>
                      </span>
                    ))
                  )}
                </div>
                <p id="wp-series-note" className="wp-note" hidden={!note.current}>
                  {note.current}
                </p>
              </div>
              <div className="wp-field">
                <label>Title</label>
                <input
                  id="wp-title"
                  placeholder="Panel title"
                  value={wpTitle.current}
                  onChange={(e) => {
                    wpTitle.current = e.target.value;
                    force();
                  }}
                  onBlur={refreshPreview}
                />
              </div>
              <div className="wp-field">
                <label>Width</label>
                <input
                  id="wp-width"
                  type="number"
                  min="1"
                  max="6"
                  value={wpWidth.current}
                  onChange={(e) => {
                    wpWidth.current = e.target.value;
                    force();
                  }}
                  onBlur={refreshPreview}
                />
              </div>
              <details
                className="wp-advanced"
                id="wp-advanced"
                open={advOpen.current}
                onToggle={(e) => (advOpen.current = (e.currentTarget as HTMLDetailsElement).open)}
              >
                <summary>Advanced: raw tool &amp; field</summary>
                <div>
                  <p className="wp-hint">
                    Pick a tool, then a field — for anything not in the measure list on the left. Adds to the measures
                    shelf above.
                  </p>
                  <div className="wp-field">
                    <label>Tool</label>
                    <select
                      id="wp-adv-tool"
                      value={advTool.current}
                      onChange={(e) => {
                        advTool.current = e.target.value;
                        if (advTool.current) populateAdvFieldsInitial(advTool.current);
                        else {
                          advFieldSel.current = "";
                        }
                        force();
                      }}
                    >
                      <option value="">— select tool —</option>
                      {((catalogCache.current && catalogCache.current.tools) || []).map((t: string) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="wp-field">
                    <label>Field</label>
                    <select
                      id="wp-adv-field-sel"
                      value={advFieldSel.current}
                      onChange={(e) => {
                        advFieldSel.current = e.target.value;
                        force();
                        if (advFieldSel.current !== ADV_CUSTOM) addAdvancedField();
                      }}
                    >
                      {advFields.map((f: any) => (
                        <option key={f.field} value={f.field}>
                          {f.label + " (" + f.field + ")"}
                        </option>
                      ))}
                      <option value={ADV_CUSTOM}>{advFields.length ? "Custom field…" : "Custom field… (no presets)"}</option>
                    </select>
                    <input
                      id="wp-adv-field"
                      placeholder="custom field, e.g. totals.commits"
                      hidden={advFieldSel.current !== ADV_CUSTOM}
                      value={advFieldCustom.current}
                      onChange={(e) => {
                        advFieldCustom.current = e.target.value;
                        force();
                      }}
                      onBlur={addAdvancedField}
                    />
                  </div>
                </div>
              </details>
              <div className="wp-field">
                <label>Preview</label>
                <div id="wp-preview" ref={wpPreviewRef}></div>
              </div>
            </div>
          </div>
          <div className="dfoot">
            <button id="wp-cancel" type="button" onClick={closePicker}>
              Cancel
            </button>
            <button id="wp-add" className="primary" type="button" disabled={!currentPanel()} onClick={addPanel}>
              Add
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
