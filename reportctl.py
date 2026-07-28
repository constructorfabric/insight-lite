#!/usr/bin/env python3
"""Command wrapper for collecting, rendering, exporting, and serving reports."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import paths

ROOT = Path(__file__).resolve().parent
EXPORTS = paths.data_path("exports")
RUNTIME = paths.data_path(".runtime")


def run_python(script: str, *, no_cache: bool = False) -> None:
    env = os.environ.copy()
    if no_cache:
        env["NO_CACHE"] = "1"
    subprocess.run([sys.executable, script], cwd=ROOT, env=env, check=True)


def collect(*, no_cache: bool = False) -> None:
    run_python("collect.py", no_cache=no_cache)


def render() -> None:
    run_python("render.py")


def directory() -> None:
    run_python("directory.py")


def refresh(*, no_cache: bool = False) -> None:
    collect(no_cache=no_cache)          # DB updated; the portal renders /report live


def all_steps(*, no_cache: bool = False) -> None:
    collect(no_cache=no_cache)
    directory()                         # DB updated; the portal renders /report live


def export_snapshot() -> Path:
    EXPORTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = EXPORTS / f"insight-report-{stamp}"
    # (no people.yaml copy: there is no such file any more. The curated roster reaches
    # the bundle inside the run blob below, which is what the report is built from.)
    # report.html is no longer baked — render it on demand into the bundle
    try:
        import render
        with open(base.with_suffix(".html"), "w") as fh:
            fh.write(render.render_report(render.build_model(render.load_data())))
    except (Exception, SystemExit):          # load_data() raises SystemExit on empty DB
        pass
    # the run blob lives in the DB (source of truth) — serialise it into the bundle
    try:
        import json
        import store
        blob = store.read_latest_run(store.connect())
        if blob is not None:
            with open(base.with_suffix(".data.json"), "w") as fh:
                json.dump(blob, fh, indent=2)
    except Exception:                        # noqa: BLE001 — export is best-effort
        pass
    return base.with_suffix(".html")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Constructor Insight report helper")
    p.add_argument(
        "command",
        choices=["collect", "render", "directory", "reindex", "reconfig", "refresh", "all",
                 "snapshot-status", "export", "serve"],
    )
    p.add_argument("--no-cache", action="store_true", help="Bypass GitHub API cache")
    p.add_argument("--host", default=os.environ.get("PORTAL_HOST")
                   or os.environ.get("REPORT_HOST") or "127.0.0.1")
    # honour the preview-assigned PORT env (autoPort) before the 8080 default
    p.add_argument("--port", type=int,
                   default=int(os.environ.get("REPORT_PORT") or os.environ.get("PORT") or "8080"))
    args = p.parse_args(argv)

    if args.command == "collect":
        collect(no_cache=args.no_cache)
    elif args.command == "render":
        render()
    elif args.command == "directory":
        directory()
    elif args.command == "reindex":
        import reindex
        r = reindex.apply()
        print(f"Reindexed {r['people']} people, {r['aliases']} aliases folded "
              f"({r['folded_rows']} rows); report.html re-rendered.")
    elif args.command == "reconfig":
        import reconfig
        r = reconfig.apply()
        print(f"Reconfigured {r['repos']} repos; report.html re-rendered.")
    elif args.command == "refresh":
        refresh(no_cache=args.no_cache)
    elif args.command == "all":
        all_steps(no_cache=args.no_cache)
    elif args.command == "snapshot-status":
        import collect
        n = collect.snapshot_status_only()
        print(f"Board status snapshot: {n} items recorded.")
    elif args.command == "export":
        path = export_snapshot()
        print(path)
    elif args.command == "serve":
        from server import serve

        serve(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
