#!/usr/bin/env python3
"""Metric registry — the single source of truth for the metrics catalog.

Metrics are NOT hardcoded in a separate list: each is registered right next to the
function that computes it via `register_for(fn, [...])`, so the metadata lives with
the code and carries a real function reference (verified by a drift test). The
`/metrics` page and the drift test both read `all_metrics()` — nothing else.

    # in store.py, right after the function:
    def aggregate(...): ...
    metrics_registry.register_for(aggregate, [
        metrics_registry.metric("commits", type="direct", group="volume",
            desc="…", formula="…", snippet="…"),
        ...
    ])
"""
from __future__ import annotations

# group id -> title, controls catalog layout order
GROUPS = [
    ("volume", "Volume & people"),
    ("code", "Code composition"),
    ("delivery", "Delivery — PRs & issues"),
    ("flow", "Flow & CI health"),
    ("review", "Collaboration — reviews"),
    ("score", "Developer score (experimental)"),
    ("ai", "AI-assisted work"),
    ("impact", "Lasting impact (git blame)"),
    ("company", "Company & concentration"),
    ("trend", "Trend & comparison"),
    ("usage", "Usage — traffic"),
    ("quality", "Identity & data quality"),
]

# modules whose import registers metrics (imported lazily by all_metrics())
PRODUCERS = ("store", "collect", "render", "semantic_metrics")

_REGISTRY: dict = {}


def metric(name: str, *, type: str, group: str, desc: str, formula: str,
           unit: str = "", snippet: str = "") -> dict:
    """Build one metric spec (registered via register_for)."""
    return {"name": name, "type": type, "group": group, "unit": unit,
            "desc": desc, "formula": formula, "snippet": snippet}


def register_for(fn, metrics: list) -> None:
    """Register metrics against the function that computes them. The 'computed in'
    reference is derived from the real function object, so it can't go stale."""
    where = f"{fn.__module__}.py · {fn.__qualname__}()"
    for m in metrics:
        m = dict(m)
        m["where"] = where
        m["fn"] = f"{fn.__module__}.{fn.__qualname__}"
        _REGISTRY[m["name"]] = m


def all_metrics() -> list:
    """Every registered metric. Imports the producer modules so their module-level
    register_for() calls have run."""
    for mod in PRODUCERS:
        try:
            __import__(mod)
        except Exception:                # noqa: BLE001 — a producer that won't import
            pass                         # just contributes no metrics
    order = {gid: i for i, (gid, _) in enumerate(GROUPS)}
    return sorted(_REGISTRY.values(), key=lambda m: (order.get(m["group"], 99), m["name"]))


def names() -> set:
    return {m["name"] for m in all_metrics()}
