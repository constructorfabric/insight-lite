# Contributing

This tool was built and run by one person against one organisation before it was
published, so expect rough edges in places only that setup exercised. Issues that say
"this assumed something about your org that isn't true for mine" are especially useful.

## Getting a dev loop

```bash
pip install -r requirements.txt
python -m pytest tests -q
```

There is no build step for the Python side. The test suite needs no GitHub token, no
network and no collected data: `tests/_state.py` redirects all runtime state to a
temporary directory and seeds a deterministic fixture store, so a fresh clone runs
green. It also means a test can never write into your checkout — if one does, the
canary in `tests/test_state_isolation.py` fails and names the path.

Both runners work and CI uses the second:

```bash
python -m pytest tests -q
python -m unittest discover -s tests -t . -v
```

The `-t .` is load-bearing for `unittest`: without it, test modules import as top-level
names, `tests/__init__.py` never runs, and the isolation and fixture store are silently
absent. The canary above fails loudly if that happens, rather than letting the run write
real state.

To run against your own collected data instead of the fixture, point `REPORT_DB` at a
**copy** — the suite writes to whatever that names:

```bash
python -c "import sqlite3; s=sqlite3.connect('file:history/report.db?mode=ro',uri=True); d=sqlite3.connect('/tmp/copy.db'); s.backup(d)"
REPORT_DB=/tmp/copy.db python -m pytest tests -q
```

## Frontend

The React frontend lives in `frontend/`. `npm install && npm run build` inside it emits
the bundle into `assets/app/`, which the server serves. Python-side changes need no
frontend build unless you touched `frontend/`.

## What CI does

One workflow, two jobs, and the second declares `needs: test` — so an image can only
exist for a commit whose suite passed. A pull request builds the image but does not
publish it, which still catches a broken Dockerfile without putting anything unreviewed
in the registry.

The build then smoke-tests the image before publishing: starts it, waits for `/health`,
checks that a dataless container redirects to the setup wizard, asserts the Vite manifest
and `templates/` resolve from inside the image, and seeds the demo data to render a full
report. That last one exists because "it builds" and "it works" are different claims —
a wrong path inside the image would not surface until a page was rendered.

## Before you open a PR

- **Never commit collected data.** `history/*.jsonl`, `history/report.db`, `data.json`,
  `people.yaml` and the editor HTML files are git-ignored because they contain real
  names, email addresses and per-company figures. Check `git status` after any collect
  run. An earlier internal version of this repo tracked some of them, which is why the
  ignore rules are as emphatic as they are.
- **Keep your org out of the code.** Every org, repo, element, company and person in
  `config.yaml`, the tests and the docs is invented. Real values belong in your own
  config or, better, in the database via the Manage UI.
- **Say when a signal is a guess.** Every metric is badged `exact` or `heuristic`, and
  the distinction is the point — a bot trailer and a word in a commit message are not
  the same evidence. New signals need a badge.
- **A failure that is survivable still has to be audible.** Panels degrade rather than
  crash, but a degraded path logs a traceback and says why in the UI. A bare
  `except: pass` will be sent back — see `server.log_degraded`.

## Commit messages

Long and explanatory, in the style already in the log: what changed, and why the
alternative was rejected. The history is meant to be readable as documentation.
