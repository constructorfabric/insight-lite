#!/usr/bin/env python3
"""Centralised runtime-state path resolution.

Everything the app WRITES or reads at runtime as durable state — the SQLite
store, generated report.html/data.json, the GitHub API cache, git clones,
history backups/exports — is resolved under ONE directory: DATA_DIR. Code,
templates, and the baked frontend bundle are NOT state and always stay under the
repo root regardless of DATA_DIR (they come from the Docker image, not the volume).

  DATA_DIR   env override; default "." (resolves to the current working
             directory, i.e. the repo root for every existing local/test
             invocation). The Docker image sets DATA_DIR=/work/data (a
             mounted, persistent volume) so an image swap never loses data;
             local development and the test suite keep writing next to the
             checkout exactly as before.

Individual pieces still honour their OWN longstanding env overrides where
one already existed (REPORT_DB, CLONE_DIR) — those continue to win over the
DATA_DIR-derived default, so existing test harnesses and deploy tooling keep
working unchanged. PEOPLE_YAML / CONFIG_LOCAL used to be on that list; both
files are gone (the override table is the only source), so both are ignored.
"""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", ".")).resolve()


def data_path(*parts: str) -> Path:
    """A path under DATA_DIR. Does not create parent directories — callers that
    write should mkdir(parents=True, exist_ok=True) as needed (most already do)."""
    return DATA_DIR.joinpath(*parts)
