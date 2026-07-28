# Prod cutover to the React frontend — plan & runbook

**Branch:** `feat/react-migration` (58 commits ahead of `main`, clean fast-forward; no remote → local merge only).
**Target:** flip prod (`insight.example.com` @ `your-server`) from the Jinja monolith to the React MPA.
**Author aid:** written 2026-07-24 after inspecting the live server. **I prepare; the operator runs the prod/merge steps.**

---

## 0. Risk profile (why this is low-risk *code*-wise)
- **No DB schema change** across `main..branch` (`store.py` +13 lines, zero DDL), **no new Python deps** (`requirements.txt` unchanged). The report DB stays compatible; no first-connect migration.
- Change is almost entirely `frontend/` + rendering/routing. Every migrated page keeps a `?legacy=1` Jinja fallback; the monolith stays reachable at `/report/legacy`.
- Rollback is fast (revert code/image; data untouched).

## 1. ⚠️ Prod does NOT match the documented deploy model — read first
Inspected `user@your-server:~/insight-report` on 2026-07-24:

| Documented (`deploy.sh` + repo `docker-compose.yml`) | Actual prod |
|---|---|
| image-swap: `docker save insight-report:latest \| ssh docker load` | **build-from-source on the box** (`build: .`) — images `insight-report-report/-mcp`; `insight-report:latest` absent |
| state on named volume `report-data` at `/work/data` | **no `report-data` volume**; `DATA_DIR` empty; state in the bind-mounted repo dir → `~/insight-report/history/report.db` (84 MB, live) |
| only compose + oauth assets on the server | **full source tree** shipped as files (+ `FabricReporting.zip`), **no `.git`** |
| `docker-compose.yml` = repo base (image + volume) | server has a **custom** `docker-compose.yml`: `build: .` + `volumes: - .:/work` |

**Consequence:** running `./deploy.sh` as-is would rsync the repo's base `docker-compose.yml` over the server's custom one and `up -d` against an **empty** `report-data` volume → app boots into the setup wizard with no data (data still safe on disk, but a broken cutover). **Do not run `deploy.sh` against this server until the model is reconciled (Path A).**

## 2. Two cutover paths — pick one

### Path B — minimal, data-safe (RECOMMENDED for the cutover itself)
Keep the current build-from-source + bind-mount model; ship only the new code + built assets. Live data in `~/insight-report/history/` is never moved.

Because `.:/work` shadows the image's baked `assets/app`, the **built** React bundle must exist in the server's repo dir. Steps (operator runs):

1. **Local:** on `main` (after the merge in §3), build the frontend so `assets/app/` holds the hashed bundle + manifest:
   ```bash
   cd frontend && npm ci && npm run build && cd ..
   git checkout -- assets/app/.gitkeep
   ```
2. **Snapshot + rollback safety on the server:**
   ```bash
   ssh -i ~/.ssh/ct_server user@your-server '
     cd ~/insight-report &&
     cp -a . ../insight-report.bak.$(date +%Y%m%dT%H%M%SZ) 2>/dev/null || true;   # or at least back up history/report.db
     docker compose exec -T report python3 -c "import store,shutil,os,datetime; db=store.db_path(); \
       os.path.exists(db) and shutil.copy2(db, db+\".pre-react.\"+datetime.datetime.utcnow().strftime(\"%Y%m%dT%H%M%SZ\"))"'
   ```
   (Cheap alternative: just `cp history/report.db history/report.db.pre-react` on the server.)
3. **Ship the React source + built assets** to the server's repo dir, EXCLUDING all runtime state (mirror `.dockerignore`): `history/ .cache/ .repos/ .runtime/ exports/ .env* people.yaml config.local.yaml data.json report.html *-editor.html frontend/node_modules`:
   ```bash
   rsync -az --delete-excluded \
     --exclude 'history/' --exclude '.cache/' --exclude '.repos/' --exclude '.runtime/' \
     --exclude 'exports/' --exclude '.env*' --exclude 'people.yaml' --exclude 'config.local.yaml' \
     --exclude 'data.json' --exclude 'report.html' --exclude '*-editor.html' \
     --exclude 'frontend/node_modules' --exclude '.git' --exclude 'FabricReporting.zip' \
     -e "ssh -i ~/.ssh/ct_server" ./ user@your-server:~/insight-report/
   ```
   (Keep the server's custom `docker-compose.yml` — add `--exclude docker-compose.yml` if the repo's base differs from the server's, which it does. **Exclude it.**)
4. **Rebuild + restart** (bind-mount serves host code; `--build` refreshes the image layer, deps unchanged so it's fast):
   ```bash
   ssh -i ~/.ssh/ct_server user@your-server 'cd ~/insight-report && docker compose up -d --build report mcp'
   ```
5. Verify (§4). Rollback (§5) = restore the code snapshot + `up -d --build`.

*Downside:* perpetuates the infra drift (build-on-box, bind-mount, no volume, `deploy.sh` still unusable). Fix later via Path A.

### Path A — reconcile to the documented image-swap model (do LATER, not during cutover)
One-time migration so `deploy.sh` works as designed forever:
1. Create the `report-data` volume and copy the live state into it: `history/`, `people.yaml`, `.cache/`, `.repos/`, `.env`, `config.local.yaml` → `/work/data/…` (via a throwaway container mounting both the repo dir and the volume).
2. Replace the server `docker-compose.yml` with the repo base (image + `report-data` volume, `DATA_DIR=/work/data`).
3. From then on, `./deploy.sh` (image save/load) is the deploy path; delete `FabricReporting.zip`, consider `git init` on the box or keep pure image-swap.
*Bigger, moves 84 MB of live data — schedule as its own maintenance task, verify data parity before killing the old dir.*

## 3. Merge (local, no remote)
```bash
git checkout main
git merge --ff-only feat/react-migration
git checkout feat/react-migration   # (optional) stay on branch for future work
```
No PR (no remote). No push. Operator runs this.

## 4. Post-deploy verification checklist
- `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/health` → 200; container logs clean.
- `/overview` → 200 and HTML references `assets/app/overview-*.js` + `report-chrome-*.js`.
- `/` → 302 `/overview`; `/report#trend` → shim → `/trend`; `/report/legacy` still serves the monolith (break-glass).
- Manage pages load (`/identity /config /update /setup /semantic /dashboards /metrics /calibrate /chat-log`); one save works (e.g. company change in Identity).
- Report interactivity: chat fab opens, drill (`data-drill` cell → rows), column sort, **person link → `/person?person=…`**.
- oauth attribution still works (headers pass through): `/calibrate` under a real login; the owner-only dashboard editor opens for its owner.
- `/mcp` responds with the 12 tools (bearer token). MCP is unchanged by this migration.

## 5. Rollback
- **Path B:** restore the code snapshot dir (or `git`-revert `main` locally + re-ship prior source) → `docker compose up -d --build`. Data untouched throughout.
- **Per-page soft fallback:** any page is available at `?legacy=1` (Jinja) if one route misbehaves; the monolith lives at `/report/legacy`.
- report.db backup taken in §2.

## 6. Do NOT / after-cutover
- **Never run `frontend/visual/seed_dashboards.py` on prod** — it's a local pixel-gate test helper (seeds `dash_gate_*`).
- Keep `?legacy=1` + the monolith through the first stabilization window; retiring them + the dashboard `.vl-panel` HTML path is **WS2-T6**, a separate later pass.
- Changelog: the migration is pixel-identical (users see no visual change) — a "What's new" entry is optional; if desired, one line like "Frontend rebuilt on React (faster, same UI)".
- Recommend a follow-up to reconcile the deploy model (Path A) so `deploy.sh` and the docs match reality; and to remove `FabricReporting.zip` from the server.
