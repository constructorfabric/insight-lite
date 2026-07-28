// /update — collect/rebuild portal with live job-status polling, migrated to
// React (Manage migration). Reproduces server.portal_html()'s markup + classes
// (see ../styles/update.css). Both the React page and the legacy JS poll GET
// /api/status (= snapshot_state) every 2s for live data and drive jobs via
// POST /api/{refresh,directory,export} — no new endpoint added.
//
// SSR-safe: no top-level window/document access — only inside effects / handlers.
import { useEffect, useRef, useState } from "react";

type State = {
  job: { running?: boolean; status: string; message?: string; log?: string };
  files: { report_html: string };
  store: { latest_run: string; runs: number; people: number; repos: number; snapshots: number };
  cache: { enabled: boolean; api_files: number; api_newest: string; clone_repos: number };
  has_token: boolean;
  token_source: string;
};

const TOKEN_NOTE: Record<string, string> = { db: "Saved in the app", env: "From environment", none: "Not set" };

function api(p: string) {
  // Build against location.origin — a relative fetch would inherit any userinfo
  // from a basic-auth URL, which fetch() rejects.
  return location.origin + p;
}

export default function UpdatePortal() {
  const [s, setS] = useState<State | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  async function refreshStatus() {
    try {
      const r = await fetch(api("/api/status"));
      setS(await r.json());
    } catch {
      /* leave last known state */
    }
  }
  async function startJob(kind: string) {
    await fetch(api("/api/" + kind), { method: "POST" });
    await refreshStatus();
  }

  useEffect(() => {
    refreshStatus();
    timer.current = setInterval(refreshStatus, 2000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  const job = s?.job ?? { status: "", message: "", running: false, log: "" };
  const running = !!job.running;
  const jobClass = running ? "run" : job.status === "success" ? "ok" : job.status === "failed" ? "bad" : "";
  const tokenNote = TOKEN_NOTE[s?.token_source ?? "none"] || "Not set";
  const cacheNote = s
    ? s.cache.enabled
      ? `${s.cache.api_files} files, newest ${s.cache.api_newest}`
      : "disabled by NO_CACHE=1"
    : "";

  return (
    <>
      <div className="hero">
        <div>
          <h1>Update workspace</h1>
          <p className="sub">
            Collect from GitHub, curate identities, and configure what's measured. The database is the source of truth;
            the YAML files are auto-generated backups.
          </p>
        </div>
        <div className="hero-actions">
          <a className="btn primary" href="/report">
            Open report
          </a>
        </div>
      </div>

      <div className={"jobbar " + jobClass} id="jobbar">
        <span className="dot"></span>
        <b id="job-status">
          {job.status}
          {running ? "…" : ""}
        </b>
        <span className="jmsg" id="job-message">
          {job.message || "No job is running."}
        </span>
      </div>

      <h2>Status</h2>
      <div className="status-strip">
        <div className="status-card">
          <div className="label">Database · source of truth</div>
          <div className="value" id="store-run">
            {s?.store.latest_run ?? ""}
          </div>
          <div className="fine" id="store-rows">
            {s ? `${s.store.runs} runs · ${s.store.people} people · ${s.store.repos} repos · ${s.store.snapshots} snapshots` : ""}
          </div>
        </div>
        <div className="status-card">
          <div className="label">Report</div>
          <div className="value" id="report-stamp">
            {s?.files.report_html ?? ""}
          </div>
          <div className="fine">rendered live from the DB</div>
        </div>
        <div className="status-card">
          <div className="label">GitHub token</div>
          <div className={"value " + (s?.has_token ? "ok" : "bad")} id="token-note">
            {tokenNote}
          </div>
          <div className="fine">
            <a href="/setup">Change / add →</a>
          </div>
        </div>
        <div className="status-card">
          <div className="label">Cache &amp; clones</div>
          <div className="value" id="cache-note">
            {cacheNote}
          </div>
          <div className="fine">
            <span id="clone-note">{s ? `${s.cache.clone_repos} repos` : ""}</span> cloned
          </div>
        </div>
      </div>

      <h2>Operations</h2>
      <div className="ops">
        <div className="op-card">
          <h3>Collect from GitHub</h3>
          <p>
            Fetch new commits, PRs, issues and traffic, refresh clones, then rebuild the report. Reuses cache and local
            clones.
          </p>
          <div className="op-meta">Needs a token · can take minutes on a large org.</div>
          <button className="primary" disabled={running} onClick={() => startJob("refresh")}>
            Collect &amp; rebuild
          </button>
        </div>
        <div className="op-card">
          <h3>Regenerate editors</h3>
          <p>Rebuild the Identity &amp; Config editor pages from the database. No GitHub.</p>
          <div className="op-meta">Edits themselves apply instantly on Save — this is for after updates.</div>
          <button disabled={running} onClick={() => startJob("directory")}>
            Regenerate
          </button>
        </div>
        <div className="op-card">
          <h3>Export snapshot</h3>
          <p>
            Copy the current report, data, and identity directory into <code>exports/</code> as timestamped files.
          </p>
          <div className="op-meta">A shareable point-in-time snapshot.</div>
          <button disabled={running} onClick={() => startJob("export")}>
            Export snapshot
          </button>
        </div>
      </div>

      <h2>Configure &amp; explore</h2>
      <div className="quick-links">
        <a className="link-card" href="/setup">
          <b>Setup</b>
          <span>GitHub token, primary org, extra orgs / repos.</span>
        </a>
        <a className="link-card" href="/config">
          <b>Config</b>
          <span>Repo classification, elements, company domains.</span>
        </a>
        <a className="link-card" href="/identity">
          <b>Identity</b>
          <span>People, companies, bots, aliases, merges.</span>
        </a>
        <a className="link-card" href="/metrics">
          <b>Metrics catalog</b>
          <span>Every number — description, formula, code.</span>
        </a>
        <a className="link-card" href="/views">
          <b>View catalog</b>
          <span>Reusable visual components — for dashboards &amp; MCP.</span>
        </a>
        <a className="link-card" href="/mcp-info">
          <b>MCP access</b>
          <span>Connection URL, bearer token, and the tool catalog.</span>
        </a>
        <a className="link-card" href="/usage-insights">
          <b>Usage insights</b>
          <span>Who opens the report and which widgets they view.</span>
        </a>
      </div>

      <details className="log">
        <summary>Last job log</summary>
        <pre id="log">{job.log || "No job has run from this portal yet."}</pre>
      </details>
    </>
  );
}
