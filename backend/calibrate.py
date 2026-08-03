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
    conn = store.connect()
    try:
        board = store.developer_scores(conn, "2008-01-01T00:00:00Z",
                                       "2099-01-01T00:00:00Z")["board"]
        summ = store.label_summary(conn)
        mine = {r["subject_login"]: r["rating"]
                for r in store.read_score_labels(conn) if r["rater"] == rater}
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
            "weights": {"cur": store._score_weights(), "def": dict(store._SCORE_WEIGHTS)}}
