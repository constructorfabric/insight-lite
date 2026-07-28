#!/usr/bin/env python3
"""Semantic-config resolver — the core of the configurable semantic layer.

Turns a set of scoped config patches into the *effective* config for one entity
(a repo / element / org / project), by deep-merging along a most-specific-wins
chain and recording where each value came from (provenance).

    global  <  org  <  element  <  repo  <  project

Storage: patches live in the `override` table under scope "semantic", one row per
(level, target, axis) — key = "<level>:<target>:<axis>", value = the axis patch.
This module has NO GitHub or DB dependency for the merge itself; `load_layers`
is the only DB adapter. Because the effective config is pure-derived from the
override rows, `reconfig` can recompute everything without a re-collect.

See docs/semantic-config.md for the schema this resolves.
"""
from __future__ import annotations

import copy

# Precedence low → high. Each level reads its target from an entity dict key;
# `global` always applies (no target). More-specific levels merge in last and win.
LEVELS = ("global", "org", "element", "repo", "project")
_LEVEL_KEY = {"org": "org", "element": "element", "repo": "repo", "project": "project"}

# The axes a semantic patch may carry (kept as a guardrail for key validation).
AXES = ("categories", "stages", "ci", "effort", "sprints", "profile")


def make_key(level: str, target: str, axis: str) -> str:
    """Storage key for one scoped axis patch, e.g. 'repo:org/name:ci'."""
    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}")
    if level == "global" and target:
        raise ValueError("global scope takes no target")
    return f"{level}:{target}:{axis}"


def parse_key(key: str):
    """'repo:org/name:ci' -> ('repo', 'org/name', 'ci'). None if malformed.
    Targets may contain '/', never ':', so a 2-split from the left is safe."""
    parts = key.split(":", 2)
    if len(parts) != 3:
        return None
    level, target, axis = parts
    if level not in LEVELS:
        return None
    return level, target, axis


def applicable_chain(entity: dict) -> list:
    """Ordered (level, target) pairs that apply to this entity, low → high
    precedence. `entity` supplies whichever of org/element/repo/project are
    relevant; absent dimensions are simply skipped (global always applies)."""
    chain = [("global", "")]
    for level in ("org", "element", "repo", "project"):
        target = entity.get(_LEVEL_KEY[level])
        if target:
            chain.append((level, target))
    return chain


def _merge_into(base: dict, patch: dict, level: str, prov: dict, path: tuple) -> None:
    """Deep-merge `patch` into `base` (in place), recording provenance per leaf.

    - dict + dict  -> recurse
    - '+key' list  -> append (deduped) onto the inherited list
    -  key         -> replace (scalars, lists, dict-as-value)
    `prov[path]` is the ordered list of levels that contributed to that path."""
    for raw_k, val in patch.items():
        append = isinstance(raw_k, str) and raw_k.startswith("+")
        k = raw_k[1:] if append else raw_k
        p = path + (k,)
        cur = base.get(k)
        if isinstance(val, dict):
            # maps ALWAYS deep-merge (never wholesale-replace); introducing a new
            # subtree recurses into a fresh dict so every leaf gets provenance
            if not isinstance(cur, dict):
                base[k] = {}
            _merge_into(base[k], val, level, prov, p)
        elif append and isinstance(cur, list) and isinstance(val, list):
            base[k] = cur + [x for x in val if x not in cur]
            prov.setdefault(p, [])
            if level not in prov[p]:
                prov[p].append(level)
        else:
            base[k] = copy.deepcopy(val)
            prov[p] = [level]


def resolve(layers: list, entity: dict) -> dict:
    """Merge all applicable `layers` for `entity` into the effective config.

    layers: [{level, target, axis, patch}]  (as produced by load_layers)
    entity: {org?, element?, repo?, project?}
    Returns {"config": {axis: merged}, "provenance": {(axis, *path): [levels]}}.
    Later (more specific) layers win; provenance says who set each leaf."""
    chain = applicable_chain(entity)
    index: dict = {}
    for lyr in layers:
        index.setdefault((lyr["level"], lyr["target"]), []).append(lyr)
    config: dict = {}
    prov: dict = {}
    for level, target in chain:
        for lyr in index.get((level, target), []):
            axis = lyr["axis"]
            config.setdefault(axis, {})
            _merge_into(config[axis], lyr["patch"], level, prov, (axis,))
    return {"config": config, "provenance": prov}


def load_layers(conn) -> list:
    """Read every semantic patch from the override table into resolver layers.
    Malformed keys are skipped (never crash the report on a stray row)."""
    import store
    layers = []
    for key, patch in store.read_overrides(conn, "semantic").items():
        parsed = parse_key(key)
        if not parsed:
            continue
        level, target, axis = parsed
        layers.append({"level": level, "target": target, "axis": axis, "patch": patch})
    return layers


def effective_for(conn, entity: dict) -> dict:
    """Convenience: load layers from the DB and resolve for one entity."""
    return resolve(load_layers(conn), entity)


# --- consumers: turn a resolved config into a verdict for one item -----------
def categorize_issue(resolved: dict, labels, issue_type: str = "") -> str:
    """Primary category for an issue given the RESOLVED categories config for its
    scope. Sources (native type / labels) are tried in `prefer_source` order — the
    same rule the schema documents. Returns the `unmatched` bucket if nothing hits."""
    cats = resolved.get("categories") or {}
    labels_map = cats.get("labels") or {}
    types_map = cats.get("types") or {}
    prefer = cats.get("prefer_source") or ["issue_type", "label", "title"]
    label_hits = [labels_map[l] for l in (labels or []) if l in labels_map]
    for src in prefer:
        if src == "issue_type" and issue_type and issue_type in types_map:
            return types_map[issue_type]
        if src == "label" and label_hits:
            return label_hits[0]
        # "title" convention not stored on the row → skipped here
    return cats.get("unmatched", "uncategorized")


def stage_for(resolved: dict, status_raw: str) -> str:
    """Canonical stage for a board status given the RESOLVED stages config."""
    stages = resolved.get("stages") or {}
    return (stages.get("statuses") or {}).get(status_raw) or stages.get("unmatched", "other")


def ci_role(resolved: dict, workflow: str) -> str:
    """Role (gate/nightly/release/ignore/…) for a workflow given resolved ci config."""
    return ((resolved.get("ci") or {}).get("roles") or {}).get(workflow, "")
