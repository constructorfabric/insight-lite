#!/usr/bin/env python3
"""Bridge to the Vite-built React frontend (frontend/ -> assets/app/).

Reads the Vite manifest emitted at build time (assets/app/.vite/manifest.json,
see frontend/vite.config.ts) and resolves an entry's hashed JS/CSS URLs so
server.py/render.py can mount a React entry into the existing server-rendered
shell (see render.render_spa_page).

Dependency-free on purpose (json + pathlib only) — no jinja2/store/etc, so
importing this module never pulls in the rest of the app.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "assets" / "app" / ".vite" / "manifest.json"

# Keyed by resolved path string -> (mtime, parsed manifest). A rebuild changes
# the file's mtime, so a running dev server picks up new hashes without a
# restart; tests that monkeypatch MANIFEST_PATH to a fixture never collide
# with the real manifest's cache entry.
_cache: dict[str, tuple[float, dict]] = {}


def _load_manifest(path: Path) -> dict | None:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    cached = _cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    _cache[key] = (mtime, data)
    return data


def _collect_css(manifest: dict, key: str, seen: set[str]) -> list[str]:
    """This manifest chunk's own `css` plus its transitive `imports`' css,
    depth-first. Needed because Vite only lists a CSS file directly under an
    entry's own `css` key while that entry is the file's SOLE consumer; once a
    second entry statically imports the same CSS (as every migrated report
    view does — base/shell/chart/report.css, plus whatever a shared component
    like VegaChart pulls in), Vite hoists it into a shared chunk reachable only
    via `imports`, not `css` — see
    https://vite.dev/guide/backend-integration.html's own recommended
    `findCssFiles` walk. `seen` guards against revisiting a chunk reachable via
    more than one import path (diamond dependencies)."""
    rec = manifest.get(key)
    if not rec:
        return []
    css = list(rec.get("css") or [])
    for dep in rec.get("imports") or []:
        if dep in seen:
            continue
        seen.add(dep)
        css.extend(_collect_css(manifest, dep, seen))
    return css


def entry_assets(name: str) -> dict | None:
    """Hashed asset URLs for the Vite entry `src/entries/<name>.tsx`, e.g.
    {"js": "/assets/app/assets/whatsnew-BZ53WYBJ.js",
     "css": ["/assets/app/assets/whatsnew-BRwL1u47.css"]}.
    `css` is resolved transitively (see _collect_css) and de-duplicated,
    preserving first-seen order, so a CSS file shared by multiple entries
    (hoisted into a common chunk by Vite) is still linked exactly once.

    Returns None when the manifest is missing (unbuilt frontend) or the entry
    isn't in it — callers (render.render_spa_page) degrade gracefully instead
    of emitting a broken <script> tag."""
    manifest = _load_manifest(MANIFEST_PATH)
    if not manifest:
        return None
    key = f"src/entries/{name}.tsx"
    rec = manifest.get(key)
    if not rec or not rec.get("file"):
        return None
    css: list[str] = []
    for c in _collect_css(manifest, key, {key}):
        if c not in css:
            css.append(c)
    return {
        "js": "/assets/app/" + rec["file"],
        "css": ["/assets/app/" + c for c in css],
    }
