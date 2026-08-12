// /person — the sixth migrated report view (see
// docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P6).
// Reproduces the monolith's "person" mode-section class-for-class against
// templates/report.j2 (the `<div class="mode-section" data-modes="person">`
// shell at lines ~637-659: h2 + card + person picker + #person-view) and the
// person_dashboard() / panel_person_weekly() macros
// (templates/panels/05_people.j2) — driven by GET /api/report/person
// (render.person_json) instead of the /api/person HTML fragment. Person has an
// SVG gauge (Developer score) but NO Vega charts. SSR-safe: no window/document
// access outside hooks/effects.
import { Fragment, useEffect, useMemo, useState } from "react";
import FilterBar from "../components/FilterBar";
import KpiTile, { type KpiTileData } from "../components/KpiTile";
import { BarRow, SplitBar, Legend, StatRow, HeatStrip, PersonScore, type Segment, type ScoreBlock } from "../widgets";
import { useReportData, useReportQuery, setReportQuery } from "../hooks/useReportData";
import { fmtNum, jr } from "../lib/format";
import Loading from "../components/Loading";

// ---- types (mirror render.person_json's payload) ---------------------------
type PersonOption = {
  login: string; name: string; company: string; emails: string;
  // All-time commit count, from the roster table (backend/render.py). Only used to
  // rank the empty state's shortcuts, and labelled all-time where it is shown.
  commits?: number;
};

type WeeklyCell = { commits: number; add: number; del: number } | null;
type WeeklyRow = { week: string; week_end: string; cells: WeeklyCell[]; issues: number };
type WeeklyCol = { repo: string; name: string };
type WeeklyTot = { commits: number; add: number; del: number };
type Weekly = {
  login: string; columns: WeeklyCol[]; rows: WeeklyRow[]; col_totals: WeeklyTot[];
  grand: { commits: number; add: number; del: number; issues: number };
  since: string; until: string;
};

type RepoRow = { repo: string; name: string; commits: number; add: number; commitsText: string; addText: string };
type RepoType = { name: string; color: string; commits: number; pctText: string };
type ElementRow = { element: string; commits: number; loc: number; commitsText: string; locText: string };
type WorkTypeRow = { type: string; count: number; countText: string };
type Split2 = { empty: boolean; a?: number; b?: number; pa?: string; pb?: string; aNum?: string; bNum?: string };

type Dashboard = {
  empty: boolean;
  login: string;
  header?: {
    login: string; name: string; company: string | null; isMember: boolean;
    identityConfidence: string | null; identityEvidence: string | null;
    rank: number | null; nPeople: number | null; emails: string;
  };
  ghProfile?: { name?: string; company?: string; location?: string; bio?: string } | null;
  kpis?: KpiTileData[];
  heat?: { week: string; commits: number; issues: number }[];
  weekly?: Weekly;
  repos?: RepoRow[];
  repoTypes?: { total: number; types: RepoType[] } | null;
  codeSpecs?: Split2;
  elements?: ElementRow[] | null;
  workType?: WorkTypeRow[] | null;
  impact?: {
    survHuman: string; survHumanRaw: number; survAi: string; survAiRaw: number; survSpec: string;
    survAlive: number; reviews: number; approvals: number; mergedPrs: number;
    mergeRateText: string; ttmText: string; aiCommits: number; cptLines: string; commitsText: string;
  };
  score?: ScoreBlock | null;
  // Why the score panel is missing, when it is (render._score_availability):
  // "error" = the server's builder raised (traceback is in the server log),
  // "no_data" = it built fine and this window has nobody ranked. `detail` is
  // finished prose from the server — never an exception message.
  scoreUnavailable?: { reason: "error" | "no_data"; detail: string } | null;
};

type PersonData = {
  meta: { org: string; allTime: boolean; windowStart: string; lookbackDays: number; generatedText: string };
  period: { preset: string; label: string; from: string | null; to: string | null };
  periodPresets: { key: string; label: string }[];
  scope: string;
  scopeTargets: { org?: string[]; element?: string[]; repo?: string[] };
  dataQuality: { apiRateLimited: boolean; apiReset: string | null };
  personOptions: PersonOption[];
  personCompanies: string[];
  person: string | null;
  dashboard: Dashboard | null;
};

const SUBJECT_HELP = [
  "The person is this page's subject, not a filter: it decides whose numbers you are "
    + "looking at, and it is forgotten as soon as you leave Person.",
  "Period and Scope, in the bar above, are the global filters — they follow you from "
    + "page to page and keep narrowing this page too.",
].join("\n");

// ---- person picker (searchable combo, mirrors report.j2's inline JS) -------
function PersonPicker({ options, companies, selected }: {
  options: PersonOption[]; companies: string[]; selected: string | null;
}) {
  const byLogin = useMemo(() => {
    const m = new Map<string, PersonOption>();
    for (const o of options) m.set(o.login, o);
    return m;
  }, [options]);
  const labelFor = (login: string | null) => {
    if (!login) return "";
    const p = byLogin.get(login);
    return p ? `${p.name || p.login} (${p.login})` : login;
  };

  const [company, setCompany] = useState("");
  const [text, setText] = useState(() => labelFor(selected));
  const [open, setOpen] = useState(false);

  // Keep the input's shown text synced to the URL-driven selection (deep link /
  // clear / back-forward), unless the user is actively browsing the list.
  useEffect(() => {
    if (!open) setText(labelFor(selected));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, options]);

  const searchable = useMemo(
    () => options.map((p) => ({
      ...p,
      _s: `${p.name || ""} ${p.login} ${p.emails || ""} ${p.company || ""}`.toLowerCase(),
    })),
    [options],
  );
  const terms = text.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const matches = searchable.filter((p) => {
    if (company && p.company !== company) return false;
    return terms.every((t) => p._s.indexOf(t) !== -1);
  });
  const shown = matches.slice(0, 60);

  const pick = (login: string) => {
    setOpen(false);
    setText(labelFor(login));
    setReportQuery({ person: login });
  };
  const reset = () => {
    setText("");
    setOpen(true);
    setReportQuery({ person: null });
  };

  return (
    <>
      {/* Search leads, company narrows it: the combo IS the control, the select only
          shrinks its candidate list — which is also the order the list itself reads
          in (`login · company`). Both are plain siblings of the PERSON label now,
          so the row is one flat flex line instead of a bar nested in a bar. */}
      <span className="person-combo">
        <input type="hidden" id="person-select" value={selected || ""} readOnly />
        <input
          type="text" id="person-search" className="person-search" autoComplete="off"
          placeholder="Search person — name, login, or email…" aria-label="Search person"
          value={text}
          onChange={(e) => { setText(e.target.value); setOpen(true); }}
          onFocus={(e) => { if (selected) e.target.select(); setOpen(true); }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && shown[0]) { e.preventDefault(); pick(shown[0].login); }
            else if (e.key === "Escape") setOpen(false);
          }}
          onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        />
        <button
          type="button" id="person-clear" className="person-clear" hidden={!selected}
          aria-label="Clear selection" title="Clear" onMouseDown={(e) => { e.preventDefault(); reset(); }}
        >
          ×
        </button>
        <div id="person-list" className="combo-list" hidden={!open}>
          {shown.length ? (
            <>
              {shown.map((p) => (
                <div
                  key={p.login} className="combo-item" data-login={p.login}
                  onMouseDown={(e) => { e.preventDefault(); pick(p.login); }}
                >
                  <b>{p.name || p.login}</b>{" "}
                  <span className="mut">{p.login}{p.company ? ` · ${p.company}` : ""}</span>
                  {p.emails && <div className="combo-em">{p.emails}</div>}
                </div>
              ))}
              {matches.length > shown.length && (
                <div className="combo-empty">+{matches.length - shown.length} more — keep typing</div>
              )}
            </>
          ) : (
            <div className="combo-empty">No match</div>
          )}
        </div>
      </span>
      <select
        id="person-company" className="person-select" value={company}
        aria-label="Narrow the search to one company"
        onChange={(e) => setCompany(e.target.value)}
      >
        <option value="">All companies</option>
        {companies.map((co) => <option key={co} value={co}>{co}</option>)}
      </select>
    </>
  );
}

// ---- dashboard building blocks (person_dashboard / panel_person_weekly) ----
function Split2Card({ s }: { s: Split2 }) {
  // split2() macro: code (var(--acc)) vs specs (var(--app)); labels fixed.
  if (s.empty) return <p className="hint">Nothing in the selected period.</p>;
  const a = s.a || 0, b = s.b || 0, tot = a + b;
  const segs: Segment[] = [
    { pct: Math.round((100 * a) / tot), color: "var(--acc)", tip: `code: ${s.aNum}`, label: "code", pctText: s.pa, value: s.aNum },
    { pct: Math.round((100 * b) / tot), color: "var(--app)", tip: `specs: ${s.bNum}`, label: "specs", pctText: s.pb, value: s.bNum },
  ];
  return (
    <>
      <SplitBar segments={segs} />
      <Legend segments={segs} />
    </>
  );
}

function drillCell(login: string, repo: string, from: string, to: string): Record<string, string> {
  return { "data-drill": "commit", "data-author": login, "data-scope": `repo:${repo}`, "data-from": from, "data-to": to };
}

// Shown until somebody is picked. The page cannot render anything without a
// subject, so the job here is to say what picking one gets you and then hand over
// the shortest possible route to picking — not to leave an empty card with an
// apology in it.
function PersonEmpty({ options }: { options: PersonOption[] }) {
  // Ranked by the all-time commit count carried in the picker payload, and captioned
  // as all-time: the options list itself is name-sorted, so its first few entries
  // would be an alphabetical accident dressed up as a leaderboard.
  const top = useMemo(
    () => options.filter((p) => (p.commits || 0) > 0)
      .sort((a, b) => (b.commits || 0) - (a.commits || 0))
      .slice(0, 8),
    [options],
  );
  return (
    <div className="pempty">
      <p className="pempty-lead">Pick a person to open their page.</p>
      <ul className="pempty-what">
        <li><b>Overview</b> — headline numbers for the selected period</li>
        <li><b>Activity</b> — weekly commits, lines and issues, per repository</li>
        <li><b>Composition</b> — where the effort went: code vs specs, platform vs app</li>
        <li><b>Impact</b> — surviving lines, review load, who they work with</li>
        <li><b>Score</b> — the composite, with every input it is built from</li>
      </ul>
      {top.length > 0 && (
        <>
          <p className="pempty-cap">Most commits all-time</p>
          <div className="pempty-chips">
            {top.map((p) => (
              <button
                key={p.login} type="button" className="pempty-chip"
                onClick={() => setReportQuery({ person: p.login })}
              >
                {p.name || p.login}
                <span className="mut">{p.commits}</span>
              </button>
            ))}
          </div>
        </>
      )}
      <p className="cnt">
        Everything on the page is computed on the fly for the person and period you choose.
      </p>
    </div>
  );
}

// "raw" is the load-bearing word. Somebody asked whether Lines here means LOC, docstrings or
// everything, and the honest answer is everything the diff touched — including lock files,
// generated and vendored code. The report's OTHER lines figure (the contribution KPIs) uses
// meaningful_additions, which drops those paths, so the two numbers disagree on purpose and
// nothing on screen used to say so.
const WEEKLY_NOTE =
  "weeks bucketed by author date · Lines = RAW git diff +/- (every changed line, "
  + "including generated and vendored files) · Issues = opened by author";

function WeeklyTable({ pw }: { pw: Weekly }) {
  if (!(pw.grand.commits || pw.grand.issues)) {
    return (
      <p className="hint">No commit or issue activity for <b>{pw.login}</b> in the selected period.</p>
    );
  }
  const cols = pw.columns;
  const lastOther = cols.length > 0 && cols[cols.length - 1].repo === "(other)";
  return (
    <>
      <div style={{ overflowX: "auto" }}>
        {/* dt opts into the shared click-to-sort (installSort in reportChromeEffects), which
            is already mounted on this route — verified by driving a probe table through it
            rather than trusting the router comment. Nothing new is written here: same
            th.sortable + data-sort idiom as the AI-tools table, so the arrows, the aria-sort
            and the asc/desc toggle come with it.

            This is a TIME SERIES, so the default stays chronological and Week is sortable
            precisely so chronology is recoverable — the shared sorter toggles asc/desc and has
            no third "unsorted" state, so without that click there would be no way back.

            Only the LEAF header row is sortable, plus Week. The group row's repo headers are
            colSpan=2, and the sorter maps a header's index within its own row onto cells[idx]
            of the body rows, so a spanning header would read the wrong column. */}
        {/* Keyed on the data, so new data REMOUNTS the table instead of patching it. That is
            not tidiness: installSort reorders rows with appendChild, and React reconciles its
            children against its own record of their order without looking at the DOM. Sort by
            commits, then change the period, and content follows the virtual list while
            positions keep the sorted order — the table came back as 07-27, 07-06, 08-03,
            07-13, 07-20, neither chronological nor sorted, with the header still claiming
            Commits=descending. A remount gives React a DOM that matches what it thinks it
            rendered, and resetting to chronological is the right answer for a new window
            anyway. The signature has to cover everything that changes the rows: the window,
            whose they are, and which repos became columns. */}
        <table className="grouped person-weekly dt"
               key={`${pw.login}|${pw.since}|${pw.until}|${cols.map((c) => c.repo).join(",")}`}>
          <thead>
            <tr className="grp">
              <th className="sortable">Week (starting)</th>
              {cols.map((c, i) => <th key={i} colSpan={2} className="g" data-tip={c.repo}>{c.name}</th>)}
              <th className="g">Issues</th>
            </tr>
            <tr>
              <th />
              {cols.map((_, i) => (
                <Fragment key={i}>
                  <th className="g sortable">Commits</th>
                  <th className="sortable" data-tip="sorts by lines added + deleted">Lines +/-</th>
                </Fragment>
              ))}
              <th className="g sortable">opened</th>
            </tr>
          </thead>
          <tbody>
            {pw.rows.map((row, ri) => (
              <tr key={ri}>
                {/* data-sort, because the sorter decides numeric-vs-text by parsing every
                    key: "2026-05-04" parses as 2026 for EVERY row, so the column would sort
                    into a silent no-op. 20260504 is numeric and monotonic. */}
                <td data-sort={row.week.replace(/-/g, "")}>{row.week}</td>
                {row.cells.map((cell, ci) => {
                  const col = cols[ci];
                  const dr = !!(cell && cell.commits && col.repo !== "(other)");
                  const attrs = dr ? drillCell(pw.login, col.repo, row.week, row.week_end) : {};
                  if (cell) {
                    return (
                      <Fragment key={ci}>
                        {/* Raw keys throughout: fmtNum is toLocaleString, so a rendered
                            "1,500" would parseFloat to 1 and sort every four-figure week as
                            if it were a one. The lines cell holds two numbers, and the one
                            worth ranking is the churn — the header says which. */}
                        <td className="g" data-sort={String(cell.commits)} {...attrs}>{fmtNum(cell.commits)}</td>
                        <td className="pw-lines" data-sort={String(cell.add + cell.del)} {...attrs}>
                          <span className="pw-add">+{fmtNum(cell.add)}</span>/<span className="pw-del">-{fmtNum(cell.del)}</span>
                        </td>
                      </Fragment>
                    );
                  }
                  return (
                    <Fragment key={ci}>
                      <td className="g mut" data-sort="0">–</td>
                      <td className="mut" data-sort="0">–</td>
                    </Fragment>
                  );
                })}
                <td
                  className="g" data-sort={String(row.issues)}
                  {...(row.issues
                    ? { "data-drill": "issue", "data-author": pw.login, "data-scope": "none", "data-from": row.week, "data-to": row.week_end }
                    : {})}
                >
                  {row.issues ? fmtNum(row.issues) : <span className="mut">–</span>}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="pw-tot">
              <td>Total</td>
              {pw.col_totals.map((t, i) => (
                <Fragment key={i}>
                  <td className="g">{fmtNum(t.commits)}</td>
                  <td className="pw-lines">
                    <span className="pw-add">+{fmtNum(t.add)}</span>/<span className="pw-del">-{fmtNum(t.del)}</span>
                  </td>
                </Fragment>
              ))}
              <td className="g">{fmtNum(pw.grand.issues)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="conc">
        <b>Grand total</b> — commits: {fmtNum(pw.grand.commits)}, lines{" "}
        <span className="pw-add">+{fmtNum(pw.grand.add)}</span>/<span className="pw-del">-{fmtNum(pw.grand.del)}</span>,
        {" "}issues opened: {fmtNum(pw.grand.issues)} · window {pw.since} → {pw.until}. Commits = git log on the
        cloned default branch (no merges), bucketed by author week; Issues = GitHub issues opened by
        this author. <b>Lines</b> = the <b>raw</b> <code>git diff</code> insertion and deletion counts:
        every line the commit changed, comments and docstrings included, and lock files, generated
        and vendored code with them. It is deliberately not the same figure as the LOC on the
        contribution KPIs, which counts only <i>meaningful</i> paths — that filter drops whole files
        (<code>node_modules</code>, <code>dist</code>, <code>*.lock</code>, minified and binary
        assets), never individual lines inside a source file. Top {cols.length} repos by commits
        {lastOther && <> (rest folded into <b>(other)</b>)</>}.
      </p>
    </>
  );
}

// Avatar that removes itself on load failure — mirrors the monolith's
// `<img ... onerror="this.remove()">` so a 404/blocked avatar collapses the
// header identically on both front-ends (rendering null == removed from DOM).
function Avatar({ login }: { login: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) return null;
  return (
    <img
      className="pav" src={`https://avatars.githubusercontent.com/${login}?s=120`} alt=""
      loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)}
    />
  );
}

// ---- the dashboard (person_dashboard macro) --------------------------------
function PersonDashboard({ d, view }: { d: Dashboard; view: string }) {
  if (d.empty) {
    return (
      <p className="hint">
        No activity for <b>{d.login}</b> in the selected period, and no lasting footprint on record.
      </p>
    );
  }
  const h = d.header!;
  const ghp = d.ghProfile;
  const heat = d.heat || [];
  const repos = d.repos || [];
  const rmx = Math.max(1, ...repos.map((r) => r.commits));
  const rt = d.repoTypes;
  const rtSegs: Segment[] = rt
    ? rt.types.map((t) => ({
        pct: jr((100 * t.commits) / rt.total, 1), color: t.color,
        tip: `${t.name}: ${t.commits}`, label: t.name, pctText: t.pctText,
      }))
    : [];
  const elements = d.elements;
  const emx = elements ? Math.max(1, ...elements.map((e) => e.commits)) : 1;
  const wt = d.workType;
  const wmx = wt ? Math.max(1, ...wt.map((w) => w.count)) : 1;
  const im = d.impact!;
  return (
    <>
      <div className="pcard phead">
        <Avatar login={h.login} />
        <div className="pid">
          <div className="pname">
            {h.name}{" "}
            <a className="mut" href={`https://github.com/${h.login}`} target="_blank" rel="noopener">@{h.login}</a>
          </div>
          <div className="pmeta">
            {h.company && <span className="chip">{h.company}</span>}
            <span className={`chip ${h.isMember ? "mem" : "ext"}`}>{h.isMember ? "member" : "external"}</span>
            {h.identityConfidence && (
              <span className="chip idc" {...(h.identityEvidence ? { "data-tip": h.identityEvidence } : {})}>
                {h.identityConfidence}
              </span>
            )}
            {h.rank && <span className="chip rk" data-tip="rank by commits in this period">#{h.rank} of {h.nPeople}</span>}
          </div>
          {h.emails && <div className="pemails">{h.emails}</div>}
        </div>
      </div>

      {view === "overview" && (
      <>
      {ghp && (
        <div className="pcard ghprof" data-tip="Straight from the person's GitHub profile — free text they wrote about themselves, not our resolved identity.">
          <div className="ghprof-h">GitHub profile <span className="mut">— as they state it, not our resolved identity</span></div>
          <div className="ghprof-rows">
            {ghp.name && <div><span className="ghl">Name</span>{ghp.name}</div>}
            {ghp.company && <div><span className="ghl">Company</span>{ghp.company}</div>}
            {ghp.location && <div><span className="ghl">Location</span>{ghp.location}</div>}
            {ghp.bio && <div className="ghbio"><span className="ghl">Bio</span>{ghp.bio}</div>}
          </div>
        </div>
      )}

      <div className="kpis pkpis">
        {(d.kpis || []).map((tile, i) => <KpiTile key={i} {...tile} />)}
      </div>
      </>
      )}

      {view === "activity" && (
        <>
          <HeatStrip heat={heat} />
          {d.weekly && (
            <>
              {/* Above the table, not under it: it defines what the columns mean
                  ("Lines = git diff +/-"), which you need before reading them, and
                  the table is long enough to scroll its own footer out of sight. */}
              <p className="cnt cnt-top">{WEEKLY_NOTE}</p>
              <WeeklyTable pw={d.weekly} />
            </>
          )}
        </>
      )}

      {view === "work" && (
      <div className="pgrid">
        <div className="pcard">
          <h4>Top repositories</h4>
          {repos.length ? repos.map((r, i) => (
            <BarRow
              key={i}
              drill={{ "data-drill": "commit", "data-author": h.login, "data-scope": `repo:${r.repo}` }}
              label={r.name}
              tip={r.repo}
              pct={Math.round((100 * r.commits) / rmx)}
              color="var(--acc)"
              value={<>{r.commitsText} · <span className="pw-add">+{r.addText}</span></>}
            />
          )) : <p className="hint">No commits in period.</p>}
        </div>
        <div className="pcard">
          <h4>Repo types</h4>
          {rt ? (
            <>
              <SplitBar segments={rtSegs} />
              <Legend segments={rtSegs} show={["pct"]} />
            </>
          ) : <p className="hint">No commits in period.</p>}
        </div>
        <div className="pcard">
          <h4>Code vs specs</h4>
          {d.codeSpecs && <Split2Card s={d.codeSpecs} />}
        </div>
        {elements && (
          <div className="pcard">
            <h4>By Element</h4>
            {elements.map((e, i) => (
              <BarRow
                key={i}
                drill={{ "data-drill": "commit", "data-author": h.login, "data-scope": `element:${e.element}` }}
                label={e.element}
                pct={Math.round((100 * e.commits) / emx)}
                color="var(--good)"
                value={<>{e.commitsText} · {e.locText} LOC</>}
              />
            ))}
          </div>
        )}
        {wt && (
          <div className="pcard">
            <h4>Work type — conventional commits</h4>
            {wt.map((w, i) => (
              <BarRow
                key={i}
                drill={{ "data-drill": "commit", "data-author": h.login, "data-commit-type": w.type, "data-scope": "none" }}
                label={w.type}
                pct={Math.round((100 * w.count) / wmx)}
                color="var(--warn)"
                value={w.countText}
              />
            ))}
          </div>
        )}
      </div>
      )}

      {view === "impact" && (
      <>
      <div className="pgrid">
        <div className="pcard">
          <h4>Code alive in the tree today</h4>
          <StatRow k="Human-written LOC" v={im.survHuman} tip="still present in HEAD, git blame" />
          <StatRow k="AI-assisted LOC" v={im.survAi} tip="surviving lines on AI-marked commits" />
          <StatRow k="Spec LOC" v={im.survSpec} tip="surviving lines in spec docs" />
          {im.survAlive > 0 && (
            <SplitBar
              style={{ marginTop: 8 }}
              segments={[
                { pct: Math.round((100 * im.survHumanRaw) / im.survAlive), color: "var(--good)", tip: "human" },
                { pct: Math.round((100 * im.survAiRaw) / im.survAlive), color: "var(--app)", tip: "AI-assisted" },
              ]}
            />
          )}
        </div>
        <div className="pcard">
          <h4>Review &amp; delivery</h4>
          <StatRow k="Reviews given" v={im.reviews} />
          <StatRow k="Approvals given" v={im.approvals} />
          <StatRow k="PRs merged" v={im.mergedPrs} />
          <StatRow k="Merge rate" v={im.mergeRateText} tip="merged / opened, all-time" />
          <StatRow k="Median time-to-merge" v={im.ttmText} />
        </div>
        <div className="pcard">
          <h4>AI-assisted work</h4>
          <StatRow k="AI-marked commits" v={im.aiCommits} tip="commits carrying an AI-tool marker" />
          <StatRow k="Marked LOC (blame)" v={im.cptLines} />
          <StatRow k="Commits (all-time)" v={im.commitsText} />
        </div>
      </div>
      <p className="conc">
        This view is all-time by nature and does not move with the period: surviving LOC
        is a git-blame of today's tree, reviews and merge stats are cumulative.
      </p>
      </>
      )}

      {view === "score" && (
      <>
      {/* Explicitly EXPERIMENTAL and not a performance rating — see the README's note
          on measuring people. Its own view rather than a block on a long page, so it
          is opened deliberately instead of met on the way past. */}
      {d.score && d.score.board && d.score.board.length > 0 ? (
        <PersonScore score={d.score} login={d.login} />
      ) : d.scoreUnavailable ? (
        // The panel used to just vanish — indistinguishable from "this person has
        // no score", whether the builder had crashed or the window was empty. One
        // line in its place says which, so a missing section is a statement instead
        // of an absence. Nothing is added when the score renders, so the pixel-gate
        // baseline for the working case is untouched.
        <p className="hint">Developer score unavailable — {d.scoreUnavailable.detail}.</p>
      ) : (
        <p className="hint">No score for this person in the selected period.</p>
      )}
      </>
      )}
    </>
  );
}

export default function Person() {
  const query = useReportQuery();
  const person = query.person || null;
  // Which part of this person to show. The sidebar's pane holds one link per view and
  // the route echoes it back as the active key, so this only has to pick a default —
  // and it must agree with server._nav_view's default, or the pane would highlight a
  // view the page is not rendering.
  const view = ["overview", "activity", "work", "impact", "score"].includes(query.view || "")
    ? (query.view as string)
    : "overview";
  const { data, error } = useReportData<PersonData>("person");

  // With no explicit ?person=, default the view to the signed-in viewer — parity
  // with the monolith's initFromURL (templates/report.j2), which resolved
  // /api/whoami and preset the picker. Runs ONCE on mount and only when the
  // selection is empty, so an explicit ?person= (or a later manual pick / Clear)
  // always wins. /api/whoami returns {ok, login:null} when the proxy identity maps
  // to no person (or there is no auth at all — e.g. the screenshot harness), in
  // which case nothing happens and the "pick a person" hint stays.
  useEffect(() => {
    if (query.person) return;
    let cancelled = false;
    fetch("/api/whoami")
      .then((r) => r.json())
      .then((w) => {
        if (cancelled || !w || !w.ok || !w.login) return;
        setReportQuery({ person: w.login });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error && !data) return <p className="hint" style={{ padding: 24 }}>Could not load the report ({error}).</p>;
  if (!data) return <Loading />;

  const periodLabel = data.period.label;

  return (
    <>

      <FilterBar
        periodPresets={data.periodPresets} period={data.period} scope={data.scope}
        scopeTargets={data.scopeTargets}
      />

      {/* Titled by the VIEW: "Per-person weekly activity" sat above Lasting impact and
          the score too, describing neither. The labels match the sidebar pane's, so the
          heading and the highlighted pane entry always read the same. */}
      <h2 id="person">
        {{
          overview: "Person overview",
          activity: "Activity by week",
          work: "Where the work goes",
          impact: "Lasting impact & collaboration",
          score: "Developer score",
        }[view] || "Person"}{" "}
        <span className="period-tag">{periodLabel}</span>
      </h2>
      {/* Not inside the card and not in the FilterBar: see .subjectbar in report.css —
          the person is this page's subject, not one of the two global filters. */}
      <div className="subjectbar">
        <span className="subj-lbl">Person</span>
        <PersonPicker options={data.personOptions} companies={data.personCompanies} selected={person} />
        <span className="subj-scope">
          this page only
          {/* Same split as the global controls' "?" (components/FilterBar): the button
              announces as a name, the prose is the description. */}
          <button
            type="button" className="legend-help" data-tip={SUBJECT_HELP}
            aria-label="What this page only means" aria-describedby="subject-help"
          >
            ?
          </button>
          <span id="subject-help" className="vh">{SUBJECT_HELP}</span>
        </span>
      </div>
      <div className="card">
        <div id="person-view">
          {data.dashboard ? (
            <PersonDashboard d={data.dashboard} view={view} />
          ) : (
            <PersonEmpty options={data.personOptions} />
          )}
        </div>
      </div>

      {/* Page footer, verbatim from the monolith (templates/report.j2 line ~1077) and
          duplicated across ten pages. It was already held back on the empty state, for the
          reason that "every term it defines belongs to a panel that is not rendered" — which
          is just as true of most VIEWS. Of the four things it defines, Person renders exactly
          one: Specs, on Composition, which has the "Code vs specs" card. Contributing to
          Fabric and Using-not-contributing-back describe org-report widgets this page does
          not have, and "Platform-vs-app below" points at nothing below on any Person view —
          the only "platform vs app" here is one word in the empty state's tab list. On Score
          it defined none of its nine terms (measured against the rendered view), which is
          what made it a wall of text for nothing. Narrowed to the view it speaks about.
          Trimming the text itself is a ten-page job and is not this change. */}
      {data.dashboard && view === "work" && (
      <p className="foot">
        Definitions — <b>Contributing to Fabric</b>: any commit, PR, spec edit, bug or user story in{" "}
        <b>any</b> repo of the org (apps included). <b>Using, not contributing back</b>: forked an org
        repo but made zero contribution to any org repo in the window. <b>Specs</b>: commits touching
        markdown under <code>specs/</code> directories (templates &amp; vendored SDLC framework
        excluded — see <code>config.yaml</code>). Platform-vs-app below is a "where effort goes"
        breakdown, not the contribute/use line. GitHub-only data; passive consumption beyond
        forks/stars is not observable.
      </p>
      )}
    </>
  );
}
