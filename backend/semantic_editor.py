#!/usr/bin/env python3
"""Scoped semantic-config editor (/semantic).

Taxonomy is stored as item→bucket maps, one patch per (scope, target, axis) in the
`semantic` override scope. The resolver deep-merges per key, so a narrow scope
overrides individual items — the same label can resolve to a different category per
element/repo/project. This editor lets you pick a scope (global / org / element /
repo / project), see the vocab that actually occurs there with the INHERITED value
per item, and override just the deltas. See docs/semantic-config.md.
"""
from __future__ import annotations

import json
import os

import discovery
import semantic

# The repo root, one level up from backend/: templates/, assets/ and config.yaml
# live there, not next to the modules.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


BUCKETS = {
    "categories": ["bug", "feature", "task", "epic", "spec", "docs", "test"],
    "stages": ["backlog", "ready", "in_progress", "review", "qa", "done", "released"],
    "ci": ["gate", "nightly", "release", "ignore"],
}
_AXES = ("categories", "stages", "ci")

# When the canonical vocabulary changes, already-saved override values must be remapped
# so existing taxonomies keep resolving. Idempotent: values already in the new vocab
# (or unknown) are left untouched. See migrate_saved_buckets().
_BUCKET_MIGRATION = {
    "categories": {"story": "feature", "chore": "task", "question": "task", "ci": "task"},
    "stages": {"spec": "ready"},          # the old grooming stage folds into "ready"
    "ci": {},
}
# item-map keys per axis + which of them the editor edits
_ITEM_KEYS = {"categories": ("labels", "types"), "stages": ("statuses",), "ci": ("roles",)}
# axis-level settings that live only at global (not per-item, so not scope-edited here)
_SETTINGS = {
    "categories": {"prefer_source": ["issue_type", "label", "title"], "unmatched": "uncategorized"},
    "stages": {"order": discovery._STAGE_ORDER, "terminal": ["done"], "unmatched": "other"},
    "ci": {"count_events": ["pull_request", "push"], "default_branch_only": True,
           "success_conclusions": ["success"],
           "failure_conclusions": ["failure", "timed_out", "startup_failure"],
           "ignore_conclusions": ["skipped", "cancelled", "neutral"]},
}


def _entity_for(conn, level, target):
    """A representative entity dict so the resolver picks the right parent chain."""
    if level == "global":
        return {}
    if level == "org":
        return {"org": target}
    if level == "element":
        row = conn.execute("SELECT org FROM repo WHERE element=? LIMIT 1", (target,)).fetchone()
        return {"org": row[0] if row else "", "element": target}
    if level == "repo":
        row = conn.execute("SELECT org, element FROM repo WHERE key=?", (target,)).fetchone()
        return ({"org": row[0], "element": row[1], "repo": target} if row else {"repo": target})
    if level == "project":
        r = conn.execute("SELECT DISTINCT repo FROM work_item_status "
                         "WHERE project=? AND repo IS NOT NULL LIMIT 1", (target,)).fetchone()
        org = ""
        if r:
            o = conn.execute("SELECT org FROM repo WHERE key=?", (r[0],)).fetchone()
            org = o[0] if o else ""
        return {"org": org, "project": target}
    return {}


def _own_maps(overrides, level, target):
    """This scope's stored item→bucket maps (only), per axis."""
    own = {}
    for ax in _AXES:
        patch = overrides.get(semantic.make_key(level, target, ax), {})
        own[ax] = {k: patch.get(k, {}) for k in _ITEM_KEYS[ax]}
    return own


def _inherited_maps(conn, level, target):
    """Effective item→bucket maps from the PARENT chain (this scope excluded)."""
    layers = [l for l in semantic.load_layers(conn)
              if not (l["level"] == level and l["target"] == target)]
    resolved = semantic.resolve(layers, _entity_for(conn, level, target))["config"]
    out = {}
    for ax in _AXES:
        axcfg = resolved.get(ax) or {}
        out[ax] = {k: axcfg.get(k, {}) for k in _ITEM_KEYS[ax]}
    return out


def scope_data(conn, level="global", target=""):
    import store
    repos, project = discovery.repos_for_scope(conn, level, target)
    d = discovery.discover(conn, repos, project)
    overrides = store.read_overrides(conn, "semantic")
    return {
        "scope": {"level": level, "target": target},
        "targets": discovery.scope_targets(conn),
        "scan": d["scan"],
        "own": _own_maps(overrides, level, target),
        "inherited": _inherited_maps(conn, level, target),
        "suggestion": d["config"],          # for global reset
        "buckets": BUCKETS,
        "is_global": level == "global",
        "version": store.overrides_version(conn, ("semantic",)),
    }


def effective_data(conn, level="global", target=""):
    """Read-only resolved taxonomy for an entity, with per-item provenance (which
    scope set each value) — the Inspector. Like a browser's computed-styles panel."""
    import store
    entity = _entity_for(conn, level, target)
    resolved = semantic.resolve(semantic.load_layers(conn), entity)
    cfg, prov = resolved["config"], resolved["provenance"]

    def items(axis, mk):
        out = []
        for name, bucket in sorted(((cfg.get(axis) or {}).get(mk) or {}).items()):
            levels = prov.get((axis, mk, name), [])
            out.append({"name": name, "bucket": bucket,
                        "from": levels[-1] if levels else "?", "chain": levels})
        return out

    chain = [{"level": lv, "target": tg} for lv, tg in semantic.applicable_chain(entity)]
    return {
        "scope": {"level": level, "target": target},
        "chain": chain,
        "targets": discovery.scope_targets(conn),
        "categories": {"types": items("categories", "types"),
                       "labels": items("categories", "labels")},
        "stages": {"statuses": items("stages", "statuses")},
        "ci": {"roles": items("ci", "roles")},
        "version": store.overrides_version(conn, ("semantic",)),
    }


def save(conn, level, target, assignments):
    import store
    overrides = store.read_overrides(conn, "semantic")
    for ax in _AXES:
        maps = assignments.get(ax) or {}
        key = semantic.make_key(level, target, ax)
        patch = dict(overrides.get(key, {}))     # preserve any existing settings
        empty = True
        for mk in _ITEM_KEYS[ax]:
            cleaned = {k: v for k, v in (maps.get(mk) or {}).items() if v}
            if cleaned:
                patch[mk] = cleaned
                empty = False
            else:
                patch.pop(mk, None)
        if level == "global":                     # global always carries the settings
            patch = {**_SETTINGS[ax], **patch}
            empty = False
        if empty:
            store.delete_override(conn, "semantic", key, commit=False)
        else:
            store.write_override(conn, "semantic", key, patch, commit=False)
    conn.commit()
    return store.overrides_version(conn, ("semantic",))


# ---- wizard data (triage + coverage + pipeline) ---------------------------
# A label/status/workflow on fewer than this many items is "long tail" — it falls to
# the tray/unmatched unless the user maps it. Above it, an unmapped item is worth a
# deliberate decision ("your call").
_TAIL_MAX = 30
_LANE_LABELS = {"backlog": "Backlog", "ready": "Ready for dev", "in_progress": "In progress",
                "review": "In review", "qa": "QA / Test", "done": "Done", "released": "Released"}


def _current(name, own_mk, inh_mk, sug_mk, is_global):
    """Effective bucket to show for an item: own override > inherited > (global seed)."""
    return own_mk.get(name) or inh_mk.get(name) or (sug_mk.get(name) if is_global else None)


def _triage(items, own_mk, inh_mk, sug_mk, is_global):
    """Split scanned items into auto (has a value to confirm), decide (unmapped but
    high-volume) and tail (unmapped, rare). Volume-ordered within each group."""
    auto, decide, tail = [], [], []
    for it in items:
        name, count = it["name"], it["count"]
        cur = _current(name, own_mk, inh_mk, sug_mk, is_global)
        rec = {"name": name, "count": count, "current": cur,
               "suggest": sug_mk.get(name),
               "own": bool(own_mk.get(name)), "inherited": bool(inh_mk.get(name))}
        (auto if cur else decide if count >= _TAIL_MAX else tail).append(rec)
    return auto, decide, tail


def _effective_categories(sug, own, inh, is_global):
    """A resolved categories config (labels+types maps + settings) for coverage —
    the base the wizard starts from at this scope, with own overrides on top."""
    base_l = dict(sug["categories"]["labels"] if is_global else inh["categories"]["labels"])
    base_t = dict(sug["categories"]["types"] if is_global else inh["categories"]["types"])
    base_l.update({k: v for k, v in own["categories"]["labels"].items() if v})
    base_t.update({k: v for k, v in own["categories"]["types"].items() if v})
    return {"labels": base_l, "types": base_t,
            "prefer_source": ["issue_type", "label", "title"], "unmatched": "uncategorized"}


def _category_coverage(conn, repos, cfg):
    """Exact share of (non-bot) issues in scope whose label/type resolves to a real
    category under `cfg` — the honest coverage number, same logic the report uses."""
    rc, rp = discovery._repo_clause(repos)
    resolved = {"categories": cfg}
    unmatched = cfg.get("unmatched", "uncategorized")
    total = covered = 0
    for labels_json, itype in conn.execute(
            f"SELECT labels, issue_type FROM issue WHERE is_bot=0 {rc}", rp):
        try:
            labels = json.loads(labels_json or "[]")
        except (ValueError, TypeError):
            labels = []
        total += 1
        if semantic.categorize_issue(resolved, labels, itype or "") != unmatched:
            covered += 1
    return {"pct": round(covered / total * 100) if total else 0,
            "covered": covered, "total": total}


def coverage_preview(conn, level, target, assignments):
    """Exact issue coverage for a hypothetical categories mapping at this scope —
    the base (inherited, or empty at global) overlaid with the wizard's in-progress
    `assignments`. Lets the live meter show the true number, not an estimate."""
    repos, _ = discovery.repos_for_scope(conn, level, target)
    inh = _inherited_maps(conn, level, target)
    labels = {} if level == "global" else dict(inh["categories"]["labels"])
    types = {} if level == "global" else dict(inh["categories"]["types"])
    a = (assignments or {}).get("categories") or {}
    labels.update({k: v for k, v in (a.get("labels") or {}).items() if v})
    types.update({k: v for k, v in (a.get("types") or {}).items() if v})
    cfg = {"labels": labels, "types": types,
           "prefer_source": ["issue_type", "label", "title"], "unmatched": "uncategorized"}
    return _category_coverage(conn, repos, cfg)


def wizard_data(conn, level="global", target=""):
    """Everything the setup wizard needs for one scope: triaged categories & CI,
    the ordered flow pipeline with statuses pre-placed, and baseline coverage. Writes
    go through the existing save() / POST /api/semantic path — this is read-only."""
    import store
    repos, project = discovery.repos_for_scope(conn, level, target)
    d = discovery.discover(conn, repos, project)
    scan, sug = d["scan"], d["config"]
    overrides = store.read_overrides(conn, "semantic")
    own = _own_maps(overrides, level, target)
    inh = _inherited_maps(conn, level, target)
    is_global = level == "global"

    # categories: native types shown on their own; labels triaged by volume
    types = [{"name": it["name"], "count": it["count"],
              "current": _current(it["name"], own["categories"]["types"],
                                  inh["categories"]["types"], sug["categories"]["types"], is_global),
              "suggest": sug["categories"]["types"].get(it["name"]),
              "own": bool(own["categories"]["types"].get(it["name"])),
              "inherited": bool(inh["categories"]["types"].get(it["name"]))}
             for it in scan["issue_types"]]
    cauto, cdecide, ctail = _triage(scan["labels"], own["categories"]["labels"],
                                    inh["categories"]["labels"], sug["categories"]["labels"], is_global)
    coverage = _category_coverage(conn, repos, _effective_categories(sug, own, inh, is_global))

    # stages: ordered pipeline, statuses pre-placed into lanes; the rest in the tray
    lanes = [{"key": k, "name": _LANE_LABELS.get(k, k)} for k in BUCKETS["stages"]]
    placed = {k: [] for k in BUCKETS["stages"]}
    tray = []
    for it in scan["statuses"]:
        name = it["name"]
        lane = _current(name, own["stages"]["statuses"], inh["stages"]["statuses"],
                        sug["stages"]["statuses"], is_global)
        rec = {"name": name, "count": it["count"], "suggest": sug["stages"]["statuses"].get(name),
               "own": bool(own["stages"]["statuses"].get(name)),
               "inherited": bool(inh["stages"]["statuses"].get(name))}
        if lane in placed:
            rec["lane"] = lane
            placed[lane].append(rec)
        else:
            tray.append(rec)

    # ci: same triage as categories
    ciauto, cidecide, citail = _triage(scan["workflows"], own["ci"]["roles"],
                                       inh["ci"]["roles"], sug["ci"]["roles"], is_global)

    return {
        "scope": {"level": level, "target": target},
        "targets": discovery.scope_targets(conn),
        "is_global": is_global,
        "buckets": BUCKETS,
        "version": store.overrides_version(conn, ("semantic",)),
        "categories": {"types": types, "auto": cauto, "decide": cdecide,
                       "tail": ctail, "coverage": coverage},
        "stages": {"lanes": lanes, "placed": placed, "tray": tray},
        "ci": {"auto": ciauto, "decide": cidecide, "tail": citail},
    }


def migrate_saved_buckets(conn) -> int:
    """Remap stored `semantic` override values to the current vocabulary and refresh
    the global axis settings (e.g. stage order). Idempotent — returns the number of
    patches rewritten. Safe to run on every startup; only writes when a value changes.
    """
    import store
    overrides = store.read_overrides(conn, "semantic")
    changed = 0
    for key, patch in overrides.items():
        parsed = semantic.parse_key(key)
        if not parsed:
            continue
        level, target, axis = parsed
        remap = _BUCKET_MIGRATION.get(axis, {})
        new_patch = dict(patch)
        dirty = False
        for mk in _ITEM_KEYS.get(axis, ()):
            m = new_patch.get(mk)
            if not isinstance(m, dict):
                continue
            fixed = {k: remap.get(v, v) for k, v in m.items()}
            if fixed != m:
                new_patch[mk] = fixed
                dirty = True
        # keep the global settings block current (stage order, etc.) — settings win
        # over any stale stored settings, but the item maps in new_patch are preserved
        if level == "global":
            merged = {**new_patch, **_SETTINGS[axis]}
            if merged != new_patch:
                new_patch = merged
                dirty = True
        if dirty:
            store.write_override(conn, "semantic", key, new_patch, commit=False)
            changed += 1
    if changed:
        conn.commit()
    return changed


def seed_split_categories(conn) -> bool:
    """One-time taxonomy correction for the unified Bugs / Epics / Features scheme.

    Two fixes to the pre-split `global::categories` taxonomy:
      * Epic → epic  (was folded into `feature`; epics are now their own tile).
      * drop the `pr-issue` → bug mapping — it is an automation/link label, not a
        defect, and it tagged the large majority of "bugs". Removing it lets those
        issues fall through to `uncategorized` (or whatever a real label/type says).

    Guarded by a `setting` flag so it runs once and never fights a later manual edit
    in the /semantic editor. Returns True if it applied. Callers should recategorise
    + re-render afterwards."""
    import store
    if store.read_overrides(conn, "setting").get("categories_split_v1"):
        return False
    key = semantic.make_key("global", "", "categories")
    patch = store.read_overrides(conn, "semantic").get(key)
    if isinstance(patch, dict):
        labels = dict(patch.get("labels") or {})
        types = dict(patch.get("types") or {})
        if labels.get("Epic") == "feature":          # only override the stale default
            labels["Epic"] = "epic"
        if labels.get("pr-issue") == "bug":          # link label, not a defect
            labels.pop("pr-issue")
        types.setdefault("Epic", "epic")
        store.write_override(conn, "semantic", key,
                             {**patch, "labels": labels, "types": types}, commit=False)
    store.write_override(conn, "setting", "categories_split_v1", {"value": True}, commit=False)
    conn.commit()
    return True


