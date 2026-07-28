#!/usr/bin/env python3
"""Fast identity re-apply — no GitHub, seconds.

Folds the curated identity overrides (company / name / login aliases) into the data
that was ALREADY collected — the granular event tables, the person dimension, and the
run blob (in the DB run table) — then re-renders report.html. This is what the /identity
editor's Save calls so edits show up in the report immediately, without a full
`reportctl all` (which re-fetches from GitHub and recomputes git-blame LOC).

Caveat: surviving-LOC (git blame) is not recomputed here, so when two accounts are
merged their *activity* (commits/PRs/issues) folds correctly but their blame-based
surviving lines only fully reconcile on the next full collect. Company/name/alias
folding for all counted activity is exact.
"""
from __future__ import annotations

import os

import directory
import render
import store

# The repo root, one level up from backend/: templates/, assets/ and config.yaml
# live there, not next to the modules.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NUM = (int, float)


def _alias_map(people: dict) -> dict:
    """alias login -> canonical login, from each person's `aliases:` list."""
    out: dict = {}
    for canon, meta in people.items():
        for a in ((meta or {}).get("aliases") or []):
            if a and a != canon:
                out[a] = canon
    return out


def apply(do_render: bool = True) -> dict:
    """Apply the curated overrides to the collected data in place; return a summary."""
    people = directory.load_existing()          # {login: {company,name,emails,aliases}}
    alias = _alias_map(people)
    conn = store.connect()

    # 1) fold aliases in the granular event tables (idempotent: re-run is a no-op)
    folded = 0
    for a, canon in alias.items():
        for table, col in (("commits", "author_login"), ("pull_request", "author_login"),
                           ("issue", "author_login"), ("review", "reviewer_login")):
            folded += conn.execute(
                f"UPDATE {table} SET {col}=? WHERE {col}=?", (canon, a)).rowcount

    # 2) person dimension: company/name from overrides; drop folded-alias rows
    for login, meta in people.items():
        meta = meta or {}
        conn.execute("UPDATE person SET company=?, name=? WHERE login=?",
                     (meta.get("company", "Other"), meta.get("name", "") or "", login))
    for a in alias:
        conn.execute("DELETE FROM person WHERE login=?", (a,))

    # 2b) per-person bot overrides: fold into the granular is_bot flag so the
    # period panels honour it immediately (reversible — 0 restores the person).
    # A forced bot is also dropped from the person dim + run blob so all-time
    # panels match; un-botting a false positive fully reconciles on next collect.
    bot_forced, human_forced = [], []
    for login, meta in people.items():
        ib = (meta or {}).get("is_bot")
        if ib is None:
            continue
        val = 1 if ib else 0
        for table, col in (("commits", "author_login"), ("pull_request", "author_login"),
                           ("issue", "author_login")):
            conn.execute(f"UPDATE {table} SET is_bot=? WHERE {col}=?", (val, login))
        (bot_forced if val else human_forced).append(login)
    for login in bot_forced:
        conn.execute("DELETE FROM person WHERE login=?", (login,))
    conn.commit()

    # 3) run blob (DB run table): merge aliases + overlay company/name
    blob = store.read_latest_run(conn)
    if blob and isinstance(blob.get("people"), dict):
        ppl = blob["people"]
        for a, canon in alias.items():
            src = ppl.pop(a, None)
            if src is None:
                continue
            dst = ppl.get(canon)
            if dst is None:
                ppl[canon] = src
                continue
            for k, v in src.items():
                if isinstance(v, _NUM) and isinstance(dst.get(k), _NUM) and not isinstance(v, bool):
                    dst[k] = dst[k] + v
                elif isinstance(v, list):
                    cur = dst.get(k) or []
                    dst[k] = cur + [x for x in v if x not in cur]
                elif k not in dst:
                    dst[k] = v
        for login, meta in people.items():
            if login in ppl:
                ppl[login]["company"] = (meta or {}).get("company", "Other")
                nm = (meta or {}).get("name")
                if nm:
                    ppl[login]["name"] = nm
        for login in bot_forced:            # forced bots leave the people metrics
            ppl.pop(login, None)
        store.upsert_run(conn, blob)          # DB run table is the source of truth
    conn.close()

    # keep the identity editor in sync (regenerated from the data + the overrides)
    try:
        directory.refresh_editor()
    except Exception:                # noqa: BLE001 — editor refresh is best-effort
        pass

    if do_render:
        render.main()
    return {"people": len(people), "aliases": len(alias), "folded_rows": folded,
            "bots_forced": len(bot_forced), "humans_restored": len(human_forced)}


if __name__ == "__main__":
    r = apply()
    print(f"Reindexed: {r['people']} people, {r['aliases']} aliases folded "
          f"({r['folded_rows']} rows), report.html re-rendered.")
