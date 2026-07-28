#!/usr/bin/env python3
"""Generate / refresh the people directory (identity + company).

The curated roster lives ONLY in the SQLite `override` table (scope `person`); this
module reads it, merges it with the collected run, and bakes one file:
  * identity-editor.html — self-contained editor. Open through the local portal
                           for direct server-side save, or as a file for the
                           browser save/download fallback.

It used to also emit people.yaml as a "read-only backup". That file is gone: it held
nothing the table did not, and the code that read it back imported a test fixture into
the prod override table (see store.py, where the seed used to live).

Workflow:
    python collect.py        # collects into the store (auto identity + company)
    python directory.py      # identity-editor.html
    python reportctl.py serve --host 127.0.0.1 --port 8080
    # edit at /identity, Save -> the override table
    python collect.py        # honours the overrides
"""
from __future__ import annotations

import json
import os
from difflib import SequenceMatcher

import paths

ROOT = os.path.dirname(os.path.abspath(__file__))
EDITOR = str(paths.data_path("identity-editor.html"))


# Contact handles curated in the identity editor. Deliberately NOT part of identity
# RESOLUTION: the resolver maps commits to people through email addresses and scores
# identity_confidence from that evidence, and a chat handle says nothing about who
# authored a commit. Feeding these in would dilute the one signal there is a reason to
# trust. They are contact attributes, stored alongside the roster, read by nothing that
# attributes work.
CONTACT_FIELDS = ("discord", "telegram")


def normalize_handle(kind: str, raw) -> str:
    """Trim/normalise one chat handle, or raise ValueError with a usable message.

    Lenient on purpose. A roster editor that rejects a legitimate handle is worse than
    one that stores an odd-looking string: the value is only ever displayed and copied,
    never resolved against anything, so the cost of a typo is low and the cost of a
    false rejection is a human unable to record a real fact. So: strip a leading '@'
    (everyone pastes it), collapse whitespace, cap the length, and reject only what
    cannot be a handle at all.
    """
    s = ("" if raw is None else str(raw)).strip().lstrip("@").strip()
    if not s:
        return ""
    if len(s) > 64:
        raise ValueError(f"{kind} handle is too long (max 64 characters): {s[:20]}…")
    if any(c.isspace() for c in s):
        raise ValueError(f"{kind} handle cannot contain spaces: {s!r}")
    if kind == "telegram":
        # Telegram usernames are 5-32 of [A-Za-z0-9_]. Length is not enforced — some
        # legacy and reserved names fall outside it — but the character set is, because
        # anything else is a paste of a URL or a display name rather than a handle.
        bad = [c for c in s if not (c.isascii() and (c.isalnum() or c == "_"))]
        if bad:
            raise ValueError(
                f"Telegram handle may only contain letters, digits and underscores "
                f"(got {s!r}). Paste the @username, not a link or display name.")
    elif kind == "discord":
        # Discord moved from name#1234 to a plain lowercase username; both still exist
        # in the wild, so both are accepted and neither is rewritten.
        bad = [c for c in s if not (c.isascii() and (c.isalnum() or c in "._#"))]
        if bad:
            raise ValueError(
                f"Discord handle may only contain letters, digits, dots, underscores "
                f"and '#' (got {s!r}). Use the username, not a link or nickname.")
    return s


def normalize_contacts(info: dict) -> dict:
    """The contact subset of one roster entry, normalised. Empty values are dropped so
    a cleared field removes the key instead of storing ''."""
    out = {}
    for field in CONTACT_FIELDS:
        val = normalize_handle(field, info.get(field))
        if val:
            out[field] = val
    return out


def _asset(name: str) -> str:
    """Raw editor-HTML page from templates/editors/ (extracted from the inline
    r-strings; consumed via .replace('/*DATA*/', ...))."""
    with open(os.path.join(ROOT, "templates", "editors", name), encoding="utf-8") as fh:
        return fh.read()


def load_existing() -> dict:
    """Curated identity, from the DB override table (the source of truth). Shape is the
    people-map the roster has always used: {login: {company,name,emails,aliases,is_bot?}}.

    Raises if the store cannot be read. There used to be a `except Exception: pass`
    fallback onto the people.yaml backup here, and it was a data-loss path, not a
    safety net: a Save REPLACES the whole person scope, so one momentary DB problem
    served the editor a stale roster and the next Save made that stale copy
    canonical — silently discarding every override curated since the backup was
    written. Failing the page load is recoverable; that was not.
    """
    import store
    conn = store.connect()
    try:
        return store.read_overrides(conn, "person")
    finally:
        conn.close()


def render_editor_html(payload: dict) -> str:
    """Embed the JSON payload into the editor template, script-context safe.

    A "</script>" inside any payload string (display name, commit email,
    identity evidence — all attacker-influenced) would otherwise terminate
    the inline <script> element and inject markup.  Escaping "</" as "<\\/"
    is a no-op for the JS parser but neutralises the HTML one.  json.dumps
    uses ensure_ascii=True, so U+2028/U+2029 (JS line separators) are
    already emitted as \\u2028/\\u2029 escapes; the extra replaces below are
    belt-and-braces in case ensure_ascii is ever turned off.
    """
    blob = (json.dumps(payload)
            .replace("</", "<\\/")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))
    import shell
    return (EDITOR_HTML.replace("/*DATA*/", blob)
            .replace("/*SHELL_CSS*/", shell.SHELL_CSS)
            .replace("</style>", shell.BASE_CSS + "</style>", 1)
            .replace("<!--SIDEBAR-->", shell.sidebar_html("identity")))


def build_roster(people: dict, existing: dict) -> dict:
    # logins that are aliases of another entry must NOT reappear as their own row
    aliased = {a for prev in existing.values() for a in ((prev or {}).get("aliases") or [])}
    roster = {}
    for login, p in people.items():
        if login in aliased:               # collect already folded it into its primary
            continue
        prev = existing.get(login, {}) or {}
        # A curated override's emails are AUTHORITATIVE — do not re-union the
        # collect-discovered set, or an email deleted in the editor resurrects on
        # the next refresh and gets re-saved. Only auto-fill emails for logins with
        # no override yet.
        emails = (sorted(prev["emails"]) if prev.get("emails")
                  else sorted(set(p.get("emails", []))))
        company = prev.get("company") or p.get("company", "Other")
        name = prev.get("name") or p.get("name", "")
        roster[login] = {"name": name, "company": company, "emails": emails,
                         "aliases": sorted(prev.get("aliases", []) or []),
                         "commits": p.get("commits", 0), "is_member": p.get("is_member", False),
                         "is_bot": prev.get("is_bot"),
                         # contact handles are curated only — never collected, so the
                         # stored override is the sole source and there is nothing to
                         # merge them against
                         **{f: prev.get(f, "") for f in CONTACT_FIELDS},
                         "identity_confidence": p.get("identity_confidence", "unknown"),
                         "identity_evidence": p.get("identity_evidence", [])}
    for login, prev in existing.items():
        if login in aliased:
            continue
        roster.setdefault(login, {
            "name": (prev or {}).get("name", ""),
            "company": (prev or {}).get("company", "Other"),
            "emails": sorted((prev or {}).get("emails", []) or []),
            "aliases": sorted((prev or {}).get("aliases", []) or []),
            "commits": 0, "is_member": False, "is_bot": (prev or {}).get("is_bot"),
            **{f: (prev or {}).get(f, "") for f in CONTACT_FIELDS},
            "identity_confidence": "manual", "identity_evidence": ["curated override"],
        })
    return roster


def merge_suggestions(roster: dict) -> list:
    """Login pairs that might be the same person (fuzzy name / email local-part)."""
    def toks(login, emails):
        s = login.lower()
        for e in emails:
            s += " " + e.split("@")[0].lower()
        return s
    items = [(l, toks(l, r["emails"])) for l, r in roster.items()]
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, ta = items[i]; b, tb = items[j]
            score = SequenceMatcher(None, ta, tb).ratio()
            if score >= 0.72:
                out.append({"a": a, "b": b, "score": round(score, 2)})
    return sorted(out, key=lambda x: -x["score"])[:30]


def _load_run() -> dict:
    """Latest collected data — from the SQLite store (the source of truth)."""
    import store
    data = store.read_latest_run(store.connect())
    if data is None:
        raise SystemExit("No data in the SQLite store — run `python collect.py` first.")
    return data


def _company_colors(roster: dict) -> dict:
    """Company → colour, using the SAME rule as the report (store.aggregate): the
    fixed CO_COLORS for known companies, else a rank-ordered fallback palette by
    descending commits. Keeps the Identity screen's company dots consistent with the
    company colours everywhere else in the report."""
    try:
        import store
        fixed, palette = store.CO_COLORS, store._PALETTE
    except Exception:                    # noqa: BLE001 — degrade to neutral dots
        return {}
    totals: dict = {}
    for r in roster.values():
        totals[r["company"]] = totals.get(r["company"], 0) + r.get("commits", 0)
    colors, pi = {}, 0
    for co in sorted(totals, key=lambda c: -totals[c]):
        if co in fixed:
            colors[co] = fixed[co]
        else:
            colors[co] = palette[pi % len(palette)]; pi += 1
    return colors


def editor_payload(roster: dict, data: dict) -> dict:
    """Build the identity-editor data payload from a roster. Also attaches each
    person's GitHub-profile hint (name/company/bio) read from the DB person dim —
    the DB is the source, this is only a resolution suggestion."""
    try:
        import store
        conn = store.connect()
        gh = store.gh_profiles(conn)
        conn.close()
    except Exception:                    # noqa: BLE001 — the hint is optional
        gh = {}
    return {
        "people": [{"login": l, "name": r.get("name", ""), "company": r["company"],
                    "emails": r["emails"], "aliases": r.get("aliases", []),
                    "commits": r["commits"], "is_member": r["is_member"],
                    "is_bot": r.get("is_bot"),
                    # a person field has to be listed here as well as in build_roster.
                    # It used to also need listing in each editor's YAML serialiser —
                    # those are gone (the editors POST the roster as JSON), which is
                    # two places fewer to forget. Omitting it here is still invisible
                    # in a build_roster test and shows up only as a field that saves
                    # and then comes back empty, so the payload is pinned in tests.
                    **{f: r.get(f, "") for f in CONTACT_FIELDS},
                    "identity_confidence": r.get("identity_confidence", "unknown"),
                    "identity_evidence": r.get("identity_evidence", []),
                    "gh": gh.get(l, {})}
                   for l, r in sorted(roster.items(), key=lambda kv: -kv[1]["commits"])],
        "companies": sorted({r["company"] for r in roster.values()}
                            | {"Constructor", "Example Inc", "Partner Ltd", "Other"}),
        "company_colors": _company_colors(roster),
        "suggestions": merge_suggestions(roster),
        "bots": data.get("bots", {}),
        # (no "header": the editors used to prepend one to the people.yaml they built
        # themselves; there is no YAML file to head any more)
        "version": _overrides_version(),
    }


def directory_json() -> dict:
    """RAW data payload for the React /identity route — the SAME dict the Jinja
    editor embeds via /*DATA*/ (people/companies/company_colors/suggestions/bots/
    header/version). React escapes on output, so values stay raw. Kept PARALLEL
    to render_page() (the pixel-gate baseline), which must stay byte-unchanged."""
    data = _load_run()
    roster = build_roster(data["people"], load_existing())
    return editor_payload(roster, data)


def render_page(active: str = "identity") -> str:
    """Render the identity editor live from the current DB roster. This is what
    the portal serves at /identity — no baked file involved, so it always
    reflects the latest overrides, concurrency token, and shared sidebar."""
    data = _load_run()
    roster = build_roster(data["people"], load_existing())
    return render_editor_html(editor_payload(roster, data))


def _write_editor(roster: dict, data: dict) -> None:
    """Render identity-editor.html from a roster. The only file this module writes."""
    with open(EDITOR, "w") as fh:
        fh.write(render_editor_html(editor_payload(roster, data)))


def _overrides_version() -> str:
    """Concurrency token for person overrides (empty if the store is unavailable)."""
    try:
        import store
        conn = store.connect()
        v = store.overrides_version(conn, ("person",))
        conn.close()
        return v
    except Exception:                # noqa: BLE001
        return ""


def refresh_editor() -> None:
    """Regenerate identity-editor.html from the current data + the curated overrides.
    Used by the fast reindex so the editor stays in sync with the collected data and
    the current edits after every Save."""
    data = _load_run()
    roster = build_roster(data["people"], load_existing())
    _write_editor(roster, data)


def main() -> None:
    data = _load_run()
    roster = build_roster(data["people"], load_existing())

    by_co = {}
    for r in roster.values():
        by_co[r["company"]] = by_co.get(r["company"], 0) + 1
    print(f"Roster: {len(roster)} people")
    for co, n in sorted(by_co.items(), key=lambda x: -x[1]):
        print(f"  {co:12} {n}")

    _write_editor(roster, data)
    other = by_co.get("Other", 0)
    print(f"Wrote identity-editor.html  ({other} still 'Other' — open it to assign)")


EDITOR_HTML = _asset("identity.html")


if __name__ == "__main__":
    main()
