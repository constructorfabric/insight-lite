#!/usr/bin/env bash
# Local one-shot: collect -> render -> open the report.
# Same code path as the GitHub Action, just on your machine.
#
#   ./run.sh            # collect + render + open report.html
#   ./run.sh --no-open  # skip opening the browser
#   ./run.sh --email    # also run the email step (honours config.yaml + SMTP_* env)
#
# Token: uses $GH_TOKEN / $GITHUB_TOKEN if set, otherwise falls back to `gh auth token`.
set -euo pipefail
cd "$(dirname "$0")"

# --- token ---------------------------------------------------------------
if [[ -z "${GH_TOKEN:-}" && -z "${GITHUB_TOKEN:-}" ]]; then
  if command -v gh >/dev/null 2>&1; then
    export GH_TOKEN="$(gh auth token)"
  else
    echo "No GH_TOKEN/GITHUB_TOKEN and gh CLI not found. Set a token first." >&2
    exit 1
  fi
fi

# --- venv ----------------------------------------------------------------
if [[ ! -d .venv ]]; then
  echo "Creating .venv…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

# --- pipeline ------------------------------------------------------------
python collect.py
python render.py

for arg in "$@"; do
  [[ "$arg" == "--email" ]] && python email_report.py
done

# --- open ----------------------------------------------------------------
if [[ " $* " != *" --no-open "* ]]; then
  if command -v open >/dev/null 2>&1; then open report.html        # macOS
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open report.html  # Linux
  fi
fi
echo "Done -> report.html"
