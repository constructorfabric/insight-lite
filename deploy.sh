#!/usr/bin/env bash
# Build a ready-to-run image locally, ship it, and flip the server over to it.
#
# Image-based deploy: code + templates + the baked React frontend are built
# into `insight-report:latest` locally (`docker build .` — runs the Node
# frontend stage, then the Python stage), shipped via `docker save | ssh |
# docker load` (no registry needed for a single box), then the server just
# `docker compose up -d` to recreate containers from the loaded image.
#
# ALL runtime state (report.db, caches, clones, history/, exports/, generated
# report.html/data.json) lives on the server's `report-data` Docker volume,
# mounted at DATA_DIR (/work/data) — NEVER baked into the image (see
# .dockerignore) — so swapping images never touches data.
#
#   ./deploy.sh              build + ship + up -d (fast, no collect).
#                            Use for code / render / template / frontend changes.
#   ./deploy.sh --refresh    ...then run a full collect (reportctl all) — use when
#                            collection logic, config.yaml or bot_logins changed.
#                            Hits GitHub; takes a few minutes.
#
# There used to be --identity / --pull-identity, which rsynced people.yaml between
# this laptop and the server's data volume. Removed on 2026-07-28 with the file: the
# roster is rows in the DB `override` table, so there is nothing left to copy. Neither
# direction is worth rebuilding against the DB — a push would have to reach into the
# authoritative table from outside the app (the exact "overwrite the whole person
# scope from a stale local copy" shape that has already cost a roster once), and the
# pull's whole purpose was `git commit people.yaml`, which is how a test fixture and
# ~130 real employee addresses ended up in and next to git history. If a roster ever
# has to move or be restored, the transport is a report.db snapshot from
# history/backups/ (deploy.sh writes one below, before every image swap).
#
# Identity is SERVER-OWNED: browser edits at /identity land in the server's
# report.db, so a normal deploy NEVER touches them. report.db / .env / the rest of
# the data volume are likewise untouched by a deploy — only the image (code +
# templates + baked frontend) changes; state lives only on the volume.
# Override host/key/dir with DEPLOY_HOST / DEPLOY_KEY / DEPLOY_DIR env vars.
set -euo pipefail

IMAGE="insight-report:latest"
KEY="${DEPLOY_KEY:-$HOME/.ssh/deploy_key}"
HOST="${DEPLOY_HOST:-user@your-server}"
DIR="${DEPLOY_DIR:-insight-report}"
URL="${DEPLOY_URL:-http://your-server:8081/}"
SSH=(ssh -i "$KEY" -o BatchMode=yes "$HOST")
RSYNC_SSH=(ssh -i "$KEY" -o BatchMode=yes)

mode="${1:-}"
refresh=0
[[ "$mode" == "--refresh" ]] && refresh=1

echo "→ build $IMAGE locally (runs the frontend build stage)"
docker build -t "$IMAGE" .

echo "→ ship image to $HOST"
docker save "$IMAGE" | gzip | "${SSH[@]}" "gunzip | docker load"

# Everything else the container needs now comes FROM the image — only the
# compose file itself (+ the optional oauth2-proxy assets it can bind-mount)
# need to exist on the server's disk. .env / the data volume are never touched.
echo "→ syncing docker-compose.yml (+ deploy/oauth assets) to $HOST:~/$DIR"
"${SSH[@]}" "mkdir -p $DIR"
rsync -az -e "${RSYNC_SSH[*]}" docker-compose.yml "$HOST:$DIR/docker-compose.yml"
rsync -az -e "${RSYNC_SSH[*]}" --exclude='htpasswd' deploy/oauth/ "$HOST:$DIR/deploy/oauth/"

# Back up the SQLite DB BEFORE the new image runs — a schema migration on first
# connect could corrupt/lose data. WAL is checkpointed so a plain copy is
# consistent; last 10 backups kept under the data volume's history/backups/.
# Runs against whatever container is CURRENTLY up (the old image) — `up -d`
# below is what actually swaps to the new one.
echo "→ backup report.db before deploy"
"${SSH[@]}" "cd $DIR && docker compose exec -T report python3 -c \"
import datetime, os, shutil, sqlite3
import paths, store
db = store.db_path()
if os.path.exists(db):
    sqlite3.connect(db).execute('PRAGMA wal_checkpoint(TRUNCATE)')
    bdir = str(paths.data_path('history', 'backups'))
    os.makedirs(bdir, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dst = os.path.join(bdir, f'report-{stamp}.db')
    shutil.copy2(db, dst)
    kept = sorted(f for f in os.listdir(bdir) if f.startswith('report-'))
    for old in kept[:-10]:
        os.remove(os.path.join(bdir, old))
    print(f'  saved {dst} (kept last 10)')
else:
    print('  no report.db yet — skipping backup')
\"" || echo "  (skipping backup — no running report container yet)"

echo "→ recreate containers from the loaded image"
"${SSH[@]}" "cd $DIR && docker compose up -d" >/dev/null

if [[ $refresh -eq 1 ]]; then
  echo "→ full refresh (collect → directory); collecting from GitHub…"
  "${SSH[@]}" "cd $DIR && docker compose exec -T report python reportctl.py all"
else
  echo "→ report is served live from the DB (no bake); container restart cleared its cache"
fi

echo -n "→ health: "
"${SSH[@]}" "curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/health"
echo "✓ deployed → $URL"
