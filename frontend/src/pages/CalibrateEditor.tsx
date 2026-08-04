// /calibrate — collect ground-truth developer-score labels + tune pillar weights,
// migrated to React (Manage migration). Fetches GET /api/manage/calibrate.json and
// reproduces calibrate.render_page()'s markup + classes (see ../styles/calibrate.css).
// Behaviours ported from the page's inline JS: click a star → POST /api/score-label;
// weight sliders with live %, Save → POST /api/score-weights, Reset.
//
// SSR-safe: no top-level window/document access — only inside effects / handlers.
import { useEffect, useState } from "react";

type Person = { login: string; name: string; score: number | null; avg: string; mine: number };
type Weights = { cur: Record<string, number>; def: Record<string, number> };
// The band scale: where the score stops being one label and becomes the next. `suggested`
// is fitted to THIS window's distribution and is null when there is too little to fit —
// and it stays a suggestion, because floors pinned to quantiles every window would make a
// person's label move when the team moves, on top of the score already doing so.
type Bands = {
  cur: Record<string, number>; def: Record<string, number>;
  suggested: Record<string, number> | null;
};
type Data = { rater: string; board: Person[]; weights: Weights; bands: Bands };

// Ascending, and the lowest is fixed at 0 — the scale has to start somewhere, and the
// server rejects anything that does not ascend from there.
const BAND_KEYS: [string, string][] = [
  ["Developing", "Developing from"],
  ["Solid", "Solid from"],
  ["Strong", "Strong from"],
];

const KEYS: [string, string][] = [
  ["engagement", "Engagement"],
  ["delivery", "Delivery"],
  ["craft", "Craft & rework"],
  ["flow", "Flow"],
];

export default function CalibrateEditor() {
  const [data, setData] = useState<Data | null>(null);
  const [ratings, setRatings] = useState<Record<string, number>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<Record<string, { text: string; color: string }>>({});
  const [wt, setWt] = useState<Record<string, number>>({});
  const [wtStatus, setWtStatus] = useState<{ text: string; color: string }>({ text: "", color: "var(--mut)" });
  const [wtSaving, setWtSaving] = useState(false);
  const [bd, setBd] = useState<Record<string, number>>({});
  const [bdStatus, setBdStatus] = useState<{ text: string; color: string }>({ text: "", color: "var(--mut)" });
  const [bdSaving, setBdSaving] = useState(false);

  function saveBands() {
    setBdSaving(true);
    setBdStatus({ text: "saving…", color: "var(--mut)" });
    fetch("/api/score-bands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bands: { ...bd, Building: 0 } }),
    })
      .then((r) => r.json())
      .then((j) => {
        if (j.ok) {
          setBd({ ...j.bands });
          setBdStatus({ text: "saved", color: "var(--good)" });
        } else {
          // The server validates the SCALE, not each field, so its message names the rule.
          setBdStatus({ text: j.error || "not saved", color: "var(--bad)" });
        }
      })
      .catch(() => setBdStatus({ text: "not saved", color: "var(--bad)" }))
      .finally(() => setBdSaving(false));
  }

  useEffect(() => {
    let cancelled = false;
    fetch("/api/manage/calibrate.json")
      .then((r) => r.json())
      .then((d) => {
        if (cancelled || !d.ok) return;
        setData(d);
        setRatings(Object.fromEntries(d.board.map((p: Person) => [p.login, p.mine || 0])));
        setWt({ ...d.weights.def, ...d.weights.cur });
        setBd({ ...d.bands.def, ...d.bands.cur });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  function saveLabel(login: string, rating: number) {
    setRatings((r) => ({ ...r, [login]: rating }));
    setSaved((s) => ({ ...s, [login]: { text: "…", color: "var(--mut)" } }));
    fetch("/api/score-label", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subject: login, rating, note: notes[login] || "" }),
    })
      .then((r) => r.json())
      .then((j) => {
        setSaved((s) => ({
          ...s,
          [login]: j && j.ok ? { text: "saved ✓", color: "var(--good)" } : { text: (j && j.error) || "failed", color: "var(--bad)" },
        }));
      })
      .catch(() => setSaved((s) => ({ ...s, [login]: { text: "failed", color: "var(--bad)" } })));
  }

  const wtTotal = () => KEYS.reduce((t, [k]) => t + Math.round(wt[k] || 0), 0);

  function saveWeights() {
    if (wtTotal() <= 0) {
      setWtStatus({ text: "at least one pillar must be > 0", color: "var(--bad)" });
      return;
    }
    setWtSaving(true);
    setWtStatus({ text: "saving…", color: "var(--mut)" });
    fetch("/api/score-weights", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ weights: wt }),
    })
      .then((r) => r.json())
      .then((j) => {
        setWtSaving(false);
        if (j && j.ok) {
          setWt({ ...j.weights });
          setWtStatus({ text: "saved ✓ — new scores on next report load", color: "var(--good)" });
        } else {
          setWtStatus({ text: (j && j.error) || "save failed", color: "var(--bad)" });
        }
      })
      .catch(() => {
        setWtSaving(false);
        setWtStatus({ text: "save failed", color: "var(--bad)" });
      });
  }

  const rater = data?.rater ?? "";
  const board = data?.board ?? [];
  const total = wtTotal() || 1;

  return (
    <>
      <h1>
        Calibrate the developer score <span className="exp">Ground truth</span>
      </h1>
      <p className="sub">
        Rate people 1–5 on overall engineering contribution as you see it — this is the ground truth
        the score is calibrated against, not the score itself. Your ratings are private to you until
        aggregated; re-rating updates.{" "}
        {rater && rater !== "anon" ? (
          <>
            Signed in as <b>{rater}</b>.
          </>
        ) : (
          <>
            <b>Note:</b> your identity could not be read from the proxy, so ratings save under ‘anon’ —
            set up user headers to attribute them.
          </>
        )}
      </p>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Person</th>
              <th className="num">Score</th>
              <th>Your rating</th>
              <th className="num">All raters</th>
              <th>Note</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {data && board.length === 0 ? (
              <tr>
                <td colSpan={6} className="empty">
                  No scored people yet.
                </td>
              </tr>
            ) : (
              board.map((p) => {
                const cur = ratings[p.login] || 0;
                return (
                  <tr key={p.login} data-subject={p.login}>
                    <td className="who">
                      <b>{p.name}</b>
                      <span className="lg">{p.login}</span>
                    </td>
                    <td className="num sc">{p.score ?? "—"}</td>
                    <td className="stars">
                      {[1, 2, 3, 4, 5].map((n) => (
                        <button
                          key={n}
                          type="button"
                          className={"star" + (cur && n <= cur ? " on" : "")}
                          aria-label={String(n)}
                          onClick={() => saveLabel(p.login, n)}
                        >
                          &#9733;
                        </button>
                      ))}
                    </td>
                    <td className="num avg">{p.avg}</td>
                    <td>
                      <input
                        className="note"
                        placeholder="optional note"
                        aria-label={`note for ${p.login}`}
                        value={notes[p.login] || ""}
                        onChange={(e) => setNotes((nn) => ({ ...nn, [p.login]: e.target.value }))}
                      />
                    </td>
                    <td className="saved" aria-live="polite" style={saved[p.login] ? { color: saved[p.login].color } : undefined}>
                      {saved[p.login]?.text ?? ""}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <section className="wtcard">
        <h2>
          Band floors <span className="exp">Experimental</span>
        </h2>
        <p className="wsub">
          Where the score stops being one label and becomes the next. The score is a weighted
          mean of percentiles, so its median is <b>50 by construction</b> — a floor is therefore
          also a decision about what share of people carry a label, whether it was meant as one
          or not. The floors were 45 / 60 / 75 until they were measured: those put <b>41%</b> of
          the banded population in Building against <b>7%</b> in Strong. Defaults now:
          Developing 30 · Solid 50 · Strong 70. The lowest band always starts at 0.
        </p>
        <div id="bdRows">
          {BAND_KEYS.map(([k, label]) => (
            <div className="wtrow" key={k}>
              <span className="wtlab">{label}</span>
              <input
                type="range" min="1" max="99" step="1"
                value={Math.round(bd[k] || 0)}
                aria-label={`${label} floor`}
                onChange={(e) => {
                  setBd((b) => ({ ...b, [k]: +e.target.value }));
                  setBdStatus({ text: "", color: "var(--mut)" });
                }}
              />
              <span className="wtval">{Math.round(bd[k] || 0)}</span>
            </div>
          ))}
        </div>
        <div className="wtfoot">
          <span className="wt-total">
            {data?.bands.suggested
              ? <>This window suggests <b>{BAND_KEYS.map(([k]) => data.bands.suggested?.[k]).join(" · ")}</b></>
              : <>too few scored people this window to suggest a scale</>}
          </span>
          <span className="wtbtns">
            {data?.bands.suggested && (
              <button
                type="button" className="btn ghost"
                onClick={() => {
                  // Fills the fields, does NOT save. A scale should be accepted by a person
                  // and then hold still; refitting it every window would move labels under
                  // people who had not moved.
                  setBd({ ...(data.bands.suggested ?? {}) });
                  setBdStatus({ text: "suggested — not saved yet", color: "var(--mut)" });
                }}
              >
                Use this window&rsquo;s
              </button>
            )}
            <button
              type="button" className="btn ghost"
              onClick={() => {
                setBd({ ...(data?.bands.def ?? {}) });
                setBdStatus({ text: "reset — not saved yet", color: "var(--mut)" });
              }}
            >
              Reset to defaults
            </button>
            <button type="button" className="btn" disabled={bdSaving} onClick={saveBands}>
              Save floors
            </button>
            <span className="wtsaved" style={{ color: bdStatus.color }}>{bdStatus.text}</span>
          </span>
        </div>
      </section>

      <section className="wtcard">
        <h2>
          Pillar weights <span className="exp">Experimental</span>
        </h2>
        <p className="wsub">
          How the four pillars combine into the compound score. Only the <em>relative</em> sizes
          matter — per person the weights are normalised, and pillars with no data are reweighted out
          (so a person's effective share can differ). Defaults: engagement 20 · delivery 25 · craft 25
          · flow 35. Changes apply on the next report load.
        </p>
        <div id="wtRows">
          {KEYS.map(([k, label]) => {
            const v = Math.round(wt[k] || 0);
            return (
              <div className="wtrow" key={k}>
                <span className="wtlab">{label}</span>
                <input
                  type="range"
                  min="0"
                  max="60"
                  step="1"
                  value={v}
                  data-wt={k}
                  aria-label={`${label} weight`}
                  onChange={(e) => {
                    setWt((w) => ({ ...w, [k]: +e.target.value }));
                    setWtStatus({ text: "", color: "var(--mut)" });
                  }}
                />
                <span className="wtval">
                  {v}
                  <span className="eff">{Math.round((100 * v) / total)}% of score</span>
                </span>
              </div>
            );
          })}
        </div>
        <div className="wtfoot">
          <span className="wt-total">
            Total <b id="wtTotal">{wtTotal()}</b> · normalised at scoring
          </span>
          <span className="wtbtns">
            <button
              type="button"
              id="wtReset"
              className="btn ghost"
              onClick={() => {
                setWt({ ...(data?.weights.def ?? {}) });
                setWtStatus({ text: "reset — not saved yet", color: "var(--mut)" });
              }}
            >
              Reset to defaults
            </button>
            <button type="button" id="wtSave" className="btn" disabled={wtSaving} onClick={saveWeights}>
              Save weights
            </button>
            <span id="wtStatus" className="wtsaved" style={{ color: wtStatus.color }}>
              {wtStatus.text}
            </span>
          </span>
        </div>
      </section>
    </>
  );
}
