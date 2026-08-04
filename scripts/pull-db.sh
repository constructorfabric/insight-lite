#!/usr/bin/env bash
# Pull a CONSISTENT read-only copy of the server's report.db for local inspection.
#
# Why not scp the file directly: the store runs under WAL (store.connect sets
# journal_mode=WAL), so the .db file on its own is not a complete database — a plain
# copy can land mid-transaction and miss whatever is still in the -wal. sqlite3's
# backup API takes a proper snapshot of the live database instead.
#
# Nothing on the server is modified. The snapshot is written to /tmp INSIDE the
# container, never into the mounted data directory, and streamed out over ssh.
#
# Usage:  ./scripts/pull-db.sh [dest]      default dest: ./history/prod-copy.db
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f scripts/deploy.env ] && { set -a; . scripts/deploy.env; set +a; }

KEY="${DEPLOY_KEY:-$HOME/.ssh/deploy_key}"
HOST="${DEPLOY_HOST:-}"
DIR="${DEPLOY_DIR:-insight-lite}"
DEST="${1:-history/prod-copy.db}"

if [ -z "$HOST" ]; then
  echo "✗ no target: set DEPLOY_HOST, or create scripts/deploy.env" >&2
  exit 1
fi

SSH=(ssh -i "${KEY/#\~/$HOME}" -o BatchMode=yes "$HOST")

echo "→ snapshotting report.db on $HOST"
"${SSH[@]}" "cd $DIR && docker compose exec -T report python3 -c \"
import sqlite3
src = sqlite3.connect('/work/data/history/report.db')
dst = sqlite3.connect('/tmp/pull.db')
src.backup(dst)
dst.close(); src.close()
print('ok')
\"" | tr -d '\r'

echo "→ streaming it down to $DEST"
mkdir -p "$(dirname "$DEST")"
"${SSH[@]}" "cd $DIR && docker compose exec -T report cat /tmp/pull.db" > "$DEST"

echo "→ cleaning up the container's copy"
"${SSH[@]}" "cd $DIR && docker compose exec -T report rm -f /tmp/pull.db" || true

echo "→ verifying"
python3 - "$DEST" <<'EOF'
import sqlite3, sys
p = sys.argv[1]
c = sqlite3.connect(p)
assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "integrity_check failed"
for t in ("person", "commits", "pull_request", "issue", "work_item_status"):
    try:
        print(f"  {t:18} {c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    except sqlite3.Error as e:
        print(f"  {t:18} -- {e}")
EOF
echo "✓ $DEST"
