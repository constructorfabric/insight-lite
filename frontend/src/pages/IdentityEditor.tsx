// /identity — resolve who's who (companies, duplicate merges, bots), migrated to
// React (Manage migration). Fetches GET /api/manage/identity.json and reproduces
// templates/editors/identity.html's markup + classes (see ../styles/identity.css).
// Behaviours ported 1:1 from the page's inline JS: search + filter chips, the
// grouped attention list, per-person detail editor (name/company/type/emails/
// aliases/merges/contact handles), bulk company-assign / mark-bot, and Save → POST
// /api/people-yaml as JSON (optimistic-concurrency header + file-download fallback).
//
// Faithful to the legacy mutable-model + explicit-render pattern: the roster/state
// live in refs (the legacy `roster`/`state` objects), and each place the legacy
// called render() we bump a reducer (`force`). setname mutates without a re-render,
// exactly like the legacy handler, so the name field keeps focus while typing.
//
// SSR-safe: no top-level window/document access — only inside effects / handlers.
import { useEffect, useLayoutEffect, useReducer, useRef, useState } from "react";
import { token } from "../lib/tokens";

type Person = {
  name: string;
  company: string;
  emails: string[];
  aliases: string[];
  discord?: string;
  telegram?: string;
  is_bot: boolean | null;
  commits: number;
  is_member: boolean;
  identity_confidence: string;
  identity_evidence: string[];
  gh: { name?: string; company?: string; bio?: string };
};
type Item = { login: string; p: Person; detected: boolean; bot?: any };

export default function IdentityEditor() {
  const [loaded, setLoaded] = useState(false);
  const D = useRef<any>(null);
  const roster = useRef<Record<string, Person>>({});
  const companies = useRef<string[]>([]);
  const coColors = useRef<Record<string, string>>({});
  const dbots = useRef<any[]>([]);
  const st = useRef<{ filter: string; selected: Set<string>; active: string | null }>({
    filter: "attn",
    selected: new Set(),
    active: null,
  });
  const query = useRef("");
  const [statusMsg, setStatusState] = useState<{ text: string; cls: string }>({ text: "", cls: "" });
  const statusTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const toolbarRef = useRef<HTMLDivElement>(null);
  const detailWrapRef = useRef<HTMLDivElement>(null);
  const aeRef = useRef<HTMLInputElement>(null);
  const mergeRef = useRef<HTMLInputElement>(null);

  const [, force] = useReducer((x: number) => x + 1, 0);
  const render = force;

  useEffect(() => {
    let cancelled = false;
    fetch("/api/manage/identity.json")
      .then((r) => r.json())
      .then((d) => {
        if (cancelled || !d.ok) return;
        D.current = d;
        const rr: Record<string, Person> = {};
        d.people.forEach((p: any) => {
          rr[p.login] = {
            name: p.name || "",
            company: p.company,
            emails: p.emails.slice(),
            aliases: (p.aliases || []).slice(),
            discord: p.discord || "",
            telegram: p.telegram || "",
            is_bot: p.is_bot === undefined ? null : p.is_bot,
            commits: p.commits,
            is_member: p.is_member,
            identity_confidence: p.identity_confidence || "unknown",
            identity_evidence: p.identity_evidence || [],
            gh: p.gh || {},
          };
        });
        roster.current = rr;
        companies.current = d.companies.slice();
        coColors.current = d.company_colors || {};
        dbots.current = (d.bots || {}).rows || [];
        setLoaded(true);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // ---- derived helpers (mirror the legacy pure functions) -------------------
  const coColor = (co: string) => coColors.current[co] || token["company-empty"];
  const isBot = (p: Person) => p.is_bot === true;
  const initials = (p: { name?: string; login?: string }) =>
    ((p.name || p.login || "?") + "").trim().slice(0, 2).toUpperCase() || "?";
  const confClass = (c: string) => {
    c = (c || "").split(" ")[0];
    if (["manual", "verified", "pr"].includes(c)) return "conf-good";
    if (["github", "name", "email"].includes(c)) return "conf-mid";
    return "conf-low";
  };
  const lowConf = (c: string) => confClass(c) === "conf-low";

  function dupPartner(l: string): { login: string; score: number } | null {
    let best: { login: string; score: number } | null = null;
    (D.current?.suggestions || []).forEach((s: any) => {
      let o: string | null = null;
      if (s.a === l && roster.current[s.b]) o = s.b;
      else if (s.b === l && roster.current[s.a]) o = s.a;
      if (o && (!best || s.score > best.score)) best = { login: o, score: s.score };
    });
    return best;
  }
  function needsAttention(l: string) {
    const p = roster.current[l];
    if (!p || isBot(p)) return false;
    return p.company === "Other" || !!dupPartner(l) || lowConf(p.identity_confidence);
  }
  function items(): Item[] {
    const out: Item[] = Object.keys(roster.current).map((l) => ({ login: l, p: roster.current[l], detected: false }));
    dbots.current.forEach((b: any) => {
      if (!roster.current[b.login])
        out.push({
          login: b.login,
          detected: true,
          bot: b,
          p: {
            name: "",
            company: "Other",
            emails: [],
            aliases: [],
            discord: "",
            telegram: "",
            is_bot: true,
            commits: b.commits || 0,
            is_member: false,
            identity_confidence: "",
            identity_evidence: [b.kind || "detected bot"],
            gh: {},
          },
        });
    });
    return out;
  }
  function matches(it: Item) {
    const q = (query.current || "").toLowerCase().trim();
    const p = it.p;
    if (q) {
      const hay = (it.login + " " + (p.name || "") + " " + p.company + " " + p.emails.join(" ")
        + " " + (p.discord || "") + " " + (p.telegram || "")).toLowerCase();
      if (!q.split(/\s+/).every((t) => hay.includes(t))) return false;
    }
    if (st.current.filter === "attn") return needsAttention(it.login);
    if (st.current.filter === "other") return p.company === "Other" && !isBot(p);
    if (st.current.filter === "ext") return !p.is_member && !isBot(p);
    if (st.current.filter === "bots") return isBot(p);
    return true;
  }
  function counts() {
    const all = items();
    return {
      all: all.length,
      attn: Object.keys(roster.current).filter(needsAttention).length,
      other: all.filter((it) => it.p.company === "Other" && !isBot(it.p)).length,
      ext: all.filter((it) => !it.p.is_member && !isBot(it.p)).length,
      bots: all.filter((it) => isBot(it.p)).length,
    };
  }

  // ---- mutations ------------------------------------------------------------
  function status(t: string, cls = "") {
    setStatusState({ text: t, cls });
    if (statusTimer.current) clearTimeout(statusTimer.current);
    statusTimer.current = setTimeout(() => setStatusState({ text: "", cls: "" }), 4500);
  }
  function pick(l: string) {
    st.current.active = l;
    render();
  }
  function toggleSel(l: string) {
    if (st.current.selected.has(l)) st.current.selected.delete(l);
    else st.current.selected.add(l);
    render();
  }
  function bulkCompany(co: string) {
    if (!co) return;
    st.current.selected.forEach((l) => {
      const p = roster.current[l];
      if (p) {
        p.company = co;
        if (co !== "Other" && lowConf(p.identity_confidence)) p.identity_confidence = "manual";
      }
    });
    st.current.selected.clear();
    render();
    status("Assigned company to selection");
  }
  function bulkBot() {
    st.current.selected.forEach((l) => {
      if (roster.current[l]) roster.current[l].is_bot = true;
    });
    st.current.selected.clear();
    render();
    status("Marked selection as bots");
  }
  function findOwner(email: string) {
    for (const l in roster.current) if (roster.current[l].emails.includes(email)) return l;
    return null;
  }
  function addEmail(l: string) {
    const inp = aeRef.current;
    if (!inp) return;
    const e = (inp.value || "").trim().toLowerCase();
    if (!e) return;
    const owner = findOwner(e);
    if (owner && owner !== l) {
      if (!window.confirm(e + " already belongs to " + owner + ". Move it to " + l + "?")) return;
      roster.current[owner].emails = roster.current[owner].emails.filter((x) => x !== e);
    }
    if (!roster.current[l].emails.includes(e)) {
      roster.current[l].emails.push(e);
      roster.current[l].emails.sort();
    }
    if (aeRef.current) aeRef.current.value = "";
    render();
    if (aeRef.current) aeRef.current.focus();
    status("added " + e + " → " + l);
  }
  function rmEmail(l: string, e: string) {
    roster.current[l].emails = roster.current[l].emails.filter((x) => x !== e);
    render();
  }
  function rmAlias(l: string, a: string) {
    roster.current[l].aliases = (roster.current[l].aliases || []).filter((x) => x !== a);
    render();
  }
  // Contact handles. Stored verbatim as typed (minus a leading @, which everyone
  // pastes); the server normalises and validates on save, so the editor does not
  // second-guess a handle while it is being typed.
  function setcontact(l: string, field: "discord" | "telegram", v: string) {
    if (roster.current[l]) roster.current[l][field] = v.trim().replace(/^@+/, "");
  }

  function setname(l: string, v: string) {
    if (roster.current[l]) roster.current[l].name = v.trim();
  }
  function setco(l: string, v: string) {
    if (v === "__new") {
      const nv = window.prompt("Company name:", "");
      if (!nv) {
        render();
        return;
      }
      v = nv;
      if (!companies.current.includes(v)) companies.current.push(v);
    }
    roster.current[l].company = v;
    render();
  }
  function useGhCo(l: string) {
    const g = roster.current[l].gh || {};
    if (!g.company) return;
    if (!companies.current.includes(g.company)) companies.current.push(g.company);
    roster.current[l].company = g.company;
    render();
    status(`Set ${l} company → ${g.company}`);
  }
  function useGhName(l: string) {
    const g = roster.current[l].gh || {};
    if (!g.name) return;
    roster.current[l].name = g.name;
    render();
    status(`Set ${l} name → ${g.name}`);
  }
  function setBot(l: string, v: boolean) {
    if (roster.current[l]) roster.current[l].is_bot = v;
    render();
  }
  function doMerge(target: string, src: string) {
    if (!roster.current[target] || !roster.current[src] || target === src) return;
    const t = roster.current[target],
      s = roster.current[src];
    t.emails = Array.from(new Set(t.emails.concat(s.emails))).sort();
    t.aliases = Array.from(new Set((t.aliases || []).concat([src], s.aliases || []))).sort();
    t.commits = (t.commits || 0) + (s.commits || 0);
    delete roster.current[src];
    if (st.current.active === src) st.current.active = target;
    render();
    status(`Merged ${src} → ${target}`);
  }
  function mergePick(l: string) {
    const src = (mergeRef.current?.value || "").trim();
    if (!src) return;
    if (!roster.current[src]) {
      status("No such login: " + src);
      return;
    }
    if (src === l) {
      status("Pick a different account");
      return;
    }
    if ((roster.current[src].commits || 0) > (roster.current[l].commits || 0)) doMerge(src, l);
    else doMerge(l, src);
  }
  function dismissDup(a: string, b: string) {
    if (D.current)
      D.current.suggestions = (D.current.suggestions || []).filter(
        (s: any) => !((s.a === a && s.b === b) || (s.a === b && s.b === a)),
      );
    render();
  }
  function unbot(login: string) {
    const b = dbots.current.find((x: any) => x.login === login) || {};
    roster.current[login] = {
      name: "",
      company: "Other",
      emails: [],
      aliases: [],
      discord: "",
      telegram: "",
      is_bot: false,
      commits: b.commits || 0,
      is_member: false,
      identity_confidence: "manual",
      identity_evidence: ["restored: not a bot"],
      gh: {},
    };
    st.current.active = login;
    render();
    status(login + " → will be restored as a human on Save");
  }

  // The save payload: the roster as data, serialised by JSON.stringify. This used to
  // be a hand-built people.yaml (`out += "    company: " + p.company`), which silently
  // corrupted anything the YAML scanner treats as syntax — a company of "Acme #1" was
  // stored as "Acme" ('#' opened a comment), "yes" as the boolean True, and "Foo: bar"
  // failed the whole save. Every field is a value here, never a fragment of a document.
  function savePayload() {
    const people: Record<string, unknown> = {};
    Object.keys(roster.current).forEach((l) => {
      const p = roster.current[l];
      people[l] = {
        name: p.name || "",
        company: p.company,
        emails: p.emails.slice().sort(),
        aliases: (p.aliases || []).slice().sort(),
        discord: p.discord || "",
        telegram: p.telegram || "",
        is_bot: p.is_bot,
      };
    });
    return { people };
  }
  async function save() {
    const text = JSON.stringify(savePayload());
    if (location.protocol === "http:" || location.protocol === "https:") {
      try {
        const r = await fetch(location.origin + "/api/people-yaml", {
          method: "POST",
          headers: { "Content-Type": "application/json;charset=utf-8", "X-Override-Version": String(D.current?.version || "") },
          body: text,
        });
        if (r.status === 409) {
          status(
            "⚠ Another session saved since you opened this page. Reload to avoid overwriting (your edits are NOT saved).",
            "warn",
          );
          return;
        }
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.error || "save failed");
        if (j.version !== undefined && D.current) D.current.version = j.version;
        if (j.applied)
          status("Saved ✓ — companies, bots and merges apply now; email changes take effect on the next Collect.");
        else
          status(
            "Saved ✓ — but auto-apply failed" + (j.apply_error ? ": " + j.apply_error : "") + ". Run a rebuild to apply.",
            "warn",
          );
        return;
      } catch (e: any) {
        status("Server save failed: " + e.message, "bad");
        return;
      }
    }
    // file:// — there is no server to save to. Hand the edits back as the exact body
    // the portal accepts, so nothing is stranded. (The old fallback wrote people.yaml
    // in place; that only worked while the editor emitted YAML, and it is the
    // "drop a file next to the DB and hope" path the DB-as-truth model moved away from.)
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    a.download = "people.json";
    a.click();
    status("Downloaded people.json — POST it to /api/people-yaml on the portal to apply");
  }

  // Pin the detail panel just below the sticky toolbar — measured, since the
  // toolbar height changes when the filter chips wrap (narrow windows).
  useLayoutEffect(() => {
    function syncSticky() {
      const tb = toolbarRef.current,
        dw = detailWrapRef.current;
      if (tb && dw) dw.style.top = tb.offsetHeight + 14 + "px";
    }
    syncSticky();
    window.addEventListener("resize", syncSticky);
    return () => window.removeEventListener("resize", syncSticky);
  });

  // ---- render helpers -------------------------------------------------------
  const githubLink = (login: string) => (
    <a className="gh" href={`https://github.com/${encodeURIComponent(login)}`} target="_blank" rel="noopener noreferrer">
      {login}
    </a>
  );
  const coPill = (co: string) => (
    <span className="co-pill">
      <span className="co-dot" style={{ background: coColor(co) }}></span>
      {co}
    </span>
  );

  function rowNode(it: Item) {
    const l = it.login,
      p = it.p,
      sel = st.current.selected.has(l),
      active = st.current.active === l,
      dp = dupPartner(l);
    const cls = ["row", active ? "sel" : "", !isBot(p) && dp ? "dup" : !isBot(p) && needsAttention(l) ? "attn" : ""].join(" ");
    const tags: React.ReactNode[] = [];
    if (isBot(p)) tags.push(<span className="tag bot" key="bot">bot</span>);
    else if (!p.is_member) tags.push(<span className="tag ext" key="ext">external</span>);
    if (!isBot(p) && dp) tags.push(<span className="tag dup" key="dup">duplicate?</span>);
    else if (!isBot(p) && p.company === "Other") tags.push(<span className="tag other" key="other">no company</span>);
    return (
      <div className={cls} data-login={l} key={l} onClick={() => pick(l)}>
        <div
          className={"cbox " + (sel ? "on" : "")}
          onClick={(e) => {
            e.stopPropagation();
            toggleSel(l);
          }}
        ></div>
        <div className="who">
          <div className="top">
            <span className="nm">{p.name || l}</span>
            <span className="lg">{l}</span>
            {tags}
          </div>
          <div className="meta">
            {coPill(p.company)}
            <span>·</span>
            <span>{p.emails[0] || "—"}</span>
          </div>
        </div>
        <div className="rt">
          <div className="num">{(p.commits || 0).toLocaleString()}</div>
          <div className="k">commits</div>
        </div>
      </div>
    );
  }

  function listNode() {
    const rows = items().filter(matches);
    if (st.current.filter === "attn") {
      const dups = rows.filter((it) => dupPartner(it.login));
      const rest = rows.filter((it) => !dupPartner(it.login));
      dups.sort((a, b) => (b.p.commits || 0) - (a.p.commits || 0));
      rest.sort((a, b) => (b.p.commits || 0) - (a.p.commits || 0));
      if (!rows.length)
        return <div className="empty">🎉 Nothing needs attention — every identity is resolved.</div>;
      return (
        <>
          {dups.length ? (
            <>
              <div className="grouphead">
                <span className="dot" style={{ background: "var(--dup)" }}></span>Likely duplicates{" "}
                <span className="c">{dups.length} to review</span>
              </div>
              <div className="list">{dups.map(rowNode)}</div>
            </>
          ) : null}
          {rest.length ? (
            <>
              <div className="grouphead">
                <span className="dot" style={{ background: "var(--warn)" }}></span>Missing / uncertain{" "}
                <span className="c">{rest.length} to classify</span>
              </div>
              <div className="list">{rest.map(rowNode)}</div>
            </>
          ) : null}
        </>
      );
    }
    rows.sort((a, b) => (b.p.commits || 0) - (a.p.commits || 0));
    if (!rows.length) return <div className="empty">No matches.</div>;
    return <div className="list">{rows.map(rowNode)}</div>;
  }

  function detailNode() {
    const active = st.current.active;
    if (active == null || (!roster.current[active] && !dbots.current.find((b: any) => b.login === active)))
      return <div className="empty">Select a person to edit their company, emails, aliases, and merges.</div>;
    const l = active;
    if (!roster.current[l]) {
      const b = dbots.current.find((x: any) => x.login === l) || {};
      return (
        <>
          <div className="d-head">
            <div className="avatar">{initials({ login: l })}</div>
            <div className="d-id">
              <div className="nm">{l}</div>
              <div className="lg">detected bot · {b.kind || "automation"}</div>
            </div>
          </div>
          <div className="d-body">
            <div className="field">
              <label>Activity</label>
              <div className="sub" style={{ margin: 0 }}>
                {(b.commits || 0).toLocaleString()} commits · {b.reviews_given || 0} reviews ·{" "}
                {(b.repos || []).slice(0, 4).join(", ") || "—"}
              </div>
            </div>
            <div className="field">
              <label>Type</label>
              <div className="seg">
                <button className="on" onClick={() => unbot(l)}>
                  Human
                </button>
                <button>Bot</button>
              </div>
              <div className="ghint">
                Detected from <code>bot_logins</code>. “Human” restores it as a person (excluded no more) on Save.
              </div>
            </div>
          </div>
        </>
      );
    }
    const p = roster.current[l],
      dp = dupPartner(l),
      dpp = dp ? roster.current[dp.login] : null;
    return (
      <>
        <div className="d-head">
          <div className="avatar">{initials({ name: p.name, login: l })}</div>
          <div className="d-id">
            <div className="nm">{p.name || l}</div>
            <div className="lg">
              {githubLink(l)}
              {p.is_member ? "" : " · external"}
            </div>
          </div>
        </div>
        <div className="d-body">
          {dpp ? (
            <div className="dup-card">
              <div className="h">⇄ Possible duplicate</div>
              <div className="body">
                <b>{p.name || l}</b> also appears as <b>{githubLink(dp!.login)}</b>{" "}
                <span className="sub" style={{ margin: 0 }}>
                  ({dpp.emails[0] || "no email"})
                </span>
                .
              </div>
              <div className="prev">
                Merge folds <code>{dp!.login}</code> into <code>{l}</code>: combines{" "}
                <b>{((p.commits || 0) + (dpp.commits || 0)).toLocaleString()}</b> commits and{" "}
                {p.emails.length + dpp.emails.length} email(s) under one identity, and survives the next collect.
              </div>
              <div className="acts">
                <button className="primary" onClick={() => doMerge(l, dp!.login)}>
                  Merge these two
                </button>
                <button onClick={() => dismissDup(l, dp!.login)}>Not the same</button>
              </div>
            </div>
          ) : null}

          <div className="field">
            <label>Name</label>
            <input
              className="nm"
              key={l + "-nm"}
              defaultValue={p.name || ""}
              placeholder="name…"
              onChange={(e) => setname(l, e.target.value)}
            />
            {p.gh && p.gh.name && p.gh.name !== (p.name || "") ? (
              <div className="ghint">
                GitHub: <b>{p.gh.name}</b> <button onClick={() => useGhName(l)}>use</button>
              </div>
            ) : null}
          </div>

          <div className="field">
            <label>Company</label>
            <select
              className="co"
              value={companies.current.includes(p.company) ? p.company : "__new"}
              onChange={(e) => setco(l, e.target.value)}
            >
              {companies.current.map((c) => (
                <option value={c} key={c}>
                  {c}
                </option>
              ))}
              <option value="__new">+ custom…</option>
            </select>
            {p.gh && p.gh.company && p.gh.company !== p.company ? (
              <div className="ghint">
                GitHub says: <b>{p.gh.company}</b> <button onClick={() => useGhCo(l)}>use</button>
              </div>
            ) : null}
          </div>

          <div className="field">
            <label>Discord</label>
            <input
              className="nm"
              key={l + "-dc"}
              defaultValue={p.discord || ""}
              placeholder="username…"
              onChange={(e) => setcontact(l, "discord", e.target.value)}
            />
          </div>

          <div className="field">
            <label>Telegram</label>
            <input
              className="nm"
              key={l + "-tg"}
              defaultValue={p.telegram || ""}
              placeholder="username (without @)…"
              onChange={(e) => setcontact(l, "telegram", e.target.value)}
            />
          </div>

          <div className="field">
            <label>Type</label>
            <div className="seg">
              <button className={isBot(p) ? "" : "on"} onClick={() => setBot(l, false)}>
                Human
              </button>
              <button className={isBot(p) ? "on" : ""} onClick={() => setBot(l, true)}>
                Bot
              </button>
            </div>
          </div>

          <div className="field">
            <label>Emails &amp; aliases</label>
            <div className="emails">
              {p.emails.length
                ? p.emails.map((e) => (
                    <div className="erow" key={"e" + e}>
                      <span className="x" onClick={() => rmEmail(l, e)}>
                        ×
                      </span>{" "}
                      <span>{e}</span>
                    </div>
                  ))
                : (
                    <div className="sub" style={{ margin: 0 }}>
                      no commit-email
                    </div>
                  )}
              {(p.aliases || []).map((a) => (
                <div className="erow alias" key={"a" + a}>
                  <span className="x" onClick={() => rmAlias(l, a)}>
                    ×
                  </span>{" "}
                  <span className="kind">alias</span> {a}
                </div>
              ))}
              <div className="addrow">
                <input
                  className="ae"
                  ref={aeRef}
                  key={l + "-ae"}
                  placeholder="add email or alias…"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") addEmail(l);
                  }}
                />
                <button onClick={() => addEmail(l)}>Add</button>
              </div>
            </div>
          </div>

          <div className="field">
            <label>Merge another account into this one</label>
            <div className="addrow">
              <input
                className="mergepick"
                ref={mergeRef}
                key={l + "-mp"}
                list="all-logins"
                placeholder="type a login…"
                onKeyDown={(e) => {
                  if (e.key === "Enter") mergePick(l);
                }}
              />
              <button onClick={() => mergePick(l)}>Merge</button>
            </div>
          </div>

          <div className="field">
            <label>Identity confidence</label>
            <span className={"confpill " + confClass(p.identity_confidence)}>{p.identity_confidence || "unknown"}</span>
            <div className="evid">{(p.identity_evidence || []).join(" · ") || "—"}</div>
          </div>
        </div>
        <datalist id="all-logins">
          {Object.keys(roster.current)
            .filter((x) => x !== l)
            .map((x) => (
              <option value={x} key={x}></option>
            ))}
        </datalist>
      </>
    );
  }

  const c = loaded ? counts() : { all: 0, attn: 0, other: 0, ext: 0, bots: 0 };
  const chipDefs: [string, string, number, boolean][] = [
    ["attn", "Needs attention", c.attn, true],
    ["all", "All", c.all, false],
    ["other", "No company", c.other, false],
    ["ext", "External", c.ext, false],
    ["bots", "Bots", c.bots, false],
  ];
  const selCount = st.current.selected.size;

  return (
    <>
      <div className="head">
        <div>
          <p className="eyebrow">Constructor Insight</p>
          <h1>Identity &amp; company</h1>
          <p className="sub">
            Resolve who's who: assign companies, merge duplicate accounts, mark bots. Work the{" "}
            <b>Needs attention</b> queue first — everything else is already settled. <b>Save &amp; apply</b> writes to
            the database and the report updates immediately.
          </p>
        </div>
        <div className="savebar">
          <span id="status" className={statusMsg.cls}>
            {statusMsg.text}
          </span>
          <button className="primary" onClick={save}>
            Save &amp; apply
          </button>
        </div>
      </div>

      <div className="toolbar" ref={toolbarRef}>
        <div className="search">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <circle cx="11" cy="11" r="7" />
            <path d="m21 21-4.3-4.3" />
          </svg>
          <input
            id="q"
            placeholder="Search login, name, email, company…"
            onChange={(e) => {
              query.current = e.target.value;
              render();
            }}
          />
        </div>
        <div className="chips" id="chips">
          {chipDefs.map(([k, lbl, n, warn]) => (
            <button
              className={"chip " + (st.current.filter === k ? "on" : "") + " " + (warn ? "warn" : "")}
              key={k}
              onClick={() => {
                st.current.filter = k;
                st.current.selected.clear();
                render();
              }}
            >
              {lbl}
              <span className="n">{n}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="layout">
        <div>
          <div id="list">{listNode()}</div>
          <p className="legendnote">
            <b>Needs attention</b> = no company, a likely duplicate, or a low-confidence identity. Bots are folded in
            here (toggle Human/Bot in the panel), not a separate table. Tick rows to assign a company or mark bots in
            bulk.
          </p>
        </div>
        <div className="detail-wrap" ref={detailWrapRef}>
          <div className="detail" id="detail">
            {detailNode()}
          </div>
        </div>
      </div>

      <div className={"bulk " + (selCount ? "" : "hidden")} id="bulk">
        <span id="bulk-n">{selCount} selected</span>
        <select
          id="bulk-co"
          defaultValue=""
          onChange={(e) => {
            const v = e.target.value;
            e.target.value = "";
            bulkCompany(v);
          }}
        >
          <option value="">Assign company…</option>
          {companies.current.map((cc) => (
            <option value={cc} key={cc}>
              {cc}
            </option>
          ))}
        </select>
        <button className="bb" onClick={bulkBot}>
          Mark as bot
        </button>
        <span className="clear" onClick={() => { st.current.selected.clear(); render(); }}>
          Clear
        </span>
      </div>
    </>
  );
}
