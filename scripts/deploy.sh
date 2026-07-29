#!/usr/bin/env bash
# Point a deployment at a published image.
#
# There is nothing to build here any more. CI publishes an image for every commit whose
# test suite passed, so a deploy is no longer "compile and ship" — it is choosing WHICH
# published commit runs, which is one variable on the server (INSIGHT_TAG in .env) plus
# a pull.
#
# The previous version built the image locally and shipped it with
# `docker save | ssh | docker load`. After the 2026-07-29 cutover to a pinned image that
# quietly did NOTHING: the server resolves its image from GHCR via INSIGHT_TAG, so a
# locally-loaded `insight-report:latest` would sit on disk unreferenced while the script
# printed "✓ deployed". A deploy tool that cannot fail is worse than no deploy tool, so
# every step below either verifies its effect or exits non-zero.
#
# ALL runtime state lives under the server's DATA_DIR (./data, bind-mounted at
# /work/data) and is never baked into the image — swapping images does not touch data.
# Identity is SERVER-OWNED: browser edits at /identity land in the server's report.db,
# so a deploy never overwrites them. The report.db backup below (newest 10 retained
# under history/backups/) is the recovery path if one is ever needed.
#
#   ./scripts/deploy.sh                  deploy HEAD (must be pushed and published)
#   ./scripts/deploy.sh sha-<commit>     deploy a specific published tag — also the
#                                        rollback path: same command, older tag
#   ./scripts/deploy.sh --refresh        ...then run a full collect (hits GitHub)
#
# Where to deploy is per-installation, so it does not belong in git. Put it in
# scripts/deploy.env (gitignored) and this picks it up automatically:
#
#     DEPLOY_HOST=user@host
#     DEPLOY_KEY=~/.ssh/your_key
#     DEPLOY_DIR=the-directory-on-the-server
#
# Environment variables of the same names still win, so CI or a one-off can override.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
[[ -f "$HERE/deploy.env" ]] && . "$HERE/deploy.env"

IMAGE="${DEPLOY_IMAGE:-ghcr.io/constructorfabric/insight-lite}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/deploy_key}"
HOST="${DEPLOY_HOST:-}"
DIR="${DEPLOY_DIR:-insight-lite}"

if [[ -z "$HOST" ]]; then
  echo "✗ no deploy target: set DEPLOY_HOST, or create scripts/deploy.env (see the header)" >&2
  exit 2
fi
SSH=(ssh -i "${KEY/#\~/$HOME}" -o BatchMode=yes "$HOST")

refresh=0
tag=""
for arg in "$@"; do
  case "$arg" in
    --refresh) refresh=1 ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *)  tag="$arg" ;;
  esac
done

if [[ -z "$tag" ]]; then
  # Deploying "HEAD" only means something if HEAD is what CI built. A dirty tree or an
  # unpushed commit would ship an image that does not contain the code in front of you.
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "✗ working tree is dirty — commit or stash first, or name a tag explicitly" >&2
    exit 1
  fi
  sha="$(git rev-parse HEAD)"
  if [[ -z "$(git branch -r --contains "$sha" 2>/dev/null)" ]]; then
    echo "✗ HEAD ($sha) is not on any remote branch — push it so CI can publish it" >&2
    exit 1
  fi
  tag="sha-$sha"
fi

echo "→ tag: $tag"

# Check the registry BEFORE touching the server: a typo'd or not-yet-built tag should
# fail here, not halfway through recreating containers.
echo -n "→ is $tag published? "
tok="$(curl -fsS "https://ghcr.io/token?scope=repository:${IMAGE#ghcr.io/}:pull&service=ghcr.io" \
       | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $tok" \
        -H 'Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json' \
        "https://ghcr.io/v2/${IMAGE#ghcr.io/}/manifests/$tag")"
if [[ "$code" != 200 ]]; then
  echo "no (HTTP $code)"
  echo "✗ $IMAGE:$tag is not in the registry — has CI finished for that commit?" >&2
  exit 1
fi
echo "yes"

ROWS="import sys; sys.path.insert(0, '/work/backend')
import store
c = store.connect()
print(','.join(str(c.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0])
               for t in ('person','commits','pull_request','issue','runs','override')))"

# Back up the SQLite DB BEFORE the new image runs: a schema migration on first connect
# is the one step that could lose data. WAL is checkpointed so a plain copy is consistent.
echo "→ backup report.db"
"${SSH[@]}" "cd $DIR && docker compose exec -T report python3 -c \"
import datetime, os, shutil, sqlite3, sys
sys.path.insert(0, '/work/backend')
import paths, store
db = store.db_path()
if os.path.exists(db):
    sqlite3.connect(db).execute('PRAGMA wal_checkpoint(TRUNCATE)')
    bdir = str(paths.data_path('history', 'backups')); os.makedirs(bdir, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dst = os.path.join(bdir, 'report-' + stamp + '.db')
    shutil.copy2(db, dst)
    kept = sorted(f for f in os.listdir(bdir) if f.startswith('report-'))
    for old in kept[:-10]:
        os.remove(os.path.join(bdir, old))
    print('  saved ' + dst)
else:
    print('  no report.db yet — skipping backup')
\"" || { echo "✗ backup failed — refusing to swap images" >&2; exit 1; }

# Row counts before, so the check at the end compares against something real instead of
# just asking whether a process is up.
before="$("${SSH[@]}" "cd $DIR && docker compose exec -T report python3 -c \"$ROWS\"" | tr -d '\r')"
echo "→ rows before: $before"

echo "→ pin INSIGHT_TAG=$tag and recreate"
"${SSH[@]}" "cd $DIR && \
  if grep -q '^INSIGHT_TAG=' .env; then sed -i 's|^INSIGHT_TAG=.*|INSIGHT_TAG=$tag|' .env; \
  else printf 'INSIGHT_TAG=%s\n' '$tag' >> .env; fi && \
  docker compose pull -q && docker compose up -d" >/dev/null

echo -n "→ running image matches the pin: "
running="$("${SSH[@]}" "docker inspect \$(cd $DIR && docker compose ps -q report) --format '{{.Config.Image}}'" | tr -d '\r')"
if [[ "$running" != "$IMAGE:$tag" ]]; then
  echo "NO"
  echo "✗ container runs $running, expected $IMAGE:$tag" >&2
  echo "  (is COMPOSE_FILE in the server's .env still loading docker-compose.prod.yml?)" >&2
  exit 1
fi
echo "yes"

if [[ $refresh -eq 1 ]]; then
  echo "→ full refresh (collect → directory); hits GitHub…"
  "${SSH[@]}" "cd $DIR && docker compose exec -T report python backend/reportctl.py all"
fi

# The check that matters. /health answers "ok" on an EMPTY database, so it would pass
# even if the data mount were wrong and the portal were serving the setup wizard.
# Compare the row counts, and require the report route rather than the wizard.
echo -n "→ rows after:  "
after="$("${SSH[@]}" "cd $DIR && docker compose exec -T report python3 -c \"$ROWS\"" | tr -d '\r')"
echo "$after"
if [[ $refresh -eq 0 && "$before" != "$after" ]]; then
  echo "✗ row counts changed across an image swap that should not touch data" >&2
  echo "  before=$before after=$after — check the data mount before serving this" >&2
  exit 1
fi

echo -n "→ portal: "
dest="$("${SSH[@]}" "curl -s -o /dev/null -w '%{redirect_url}' http://127.0.0.1:8080/" | tr -d '\r')"
case "$dest" in
  */setup)    echo "redirects to /setup — this deployment sees NO data"; exit 1 ;;
  */overview) echo "serves the report ($dest)" ;;
  *)          echo "unexpected destination: '$dest'"; exit 1 ;;
esac

echo "✓ deployed $tag"
