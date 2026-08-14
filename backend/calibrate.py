#!/usr/bin/env python3
"""Calibrate page (/calibrate) — collect GROUND-TRUTH labels for the developer score.

Managers/peers rate people 1–5; those labels are the target the score backtest
validates against (the score itself has no external "good developer" signal). One
rating per rater per subject; re-rating updates. Deliberately plain and private
(org-gated) — this is a calibration tool, not a public scoreboard.
"""
from __future__ import annotations


def calibrate_json(rater: str = "") -> dict:
    """Data for the React /calibrate route: the score board with each person's
    all-rater summary + this rater's own rating, plus the pillar weights. Raw
    values; the React page escapes on output."""
    import store
    SINCE, UNTIL = "2008-01-01T00:00:00Z", "2099-01-01T00:00:00Z"
    conn = store.connect()
    try:
        # ONE scoring run for the whole request. It used to be three — this line, then
        # suggest_score_bands scoring the window again internally, then a third call kept
        # only for active_pillars — over a 2008-2099 span, where a single run costs seconds.
        sc = store.developer_scores(conn, SINCE, UNTIL)
        board, act = sc["board"], sc["active_pillars"]
        summ = store.label_summary(conn)
        mine = {r["subject_login"]: r["rating"]
                for r in store.read_score_labels(conn) if r["rater"] == rater}
        # Suggested band floors from THIS board's own distribution — the same run the page
        # shows, so the suggestion and the rows a rater is looking at cannot disagree. Only
        # ever a suggestion: floors pinned to quantiles every window would make a person's
        # label move when the team moves, on top of the score already doing so.
        suggested = store.suggest_score_bands(sc)
        # The scores that actually get banded, sorted. Sent so the editor can say what share
        # a floor CAPTURES as the slider moves — the number you actually want when setting a
        # threshold, and the one that stops a floor being read as a percentile. A floor is a
        # SCORE, and the score is a weighted mean of percentiles, so the two are not the same
        # scale: 30 sits near the 11th percentile of people, not the 30th, and averaging keeps
        # the whole population inside roughly 18-85 rather than 0-100.
        # Filter on each person's OWN scored set (scored_on), NOT the team's active_pillars:
        # someone whose flow was renormalised away for want of data is still banded and still a
        # real score, so they belong in this population. This predicate MUST stay identical to
        # suggest_score_bands' full-coverage filter — the floors it computes and the "captures
        # N%" this dist annotates them with have to be measured over the same people.
        dist = sorted(p["score"] for p in board
                      if all(p["pillars"].get(k) is not None for k in (p.get("scored_on") or act)))
    finally:
        conn.close()
    rows = []
    for p in board:
        lg = p["login"]
        s = summ.get(lg)
        avg = (f'{s["mean"]} · {s["n"]} rater' + ("s" if s["n"] != 1 else "")) if s else "—"
        rows.append({"login": lg, "name": p["name"], "score": p["score"],
                     "avg": avg, "mine": mine.get(lg, 0)})
    return {"rater": rater, "board": rows,
            "weights": {"cur": store._score_weights(), "def": dict(store._SCORE_WEIGHTS)},
            "bands": {"cur": store._score_band_floors(),
                      "def": {b: lo for lo, b, _ in store._SCORE_BANDS},
                      "suggested": suggested, "dist": dist}}
