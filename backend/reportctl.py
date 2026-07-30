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

# The repo root, one level up from backend/: templates/, assets/ and config.yaml
# live there, not next to the modules.
ROOT = Path(__file__).resolve().parent.parent
BACKEND = Path(__file__).resolve().parent
EXPORTS = paths.data_path("exports")
RUNTIME = paths.data_path(".runtime")


def run_python(script: str, *, no_cache: bool = False) -> None:
    env = os.environ.copy()
    if no_cache:
        env["NO_CACHE"] = "1"
    # BACKEND, not ROOT: the modules live in backend/ while ROOT is the repo root
    # (that is where templates/, assets/ and config.yaml are). cwd stays ROOT so a
    # child's relative DATA_DIR default resolves the same as the parent's.
    subprocess.run([sys.executable, str(BACKEND / script)], cwd=ROOT, env=env, check=True)


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
    EXPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = EXPORTS / f"insight-report-{stamp}"
    # (no people.yaml copy: there is no such file any more. The curated roster reaches
    # the bundle inside the run blob below, which is what the report is built from.)
    # No HTML in the bundle: the standalone one-pager went with the Jinja monolith,
    # so an export is the DB plus the run blob — the two things a report is rebuilt
    # from — and the portal is the only thing that renders.
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
                 "snapshot-status", "export", "serve", "config-capture", "config-verify", "demo-seed"],
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
              f"({r['folded_rows']} rows).")
    elif args.command == "reconfig":
        import reconfig
        r = reconfig.apply()
        print(f"Reconfigured {r['repos']} repos.")
    elif args.command == "refresh":
        refresh(no_cache=args.no_cache)
    elif args.command == "all":
        all_steps(no_cache=args.no_cache)
    elif args.command == "snapshot-status":
        import collect
        n = collect.snapshot_status_only()
        print(f"Board status snapshot: {n} items recorded.")
    elif args.command == "config-capture":
        # Run this ONCE on a live deployment before its config.yaml starts arriving
        # from git or from inside an image. It copies BOTH halves of the file into the
        # DB overlay (which lives on the data volume): the policy blocks — AI-tool
        # markers, provenance / framework / tracker blocks, bot denylist, identity
        # bridges, spec and LOC filters, email — and the structural config, i.e. the
        # org, the repo classification, the elements, repo types and company domains.
        # After that the file can be the published generic one without a number
        # changing. See configstore.BLOB_KEYS and BASE_KEYS.
        import configstore
        written = configstore.capture_base_into_overlay()
        if written:
            print(f"Captured into the DB overlay: {', '.join(written)}")
        else:
            print("Nothing to capture — every policy block is already DB-owned.")
        expected = list(configstore.BLOB_KEYS) + [f"base/{k}" for k in configstore.BASE_KEYS]
        skipped = [k for k in expected if k not in written]
        if skipped:
            print(f"Left as-is (already overridden, or absent from config.yaml): "
                  f"{', '.join(skipped)}")
        print("\nThis deployment now reads its configuration from the database. Verify "
              "before changing config.yaml:\n"
              "  python reportctl.py config-verify")
    elif args.command == "config-verify":
        # Answers "is the database now sufficient" with a diff rather than a promise:
        # merges the overlay over the real config.yaml and over an EMPTY file, and
        # reports anything that would change. Run it after config-capture and before
        # letting the file arrive from git or an image.
        import configstore
        res = configstore.verify_capture()
        if res["ok"]:
            print("OK — the database alone reproduces this configuration.")
            print("     config.yaml can be replaced without changing a number.")
        else:
            print("NOT SAFE YET — these keys would change if config.yaml were replaced:")
            for key, info in res["differ"].items():
                where = "in the DB but still differs" if info["in_db"] else "NOT captured"
                print(f"  {key:26} {where}")
            print("\nRun `python reportctl.py config-capture` first.")
        if res["file_only"]:
            print(f"\nRead from the file only, falling back to code defaults (fine for "
                  f"tuning knobs): {', '.join(res['file_only'])}")
        return 0 if res["ok"] else 1
    elif args.command == "demo-seed":
        # Populate the store with an entirely invented dataset so the UI can be seen
        # without a GitHub token — for evaluating the tool, for documentation
        # screenshots, and as the full-report fixture the test suite otherwise lacks.
        import demo
        counts = demo.seed()
        print("Demo data written (everything in it is invented):")
        for tbl, n in counts.items():
            print(f"  {tbl:14} {n}")
        print("\n  python reportctl.py serve --port 8080")
    elif args.command == "export":
        path = export_snapshot()
        print(path)
    elif args.command == "serve":
        from server import serve

        serve(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
