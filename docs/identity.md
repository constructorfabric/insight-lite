# Identity resolution

How commits, PRs and reviews are attributed to people — and how to correct it.

[← back to the README](../README.md)

## Identity resolution

The report attributes git-history activity by commit-author email, but the
tables are grouped by GitHub login. That mapping is the most sensitive part of
the report: corporate emails, personal emails, renamed accounts, imported
history, and fork-based PR workflows can all make the same person appear as
multiple identities unless we resolve them deliberately.

The resolver builds `email -> GitHub login` from the evidence available in the
repos. It never asks contributors to change their Git config, and it does not
drop unresolved human emails silently.

### Inputs

- The **curated identity overrides** (`override` table, scope `person`, in
  `history/report.db`): reviewed canonical logins, display names, company
  affiliation, and known commit-email aliases. Edited at `/identity`. This is the
  preferred long-lived source of truth.
- `config.identity_overrides`: smaller manual override map for known
  `email-or-name -> login` cases.
- Git history from `.repos/`: commit author emails and commit author names
  observed in the lookback window.
- GitHub commits API: verified `email -> login` pairs where GitHub itself links
  a commit author to an account.
- GitHub PR API: authenticated PR authors and the commit emails inside their
  PRs, used for the PR-bridge layer.
- `config.bot_logins`: substring list used to exclude bot/service accounts from
  people metrics and from human-review suggestions.

### Resolution order

For every commit-author email seen in git history, the resolver applies these
layers in priority order:

1. **Reviewed directory / override**. The curated overrides' emails are merged on
   top of `config.identity_overrides`, so reviewed entries win. Use this for
   confirmed aliases, company corrections, renamed accounts, or any case where
   automatic evidence is insufficient.
2. **GitHub-verified author**. If the commits API returns a non-null GitHub
   `author` for a commit, GitHub has linked that commit email to a login. This
   is treated as ground truth unless a reviewed override exists.
3. **PR bridge**. For each PR author, the collector samples recent PR commits
   and maps the commit emails in those PRs back to the authenticated PR author.
   This catches common fork workflows where the commit email is not linked to
   GitHub, but the PR author is known.
4. **Name bridge**. If an unresolved email uses the same git author name as an
   already verified or overridden email, it inherits that login. Example: a
   personal email and a corporate email both commit as the same full name.
5. **Suggestion only**. If no layer resolves the email, the resolver computes a
   fuzzy suggestion from the email local-part and author names against known
   login/name tokens. Suggestions are written to `identity_suggestions.yaml` for
   human review; they are not treated as confirmed identity.

The `data.json` identity block summarizes how many emails were resolved by each
layer:

```json
{
  "identity": {
    "verified": 4,
    "pr_bridge": 0,
    "name_bridge": 0,
    "override": 58,
    "unresolved_human": 2
  }
}
```

`unresolved_human` should be read as a trust gap. The affected activity is not
silently assigned to a guessed person; review `identity_suggestions.yaml` or
`identity-editor.html`, confirm the correct mapping, and rerun collection.

### Manual review workflow

1. Run `python3 collect.py`. This writes `data.json` and may write
   `identity_suggestions.yaml`.
2. Run `python3 directory.py`. This writes `identity-editor.html` from the
   collected run plus the existing reviewed entries.
3. Open <http://localhost:8080/identity> from the local portal.
4. Review people in `Other`, duplicate suggestions, and any unresolved email
   aliases. Assign company, merge identities, and add/remove email aliases.
5. Click **Save**. The roster is POSTed as JSON and the local server writes it to
   the `override` table. If you are using the standalone HTML file instead of the
   portal, it downloads `people.json` for you to POST to `/api/people-yaml`.
6. Run `python3 collect.py` again so the reviewed directory overrides the
   automatic resolver.
7. Run `python3 render.py` to rebuild `report.html`.

Prefer curating confirmed long-lived mappings at `/identity`. Keep
`config.identity_overrides` for small exceptional mappings that are easier to
document next to the config.

There is deliberately **no YAML mirror** of the roster. A `people.yaml` was written
after every save as a "human-readable backup" and read back to seed an empty
override scope. Both roles were removed on 2026-07-28: when a roster actually had to
be restored, all 50 dated copies under `history/people/` turned out to be test-fixture
output, and the read path had already imported one of those fixtures into the prod
override table as curated data. Recovery came from a `report.db` snapshot, which is
what `deploy.sh` writes and what a restore should use.

### Trust rules

- A GitHub login is the canonical person key in the report.
- A commit email can belong to only one canonical login after resolution.
- A curated override wins over all automatic evidence.
- Bot/service accounts matched by `config.bot_logins` are excluded from people
  metrics.
- Fuzzy suggestions are review aids, not facts.
- Company attribution uses the curated overrides first, then
  `companies.overrides`, then email-domain mapping, then `companies.default`.

