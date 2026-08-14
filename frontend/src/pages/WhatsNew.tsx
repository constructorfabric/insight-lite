// /whats-new — the pilot route for the React migration (see
// docs/superpowers/plans/2026-07-22-react-phase0.md P0-T4). Fetches the changelog
// from GET /api/whats-new and reproduces the pre-migration changelog.py render_page()
// markup + classes exactly (pill per change `type`, date headings, title/detail),
// so the screenshot-diff gate sees no pixel difference. See ../styles/whatsnew.css
// for the page-specific CSS this markup depends on.
//
// SSR-safe: no top-level window/document access — only inside effects, which never
// run outside a browser.
import { useEffect, useState } from "react";

type Change = { type: string; title: string; detail: string };
type Release = { date: string; changes: Change[] };

// Mirrors changelog.py's _PILL: known types get a short label + a dedicated CSS
// class; anything else (e.g. "improvement") falls back to a title-cased label on
// the "feat" pill class — same fallback changelog.py's dict.get(...) applies.
const PILL: Record<string, [string, string]> = {
  security: ["Security", "sec"],
  fix: ["Fix", "fix"],
  feature: ["New", "feat"],
  redesign: ["Design", "des"],
};

function pillFor(type: string): [string, string] {
  const known = PILL[type];
  if (known) return known;
  return [type.charAt(0).toUpperCase() + type.slice(1), "feat"];
}

export default function WhatsNew() {
  const [releases, setReleases] = useState<Release[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/whats-new")
      .then((res) => res.json())
      .then((data) => {
        if (!cancelled && data && data.ok) setReleases(data.releases);
      })
      .catch(() => {
        if (!cancelled) setReleases([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <h1>What&#x27;s new</h1>
      <p className="sub">Notable changes to the report and portal, newest first.</p>
      {(releases ?? []).map((rel) => (
        <section className="release" key={rel.date}>
          <h2 className="rdate">
            {rel.date}
            <span className="rcount">
              {rel.changes.length} {rel.changes.length === 1 ? "change" : "changes"}
            </span>
          </h2>
          <ul className="clist">
            {rel.changes.map((c, i) => {
              const [label, cls] = pillFor(c.type);
              return (
                <li className="citem" key={i}>
                  <span className={`cpill ${cls}`}>{label}</span>
                  <div className="ctext">
                    <div className="ctitle">{c.title}</div>
                    <p className="cdetail">{c.detail}</p>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </>
  );
}
