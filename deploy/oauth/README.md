# GitHub-OAuth gate for the portal (oauth2-proxy)

Let in members of the connected GitHub org; refuse everyone else. Basic-auth stays
as a break-glass fallback. `/mcp` is untouched (it keeps its own bearer token).

```
browser ──▶ nginx :443 ──▶ oauth2-proxy :4180 ──▶ report portal :8080
                    └────▶ /mcp ──▶ mcp :8082   (bypasses the gate)
```

It runs as a compose service behind the **`oauth`** profile, so it does **not** start
with the normal deploy until you enable it.

## One-time setup

### 1. Register a GitHub OAuth App
GitHub → Settings → Developer settings → **OAuth Apps** → New OAuth App
- **Homepage URL:** `https://insight.example.com`
- **Authorization callback URL:** `https://insight.example.com/oauth2/callback`

Copy the **Client ID** and generate a **Client secret**.

> If the org has "OAuth App access restrictions" enabled, an org **owner** must
> approve this app once, or members won't show up as org members.

### 2. Fill `.env` on the prod host
```
OAUTH_GITHUB_ORG=your-org
OAUTH_CLIENT_ID=<client id>
OAUTH_CLIENT_SECRET=<client secret>
OAUTH_COOKIE_SECRET=<python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())">
OAUTH_REDIRECT_URL=https://insight.example.com/oauth2/callback
# optional break-glass basic-auth:
OAUTH_HTPASSWD_FILE=/etc/oauth2-proxy/htpasswd
```

### 3. (optional) Break-glass basic-auth
```
htpasswd -B -c deploy/oauth/htpasswd insight
```
(leave `OAUTH_HTPASSWD_FILE` empty to skip the fallback entirely)

### 4. Swap the nginx site
```
sudo cp deploy/oauth/nginx-insight.example.com.conf /etc/nginx/sites-available/insight.example.com
sudo ln -sf /etc/nginx/sites-available/insight.example.com /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```
TLS is terminated at the Cloudflare edge (the `*.example.com` cert), so no certbot is
needed for the new domain. The origin keeps its old Let's Encrypt cert only to satisfy
Cloudflare's Full mode; use a Cloudflare Origin certificate for Full (strict).

### 5. Start the gate
```
docker compose --profile oauth up -d
```

## Verify
- Visit `https://insight.example.com` in a logged-out browser → bounced to GitHub;
  approve → back in the portal (if you're an org member). A non-member gets 403.
- `curl -u insight:<pw> https://insight.example.com/` → 200 (break-glass works).
- `curl -H "Authorization: Bearer <MCP_TOKEN>" https://insight.example.com/mcp` → still 200
  (MCP unaffected by the gate).

## Roll back
```
docker compose --profile oauth down            # stop the gate
sudo cp <your old site> /etc/nginx/sites-available/insight.example.com  # or point / back to :8080
sudo systemctl reload nginx
```

## Branded pages
`sign_in.html` and `error.html` here restyle the login and the "access denied"
screens as Constructor Insight (loaded via `--custom-templates-dir`, wired in
compose). If a template ever misbehaves, set `OAUTH_TEMPLATES_DIR=` (empty) in
`.env` to fall back to oauth2-proxy's built-in pages, and check
`docker compose --profile oauth logs oauth2-proxy`.

## Notes / limits
- `--github-org` matches a **single** org. Multiple connected orgs aren't OR-ed by
  oauth2-proxy — set the primary org here, or switch to `--github-team org:team`.
- `read:org` scope is required to see private org membership.
- Keep `PORTAL_PASSWORD` set on the `report` service too, so the portal is still
  protected if you ever point nginx straight back at `:8080`.
