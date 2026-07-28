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
type PersonOption = { login: string; name: string; company: string; emails: string };

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
    <div className="tblbar">
      <label className="person-pick">
        {/* The monolith template is `Company&nbsp;\n  <select>` — HTML collapses
            that newline into a rendered space between the nbsp and the select
            (~2px). JSX strips newlines adjacent to tags, so restore it with an
            explicit {" "} or the whole picker row shifts 2px left vs baseline. */}
        Company&nbsp;{" "}
        <select
          id="person-company" className="person-select" value={company}
          onChange={(e) => setCompany(e.target.value)}
        >
          <option value="">All companies</option>
          {companies.map((co) => <option key={co} value={co}>{co}</option>)}
        </select>
      </label>
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
      <span className="cnt">weeks bucketed by author date · Lines = git diff +/- · Issues = opened by author</span>
    </div>
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
        <table className="grouped person-weekly">
          <thead>
            <tr className="grp">
              <th>Week (starting)</th>
              {cols.map((c, i) => <th key={i} colSpan={2} className="g" data-tip={c.repo}>{c.name}</th>)}
              <th className="g">Issues</th>
            </tr>
            <tr>
              <th />
              {cols.map((_, i) => (
                <Fragment key={i}>
                  <th className="g">Commits</th>
                  <th>Lines +/-</th>
                </Fragment>
              ))}
              <th className="g">opened</th>
            </tr>
          </thead>
          <tbody>
            {pw.rows.map((row, ri) => (
              <tr key={ri}>
                <td>{row.week}</td>
                {row.cells.map((cell, ci) => {
                  const col = cols[ci];
                  const dr = !!(cell && cell.commits && col.repo !== "(other)");
                  const attrs = dr ? drillCell(pw.login, col.repo, row.week, row.week_end) : {};
                  if (cell) {
                    return (
                      <Fragment key={ci}>
                        <td className="g" {...attrs}>{fmtNum(cell.commits)}</td>
                        <td className="pw-lines" {...attrs}>
                          <span className="pw-add">+{fmtNum(cell.add)}</span>/<span className="pw-del">-{fmtNum(cell.del)}</span>
                        </td>
                      </Fragment>
                    );
                  }
                  return (
                    <Fragment key={ci}>
                      <td className="g mut">–</td>
                      <td className="mut">–</td>
                    </Fragment>
                  );
                })}
                <td
                  className="g"
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
        cloned default branch (no merges), bucketed by author week; Lines = git diff insertions/deletions;
        Issues = GitHub issues opened by this author. Top {cols.length} repos by commits
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
function PersonDashboard({ d }: { d: Dashboard }) {
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

      <h3 className="psec">Activity by week</h3>
      <HeatStrip heat={heat} />
      {d.weekly && <WeeklyTable pw={d.weekly} />}

      <h3 className="psec">Where the work goes</h3>
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

      <h3 className="psec">Lasting impact &amp; collaboration <span className="alltime-tag">all-time</span></h3>
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
        Activity blocks honour the selected period; <b>lasting impact</b> is all-time by nature — surviving LOC is
        a git-blame of today's tree, reviews and merge stats are cumulative.
      </p>

      {d.score && d.score.board && d.score.board.length > 0 ? (
        <PersonScore score={d.score} login={d.login} />
      ) : d.scoreUnavailable ? (
        // The panel used to just vanish — indistinguishable from "this person has
        // no score", whether the builder had crashed or the window was empty. One
        // line in its place says which, so a missing section is a statement instead
        // of an absence. Nothing is added when the score renders, so the pixel-gate
        // baseline for the working case is untouched.
        <p className="hint">Developer score unavailable — {d.scoreUnavailable.detail}.</p>
      ) : null}
    </>
  );
}

export default function Person() {
  const query = useReportQuery();
  const person = query.person || null;
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
      <p className="sub">
        Org <b>{data.meta.org}</b> ·{" "}
        {data.meta.allTime ? (
          <><b>all-time history</b> (since {data.meta.windowStart})</>
        ) : (
          <>window {data.meta.windowStart} → today ({data.meta.lookbackDays} days)</>
        )}{" "}
        · generated {data.meta.generatedText} UTC
      </p>

      <FilterBar
        periodPresets={data.periodPresets} period={data.period} scope={data.scope}
        scopeTargets={data.scopeTargets}
      />

      <h2 id="person">
        Per-person weekly activity <span className="period-tag">{periodLabel}</span>
      </h2>
      <div className="card">
        <PersonPicker options={data.personOptions} companies={data.personCompanies} selected={person} />
        <div id="person-view">
          {data.dashboard ? (
            <PersonDashboard d={data.dashboard} />
          ) : (
            <p className="hint">
              Pick a person to see their weekly commits / lines / issues for the selected period. Needs the portal
              (computed on the fly).
            </p>
          )}
        </div>
      </div>

      {/* Always-on page footer (templates/report.j2 line ~1077, OUTSIDE every
          mode-section) — visible in ALL modes incl. person, so the React person
          page must render it verbatim too (same as the other report views). */}
      <p className="foot">
        Definitions — <b>Contributing to Fabric</b>: any commit, PR, spec edit, bug or user story in{" "}
        <b>any</b> repo of the org (apps included). <b>Using, not contributing back</b>: forked an org
        repo but made zero contribution to any org repo in the window. <b>Specs</b>: commits touching
        markdown under <code>specs/</code> directories (templates &amp; vendored SDLC framework
        excluded — see <code>config.yaml</code>). Platform-vs-app below is a "where effort goes"
        breakdown, not the contribute/use line. GitHub-only data; passive consumption beyond
        forks/stars is not observable.
      </p>
    </>
  );
}
