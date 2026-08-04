// /setup — first-run wizard (token → org/repos → first collection), migrated to
// React (Manage migration). Markup, classes and inline-JS behaviours were ported 1:1
// from the pre-React server.SETUP_HTML page, which has since been deleted as dead
// code — `git log -- backend/server.py` has it if a detail ever needs checking.
// Styles: ../styles/setup.css. Initial config +
// token status come from the server via render_spa_page bootstrap (#spa-bootstrap);
// the collect step polls GET /api/status, and the three steps POST to the existing
// /api/setup/{token,token/clear,save,collect} endpoints.
//
// SSR-safe: no top-level window/document access — only inside effects / handlers.
import { useRef, useState } from "react";

type Boot = { token_status: string; org: string; extra_orgs: string; extra_repos: string };
type Msg = { text: string; cls: string };

function readBootstrap(): Boot {
  const fallback = { token_status: "none", org: "", extra_orgs: "", extra_repos: "" };
  if (typeof document === "undefined") return fallback;
  const el = document.getElementById("spa-bootstrap");
  if (!el || !el.textContent) return fallback;
  try {
    return { ...fallback, ...JSON.parse(el.textContent) };
  } catch {
    return fallback;
  }
}

const api = (p: string) => location.origin + p;
const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

export default function SetupWizard() {
  const boot = useRef<Boot>(readBootstrap()).current;
  const ts = boot.token_status;

  const [s1done, setS1] = useState(ts === "db" || ts === "env");
  const [s2done, setS2] = useState(false);
  const [s3done, setS3] = useState(false);
  const [tokMsg, setTokMsg] = useState<Msg>(
    ts === "db"
      ? { text: "A token is saved here. Enter a new one to replace it.", cls: "ok" }
      : ts === "env"
        ? { text: "Using the environment token. Save one here to manage it from the UI (it takes over).", cls: "" }
        : { text: "", cls: "" },
  );
  const [tokClear, setTokClear] = useState(ts === "db");
  const [scopeMsg, setScopeMsg] = useState<Msg>({ text: "", cls: "" });
  const [collectMsg, setCollectMsg] = useState<Msg>({ text: "", cls: "" });
  const [collectLogShown, setCollectLogShown] = useState(false);
  const [collectLog, setCollectLog] = useState("");
  const [doneRow, setDoneRow] = useState(false);

  const [token, setToken] = useState("");
  const [org, setOrg] = useState(boot.org);
  const [extraOrgs, setExtraOrgs] = useState(boot.extra_orgs);
  const [extraRepos, setExtraRepos] = useState(boot.extra_repos);

  const [tokSaving, setTokSaving] = useState(false);
  const [scopeSaving, setScopeSaving] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  async function clearToken() {
    if (!window.confirm("Remove the saved token? Collection falls back to the environment token if one is set.")) return;
    const r = await fetch(api("/api/setup/token/clear"), { method: "POST" });
    const j = await r.json();
    setTokMsg({ text: "Cleared — active source: " + (j.token_source || "none"), cls: j.token_source === "none" ? "err" : "" });
    setTokClear(j.token_source === "db");
  }
  async function saveToken() {
    const t = token.trim();
    if (!t) {
      setTokMsg({ text: "Paste a token first.", cls: "err" });
      return;
    }
    setTokSaving(true);
    setTokMsg({ text: "Verifying…", cls: "" });
    try {
      const r = await fetch(api("/api/setup/token"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: t }),
      });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || "failed");
      setToken("");
      setTokMsg({
        text: "Saved ✓ " + (j.login ? "as " + j.login + " · " : "") + (j.remaining != null ? j.remaining + " API calls left" : ""),
        cls: "ok",
      });
      setS1(true);
    } catch (e: any) {
      setTokMsg({ text: e.message, cls: "err" });
    }
    setTokSaving(false);
  }
  async function saveScope() {
    if (!org.trim()) {
      setScopeMsg({ text: "Primary org is required.", cls: "err" });
      return;
    }
    setScopeSaving(true);
    setScopeMsg({ text: "Saving…", cls: "" });
    try {
      const r = await fetch(api("/api/setup/save"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org: org.trim(), extra_orgs: csv(extraOrgs), extra_repos: csv(extraRepos) }),
      });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || "failed");
      setScopeMsg({ text: "Saved ✓", cls: "ok" });
      setS2(true);
    } catch (e: any) {
      setScopeMsg({ text: e.message, cls: "err" });
    }
    setScopeSaving(false);
  }
  async function pollStatus() {
    try {
      const s = await (await fetch(api("/api/status"))).json();
      setCollectLog(s.job.log || s.job.message || "");
      if (logRef.current) logRef.current.scrollTop = 1e9;
      setCollectMsg({ text: s.job.status + (s.job.running ? "…" : ""), cls: "" });
      if (!s.job.running && s.job.status === "success") {
        if (poll.current) clearInterval(poll.current);
        setS3(true);
        setCollectMsg({ text: "Done ✓", cls: "ok" });
        setDoneRow(true);
      } else if (!s.job.running && s.job.status === "failed") {
        if (poll.current) clearInterval(poll.current);
        setCollectMsg({ text: "Collection failed — see log.", cls: "err" });
        setCollecting(false);
      }
    } catch {
      /* transient */
    }
  }
  async function startCollect() {
    setCollecting(true);
    setCollectMsg({ text: "Starting…", cls: "" });
    try {
      const r = await fetch(api("/api/setup/collect"), { method: "POST" });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || "failed");
      setCollectLogShown(true);
      if (poll.current) clearInterval(poll.current);
      poll.current = setInterval(pollStatus, 2000);
      pollStatus();
    } catch (e: any) {
      setCollectMsg({ text: e.message, cls: "err" });
      setCollecting(false);
    }
  }

  return (
    <div className="wrap">
      <h1>Set up Constructor&nbsp;Insight</h1>
      <p className="lead">
        Connect a GitHub organization and run the first collection. You can refine classification, elements and
        identities afterwards from the report portal.
      </p>

      <div className={"step" + (s1done ? " done" : "")} id="s1">
        <h2>
          <span className="snum">1</span>GitHub token
        </h2>
        <p className="hint">
          Create a <b>fine-grained</b> personal access token with <b>read-only</b> access to the org's repositories
          (Contents, Metadata, Pull requests, Issues) at{" "}
          <a href="https://github.com/settings/tokens?type=beta" target="_blank" rel="noopener">
            github.com/settings/tokens
          </a>
          . It is stored server-side only, never shown again, never written to files or git.
        </p>
        <label htmlFor="token">Token</label>
        <input
          id="token"
          type="password"
          autoComplete="off"
          placeholder="github_pat_… / ghp_…"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <div className="row">
          <button className="btn primary" id="tok-save" disabled={tokSaving} onClick={saveToken}>
            Verify &amp; save token
          </button>
          <button className="btn" id="tok-clear" hidden={!tokClear} onClick={clearToken}>
            Clear saved token
          </button>
          <span className={"msg" + (tokMsg.cls ? " " + tokMsg.cls : "")} id="tok-msg">
            {tokMsg.text}
          </span>
        </div>
      </div>

      <div className={"step" + (s2done ? " done" : "")} id="s2">
        <h2>
          <span className="snum">2</span>Organization &amp; repositories
        </h2>
        <p className="hint">
          Primary org whose history is collected. Extra orgs / individual repos are optional (comma-separated) — e.g. an
          old org after a migration.
        </p>
        <label htmlFor="org">Primary org</label>
        <input id="org" placeholder="my-github-org" value={org} onChange={(e) => setOrg(e.target.value)} />
        <label htmlFor="extra-orgs">Extra orgs (comma-separated)</label>
        <input
          id="extra-orgs"
          placeholder="old-org, another-org"
          value={extraOrgs}
          onChange={(e) => setExtraOrgs(e.target.value)}
        />
        <label htmlFor="extra-repos">Extra repos (org/repo, comma-separated)</label>
        <input
          id="extra-repos"
          placeholder="vendor/pkg-repo"
          value={extraRepos}
          onChange={(e) => setExtraRepos(e.target.value)}
        />
        <div className="row">
          <button className="btn primary" id="scope-save" disabled={scopeSaving} onClick={saveScope}>
            Save scope
          </button>
          <span className={"msg" + (scopeMsg.cls ? " " + scopeMsg.cls : "")} id="scope-msg">
            {scopeMsg.text}
          </span>
        </div>
      </div>

      <div className={"step" + (s3done ? " done" : "")} id="s3">
        <h2>
          <span className="snum">3</span>First collection
        </h2>
        <p className="hint">
          Clones the repos and fetches PRs / issues from GitHub, then builds the report. This can take a few minutes on
          a large org.
        </p>
        <div className="row">
          <button className="btn primary" id="collect-start" disabled={collecting} onClick={startCollect}>
            Start first collection
          </button>
          <span className={"msg" + (collectMsg.cls ? " " + collectMsg.cls : "")} id="collect-msg">
            {collectMsg.text}
          </span>
        </div>
        <pre id="collect-log" ref={logRef} hidden={!collectLogShown}>
          {collectLog}
        </pre>
        <div className="row" id="done-row" hidden={!doneRow}>
          <a className="btn primary" href="/report">
            Open report →
          </a>
        </div>
      </div>
    </div>
  );
}
