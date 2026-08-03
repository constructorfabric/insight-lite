// /traffic — the ninth migrated report view (see
// docs/superpowers/plans/2026-07-22-react-phaseR-report.md, Task R-P9).
// Reproduces the monolith's "usage" mode-sections class-for-class against
// templates/report.j2 (the three `<div class="mode-section"
// data-modes="usage all">` blocks at ~947 "The two scenarios", ~986 "Traffic —
// clones & page views" (panel_traffic), and the conditional ~1002 "External
// contributors" chips) — driven by GET /api/report/traffic (render.traffic_json)
// instead of server-rendered HTML + the /api/period fragment swap. NOTE the
// route rename usage→traffic (migration spec's redirect table); the monolith's
// mode stays "usage". Traffic has NO Vega charts (hand-rolled scenario bars +
// the CSS clone/view panel). SSR-safe: no window/document access outside
// hooks/effects.
import FilterBar from "../components/FilterBar";
import { GhLink, BarList, MiniStats, Chips, type BarRowProps } from "../widgets";
import { useReportData } from "../hooks/useReportData";
import Loading from "../components/Loading";
import { token } from "../lib/tokens";

// ---- types (mirror render.traffic_json's payload) --------------------------
type ContributorRow = { login: string; isMember: boolean; value: number; bar: number; email: string };
type NonContributorRow = { login: string; isMember: boolean; forked: string[]; email: string };
type ExternalRow = { login: string; value: number; email: string };

type Scenarios = {
  contributorsCount: number;
  membersCount: number;
  externalCount: number;
  contributors: ContributorRow[];
  nonContributors: NonContributorRow[];
  totalStars: number;
  totalForks: number;
  platformReposCount: number;
};

type TrafficRepoRow = {
  name: string; views: string; visitors: string; clones: string; uniques: string; ci: string;
  daily: { h: number; tip: string }[];
  paths: { text: string; views: number; tip: string }[];
};
type TrafficPanel = {
  present: boolean; windowed: boolean; since: string | null; nNoAccess: number;
  views: string; visitors: string; clones: string; cloners: string;
  rows: TrafficRepoRow[];
};

type TrafficData = {
  meta: { org: string; allTime: boolean; windowStart: string; lookbackDays: number; generatedText: string };
  period: { preset: string; label: string; from: string | null; to: string | null };
  periodPresets: { key: string; label: string }[];
  scope: string;
  scopeTargets: { org?: string[]; element?: string[]; repo?: string[] };
  dataQuality: { apiRateLimited: boolean; apiReset: string | null };
  traffic: TrafficPanel;
  scenarios: Scenarios;
  externalContributors: ExternalRow[];
};

// The gh() macro (templates/panels/01_helpers.j2): a link to the person page.
// ① Contributing to Fabric — a bar list of contributors (top 14 + a collapsed
// "more" tail, mirroring templates/report.j2 ~954-970). The more-tail stays
// `hidden` (the monolith's toggle JS isn't part of the SPA chrome — same as
// the People view's category more-rows; the default screenshot state has it
// collapsed, matching the monolith's initial render). NOTE the tail row here has
// an EMPTY .bb and .vv (no bar/value) — so the tail omits pct/value.
function TwoScenarios({ s }: { s: Scenarios }) {
  const extra = s.contributorsCount - 14;
  const contribRows: BarRowProps[] = s.contributors.map((c) => ({
    label: (
      <>
        <GhLink login={c.login} />
        {!c.isMember && <span className="tag ext">external</span>}
      </>
    ),
    tip: c.email,
    pct: c.bar,
    color: "var(--good)",
    value: c.value,
  }));
  return (
    <div className="grid2">
      <div className="card good">
        <h3>① Contributing to Fabric</h3>
        <p className="hint">
          People with <b>any</b> contribution (commits, PRs, specs, bugs, features) to <b>any</b> repo in the org.
          {" "}{s.contributorsCount} people — {s.membersCount} org members, {s.externalCount} external.
        </p>
        <BarList
          rows={contribRows}
          cap={14}
          tail={s.contributorsCount > 14 ? {
            moreLabel: `▸ + ${extra} more contributors`,
            lessLabel: `▾ hide ${extra}`,
          } : undefined}
        />
        {s.contributorsCount === 0 && <p className="hint">No contributions in this window.</p>}
      </div>

      <div className="card warn">
        <h3>② Using Fabric, not contributing back</h3>
        <p className="hint">
          Accounts that <b>forked</b> an org repo (= using Fabric) but made <b>zero</b> contribution to any repo in the
          org during the window. {s.nonContributors.length} such accounts.
        </p>
        {s.nonContributors.length > 0 ? (
          <Chips
            items={s.nonContributors.map((r) => ({
              key: r.login,
              tip: r.email,
              content: (
                <>
                  <GhLink login={r.login} />
                  {r.isMember ? " ·member" : ""}
                  {" → "}
                  {r.forked.join(", ")}
                </>
              ),
            }))}
          />
        ) : (
          <p className="hint">Every forker has contributed back. 🎉</p>
        )}
        <p className="hint" style={{ marginTop: 12 }}>
          {s.totalStars} stars · {s.totalForks} forks across {s.platformReposCount} platform repos
          {" "}(forks of all org repos counted for this scenario). GitHub can't see passive consumption beyond
          forks/stars.
        </p>
      </div>
    </div>
  );
}

// panel_traffic (templates/panels/02_overview.j2 ~318): the clone/view stat
// block + per-repo legend rows. Period/slice-scoped (from pr['traffic']).
function TrafficPanelView({ t }: { t: TrafficPanel }) {
  if (!t.present) {
    return (
      <p className="hint">
        No traffic in this window (needs a push/admin token, or no accumulated daily rows in range).
      </p>
    );
  }
  return (
    <>
      <MiniStats
        items={[
          { value: t.views, label: <>page views <span className="prec exact">human</span></> },
          { value: t.visitors, label: "unique visitors (people)" },
          { value: t.clones, label: <>clones <span className="prec heuristic">incl. CI</span></> },
          { value: t.cloners, label: "unique cloners (mostly runners)" },
        ]}
      />
      {t.windowed ? (
        <p className="hint">
          Clones &amp; views summed over the selected window from accumulated daily traffic (collection began {t.since});
          windows before then are partial. Daily sparklines &amp; popular paths show only at all-time.
        </p>
      ) : (
        <p className="hint">
          <b>Page views</b> are humans browsing the web UI (CI doesn't) — the cleaner usage signal. <b>Clones</b> are
          dominated by CI: E2E/image-build workflows clone per job, so each "unique cloner" (ephemeral runner) clones
          ~14×; a spike = heavy CI that day, not more people.
          {t.nNoAccess > 0 && (
            <>{" "}<b>{t.nNoAccess} repo(s)</b> have no traffic access yet (push/admin token).</>
          )}
        </p>
      )}
      {t.rows.map((r) => (
        <div style={{ borderTop: "1px solid var(--line)", paddingTop: 10, marginTop: 10 }} key={r.name}>
          <div className="legend"><b>{r.name}</b> — {r.views} views / {r.visitors} visitors · {r.clones} clones / {r.uniques} cloners ({r.ci}× CI)</div>
          {r.daily.length > 0 && (
            <div className="cols" style={{ height: 34 }}>
              {r.daily.map((d, i) => (
                <div className="c" style={{ height: `${d.h}%` }} data-tip={d.tip} key={i} />
              ))}
            </div>
          )}
          {r.paths.length > 0 && (
            <Chips
              style={{ marginTop: 6 }}
              items={r.paths.map((p, i) => ({
                key: String(i),
                tip: p.tip,
                content: <>{p.text} · {p.views}v</>,
              }))}
            />
          )}
        </div>
      ))}
      <p className="conc">
        {!t.windowed && "Daily bars = clones/day (hover for date). Chips = most-viewed paths (what people look at: PRs, issues, docs). "}
        Clone counts badged <span className="prec heuristic">heuristic</span>; page views <span className="prec exact">exact-human</span>.
      </p>
    </>
  );
}

export default function Traffic() {
  const { data, error } = useReportData<TrafficData>("traffic");

  if (error && !data) return <p className="hint" style={{ padding: 24 }}>Could not load the report ({error}).</p>;
  if (!data) return <Loading />;

  const periodLabel = data.period.label;

  return (
    <>

      <FilterBar
        periodPresets={data.periodPresets} period={data.period} scope={data.scope}
        scopeTargets={data.scopeTargets}
      />

      {data.dataQuality.apiRateLimited && (
        <div className="card" style={{ borderColor: "var(--bad)", background: token["bad-soft"] }}>
          <p style={{ margin: 0, color: "var(--bad)", fontWeight: 600 }}>
            ⚠ GitHub API rate limit hit during collection — this report is PARTIAL.
            {data.dataQuality.apiReset && ` Quota resets at ${data.dataQuality.apiReset}.`} Re-run collection
            after the reset for complete data.
          </p>
        </div>
      )}

      {/* The two scenarios — all-time (never period/scope-scoped, so no
          data-period-panel wrapper; matches the monolith exactly). */}
      <h2 id="scenarios">The two scenarios <span className="alltime-tag">all-time</span></h2>
      <TwoScenarios s={data.scenarios} />

      {/* Traffic — clones & page views. Period-scoped: the .card IS the
          data-period-panel region (templates/report.j2 ~988). */}
      <h2 id="usage">Traffic — clones &amp; page views <span className="period-tag">{periodLabel}</span></h2>
      <div className="card" data-period-panel="traffic">
        <TrafficPanelView t={data.traffic} />
      </div>

      {/* External contributors — conditional (only when there are any) +
          all-time, same as the monolith's `{% if external_contributors %}` block. */}
      {data.externalContributors.length > 0 && (
        <>
          <h2>External contributors (non-members giving back) — healthy-community signal <span className="alltime-tag">all-time</span></h2>
          <div className="card"><Chips
            items={data.externalContributors.map((r) => ({
              key: r.login,
              tip: r.email,
              content: <><GhLink login={r.login} /> · {r.value}</>,
            }))}
          /></div>
        </>
      )}

      {/* Always-on page footer (templates/report.j2 ~1077, OUTSIDE every
          mode-section) — visible in ALL modes incl. usage, so render it
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
