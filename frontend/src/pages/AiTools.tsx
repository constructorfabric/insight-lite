// /ai-tools — the tenth and LAST migrated report view (see
// docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P10). NOTE the
// route rename fabric→ai-tools (migration spec's redirect table); the monolith's
// mode stays "fabric" and the sidebar active-key stays "fabric". Reproduces the
// monolith's eight `data-modes="fabric all"` mode-sections class-for-class
// against templates/report.j2 (~799 AI-usage panel (panel_aiusage), ~807 Cyber
// Studio provenance (studio_prov + cpt-by-company split + cpt-people details),
// ~846 Gears usage (gears_usage), ~871 per-tracker tables (fabric_trackers),
// ~891 Platform usage by company & person (fabric_company / fabric_people
// details), ~915 Bots & automation (panel_bots + per-bot detail table)) —
// driven by GET /api/report/ai-tools (render.ai_tools_json) instead of
// server-rendered HTML + the /api/period fragment swap.
//
// Two provenances (see ai_tools_json): the AI-usage panel and the Bots MINI
// stats are period/slice-scoped (data-period-panel regions); everything else —
// provenance, gears, trackers, fabric-usage rollup, and the per-bot detail
// table — is all-time. The AI-usage panel has NO Vega chart (a hand-rolled
// `.split` bar + a plain `.dt` table). SSR-safe: no window/document access
// outside hooks/effects.
import { Fragment } from "react";
import FilterBar from "../components/FilterBar";
import DataTable, { type Column, zeroClass } from "../components/DataTable";
import { SplitBar, MiniStats, MarkerTable, type Segment, type MarkerTableData } from "../widgets";
import { useReportData } from "../hooks/useReportData";
import Loading from "../components/Loading";

// ---- types (mirror render.ai_tools_json's payload) -------------------------
type Tool = { tool: string; commits: string; commitsRaw: number; pctStr: string; pctRaw: number; loc: string; locRaw: number };
type AiUsage = {
  anyCommits: number; totalCommits: number; pct: string; anyDrill: boolean;
  toolsAvailable: boolean; tools: Tool[];
};

type MiniFL = { marker: string; files: number; lines: number };
type CptSegment = { company: string; color: string; width: string; tip: string; label: string };
type CptPerson = { login: string; name: string; company: string; lines: number };
type StudioProv = {
  present: boolean; mini: MiniFL[]; table: MarkerTableData;
  cptPresent: boolean; cptSegments: CptSegment[]; cptPeopleCount: number; cptPeople: CptPerson[];
};
type GearsUsage = { present: boolean; repoCount: number; mini: MiniFL[]; table: MarkerTableData };
type Tracker = { name: string; repoCount: number; mini: { marker: string; files: number }[]; table: MarkerTableData };

type CompanyRow = Record<string, unknown>;
type PersonRow = { login: string; company: string; ai_commits: number; aiPctStr: string; cpt_lines: number };
type BotRow = Record<string, unknown>;
type BotsMini = { count: number; commits: string; additions: string; reviews: string | null; windowed: boolean };

type AiToolsData = {
  meta: { org: string; allTime: boolean; windowStart: string; lookbackDays: number; generatedText: string };
  period: { preset: string; label: string; from: string | null; to: string | null };
  periodPresets: { key: string; label: string }[];
  scope: string;
  scopeTargets: { org?: string[]; element?: string[]; repo?: string[] };
  dataQuality: { apiRateLimited: boolean; apiReset: string | null };
  aiUsage: AiUsage;
  studioProv: StudioProv;
  gearsUsage: GearsUsage;
  trackers: Tracker[];
  fabricCompany: CompanyRow[];
  fabricPeopleCount: number;
  fabricPeople: PersonRow[];
  botsMini: BotsMini;
  botRows: BotRow[];
};

// dimZeros() (report.j2's runtime JS) fades every childless <td> whose text is a
// bare zero — the DataTable does this via zeroClass(); the hand-rolled tables
// below must too, or "0 / 0" / "0" cells would render un-faded vs the monolith.
// zeroClass() returns " z" (leading space) or ""; normalise to the DOM class.
function zc(text: string): string | undefined {
  return zeroClass(text) ? "z" : undefined;
}

// ① AI-usage panel (panel_aiusage, period-scoped). A hint + a per-tool `.split`
// bar + a plain `.dt` table (NO thead/tbody in the monolith → the browser
// auto-wraps every row, header included, in ONE tbody, so `tbody tr:last-child
// td{border-bottom:none}` drops the last row's border; a single <tbody> wrapping
// the header + data rows reproduces that exactly). No precision badges here:
// pr.ai_usage.tools come from store.aggregate without a `precision` field and
// the `ai_precision` template global is absent, so the macro's badge never fires.
function AiUsagePanel({ ai }: { ai: AiUsage }) {
  const split = ai.tools.filter((t) => t.pctRaw > 0);
  return (
    <>
      <p className="hint">
        <b>
          <span className="dr" {...(ai.anyDrill ? { "data-drill": "commit", "data-flag": "ai_marked" } : {})}>
            {ai.anyCommits}
          </span>
        </b>{" "}
        of {ai.totalCommits} commits ({ai.pct}%) carry an AI-tool marker in the commit message. This is a{" "}
        <b>floor</b> — only commits that kept the marker are counted; stripped markers and company-internal
        repos (e.g. GitLab) are invisible. Accurate org-wide measurement needs tool telemetry (Insight track),
        not commit scraping.
      </p>
      {!ai.toolsAvailable ? (
        <p className="conc">
          <b>Per-tool split is unavailable for this window</b> — these commits were collected before tool names
          were stored per commit. Run <b>Refresh report</b> once to backfill; the All-time period always shows
          the split.
        </p>
      ) : (
        <>
          <h3 style={{ marginTop: 12 }}>Per-tool split</h3>
          <SplitBar
            className="split"
            segments={split.map((t): Segment => ({
              pct: t.pctStr,
              color: "var(--app)",
              tip: `${t.tool}: ${t.commits} commits (${t.pctStr}%)`,
              text: t.pctRaw >= 8 ? t.tool : "",
            }))}
          />
          <div className="card" style={{ overflowX: "auto", border: "none", padding: 0, marginTop: 12 }}>
            <table className="dt">
              <tbody>
                <tr>
                  <th className="sortable">Tool</th>
                  <th className="sortable">Commits</th>
                  <th className="sortable">% of all commits</th>
                  <th className="sortable">Meaningful LOC (in those commits)</th>
                </tr>
                {ai.tools.length > 0 ? (
                  ai.tools.map((t) => (
                    <tr key={t.tool}>
                      <td className={zc(t.tool)} data-sort={t.tool}>{t.tool}</td>
                      <td
                        className={zc(t.commits)} data-sort={String(t.commitsRaw)}
                        {...(t.commitsRaw ? { "data-drill": "commit", "data-ai-tool": t.tool } : {})}
                      >
                        {t.commits}
                      </td>
                      <td className={zc(`${t.pctStr}%`)} data-sort={String(t.pctRaw)}>{t.pctStr}%</td>
                      <td className={zc(t.loc)} data-sort={String(t.locRaw)}>{t.loc}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4} className="muted">
                      No AI-tool markers found in commit messages.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="conc">
            Markers are configurable in <code>config.yaml → ai_tools.markers</code> (the in-house assistant ={" "}
            <code>your-assistant[bot]</code> co-author; the in-house assistant/Devin/Claude/Copilot by trailer or message).
            A commit may match several tools, so per-tool rows can exceed the unique total.
          </p>
        </>
      )}
    </>
  );
}

// assistant code-marker lines by company — the top-authors table inside the
// (closed) <details> under the studio provenance section (cap=30 in the
// monolith, but its data_table macro's cap silently no-ops under autoescape, so
// every row renders; not in the gate anyway — reproduced faithfully).
const CPT_PEOPLE_COLS: Column<CptPerson>[] = [
  { label: "Person", kind: "text", key: "login" },
  { label: "Name", kind: "text", key: "name" },
  { label: "Company", kind: "text", key: "company" },
  { label: "Marked lines", kind: "loc", key: "lines" },
];

// Platform usage by company (visible) — data_table macro columns 1:1.
const FABRIC_COMPANY_COLS: Column<CompanyRow>[] = [
  { label: "Company", kind: "text", key: "company" },
  { label: "Commits", key: "commits" },
  { label: "AI✦ commits", key: "ai_commits" },
  { label: "AI%", kind: "pctp", key: "ai_pct" },
  { label: "cpt lines", kind: "raw", key: "cpt_lines" },
];

// Platform usage by person (inside a closed <details>). The AI% column uses the
// macro's `fmt:'raw'` + `unit:'%'` = a literal `{{ v }}%`, so the server sends
// `aiPctStr` = str(ai_pct) ("29.0", "100.0", "0.0") to stay byte-exact rather
// than JS's String(29)→"29".
const FABRIC_PEOPLE_COLS: Column<PersonRow>[] = [
  { label: "Person", kind: "text", key: "login" },
  { label: "Company", kind: "text", key: "company" },
  { label: "AI✦ commits", key: "ai_commits" },
  { label: "AI%", kind: "raw", key: "aiPctStr", unit: "%" },
  { label: "cpt lines", kind: "loc", key: "cpt_lines" },
];

// Per-bot detail (data_table macro, visible). Activity is the macro's kind='html'
// `<span class="muted">{kind}</span>` — a render escape hatch (its child <span>
// disqualifies it from dimZeros, matching the monolith).
const BOT_COLS: Column<BotRow>[] = [
  { label: "Bot", kind: "text", key: "login" },
  { label: "Activity", render: (r) => <span className="muted">{String(r.kind ?? "")}</span> },
  { label: "Commits", kind: "raw", key: "commits" },
  { label: "LOC +", kind: "loc", key: "additions" },
  { label: "AI✦", tip: "commits carrying an AI-tool marker", kind: "raw", key: "ai_commits" },
  { label: "Reviews", kind: "raw", key: "reviews_given" },
  { label: "Repos", kind: "text", key: "repos" },
];

export default function AiTools() {
  const { data, error } = useReportData<AiToolsData>("ai-tools");

  if (error && !data) return <p className="hint" style={{ padding: 24 }}>Could not load the report ({error}).</p>;
  if (!data) return <Loading />;

  const periodLabel = data.period.label;
  const sp = data.studioProv;
  const gu = data.gearsUsage;
  const bm = data.botsMini;

  return (
    <>

      <FilterBar
        periodPresets={data.periodPresets} period={data.period} scope={data.scope}
        scopeTargets={data.scopeTargets}
      />

      {data.dataQuality.apiRateLimited && (
        <div className="card" style={{ borderColor: "var(--bad)", background: "#fff5f5" }}>
          <p style={{ margin: 0, color: "var(--bad)", fontWeight: 600 }}>
            ⚠ GitHub API rate limit hit during collection — this report is PARTIAL.
            {data.dataQuality.apiReset && ` Quota resets at ${data.dataQuality.apiReset}.`} Re-run collection
            after the reset for complete data.
          </p>
        </div>
      )}

      {/* Made with AI tools (panel_aiusage). Period-scoped: the
          data-period-panel wraps ONLY the panel content (h2 is a sibling). */}
      <h2 id="ai-usage">Made with AI tools <span className="period-tag">{periodLabel}</span></h2>
      <div className="card">
        <div data-period-panel="aiusage">
          <AiUsagePanel ai={data.aiUsage} />
        </div>
      </div>

      {/* Content provenance — content markers (all-time, conditional on
          markers being present). */}
      {sp.present && (
        <>
          <h2 id="studio-provenance">
            Content provenance — content markers <span className="alltime-tag">all-time</span>
          </h2>
          <div className="card">
            <p className="hint">
              Scanned from <b>full repo content</b> (git grep over the default-branch tree), not commit messages —
              these markers are functional and not casually stripped, so this is the strongest in-repo provenance
              signal. Snapshot of current content (not windowed).
            </p>
            <MiniStats
              items={sp.mini.map((m) => ({
                value: m.files,
                label: <>{m.marker} — files ({m.lines} lines)</>,
              }))}
            />
            <div className="card" style={{ overflowX: "auto", border: "none", padding: 0, marginTop: 10 }}>
              <MarkerTable table={sp.table} empty="No content markers found." repoHead="Repo" />
            </div>
            {sp.cptPresent && (
              <>
                <h3 style={{ marginTop: 16 }}>assistant code-marker lines — by company (git blame)</h3>
                <SplitBar
                  className="split"
                  segments={sp.cptSegments.map((c): Segment => ({
                    pct: c.width, color: c.color, tip: c.tip, text: c.label,
                  }))}
                />
                <details className="repo-details">
                  <summary>Top authors of assistant-marked code ({sp.cptPeopleCount})</summary>
                  <div style={{ overflowX: "auto", marginTop: 8 }}>
                    <DataTable columns={CPT_PEOPLE_COLS} rows={sp.cptPeople} />
                  </div>
                </details>
              </>
            )}
            <p className="conc">
              <b>Generated specs</b> = <code>studio: true</code> frontmatter; <b>marked specs (cpt-IDs)</b> ={" "}
              spec docs referencing <code>cpt-…</code> artifact IDs; <b>assistant code markers</b> ={" "}
              <code>@cpt-…</code> traceability annotations. Code-marker lines are blame-attributed to their author
              (full-history clones). Patterns in <code>config.yaml → studio_provenance</code>. Covers only cloned
              repos (your-org); company-internal repos need telemetry.
            </p>
          </div>
        </>
      )}

      {/* Gears framework — usage (all-time, conditional). */}
      {gu.present && (
        <>
          <h2 id="framework-usage">Gears framework — usage <span className="alltime-tag">all-time</span></h2>
          <div className="card">
            <p className="hint">
              Repos that <b>depend on</b> Gears (vs the gears-* core repos, which are excluded as providers):
              frontend <code>@example-frontend/*</code> packages and Rust <code>example-crate-*</code> crates, found in
              full content.
            </p>
            <MiniStats
              items={[
                { value: gu.repoCount, label: "repos using Gears" },
                ...gu.mini.map((m) => ({
                  value: m.files,
                  label: <>{m.marker} — files ({m.lines} lines)</>,
                })),
              ]}
            />
            <div className="card" style={{ overflowX: "auto", border: "none", padding: 0, marginTop: 10 }}>
              <MarkerTable table={gu.table} empty="No gears dependencies found in cloned repos." repoHead="Repo (uses gears)" />
            </div>
            <p className="conc">
              Markers in <code>config.yaml → gears_usage.markers</code>. Provider repos (example-core / example-frontend /
              example-csharp) are excluded. This is "who builds on Gears" — the consumption side of the platform-vs-app
              lens, by content not guesswork.
            </p>
          </div>
        </>
      )}

      {/* Fabric trackers — one mode-section per markers-present tracker
          (all-time). Mini here shows "<files> — files" only (no line count). */}
      {data.trackers.map((t) => (
        <Fragment key={t.name}>
          <h2>{t.name} <span className="alltime-tag">all-time</span></h2>
          <div className="card">
            <MiniStats
              items={[
                { value: t.repoCount, label: "repos" },
                ...t.mini.map((m) => ({
                  value: m.files,
                  label: <>{m.marker} — files</>,
                })),
              ]}
            />
            <div className="card" style={{ overflowX: "auto", border: "none", padding: 0, marginTop: 10 }}>
              <MarkerTable table={t.table} empty="No matches in cloned repos." repoHead="Repo" />
            </div>
          </div>
        </Fragment>
      ))}

      {/* Platform usage — by company & person (all-time). */}
      <h2 id="fabric-usage">Platform usage — by company &amp; person <span className="alltime-tag">all-time</span></h2>
      <div className="card">
        <p className="hint">
          Who builds <b>with</b> the platform: commits carrying an AI-tool marker (in-house assistant / …) and
          assistant-marked code lines (git blame), rolled up per company and per person.
        </p>
        <div className="card" style={{ overflowX: "auto", border: "none", padding: 0 }}>
          <DataTable columns={FABRIC_COMPANY_COLS} rows={data.fabricCompany} empty="No Platform-usage markers attributed." />
        </div>
        <details className="repo-details" style={{ marginTop: 8 }}>
          <summary>By person ({data.fabricPeopleCount})</summary>
          <div style={{ overflowX: "auto", marginTop: 8 }}>
            <DataTable columns={FABRIC_PEOPLE_COLS} rows={data.fabricPeople} />
          </div>
        </details>
        <p className="conc">
          AI✦ = commits with a tool marker (incl. heuristic assistant-mention + exact bot trailers); cpt lines =
          exact assistant traceability markers, blame-attributed. Cloned repos only.
        </p>
      </div>

      {/* Bots & automation. The mini stats are period-scoped
          (data-period-panel="bots"); the per-bot detail table is all-time. */}
      <h2 id="bots">Bots &amp; automation <span className="period-tag">{periodLabel}</span></h2>
      <div className="card">
        <p className="hint">
          Detected automation accounts (<code>bot_logins</code> in <code>config.yaml</code>). Their activity is
          shown here for transparency but is <b>excluded</b> from every human contribution metric above — no bot is
          counted as a person or a company.
        </p>
        <div data-period-panel="bots">
          <MiniStats
            items={[
              { value: bm.count, label: "bot accounts" },
              { value: bm.commits, label: "commits" },
              { value: bm.additions, label: "LOC added" },
              {
                value: bm.reviews != null ? bm.reviews : "—",
                label: <>reviews given{bm.windowed && <> <span className="alltime-tag">all-time</span></>}</>,
              },
            ]}
          />
        </div>
        <h3 style={{ marginTop: 12 }}>Per-bot detail <span className="alltime-tag">all-time</span></h3>
        <div className="card" style={{ overflowX: "auto", border: "none", padding: 0, marginTop: 10 }}>
          <DataTable columns={BOT_COLS} rows={data.botRows} empty="No bot accounts detected in the window." />
        </div>
        <p className="conc">
          Edit the <code>bot_logins</code> list in <code>config.yaml</code> to add/remove automation accounts (use a
          leading <code>=</code> for an exact-login match, e.g. <code>=claude</code>, so it never catches a real
          person like <code>claudette</code>).
        </p>
      </div>

      {/* Always-on page footer (templates/report.j2 ~1077, OUTSIDE every
          mode-section) — visible in ALL modes incl. fabric, so render it
          verbatim (same as the other report views). */}
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
