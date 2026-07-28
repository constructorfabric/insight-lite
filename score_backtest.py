#!/usr/bin/env python3
"""Developer-score backtest / calibration loop (EXPERIMENTAL).

We have no external ground-truth label for "good developer", so this does NOT
prove the score is valid in an absolute sense. What it does, over consecutive
historical windows, is measure the two things that legitimately drive weight
choice:

  1. RELIABILITY (test-retest): does a pillar at window T rank people the same
     way at T+1, or is it noise?  Spearman(pillar_T, pillar_{T+1}) pooled over
     every consecutive window pair and every person present in both.  A pillar
     that doesn't persist shouldn't carry much weight.

  2. REDUNDANCY: how correlated are the pillars with each other within a window?
     Highly-correlated pillars double-count the same thing.

  3. PROXY VALIDITY (rework): does a higher score at T predict LESS rework at
     T+1?  "Rework" = reverts authored + issues the person filed that were
     reopened — signals deliberately kept OUT of the score, so this is an
     independent (if imperfect) outcome, not circular.  Expect a NEGATIVE
     correlation.  Attribution is weak (a revert is authored by whoever reverts,
     not the original author), so treat this as directional only.

Output is a readable report (stdout) + a structured dict; it suggests a weight
vector from reliability × rework-alignment, but that's a starting point to argue
with, not gospel.  Ground-truth labels remain the real next step.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import store

_PILLARS = ("engagement", "delivery", "craft", "flow")


# ---- rank-correlation helpers (no scipy) ------------------------------------
def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0                      # 1-based average rank for ties
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def _pearson(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va == 0 or vb == 0:
        return None
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / (va ** 0.5 * vb ** 0.5)


def spearman(pairs):
    """Spearman rho over (x, y) pairs (None-safe). Returns (rho|None, n)."""
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pairs) < 3:
        return None, len(pairs)
    rho = _pearson(_ranks([p[0] for p in pairs]), _ranks([p[1] for p in pairs]))
    return rho, len(pairs)


# ---- window construction ----------------------------------------------------
def _windows(until: datetime, window_days: int, n: int):
    """`n` consecutive non-overlapping [since, until] windows, most-recent first."""
    out = []
    end = until
    for _ in range(n):
        start = end - timedelta(days=window_days)
        out.append((start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    end.strftime("%Y-%m-%dT%H:%M:%SZ")))
        end = start
    return out


def _rework(conn, since, until):
    """Independent 'rework' outcome per person for a window: reverts they authored
    + issues they filed that were later reopened. Raw counts (small numbers)."""
    out: dict = {}
    for r in conn.execute(
        "SELECT author_login lg, COUNT(*) n FROM pull_request WHERE is_bot=0 AND "
        "is_migration=0 AND is_revert=1 AND author_login<>'' AND created_at>=? AND created_at<=? "
        "GROUP BY author_login", (since, until)):
        out[r["lg"]] = out.get(r["lg"], 0) + r["n"]
    for r in conn.execute(
        "SELECT author_login lg, COUNT(*) n FROM issue WHERE is_bot=0 AND is_migration=0 "
        "AND state_reason='REOPENED' AND author_login<>'' AND created_at>=? AND created_at<=? "
        "GROUP BY author_login", (since, until)):
        out[r["lg"]] = out.get(r["lg"], 0) + r["n"]
    return out


def run(conn, window_days: int = 90, n_windows: int = 6, until: str | None = None) -> dict:
    """Compute the calibration report over `n_windows` consecutive windows."""
    end = (datetime.fromisoformat(until.replace("Z", "+00:00")) if until
           else datetime.now(timezone.utc)).replace(microsecond=0)
    wins = _windows(end, window_days, n_windows)          # newest → oldest
    # per window: {login: {pillars:{...}, score:int}} and rework outcome
    boards, reworks = [], []
    for since, unt in wins:
        b = store.developer_scores(conn, since, unt)["by_login"]
        boards.append(b)
        reworks.append(_rework(conn, since, unt))

    # 1) reliability: pillar_T vs pillar_{T+1}, pooled over consecutive pairs
    #    (wins are newest-first, so the OLDER window i+1 is "T", the newer i is "T+1")
    reliability = {}
    for p in _PILLARS:
        pairs = []
        for i in range(len(boards) - 1):
            t, t1 = boards[i + 1], boards[i]
            for lg in set(t) & set(t1):
                pairs.append((t[lg]["pillars"].get(p), t1[lg]["pillars"].get(p)))
        reliability[p] = spearman(pairs)

    # 2) redundancy: pillar-vs-pillar within a window, pooled over all windows
    redundancy = {}
    for a in range(len(_PILLARS)):
        for b_ in range(a + 1, len(_PILLARS)):
            pa, pb = _PILLARS[a], _PILLARS[b_]
            pairs = []
            for board in boards:
                for row in board.values():
                    pairs.append((row["pillars"].get(pa), row["pillars"].get(pb)))
            redundancy[f"{pa}~{pb}"] = spearman(pairs)

    # 2b) discrimination: does the pillar actually SPREAD people, or do most tie?
    #     spread = 1 - (share of the single most common sub-score), pooled over
    #     windows. A near-constant pillar (e.g. flow when few items ever bounce) is
    #     "reliable" but useless for ranking — this stops it being over-weighted.
    discrimination = {}
    for p in _PILLARS:
        spreads = []
        for board in boards:
            vals = [row["pillars"].get(p) for row in board.values()
                    if row["pillars"].get(p) is not None]
            if len(vals) >= 3:
                from collections import Counter
                top = Counter(vals).most_common(1)[0][1]
                spreads.append(1.0 - top / len(vals))
        discrimination[p] = (sum(spreads) / len(spreads)) if spreads else 0.0

    # 3) proxy validity: score_T & pillar_T vs rework_{T+1} (want NEGATIVE)
    val_pairs_score = []
    val_pairs_pillar = {p: [] for p in _PILLARS}
    for i in range(len(boards) - 1):
        t, rw1 = boards[i + 1], reworks[i]
        for lg in set(t) & set(rw1):
            val_pairs_score.append((t[lg]["score"], rw1[lg]))
            for p in _PILLARS:
                val_pairs_pillar[p].append((t[lg]["pillars"].get(p), rw1[lg]))
    validity_score = spearman(val_pairs_score)
    validity_pillar = {p: spearman(val_pairs_pillar[p]) for p in _PILLARS}

    # weight suggestion: reliability × discrimination × rework-alignment. The
    # discrimination factor is the guard — a pillar that doesn't spread people
    # can't earn weight no matter how "reliable" (stable) or aligned it looks.
    raw = {}
    for p in _PILLARS:
        rel = max(0.0, reliability[p][0] or 0.0)
        v = validity_pillar[p][0]
        align = max(0.0, -(v if v is not None else 0.0))   # negative corr is good
        raw[p] = rel * discrimination[p] * (0.5 + align)
    tot = sum(raw.values()) or 1.0
    suggested = {p: round(100 * raw[p] / tot) for p in _PILLARS}

    # supervised validity: does the score agree with GROUND-TRUTH labels? Correlate
    # the most recent window's score against each subject's mean rating (when any).
    labels = store.label_summary(conn)
    recent = boards[0] if boards else {}
    label_validity = spearman([(recent[lg]["score"], labels[lg]["mean"])
                               for lg in labels if lg in recent])

    return {
        "window_days": window_days, "n_windows": n_windows,
        "windows": [{"since": s[:10], "until": u[:10]} for s, u in wins],
        "people_per_window": [len(b) for b in boards],
        "reliability": {p: {"rho": reliability[p][0], "n": reliability[p][1]} for p in _PILLARS},
        "discrimination": discrimination,
        "redundancy": {k: {"rho": v[0], "n": v[1]} for k, v in redundancy.items()},
        "validity_score": {"rho": validity_score[0], "n": validity_score[1]},
        "label_validity": {"rho": label_validity[0], "n": label_validity[1]},
        "validity_pillar": {p: {"rho": validity_pillar[p][0], "n": validity_pillar[p][1]}
                            for p in _PILLARS},
        "current_weights": store._score_weights(),
        "suggested_weights": suggested,
    }


def _fmt(x):
    return "  n/a" if x is None else f"{x:+.2f}"


def report(res: dict) -> str:
    L = []
    L.append(f"Developer-score backtest — {res['n_windows']} × {res['window_days']}d windows")
    L.append("windows (newest→oldest): " +
             ", ".join(f"{w['since']}→{w['until']}" for w in res["windows"]))
    L.append("people scored per window: " + ", ".join(map(str, res["people_per_window"])))
    L.append("")
    L.append("RELIABILITY  (pillar T → T+1; higher = more stable, want > ~0.4)")
    for p, d in res["reliability"].items():
        L.append(f"  {p:<11} rho {_fmt(d['rho'])}   (n={d['n']})")
    L.append("")
    L.append("DISCRIMINATION  (spread of people; 0 = everyone ties, 1 = well spread)")
    for p, d in res["discrimination"].items():
        L.append(f"  {p:<11} {d:.2f}")
    L.append("")
    L.append("REDUNDANCY  (pillar ~ pillar within window; high = double-counting)")
    for k, d in res["redundancy"].items():
        L.append(f"  {k:<22} rho {_fmt(d['rho'])}   (n={d['n']})")
    L.append("")
    L.append("PROXY VALIDITY vs rework at T+1  (want NEGATIVE: high score → less rework)")
    L.append(f"  composite score      rho {_fmt(res['validity_score']['rho'])}   (n={res['validity_score']['n']})")
    for p, d in res["validity_pillar"].items():
        L.append(f"  {p:<11}          rho {_fmt(d['rho'])}   (n={d['n']})")
    L.append("")
    lv = res["label_validity"]
    if lv["n"]:
        L.append(f"GROUND-TRUTH VALIDITY  score vs manager/peer label — rho {_fmt(lv['rho'])} (n={lv['n']})")
    else:
        L.append("GROUND-TRUTH VALIDITY  no labels yet — collect some at /calibrate")
    L.append("")
    L.append("WEIGHTS")
    L.append("  current   : " + ", ".join(f"{p} {int(round(v))}" for p, v in res["current_weights"].items()))
    L.append("  suggested : " + ", ".join(f"{p} {v}" for p, v in res["suggested_weights"].items())
             + "   (reliability × rework-alignment; a starting point, not gospel)")
    return "\n".join(L)


def main():
    conn = store.connect()
    try:
        res = run(conn)
    finally:
        conn.close()
    print(report(res))


if __name__ == "__main__":
    main()
