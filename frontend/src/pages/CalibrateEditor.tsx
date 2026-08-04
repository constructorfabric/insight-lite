// /calibrate — collect ground-truth developer-score labels + tune pillar weights,
// migrated to React (Manage migration). Fetches GET /api/manage/calibrate.json and
// reproduces calibrate.render_page()'s markup + classes (see ../styles/calibrate.css).
// Behaviours ported from the page's inline JS: click a star → POST /api/score-label;
// weight sliders with live %, Save → POST /api/score-weights, Reset.
//
// SSR-safe: no top-level window/document access — only inside effects / handlers.
import { useEffect, useState } from "react";

import { PILLAR_COLORS } from "../lib/tokens";

type Person = { login: string; name: string; score: number | null; avg: string; mine: number };
type Weights = { cur: Record<string, number>; def: Record<string, number> };
// The band scale: where the score stops being one label and becomes the next. `suggested`
// is fitted to THIS window's distribution and is null when there is too little to fit —
// and it stays a suggestion, because floors pinned to quantiles every window would make a
// person's label move when the team moves, on top of the score already doing so.
type Bands = {
  cur: Record<string, number>; def: Record<string, number>;
  suggested: Record<string, number> | null;
  /** The banded scores, sorted — so a floor can be shown as the share it captures. */
  dist: number[];
};
type Data = { rater: string; board: Person[]; weights: Weights; bands: Bands };

// Ascending, and the lowest is fixed at 0 — the scale has to start somewhere, and the
// server rejects anything that does not ascend from there.
const BAND_KEYS: [string, string][] = [
  ["Developing", "Developing from"],
  ["Solid", "Solid from"],
  ["Strong", "Strong from"],
];

// A floor is a SCORE, not a percentile of people. The two are easy to confuse because the
// score is built out of percentiles — but it is a weighted MEAN of them, and averaging pulls
// values toward the middle, so a floor of 30 holds the bottom 11% rather than the bottom
// 30%, and the whole population sits inside roughly 18-85 instead of 0-100. Only 50 lines up,
// and only because that is the median by construction.
//
// The first attempt printed a cumulative share per slider, "9% below", which raised the fair
// question: below what. It was also the wrong number. Three floors define four bands, and
// what you are calibrating is how big those bands come out — so the editor reports the BANDS,
// which needs no preposition to be unambiguous.
// One colour per band STEP. tone would not do: the model gives Strong and Solid the same
// "good", so two bands would come out the same colour on a scale that has to show four.
// var(), not the generated token map — see the same ramp in PersonScore: this lands in a
// `background:` inline style, so a custom property resolves and flips with the theme, while
// the JS map carries the light palette only.
const BAND_RAMP = ["var(--bad)", "var(--warn)", "var(--c-pr)", "var(--good)"];

function bandSizes(dist: number[], floors: [string, number][]): { band: string; n: number }[] {
  const asc = floors.slice().sort((a, b) => a[1] - b[1]);
  return asc.map(([band, lo], i) => {
    const hi = i + 1 < asc.length ? asc[i + 1][1] : Infinity;
    return { band, n: dist.filter((s) => s >= lo && s < hi).length };
  });
}

const KEYS: [string, string][] = [
  ["engagement", "Engagement"],
  ["delivery", "Delivery"],
  ["craft", "Craft & rework"],
  ["flow", "Flow"],
];

// The distribution with the floors drawn on it. This replaces most of what used to be prose:
// the axis is scores, so nobody has to be told a floor is a score rather than a percentile;
// the bars show the population really occupying ~18-85 rather than 0-100; and the coloured
// regions ARE the band sizes, so "41% in Building" is a thing you see instead of read.
function FloorChart({ dist, floors }: { dist: number[]; floors: [string, number][] }) {
  const BUCKET = 2;
  const bins = new Array(Math.ceil(100 / BUCKET)).fill(0) as number[];
  for (const s of dist) bins[Math.min(bins.length - 1, Math.max(0, Math.floor(s / BUCKET)))] += 1;
  const max = Math.max(...bins, 1);
  const asc = floors.slice().sort((a, b) => a[1] - b[1]);
  const bandAt = (score: number) => {
    let k = 0;
    asc.forEach(([, lo], i) => { if (score >= lo) k = i; });
    return k;
  };
  return (
    <div className="bdchart">
      <div className="bdbars">
        {bins.map((n, i) => (
          <i
            key={i}
            style={{
              height: `${(100 * n) / max}%`,
              background: BAND_RAMP[Math.round((bandAt(i * BUCKET + BUCKET / 2) * (BAND_RAMP.length - 1))
                                              / Math.max(1, asc.length - 1))],
            }}
            title={`${i * BUCKET}–${i * BUCKET + BUCKET}: ${n}`}
          />
        ))}
        {asc.slice(1).map(([band, lo]) => (
          <u key={band} style={{ left: `${lo}%` }}><em>{lo}</em></u>
        ))}
      </div>
      <div className="bdaxis">
        {[0, 25, 50, 75, 100].map((v) => <span key={v} style={{ left: `${v}%` }}>{v}</span>)}
      </div>
    </div>
  );
}

// The weights, as the split they produce. "Only the relative sizes matter" and "the total is
// normalised at scoring" are two sentences describing one picture: four segments adding to
// 100%. Dragging a slider moves the segments, so 20/25/25/35 summing to 105 and coming out as
// 19/24/24/33 needs no explaining. The colours are the pillar colours used on the Person page,
// so the split and the per-person make-up bars read as the same thing.
function WeightSplit({ wt }: { wt: Record<string, number> }) {
  const total = KEYS.reduce((a, [k]) => a + (wt[k] || 0), 0) || 1;
  return (
    <div className="wtsplit">
      <span className="wtsplit-b">
        {KEYS.map(([k]) => (
          <i key={k} style={{ width: `${(100 * (wt[k] || 0)) / total}%`,
                              background: PILLAR_COLORS[k] }} />
        ))}
      </span>
      <span className="wtsplit-k">
        {KEYS.map(([k, label]) => (
          <em key={k}>
            <i style={{ background: PILLAR_COLORS[k] }} />
            {label} <b>{Math.round((100 * (wt[k] || 0)) / total)}%</b>
          </em>
        ))}
      </span>
    </div>
  );
}

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
          // Prefixed, so the text alone distinguishes the two outcomes — colour is
          // not the only carrier and is no carrier at all when read aloud.
          setBdStatus({ text: `not saved — ${j.error || "rejected"}`, color: "var(--bad)" });
        }
      })
      .catch(() => setBdStatus({ text: "not saved — request failed", color: "var(--bad)" }))
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
          Where the score stops being one label and becomes the next. Every bar is people at
          that score; a floor is a score, not a share.
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
              <span className="wtval">
                {Math.round(bd[k] || 0)}
                <span className="eff">score</span>
              </span>
            </div>
          ))}
        </div>
        {(data?.bands.dist.length ?? 0) > 0 && (
          <FloorChart
            dist={data?.bands.dist ?? []}
            floors={[["Building", 0],
                     ...BAND_KEYS.map(([k]) => [k, Math.round(bd[k] || 0)] as [string, number])]}
          />
        )}
        {(data?.bands.dist.length ?? 0) > 0 && (
          <p className="bdsizes">
            {bandSizes(data?.bands.dist ?? [], [["Building", 0],
                                                ...BAND_KEYS.map(([k]) => [k, Math.round(bd[k] || 0)] as [string, number])])
              .map((b, i) => (
                <span key={b.band}>
                  {i > 0 && " · "}
                  <b>{b.band} {b.n}</b>
                  <span className="mut"> ({Math.round((100 * b.n) / (data?.bands.dist.length || 1))}%)</span>
                </span>
              ))}
            {" "}of {data?.bands.dist.length}.
          </p>
        )}
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
            {/* aria-live, like the per-person status cell above: without it, pressing
                Save is silent to a screen reader, including when the server rejects
                the scale and its message is the only explanation. */}
            <span className="wtsaved" role="status" aria-live="polite"
                  style={{ color: bdStatus.color }}>{bdStatus.text}</span>
          </span>
        </div>
      </section>

      <section className="wtcard">
        <h2>
          Pillar weights <span className="exp">Experimental</span>
        </h2>
        <p className="wsub">
          How the four pillars combine. A pillar a person has no data for is reweighted out, so
          their own split can differ from this one.
        </p>
        <WeightSplit wt={wt} />
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
                  <span className="eff">weight</span>
                </span>
              </div>
            );
          })}
        </div>
        <div className="wtfoot">
          <span className="wt-total">
            Weights need not sum to 100 — the split above is what they mean.
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
            <span id="wtStatus" className="wtsaved" role="status" aria-live="polite"
                  style={{ color: wtStatus.color }}>
              {wtStatus.text}
            </span>
          </span>
        </div>
      </section>
    </>
  );
}
