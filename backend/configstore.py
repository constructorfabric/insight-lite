#!/usr/bin/env python3
"""Server-owned config overlay.

`config.yaml` in the repo is the documented *base* (deployed as code, keeps its
comments). Portal edits — repo classification (platform/app/ignore), per-repo
element, new elements, and new orgs/repos — are the ONLY runtime source of truth,
in the DB `override` table: `load_overlay()` reads them from the DB and
`apply_overlay()` layers them over the base, keeping the heavily-commented base
config intact.

There used to be a `config.local.yaml` mirror, written from the DB after every save
and seeded back into it on first run. Both roles are gone (2026-07-28): the file was
a projection of these same scopes, so it stored nothing new, and the seed that read
it back is how a fixture repo classification (`repo/o/lib -> {"classification":
"sdk"}`, for a repo that does not exist) reached the prod override table.

Overlay shape (all keys optional):
    repo_class:       {repo_name: platform|app|ignore}
    repo_element:     {repo_name: ElementName}
    elements_extra:   [NewElement, ...]         # element names with no repos yet
    extra_orgs:       [org, ...]                # appended to base extra_orgs
    extra_repos:      [org/name, ...]           # appended to base extra_repos
    <blob key>:       the whole sub-tree, replacing the base (see BLOB_KEYS)

BLOB_KEYS exist because the merge-per-item scopes above only cover the parts the
editors expose. Everything else in config.yaml was FILE-only, which is fine while the
file travels with the deployment and breaks the moment it does not: a deployment that
pulls this repo gets the shipped generic config, and the AI-tool markers, the
provenance/framework/tracker blocks, the bot-login denylist and the identity bridges
silently revert to defaults — panels switch off, service accounts reappear as people,
LOC shifts. Nothing errors, which is the whole problem. Storing them in the DB (which
lives on the data volume, not in the image) means the file can stay generic and
publishable while a deployment keeps its real policy. `reportctl.py config-capture`
does the one-time import from file to DB.
"""
from __future__ import annotations

import os
import re

import yaml

import paths

# The repo root, one level up from backend/: templates/, assets/ and config.yaml
# live there, not next to the modules.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


CLASSES = ("platform", "app", "ignore")
# override scopes the config editor owns — used for its concurrency token.
# "semantic" holds the scoped taxonomy/flow patches (see semantic.py); it is part
# of the config editor's domain so its version token covers taxonomy edits too.
CONFIG_SCOPES = ("repo", "repo_type", "element_extra", "extra_org", "extra_repo",
                 "company_domain", "setting", "semantic", "base")

# Config keys the overlay replaces WHOLESALE, stored under the `setting` scope as
# {"value": <sub-tree>}. Wholesale rather than merged on purpose: these are policy
# lists, and a merge would make the shipped defaults undeletable — you could add an
# AI-tool marker but never drop one, and a denylist you cannot shorten is not a
# denylist. Absent override = the base file value, unchanged.
BLOB_KEYS = ("ai_tools", "studio_provenance", "gears_usage", "fabric_trackers",
             "specs", "meaningful_loc", "bot_logins", "identity_overrides",
             "migration_title_prefixes")

# The STRUCTURAL config — what gets collected and how it is grouped. Stored under the
# `base` scope and applied as a BASE LAYER, i.e. before every per-item rule below, so
# a captured value behaves exactly as the file's did and UI edits still win over it.
#
# Two reasons it is separate from BLOB_KEYS. First the ordering above: BLOB_KEYS replace
# at the END (they are policy, nothing layers on top), while these must sit UNDER the
# repo/element/domain overrides the editors write, or capturing would silently discard
# every classification made in the UI. Second, they are stored VERBATIM rather than
# expanded into per-repo rows: `elements` supports globs (`gears-*`), and expanding a
# glob against today's repo list would quietly change what happens to repos created
# tomorrow — a captured config must mean the same thing as the file it came from, not
# merely produce the same output once.
BASE_KEYS = ("org", "lookback_days", "extra_orgs", "extra_repos", "repo_types",
             "repos", "elements", "companies", "developer_score_weights")


def load_overlay() -> dict:
    """Config overlay assembled from the DB override table (source of truth):
    repo class/element, extra elements, extra orgs/repos, company-domain rules.
    Returns {} if the store is unavailable (base config.yaml still works)."""
    try:
        import store
        conn = store.connect()
        repo = store.read_overrides(conn, "repo")
        elems_extra = list(store.read_overrides(conn, "element_extra").keys())
        extra_orgs = list(store.read_overrides(conn, "extra_org").keys())
        extra_repos = list(store.read_overrides(conn, "extra_repo").keys())
        domains = {k: v.get("company") for k, v in
                   store.read_overrides(conn, "company_domain").items() if v.get("company")}
        colors = {k: v.get("color") for k, v in
                  store.read_overrides(conn, "company_color").items() if v.get("color")}
        repo_type_ov = store.read_overrides(conn, "repo_type")
        settings = store.read_overrides(conn, "setting")
        base_ov = store.read_overrides(conn, "base")
        conn.close()
    except Exception:                # noqa: BLE001 — overlay is optional
        return {}
    ov: dict = {}
    base = {k: v["value"] for k, v in base_ov.items() if "value" in v}
    if base:
        ov["base"] = base
    if (settings.get("org") or {}).get("value"):
        ov["org"] = settings["org"]["value"]
    if (settings.get("lookback_days") or {}).get("value") is not None:
        ov["lookback_days"] = settings["lookback_days"]["value"]
    if (settings.get("dev_score_weights") or {}).get("value"):
        ov["dev_score_weights"] = settings["dev_score_weights"]["value"]
    for key in BLOB_KEYS:
        # `is not None` rather than truthiness: an EMPTY override is a real choice
        # (no bot logins, no identity bridges) and must not silently fall back to the
        # shipped list, which is the failure this whole mechanism exists to prevent.
        val = (settings.get(key) or {}).get("value")
        if val is not None:
            ov[key] = val
    rc = {n: v["classification"] for n, v in repo.items() if v.get("classification")}
    re_ = {n: v["element"] for n, v in repo.items() if v.get("element")}
    if rc:
        ov["repo_class"] = rc
    if re_:
        ov["repo_element"] = re_
    if elems_extra:
        ov["elements_extra"] = elems_extra
    if extra_orgs:
        ov["extra_orgs"] = extra_orgs
    if extra_repos:
        ov["extra_repos"] = extra_repos
    if domains:
        ov["company_domains"] = domains
    if colors:
        ov["company_colors"] = colors
    if repo_type_ov:                          # configurable repository types
        types = [{"id": tid, "name": v.get("name") or tid,
                  "color": v.get("color"), "default": bool(v.get("default")),
                  "order": v.get("order", 0)} for tid, v in repo_type_ov.items()]
        types.sort(key=lambda t: t.get("order", 0))
        for t in types:
            t.pop("order", None)
        ov["repo_types"] = types
    return ov


def apply_overlay(cfg: dict, ov: dict | None = None) -> dict:
    """Layer the overlay onto a base config dict IN PLACE and return it.
    Pure and idempotent: same inputs -> same merged config."""
    ov = ov or {}
    import collect
    # BASE LAYER first: a captured config.yaml standing in for the file, so everything
    # below layers over it exactly as it layered over the file. Applied before the
    # per-item rules on purpose — reversing the order would let a capture wipe the
    # classifications the editors wrote. See BASE_KEYS.
    for key, val in (ov.get("base") or {}).items():
        cfg[key] = val

    # overlay-defined repo types (Config editor) win over the base set
    if ov.get("repo_types"):
        cfg["repo_types"] = ov["repo_types"]
    type_ids = [t["id"] for t in collect.repo_types(cfg)]
    default_type = collect.default_repo_type(cfg)
    repos = cfg.setdefault("repos", {})
    for c in type_ids + ["ignore"]:
        repos.setdefault(c, [])

    # repo classification overrides: move the repo into its type's list. The default
    # type is the fallback for unlisted repos, so it simply drops from every list.
    valid = set(type_ids) | {"ignore"}
    for name, cls in (ov.get("repo_class") or {}).items():
        for c in list(repos.keys()):
            if isinstance(repos[c], list) and name in repos[c]:
                repos[c].remove(name)
        if cls in valid and cls != default_type:
            repos.setdefault(cls, []).append(name)

    # element overrides: add the repo as an EXACT match under the chosen element
    # (exact wins over glob in make_element), removing it from any other bucket.
    elems = cfg.setdefault("elements", {})
    for name in (ov.get("elements_extra") or []):
        elems.setdefault(name, [])
    for repo, elem in (ov.get("repo_element") or {}).items():
        for k, lst in elems.items():
            if k != "default" and isinstance(lst, list) and repo in lst:
                lst.remove(repo)
        if elem and elem != "default":
            elems.setdefault(elem, [])
            if isinstance(elems[elem], list) and repo not in elems[elem]:
                elems[elem].append(repo)

    for key in ("extra_orgs", "extra_repos"):
        base = cfg.setdefault(key, []) or []
        cfg[key] = base
        for v in (ov.get(key) or []):
            if v not in base:
                base.append(v)

    # company-domain rules layered over the base companies.domains map
    dom = ov.get("company_domains") or {}
    if dom:
        comp = cfg.setdefault("companies", {})
        comp.setdefault("domains", {}).update(dom)
    # pinned company colours, same shape: the database wins over config.yaml so a pin
    # set in the UI survives a deployment whose config.yaml comes from git
    cols = ov.get("company_colors") or {}
    if cols:
        comp = cfg.setdefault("companies", {})
        comp.setdefault("colors", {}).update(cols)

    # first-run wizard settings: primary org + lookback window
    if ov.get("org"):
        cfg["org"] = ov["org"]
    if ov.get("lookback_days") is not None:
        cfg["lookback_days"] = ov["lookback_days"]

    # developer-score pillar weights (experimental) — overlay wins per key
    if ov.get("dev_score_weights"):
        w = cfg.setdefault("developer_score_weights", {})
        w.update(ov["dev_score_weights"])

    # policy blocks — replaced whole, see BLOB_KEYS
    for key in BLOB_KEYS:
        if key in ov:
            cfg[key] = ov[key]

    return cfg


def capture_base_into_overlay(conn=None, keys=BLOB_KEYS, base_keys=BASE_KEYS) -> list[str]:
    """Copy the CURRENT config.yaml into the DB overlay: the policy blocks (`keys`,
    scope `setting`) and the structural config (`base_keys`, scope `base`).

    The one-time migration a live deployment runs before its config.yaml stops being
    its own — because it now arrives from git, or from inside an image. After this the
    deployment reads its real configuration from the database, which lives on the data
    volume, and the file can be the generic published one without a single number
    changing. Idempotent, and it never touches a key that is already overridden, so it
    cannot clobber an edit made in the UI.

    Both groups matter for different reasons, and capturing only one is a trap:
    without the policy blocks the AI markers and bot denylist revert; without the
    structural keys the deployment starts collecting `org: your-org`.

    Returns the keys it wrote, structural ones prefixed `base/`.
    """
    import store
    base = base_config()
    own = conn is None
    conn = conn or store.connect()
    try:
        written = []
        existing_settings = store.read_overrides(conn, "setting")
        for key in keys:
            if key in existing_settings:     # already DB-owned: leave the edit alone
                continue
            if key not in base:
                continue
            store.write_override(conn, "setting", key, {"value": base[key]})
            written.append(key)
        existing_base = store.read_overrides(conn, "base")
        for key in base_keys:
            if key in existing_base:
                continue
            if key not in base:
                continue
            store.write_override(conn, "base", key, {"value": base[key]})
            written.append(f"base/{key}")
        return written
    finally:
        if own:
            conn.close()


def base_config() -> dict:
    """The base config.yaml WITHOUT the overlay (for computing minimal diffs and
    showing the documented defaults)."""
    with open(os.path.join(ROOT, "config.yaml")) as fh:
        return yaml.safe_load(fh) or {}


def editor_data() -> dict:
    """Everything the /config editor needs: every known repo with its current
    classification + element, the element vocabulary, and org/repo wiring."""
    import collect
    import ghclient
    import store

    cfg = ghclient.load_config()                 # merged (base + overlay)
    element_of = collect.make_element(cfg)
    ov = load_overlay()
    conn = store.connect()
    counts = {r["repo"]: r["n"] for r in conn.execute(
        "SELECT repo, COUNT(*) n FROM commits WHERE is_bot=0 GROUP BY repo")}
    rows = conn.execute("SELECT key, name, org, classification, element FROM repo "
                        "ORDER BY name COLLATE NOCASE").fetchall()
    version = store.overrides_version(conn, CONFIG_SCOPES)
    # Companies present in the collected data. Read HERE, while the connection is open:
    # the colour editor further down needs it, and reading it after conn.close() raised
    # inside a broad except that returned an empty set — a list quietly missing every
    # company that reached the data through an identity override.
    companies_in_data = {r[0] for r in conn.execute(
        "SELECT DISTINCT company FROM person WHERE company IS NOT NULL AND company != ''")}
    conn.close()

    rc_ov = ov.get("repo_class") or {}
    re_ov = ov.get("repo_element") or {}
    default_type = collect.default_repo_type(cfg)
    repos = []
    for r in rows:
        name = r["name"]
        repos.append({
            "key": r["key"], "name": name, "org": r["org"] or "",
            "classification": r["classification"] or _norm_cls(collect.classify(name, cfg), default_type),
            "element": r["element"] or element_of(name),
            "commits": counts.get(r["key"], 0),
            "class_overridden": name in rc_ov,
            "element_overridden": name in re_ov,
        })

    elems = cfg.get("elements", {}) or {}
    element_names = sorted({k for k in elems if k != "default"} | {"Other"})

    # company-domain rules: base config.yaml defaults + DB overrides, tagged by source
    base = base_config()
    base_domains = (base.get("companies") or {}).get("domains") or {}
    ov_domains = ov.get("company_domains") or {}
    domains = []
    for d in sorted(set(base_domains) | set(ov_domains)):
        domains.append({"domain": d, "company": ov_domains.get(d, base_domains.get(d)),
                        "source": "override" if d in ov_domains else "base"})
    # "Constructor" used to sit in this default set — our own organisation's name, in a
    # public repository, among generic placeholders. The list is now derived, and from
    # three sources rather than one: the domain rules do NOT cover a company that reached
    # the data through an identity override or a GitHub profile, and the colour editor
    # below has to list every company the report can actually draw. Only the catch-all is
    # guaranteed present.
    companies = sorted({v["company"] for v in domains} | companies_in_data | {"Other"})
    # Colours: what is PINNED (explicitly chosen, in the DB or config.yaml) and what the
    # name-derived default would be, so the editor can show a swatch for every company
    # and still say which ones are deliberate.
    # Pins are returned WHOLE, never filtered against the list above. The editor posts
    # back what it was given, so filtering here would silently delete the pin of any
    # company the list happened to miss — a save would wipe a deliberate choice.
    pinned_colors = store.pinned_company_colors()
    generated_colors = store.company_color_map(sorted(set(companies) | set(pinned_colors)),
                                               pinned={})
    return {
        "repos": repos,
        "repo_types": collect.repo_types(cfg),
        "company_colors": pinned_colors,
        "company_colors_generated": generated_colors,
        "default_type": default_type,
        "classes": list(CLASSES),
        "elements": element_names,
        "elements_extra": list(ov.get("elements_extra") or []),
        "org": cfg.get("org", ""),
        "extra_orgs": list(cfg.get("extra_orgs") or []),
        "extra_repos": list(cfg.get("extra_repos") or []),
        # Which of those came from config.yaml rather than the DB. The overlay only
        # APPENDS to these two lists, so a file-listed entry cannot be removed from
        # the UI — the editor needs to know that to say so, instead of offering an ×
        # that silently has no effect on the next render.
        "extra_orgs_from_file": [o for o in (base.get("extra_orgs") or [])
                                 if o not in (ov.get("extra_orgs") or [])],
        "extra_repos_from_file": [r for r in (base.get("extra_repos") or [])
                                  if r not in (ov.get("extra_repos") or [])],
        "domains": domains,
        "companies": companies,
        "policies": policy_data(),
        "version": version,
    }


def _norm_cls(cls: str, default_type: str = "app") -> str:
    return default_type if cls == "unclassified" else cls


def overlay_from_post(payload: dict) -> dict:
    """Turn the editor's full selection into a MINIMAL overlay: only repos whose
    class/element differ from the base config are stored, so the overlay stays a
    small, readable diff (and base changes still flow through). Repository type
    definitions (repo_types) are stored in full — they're small and user-owned."""
    import collect

    base = base_config()
    # repo types the editor is saving define the valid type ids + base default
    post_types = payload.get("repo_types")
    valid_ids = ({t["id"] for t in post_types if t.get("id")} if post_types
                 else {t["id"] for t in collect.repo_types(base)})
    base_default = collect.default_repo_type({**base, **({"repo_types": post_types} if post_types else {})})
    base_elem = collect.make_element(base)
    rc, re_ = {}, {}
    for name, cls in (payload.get("repo_class") or {}).items():
        if (cls in valid_ids or cls == "ignore") and \
                cls != _norm_cls(collect.classify(name, base), base_default):
            rc[name] = cls
    for name, elem in (payload.get("repo_element") or {}).items():
        if elem and elem != base_elem(name):
            re_[name] = elem
    ov = {}
    if rc:
        ov["repo_class"] = rc
    if re_:
        ov["repo_element"] = re_
    extra_elems = [e for e in (payload.get("elements_extra") or []) if e and e != "default"]
    if extra_elems:
        ov["elements_extra"] = sorted(set(extra_elems))
    # org/repo additions are preserved from whatever the overlay already had plus
    # any the editor kept in its lists beyond the base
    base_orgs = set(base.get("extra_orgs") or [])
    base_repos = set(base.get("extra_repos") or [])
    add_orgs = sorted({o for o in (payload.get("extra_orgs") or []) if o and o not in base_orgs})
    add_repos = sorted({r for r in (payload.get("extra_repos") or []) if r and r not in base_repos})
    if add_orgs:
        ov["extra_orgs"] = add_orgs
    if add_repos:
        ov["extra_repos"] = add_repos
    # company-domain rules: store only those that differ from the base map
    base_dom = (base.get("companies") or {}).get("domains") or {}
    dom = {}
    for d, c in (payload.get("company_domains") or {}).items():
        d = (d or "").strip().lower()
        c = (c or "").strip()
        if d and c and base_dom.get(d) != c:
            dom[d] = c
    if dom:
        ov["company_domains"] = dom
    # pinned company colours. Only those differing from the base file are stored, and an
    # empty/absent value means "no pin" — the colour goes back to being derived from the
    # name, which is the documented default rather than a missing value.
    base_cols = (base.get("companies") or {}).get("colors") or {}
    cols = {}
    for n, c in (payload.get("company_colors") or {}).items():
        n = (n or "").strip()
        c = (c or "").strip()
        if n and re.fullmatch(r"#[0-9a-fA-F]{6}", c) and base_cols.get(n) != c:
            cols[n] = c.lower()
    if cols:
        ov["company_colors"] = cols
    # repository types: stored in full only when they differ from the built-in defaults
    if post_types and post_types != collect.repo_types(base):
        clean = []
        for t in post_types:
            if t.get("id"):
                clean.append({"id": t["id"], "name": t.get("name") or t["id"],
                              "color": t.get("color"), "default": bool(t.get("default"))})
        if clean and not any(t.get("default") for t in clean):
            clean[-1]["default"] = True         # always have exactly one fallback type
        if clean:
            ov["repo_types"] = clean
    # NOTE: developer-score pillar weights are NOT handled here. They're owned by
    # the Calibrate page (save_score_weights) and live independently in the setting
    # scope, so a Config save never touches — or silently wipes — them.
    return ov


def refresh_editor() -> str:
    """(Re)write config-editor.html from the current data + config — the only file
    this module writes. Returns the path written."""
    path = str(paths.data_path("config-editor.html"))
    with open(path, "w") as fh:
        fh.write(render_page())
    return path


def save_overlay(ov: dict) -> None:
    """Persist a config overlay into the DB override table — the only place it lives
    (atomic per scope). Only the config-editor scopes are touched (repo class/element,
    extra elements, extra orgs/repos) — company_domain / person overrides are owned
    elsewhere and are NOT wiped here."""
    import store
    conn = store.connect()
    try:
        repo: dict = {}
        for n, c in (ov.get("repo_class") or {}).items():
            repo.setdefault(n, {})["classification"] = c
        for n, e in (ov.get("repo_element") or {}).items():
            repo.setdefault(n, {})["element"] = e
        store.replace_overrides(conn, "repo", repo)
        rt = {}
        for i, t in enumerate(ov.get("repo_types") or []):
            if t.get("id"):
                rt[t["id"]] = {"name": t.get("name") or t["id"], "color": t.get("color"),
                               "default": bool(t.get("default")), "order": i}
        store.replace_overrides(conn, "repo_type", rt)
        store.replace_overrides(conn, "element_extra", {n: {} for n in (ov.get("elements_extra") or [])})
        store.replace_overrides(conn, "extra_org", {o: {} for o in (ov.get("extra_orgs") or [])})
        store.replace_overrides(conn, "extra_repo", {r: {} for r in (ov.get("extra_repos") or [])})
        store.replace_overrides(conn, "company_domain",
                                {d: {"company": c} for d, c in (ov.get("company_domains") or {}).items()})
        store.replace_overrides(conn, "company_color",
                                {n: {"color": c} for n, c in (ov.get("company_colors") or {}).items()})
        # developer-score weights are owned by the Calibrate page (save_score_weights)
        # and are deliberately NOT written or cleared here — a Config save leaves the
        # `setting/dev_score_weights` override exactly as it was.
    finally:
        conn.close()


def save_score_weights(weights: dict) -> dict:
    """Persist the developer-score pillar weights (Calibrate page). Owned here,
    independent of the big config overlay: coerces the four pillars to non-negative
    integers and upserts the single `setting/dev_score_weights` override. When the
    values are all back at the base defaults the override is cleared instead of
    pinned, so "reset to defaults" truly resets. Returns the effective weights."""
    import store
    defaults = store._SCORE_WEIGHTS
    w = {}
    for k in ("engagement", "delivery", "craft", "flow"):
        if k in (weights or {}):
            try:
                w[k] = max(0, round(float(weights[k])))
            except (TypeError, ValueError):
                pass
    if sum(w.values()) <= 0:
        raise ValueError("weights must include at least one positive pillar")
    conn = store.connect()
    try:
        if all(w.get(k) == defaults[k] for k in defaults):
            store.delete_override(conn, "setting", "dev_score_weights")
        else:
            store.write_override(conn, "setting", "dev_score_weights", {"value": w})
    finally:
        conn.close()
    return store._score_weights()


# Human-facing description per policy block, shown above its editor. Kept next to
# BLOB_KEYS so a key added there without a blurb is obvious.
POLICY_LABELS = {
    "ai_tools": ("AI-tool markers",
                 "Commit-message patterns that attribute a commit to a tool. "
                 "Each marker is exact (an authenticated bot trailer) or heuristic "
                 "(a bare mention) — the badge in the report comes from this."),
    "studio_provenance": ("Content provenance",
                          "Markers grepped from the full repo tree, not commit "
                          "messages. The one named by blame_marker is attributed to "
                          "authors by git blame."),
    "gears_usage": ("Framework usage",
                    "Which repos DEPEND ON your shared framework, matched by package "
                    "or crate name. Provider repos are excluded automatically."),
    "fabric_trackers": ("Generic trackers",
                        "Open-ended markers scanned by content (git grep) or by file "
                        "path. Adding one here needs no code change."),
    "specs": ("Spec detection",
              "Every markdown file counts as a spec EXCEPT what these exclusions "
              "match. Changing this moves spec-contribution numbers."),
    "meaningful_loc": ("Meaningful-LOC filter",
                       "Paths excluded from report-facing LOC — generated, vendored, "
                       "lockfiles, binaries. Raw git LOC is unaffected."),
    "bot_logins": ("Bot / service accounts",
                   "Substring match on login; excluded from people metrics. Careful: "
                   "an account merely NAMED like a service may be a real person, and "
                   "a wrongly listed human silently vanishes from every metric."),
    "identity_overrides": ("Identity bridges",
                           "commit email -> GitHub login, for people automatic "
                           "matching cannot connect. Personal data: prefer the "
                           "Identity editor, which keeps these per person."),
    "migration_title_prefixes": ("Migration title prefixes",
                                 "PRs/issues whose title starts with one of these are "
                                 "skipped as duplicates recreated by a migration tool."),
}


def policy_data() -> dict:
    """The policy blocks for the Config editor: current effective YAML per key, the
    base file value to compare against, and whether an override is in force.

    `yaml` is what the editor shows and posts back — YAML rather than a bespoke widget
    per key on purpose: these are nested, rarely-touched structures, and ten hand-built
    forms would be ten things to keep in sync with the collector that reads them.
    """
    import store
    base = base_config()
    try:
        conn = store.connect()
        settings = store.read_overrides(conn, "setting")
        conn.close()
    except Exception:                # noqa: BLE001 — editor must still render
        settings = {}
    out = {}
    for key in BLOB_KEYS:
        label, blurb = POLICY_LABELS.get(key, (key, ""))
        overridden = key in settings and settings[key].get("value") is not None
        effective = settings[key]["value"] if overridden else base.get(key)
        out[key] = {
            "label": label, "blurb": blurb, "overridden": overridden,
            "yaml": _dump_yaml(effective),
            "base_yaml": _dump_yaml(base.get(key)),
        }
    return out


def _dump_yaml(value) -> str:
    if value is None:
        return ""
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True, default_flow_style=False)


def save_policy(key: str, text: str) -> dict:
    """Persist one policy block from YAML text, or clear it when `text` is blank.

    Blank means "back to the file default", and it CLEARS the override rather than
    storing an empty one — otherwise "reset" would pin emptiness, which for
    bot_logins means every service account reappears as a person. Deliberate
    emptiness is still expressible: `[]` or `{}` stores an empty collection.

    Raises ValueError with a readable message on bad YAML or the wrong shape; the
    caller turns that into a 400 so the editor can show it.
    """
    import store
    if key not in BLOB_KEYS:
        raise ValueError(f"unknown policy block: {key}")
    text = (text or "").strip()
    conn = store.connect()
    try:
        if not text:
            store.delete_override(conn, "setting", key)
            return {"key": key, "overridden": False, "yaml": _dump_yaml(base_config().get(key))}
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"not valid YAML: {exc}") from exc
        base_val = base_config().get(key)
        if parsed is None:
            raise ValueError("empty document — clear the field to reset to the default, "
                             "or write [] / {} for a deliberately empty value")
        if base_val is not None and not isinstance(parsed, type(base_val)):
            raise ValueError(f"expected a {type(base_val).__name__}, got "
                             f"{type(parsed).__name__}")
        store.write_override(conn, "setting", key, {"value": parsed})
        return {"key": key, "overridden": True, "yaml": _dump_yaml(parsed)}
    finally:
        conn.close()


def verify_capture() -> dict:
    """Would this deployment survive losing its config.yaml? {ok, differ, file_only}.

    The check the migration actually needs, and the reason it is a command rather than
    a paragraph of instructions: it answers "is the database now sufficient" with a
    diff instead of a promise. It merges the overlay over the REAL file and over an
    EMPTY one and compares — if the database holds everything, both produce the same
    config, and the file can be replaced by anything.

    `differ` names keys that would change: those are captured-or-not questions, and any
    entry there means the swap is not yet safe. `file_only` names keys no override can
    carry at all (cache TTLs, worker counts, timeouts) — they fall back to the code's
    defaults, which is fine for tuning knobs and is reported separately so it cannot be
    mistaken for a problem.
    """
    import copy
    ov = load_overlay()
    from_file = apply_overlay(copy.deepcopy(base_config()), copy.deepcopy(ov))
    from_nothing = apply_overlay({}, copy.deepcopy(ov))
    overlayable = set(BLOB_KEYS) | set(BASE_KEYS)
    differ, file_only = {}, []
    for key in sorted(set(from_file) | set(from_nothing)):
        if from_file.get(key) == from_nothing.get(key):
            continue
        if key in overlayable:
            differ[key] = {"in_db": key in (ov.get("base") or {}) or key in ov}
        else:
            file_only.append(key)
    return {"ok": not differ, "differ": differ, "file_only": file_only}


