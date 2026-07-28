// Drill-down modal + click-to-sort for report views — a faithful TS port of
// shell.DRILL_JS + shell.SORT_JS. Both are document-delegated DOM behaviours that
// operate on React-rendered cells/tables (data-drill attrs, table.dt th.sortable)
// and mutate the live DOM (drill opens a #drill modal appended to body; sort
// reorders tbody rows). Imperative is the right shape here — kept verbatim from the
// vanilla originals so behaviour/DOM stay byte-identical — and installed from
// ReportChrome's useEffect (each returns a cleanup). CSS lives in styles/report.css.

// ---- drill-down modal ----
export function installDrill(): () => void {
  if (!/^https?:$/.test(location.protocol)) return () => {}; // live portal only (needs /api/drill)
  if (!window.fetch) return () => {};
  const _esc = (s: any) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
  const _num = (n: any) => {
    const v = Number(n);
    return isFinite(v) ? v.toLocaleString("en-US") : n == null ? "0" : "" + n;
  };
  const _api = (p: string) => fetch(location.origin + p);

  const _PDAYS: Record<string, number> = { "7d": 7, "30d": 30, "90d": 90, "365d": 365 };
  function _periodQS() {
    const u = new URLSearchParams(location.search),
      f = u.get("from"),
      t = u.get("to"),
      p = u.get("p");
    if (f || t) return "from=" + encodeURIComponent(f || "2008-01-01") + (t ? "&to=" + encodeURIComponent(t) : "");
    return p && _PDAYS[p] ? "days=" + _PDAYS[p] : "from=2008-01-01";
  }
  const _sliceVal = () => new URLSearchParams(location.search).get("slice") || "";

  // Keep the open drill in the URL so a shared link reopens it — the monolith did
  // this via __curDrill + _syncURL (report.j2); the React port dropped it, so
  // drill-downs became unshareable. Written straight to the querystring (not
  // through setReportQuery: report-chrome is a separate React root, and `drill`
  // isn't one of useReportData's QUERY_KEYS, so no refetch is triggered). Empty
  // `extra` values are stripped — openDrill ignores them anyway, and it keeps the
  // shared link short.
  function _syncDrillURL(desc: { entity: string; flag: string; extra: any; scope: string } | null) {
    try {
      const u = new URLSearchParams(location.search);
      if (desc) {
        const extra: Record<string, string> = {};
        for (const k in desc.extra || {}) if (desc.extra[k]) extra[k] = desc.extra[k];
        u.set("drill", JSON.stringify({ entity: desc.entity, flag: desc.flag || "", extra, scope: desc.scope || "" }));
      } else {
        u.delete("drill");
      }
      const qs = u.toString();
      history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + (location.hash || ""));
    } catch {
      /* URL sync is best-effort */
    }
  }

  let _drill: any = null;
  const _DRILL_PAGE = 300;
  function _drillRow(st: any, r: any) {
    if (st.isCI) {
      const ok = r.conclusion === "success";
      return (
        '<tr><td><a href="' + _esc(r.url) + '" target="_blank" rel="noopener">#' + _esc(r.ref) + "</a></td>" +
        '<td class="dt">' + _esc(r.title) + "</td><td>" + _esc(r.repo) + "</td>" +
        '<td style="color:' + (ok ? "var(--good)" : "var(--bad)") + '">' + _esc(r.conclusion) + "</td>" +
        "<td>" + _esc(r.duration) + "</td><td>" + _esc(r.date) + "</td></tr>"
      );
    }
    if (st.isPeople) {
      return (
        '<tr><td><a class="gh" href="#person" data-person="' + _esc(r.login) + '">' + _esc(r.name) + "</a>" +
        ' <span class="dm">' + _esc(r.login) + (r.is_member ? "" : " · ext") + "</span></td>" +
        "<td>" + _esc(r.company) + "</td><td>" + _num(r.commits) + "</td><td>" + _num(r.prs) + "</td>" +
        "<td>" + _num(r.specs) + "</td><td>" + _num(r.bugs) + "</td><td>" + _num(r.epics) + "</td><td>" + _num(r.features) + "</td></tr>"
      );
    }
    if (st.isFlow) {
      const ref = r.url ? '<a href="' + _esc(r.url) + '" target="_blank" rel="noopener">' + _esc(r.ref) + "</a>" : _esc(r.ref);
      return (
        "<tr><td>" + ref + '</td><td class="dt">' + (r.title ? _esc(r.title) : '<span class="dm">—</span>') + "</td>" +
        "<td>" + _esc(r.repo) + '</td><td class="dm">' + _esc(r.item_type) + "</td><td>" + _esc(r.status) + "</td></tr>"
      );
    }
    if (st.isFlowItems) {
      const fref = r.url ? '<a href="' + _esc(r.url) + '" target="_blank" rel="noopener">' + _esc(r.ref) + "</a>" : _esc(r.ref);
      return (
        "<tr><td>" + fref + ' <span class="dm">' + _esc(r.repo) + "</span></td>" +
        '<td class="dt">' + (r.title ? _esc(r.title) : '<span class="dm">—</span>') + "</td>" +
        '<td class="dm">' + _esc(r.item_type) + "</td><td>" + _num(r.friction) + "</td>" +
        '<td class="dm">' + _esc(r.detail) + "</td></tr>"
      );
    }
    if (st.isRewinds) {
      const rwref = r.url ? '<a href="' + _esc(r.url) + '" target="_blank" rel="noopener">' + _esc(r.ref) + "</a>" : _esc(r.ref);
      const rwown = r.owner ? '<a class="gh" href="#person" data-person="' + _esc(r.owner) + '">' + _esc(r.owner_name) + "</a>" : '<span class="dm">—</span>';
      return (
        "<tr><td>" + rwref + ' <span class="dm">' + _esc(r.repo) + "</span></td>" +
        '<td class="dt">' + (r.title ? _esc(r.title) : '<span class="dm">—</span>') + "</td>" +
        "<td>" + rwown + '</td><td class="dm">' + _esc(r.move) + "</td><td>" + _esc(r.date) + "</td></tr>"
      );
    }
    const title = r.title ? _esc(r.title) : '<span class="dm">—</span>';
    return (
      '<tr><td><a href="' + _esc(r.url) + '" target="_blank" rel="noopener">' + _esc(r.short) + "</a></td>" +
      '<td class="dt">' + title + "</td>" +
      "<td>" + _esc(r.repo) + "</td><td>" + _esc(r.author) + "</td><td>" + _esc(r.date) + "</td>" +
      '<td class="dm">' + _esc(r.meta) + "</td></tr>"
    );
  }
  function _drillTitle(st: any) {
    const noun = st.isPeople
      ? st.total === 1 ? "person" : "people"
      : st.isCI ? "gate run" + (st.total === 1 ? "" : "s")
      : st.isFlow ? "work item" + (st.total === 1 ? "" : "s")
      : st.isFlowItems ? "flow item" + (st.total === 1 ? "" : "s")
      : st.isRewinds ? "board rewind" + (st.total === 1 ? "" : "s")
      : st.entity + (st.total === 1 ? "" : "s");
    if (st.total == null) return "Loading…";
    return (st.loaded < st.total ? _num(st.loaded) + " of " + _num(st.total) : _num(st.total)) + " " + noun;
  }
  function _drillLoad() {
    const st = _drill;
    if (!st || st.loading || st.done) return;
    st.loading = true;
    _api("/api/drill?" + st.qs + "&limit=" + _DRILL_PAGE + "&offset=" + st.loaded)
      .then((r) => r.json())
      .then((s) => {
        st.loading = false;
        if (!_drill || _drill !== st) return;
        if (!s || !s.ok) {
          if (!st.loaded) st.tt.textContent = (s && s.error) || "error";
          return;
        }
        st.total = s.total;
        const cols = st.isPeople ? 8 : st.isFlow || st.isFlowItems || st.isRewinds ? 5 : 6;
        if (!st.loaded && (!s.rows || !s.rows.length)) {
          st.tb.innerHTML = '<tr><td colspan="' + cols + '" class="dm">Nothing here.</td></tr>';
          st.done = true;
          st.tt.textContent = _drillTitle(st);
          return;
        }
        st.tb.insertAdjacentHTML("beforeend", s.rows.map((r: any) => _drillRow(st, r)).join(""));
        st.loaded += s.rows.length;
        if (st.loaded >= st.total || !s.rows.length) st.done = true;
        st.tt.textContent = _drillTitle(st);
        if (!st.done && st.sc.scrollHeight <= st.sc.clientHeight + 40) _drillLoad();
      })
      .catch(() => {
        st.loading = false;
        if (!st.loaded) st.tt.textContent = "failed to load";
      });
  }
  function openDrill(entity: string, flag: string, extra: any, scopeOverride: any) {
    const scope = scopeOverride === "none" ? "" : scopeOverride != null && scopeOverride !== "" ? scopeOverride : _sliceVal();
    const period = extra && extra.from ? "from=" + encodeURIComponent(extra.from) + (extra.to ? "&to=" + encodeURIComponent(extra.to) : "") : _periodQS();
    let qs = period + (scope ? "&scope=" + encodeURIComponent(scope) : "") + "&entity=" + encodeURIComponent(entity) + (flag ? "&flag=" + encodeURIComponent(flag) : "");
    if (extra) {
      for (const k in extra) {
        if (k !== "from" && k !== "to" && extra[k]) qs += "&" + k + "=" + encodeURIComponent(extra[k]);
      }
    }
    const tt = document.getElementById("drill-title")!,
      tb = document.getElementById("drill-body")!,
      th = document.getElementById("drill-head-row")!,
      ov = document.getElementById("drill")!,
      sc = document.querySelector("#drill .drill-scroll") as HTMLElement;
    const isPeople = entity === "people",
      isCI = entity === "ci",
      isFlow = entity === "flow",
      isFlowItems = entity === "flowitems",
      isRewinds = entity === "rewinds";
    th.innerHTML = isPeople
      ? "<th>Person</th><th>Company</th><th>Commits</th><th>PRs</th><th>Specs</th><th>Bugs</th><th>Epics</th><th>Features</th>"
      : isCI
      ? "<th>Run</th><th>Workflow</th><th>Repo</th><th>Conclusion</th><th>Duration</th><th>Date</th>"
      : isFlow
      ? "<th>Item</th><th>Title</th><th>Repo</th><th>Type</th><th>Status</th>"
      : isFlowItems
      ? "<th>Item</th><th>Title</th><th>Type</th><th>Friction</th><th>Events</th>"
      : isRewinds
      ? "<th>Item</th><th>Title</th><th>Owner</th><th>Move</th><th>Detected</th>"
      : "<th>Ref</th><th>Title</th><th>Repo</th><th>Author</th><th>Date</th><th>Details</th>";
    tb.innerHTML = "";
    tt.textContent = "Loading…";
    sc.scrollTop = 0;
    ov.classList.add("open");
    _drill = { qs, entity, isPeople, isCI, isFlow, isFlowItems, isRewinds, loaded: 0, total: null, loading: false, done: false, tt, tb, sc };
    // remember the open drill in the URL so a shared link reopens it
    _syncDrillURL({ entity, flag: flag || "", extra: extra || {}, scope: scopeOverride || "" });
    if (!(sc as any)._drillBound) {
      (sc as any)._drillBound = true;
      sc.addEventListener(
        "scroll",
        () => {
          if (_drill && !_drill.done && !_drill.loading && sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 160) _drillLoad();
        },
        { passive: true },
      );
    }
    _drillLoad();
  }
  (window as any).openDrill = openDrill;
  const _closeDrill = () => {
    const d = document.getElementById("drill");
    if (d) d.classList.remove("open");
    _drill = null;
    _syncDrillURL(null);
  };

  function _drillCsvSpec(st: any) {
    if (st.isPeople)
      return { h: ["Name", "Login", "Membership", "Company", "Commits", "PRs", "Specs", "Bugs", "Epics", "Features"], row: (r: any) => [r.name, r.login, r.is_member ? "member" : "external", r.company, r.commits, r.prs, r.specs, r.bugs, r.epics, r.features] };
    if (st.isCI) return { h: ["Run", "Workflow", "Repo", "Conclusion", "Duration", "Date", "URL"], row: (r: any) => [r.ref, r.title, r.repo, r.conclusion, r.duration, r.date, r.url] };
    if (st.isFlow) return { h: ["Item", "Title", "Repo", "Type", "Status", "URL"], row: (r: any) => [r.ref, r.title, r.repo, r.item_type, r.status, r.url] };
    if (st.isFlowItems) return { h: ["Item", "Repo", "Title", "Type", "Friction", "Events"], row: (r: any) => [r.ref, r.repo, r.title, r.item_type, r.friction, r.detail] };
    if (st.isRewinds) return { h: ["Item", "Repo", "Title", "Owner", "Owner login", "Move", "Detected", "URL"], row: (r: any) => [r.ref, r.repo, r.title, r.owner_name, r.owner, r.move, r.date, r.url] };
    return { h: ["Ref", "Title", "Repo", "Author", "Date", "Details", "URL"], row: (r: any) => [r.short, r.title, r.repo, r.author, r.date, r.meta, r.url] };
  }
  const _csvCell = (v: any) => {
    v = v == null ? "" : String(v);
    return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
  };
  function _drillExportCSV() {
    const st = _drill;
    if (!st) return;
    const btn = document.getElementById("drill-csv") as HTMLButtonElement | null,
      spec = _drillCsvSpec(st);
    let all: any[] = [],
      off = 0;
    const PAGE = 1000,
      MAX = 100000;
    const reset = (t?: string) => {
      if (btn) {
        btn.disabled = false;
        btn.textContent = t || "Export CSV";
      }
    };
    const fail = () => {
      reset("Export failed");
      setTimeout(() => reset(), 2200);
    };
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Exporting…";
    }
    (function step() {
      _api("/api/drill?" + st.qs + "&limit=" + PAGE + "&offset=" + off)
        .then((r) => r.json())
        .then((s) => {
          if (!s || !s.ok) {
            fail();
            return;
          }
          const got = s.rows || [];
          all = all.concat(got);
          off += got.length;
          if (got.length >= PAGE && off < MAX && (s.total == null || off < s.total)) {
            step();
            return;
          }
          const lines = [spec.h.map(_csvCell).join(",")];
          for (let i = 0; i < all.length; i++) lines.push(spec.row(all[i]).map(_csvCell).join(","));
          const csv = "﻿" + lines.join("\r\n");
          const blob = new Blob([csv], { type: "text/csv;charset=utf-8" }),
            url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = "drill-" + (st.entity || "rows") + "-" + all.length + "-rows.csv";
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          reset();
        })
        .catch(fail);
    })();
  }
  (window as any)._drillExportCSV = _drillExportCSV;

  // mount the #drill modal at body level (once)
  let mounted: HTMLElement | null = null;
  if (!document.getElementById("drill")) {
    const d = document.createElement("div");
    d.id = "drill";
    d.className = "drill-ov";
    d.setAttribute("aria-hidden", "true");
    d.innerHTML =
      '<div class="drill-box"><div class="drill-head"><b id="drill-title"></b>' +
      '<span class="drill-actions"><button id="drill-csv" type="button" title="Download all rows as CSV">Export CSV</button>' +
      '<button id="drill-close" type="button" aria-label="Close">×</button></span></div>' +
      '<div class="drill-scroll"><table class="drill-tbl"><thead><tr id="drill-head-row">' +
      "<th>Ref</th><th>Title</th><th>Repo</th><th>Author</th><th>Date</th><th>Details</th></tr></thead>" +
      '<tbody id="drill-body"></tbody></table></div></div>';
    document.body.appendChild(d);
    mounted = d;
  }

  // A shared link carrying ?drill=<json> reopens that drill (the monolith's
  // initFromURL did the same). The modal DOM is mounted just above, so this can
  // run straight away — no retry loop needed.
  try {
    const raw = new URLSearchParams(location.search).get("drill");
    if (raw) {
      const o = JSON.parse(raw);
      if (o && o.entity) openDrill(o.entity, o.flag || "", o.extra || {}, o.scope || "");
    }
  } catch {
    /* a malformed ?drill= is ignored, like the monolith */
  }

  const onClick = (e: MouseEvent) => {
    const k = (e.target as HTMLElement).closest("[data-drill]");
    if (k) {
      openDrill(
        k.getAttribute("data-drill")!,
        k.getAttribute("data-flag") || "",
        {
          author: k.getAttribute("data-author") || "",
          company: k.getAttribute("data-company") || "",
          classification: k.getAttribute("data-classification") || "",
          category: k.getAttribute("data-category") || "",
          commit_type: k.getAttribute("data-commit-type") || "",
          pr_state: k.getAttribute("data-pr-state") || "",
          abandon_reason: k.getAttribute("data-abandon-reason") || "",
          ai_tool: k.getAttribute("data-ai-tool") || "",
          spec: k.getAttribute("data-spec") || "",
          members: k.getAttribute("data-members") || "",
          reviewed: k.getAttribute("data-reviewed") || "",
          stage: k.getAttribute("data-stage") || "",
          from: k.getAttribute("data-from") || "",
          to: k.getAttribute("data-to") || "",
        },
        k.getAttribute("data-scope"),
      );
      return;
    }
    const t = e.target as HTMLElement;
    if (t.id === "drill-csv") {
      e.preventDefault();
      _drillExportCSV();
      return;
    }
    if (t.id === "drill-close" || t.id === "drill") _closeDrill();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") _closeDrill();
  };
  document.addEventListener("click", onClick);
  document.addEventListener("keydown", onKey);

  return () => {
    document.removeEventListener("click", onClick);
    document.removeEventListener("keydown", onKey);
    if (mounted && mounted.parentNode) mounted.parentNode.removeChild(mounted);
    if ((window as any).openDrill === openDrill) delete (window as any).openDrill;
  };
}

// ---- person links (a[data-person]) → the /person route ----
// Person names across the report render as `<a class="gh" href="#person"
// data-person="<login>">` (GhLink / Scorecard / the drill modal rows) — the
// monolith's convention, where a global showPerson() intercepted the click and
// switched to the person tab. On the React MPA each view is its own route, so
// this delegated handler navigates to /person?person=<login> instead, preserving
// the current period/scope context (p/from/to/slice) so the person page opens in
// the same window the reader was looking at. Full navigation (not history state)
// because the report-chrome bundle is a separate React root from the route bundle
// and can't drive its query store.
export function installPersonNav(): () => void {
  const onClick = (e: MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = (e.target as HTMLElement).closest("a[data-person]") as HTMLElement | null;
    if (!a) return;
    const login = a.getAttribute("data-person");
    if (!login) return;
    e.preventDefault();
    const cur = new URLSearchParams(location.search);
    const keep = new URLSearchParams();
    for (const k of ["p", "from", "to", "slice"]) {
      const v = cur.get(k);
      if (v) keep.set(k, v);
    }
    keep.set("person", login);
    location.assign("/person?" + keep.toString());
  };
  document.addEventListener("click", onClick);
  return () => document.removeEventListener("click", onClick);
}

// ---- "+N more" expander (.row.more-row → toggle the sibling .more-tail) ----
// BarList emits `.row.more-row[data-more][data-less]` followed by a
// `.more-tail[hidden]` holding the rest of the rows — the monolith toggled this
// with inline JS that wasn't ported to the SPA chrome, so the "+N more" rows
// (People categories, Traffic contributors, …) rendered but did nothing. This
// restores it: click the more-row → show/hide the next .more-tail and swap the
// label between data-more / data-less.
// The monolith's one listener covered TWO shapes and only the first was ported:
// the div-list `.more-row` above, and the capped-table `tr.more` that DataTable
// emits whenever a page passes `cap` (Traffic's contributors, People's category
// tables). That second row rendered "▸ Show all N" and did nothing on click.
export function installMoreRows(): () => void {
  const onClick = (e: MouseEvent) => {
    const target = e.target as HTMLElement;
    const tm = target.closest("tr.more") as HTMLElement | null;
    if (tm) {
      // Reveal via the table's own class contract: report.css has
      // `table.capped tr.extra{display:none}` / `.capped.expanded → table-row`.
      const tbl = tm.closest("table");
      if (!tbl) return;
      const open = tbl.classList.toggle("expanded");
      const td = tm.querySelector("td");
      const label = tm.getAttribute(open ? "data-less" : "data-more");
      if (td && label) td.textContent = label;
      return;
    }
    const mr = target.closest(".more-row") as HTMLElement | null;
    if (!mr) return;
    const tail = mr.nextElementSibling as HTMLElement | null;
    if (!tail || !tail.classList.contains("more-tail")) return;
    const nm = mr.querySelector(".nm");
    if (tail.hasAttribute("hidden")) {
      tail.removeAttribute("hidden");
      if (nm && mr.getAttribute("data-less")) nm.textContent = mr.getAttribute("data-less");
    } else {
      tail.setAttribute("hidden", "");
      if (nm && mr.getAttribute("data-more")) nm.textContent = mr.getAttribute("data-more");
    }
  };
  document.addEventListener("click", onClick);
  return () => document.removeEventListener("click", onClick);
}

// ---- click-to-sort for table.dt th.sortable ----
export function installSort(): () => void {
  const onClick = (e: MouseEvent) => {
    const th = (e.target as HTMLElement).closest && ((e.target as HTMLElement).closest("table.dt th.sortable") as HTMLElement | null);
    if (!th) return;
    const table = th.closest("table.dt") as HTMLTableElement,
      tbody = table.tBodies[0];
    if (!tbody) return;
    const heads = th.parentNode!.children,
      idx = Array.prototype.indexOf.call(heads, th);
    const dir = th.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending";
    for (let i = 0; i < heads.length; i++) heads[i].removeAttribute("aria-sort");
    th.setAttribute("aria-sort", dir);
    const all = Array.prototype.slice.call(tbody.rows) as HTMLTableRowElement[];
    const ctrl = all.filter((r) => r.classList.contains("more") || r.querySelector("td[colspan]"));
    const rows = all.filter((r) => ctrl.indexOf(r) === -1 && !r.querySelector("th"));
    const head = all.filter((r) => r.querySelector("th"));
    if (table.classList.contains("capped") || table.classList.contains("grouped")) table.classList.add("expanded");
    const key = (r: HTMLTableRowElement) => {
      const c = r.cells[idx];
      if (!c) return "";
      const s = c.getAttribute("data-sort");
      return s !== null ? s : (c.textContent || "").trim();
    };
    const numeric = rows.length > 0 && rows.every((r) => { const v = key(r); return v !== "" && !isNaN(parseFloat(v)); });
    const sign = dir === "ascending" ? 1 : -1;
    rows.sort((a, b) => {
      const ka = key(a), kb = key(b);
      if (numeric) return (parseFloat(ka) - parseFloat(kb)) * sign;
      return String(ka).localeCompare(String(kb), undefined, { sensitivity: "base", numeric: true }) * sign;
    });
    head.forEach((r) => tbody.appendChild(r));
    rows.forEach((r) => tbody.appendChild(r));
    ctrl.forEach((r) => tbody.appendChild(r));
  };
  document.addEventListener("click", onClick);
  return () => document.removeEventListener("click", onClick);
}

// ---- hover tooltips for [data-tip] ----
// The monolith drew these itself: a single #tip div at <body> level, positioned at the
// cursor and clamped to the viewport (templates/report.j2 ~1655-1670). The React port
// carried across the ATTRIBUTE on 36 elements and even the #tip CSS rule, but not the
// code that fills it — so `cursor:help` and the dotted underline promised a tooltip
// that never appeared. Same shape as the dead "Show all" button: markup and styling
// ported, behaviour left behind, and invisible to a screenshot gate because a tooltip
// only exists while the pointer is over the element.
export function installTips(): () => void {
  let tip: HTMLDivElement | null = null;

  const el = () => {
    if (!tip) {
      tip = document.createElement("div");
      tip.id = "tip";                    // styled by report.css's existing #tip rule
      document.body.appendChild(tip);
    } else if (tip.parentElement !== document.body) {
      // a route change can drop the node; re-attach rather than leaking a second one
      document.body.appendChild(tip);
    }
    return tip;
  };

  const move = (e: MouseEvent) => {
    const t = el();
    let x = e.clientX + 12;
    let y = e.clientY + 14;
    const w = t.offsetWidth, h = t.offsetHeight;
    if (x + w > window.innerWidth - 8) x = window.innerWidth - w - 8;
    if (y + h > window.innerHeight - 8) y = e.clientY - h - 12;
    t.style.left = `${x}px`;
    t.style.top = `${y}px`;
  };

  const onOver = (e: MouseEvent) => {
    const target = (e.target as HTMLElement)?.closest?.("[data-tip]") as HTMLElement | null;
    if (!target) return;
    const v = target.getAttribute("data-tip");
    if (!v) return;                      // an empty data-tip must not flash an empty box
    const t = el();
    t.textContent = v;
    t.style.display = "block";
    move(e);
  };
  const onMove = (e: MouseEvent) => {
    if (tip && tip.style.display === "block") move(e);
  };
  const onOut = (e: MouseEvent) => {
    if ((e.target as HTMLElement)?.closest?.("[data-tip]") && tip) tip.style.display = "none";
  };

  document.addEventListener("mouseover", onOver);
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseout", onOut);
  return () => {
    document.removeEventListener("mouseover", onOver);
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseout", onOut);
    if (tip && tip.parentNode) tip.parentNode.removeChild(tip);
    tip = null;
  };
}
