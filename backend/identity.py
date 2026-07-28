#!/usr/bin/env python3
"""Identity resolution over the repo — map every commit-author email to a
GitHub login, built ENTIRELY from repo data (no "add your email" required).

Layers, highest priority first:
  1. overrides   — config.identity_overrides: {email-or-name: login}
  2. verified    — pairs GitHub itself confirmed (commits whose `author` is
                   non-null give a ground-truth email->login)
  3. name-bridge — an unlinked email inherits the login of a verified email
                   sharing the same git author NAME (e.g. your corp email and
                   your github-linked gmail both say "Alex Doe")
  4. suggestion  — anything still unresolved gets a PROPOSED login (fuzzy match
                   of name / email local-part against known logins) for a human
                   to confirm; written to identity_suggestions.yaml. Never
                   silently dropped.

Run standalone to inspect one or more repos:
    python identity.py            # default: insight
    python identity.py insight studio example-core
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import yaml

import paths
from ghclient import GH, load_config, token

# The repo root, one level up from backend/: templates/, assets/ and config.yaml
# live there, not next to the modules.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLONE_ROOT = os.environ.get("CLONE_DIR") or str(paths.data_path(".repos"))


def norm_tokens(s: str) -> set[str]:
    out, cur = [], []
    for ch in s.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur)); cur = []
    if cur:
        out.append("".join(cur))
    return {t for t in out if len(t) > 1}


def localpart(email: str) -> str:
    return email.split("@", 1)[0]


# --------------------------------------------------------------------------
REPORT_REF = "refs/report/head"   # stable local ref we always log against


def git_cmd(dest: str, *args: str) -> list[str]:
    return ["git", "-c", f"safe.directory={os.path.abspath(dest)}", "-C", dest, *args]


def redact_token(text: str, tok: str) -> str:
    if tok:
        text = text.replace(tok, "<token>")
    return text.replace("x-access-token:<token>@", "x-access-token:<redacted>@")


def git_error(result: subprocess.CompletedProcess, tok: str) -> str:
    msg = (result.stderr or result.stdout or "").strip()
    return redact_token(msg, tok).splitlines()[-1] if msg else "no git output"


def discard_invalid_clone(dest: str) -> bool:
    """Remove an existing clone path only when it is not a valid git checkout."""
    if not os.path.lexists(dest) or os.path.isdir(os.path.join(dest, ".git")):
        return False
    if os.path.islink(dest) or os.path.isfile(dest):
        os.unlink(dest)
    else:
        shutil.rmtree(dest)
    return True


def is_blobless_clone(dest: str) -> bool:
    if not os.path.isdir(os.path.join(dest, ".git")):
        return False
    result = subprocess.run(
        git_cmd(dest, "config", "--get", "remote.origin.partialclonefilter"),
        capture_output=True, text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def log_ref(dest: str) -> str:
    """Ref to run `git log` on: the freshly-fetched REPORT_REF, else HEAD."""
    if subprocess.run(git_cmd(dest, "rev-parse", "--verify", "-q", REPORT_REF),
                      capture_output=True).returncode == 0:
        return REPORT_REF
    return "HEAD"


def clone(org: str, name: str, since_date: str, tok: str) -> str | None:
    """Clone if missing, else `git fetch` (incremental: only new objects).

    Either way refresh REPORT_REF to the remote default-branch tip using a
    fresh token, so re-runs are both up-to-date and cheap.
    """
    dest = os.path.join(CLONE_ROOT, name)
    url = f"https://x-access-token:{tok}@github.com/{org}/{name}.git"
    clean_url = f"https://github.com/{org}/{name}.git"
    try:
        if is_blobless_clone(dest):
            shutil.rmtree(dest)
            print(f"  discarded blobless clone cache: {name}", file=sys.stderr)
        if not os.path.isdir(os.path.join(dest, ".git")):
            os.makedirs(CLONE_ROOT, exist_ok=True)
            if discard_invalid_clone(dest):
                print(f"  discarded invalid clone cache: {name}", file=sys.stderr)
            # FULL history (no --shallow-since): needed for accurate `git blame`
            # attribution of content markers. Heavier, but accuracy > speed here.
            base = ["git", "clone", "--quiet", "--no-checkout", "--single-branch"]
            res = subprocess.run(base + [url, dest], capture_output=True, text=True)
            if res.returncode != 0:
                print(f"  clone failed: {org}/{name}: {git_error(res, tok)}", file=sys.stderr)
                return None
        else:
            # if a previous run left a shallow clone, deepen it to full history
            if os.path.exists(os.path.join(dest, ".git", "shallow")):
                subprocess.run(git_cmd(dest, "fetch", "--quiet", "--unshallow", url),
                               capture_output=True, text=True)
        # incremental refresh -> REPORT_REF (transfers only new commits/trees)
        spec = f"+HEAD:{REPORT_REF}"
        res = subprocess.run(git_cmd(dest, "fetch", "--quiet", url, spec),
                             capture_output=True, text=True)
        if res.returncode != 0:
            print(f"  fetch failed: {org}/{name}: {git_error(res, tok)}", file=sys.stderr)
        return dest
    finally:
        # SECURITY: `git clone` persists the tokened remote URL in .git/config.
        # Strip it on EVERY exit path — including a failed clone that left a
        # partial repo (an early return here previously leaked the token to disk).
        if os.path.isdir(os.path.join(dest, ".git")):
            subprocess.run(git_cmd(dest, "remote", "set-url", "origin", clean_url),
                           capture_output=True, text=True)


def authors_from_clone(dest: str, since_iso: str) -> dict[str, set[str]]:
    """email -> set of git author names seen with that email (in window)."""
    out = subprocess.run(
        git_cmd(dest, "log", "--no-merges", f"--since={since_iso}",
                "--format=%ae%x09%an", log_ref(dest)),
        capture_output=True, text=True,
    ).stdout
    email_names: dict[str, set[str]] = defaultdict(set)
    for ln in out.splitlines():
        if "\t" in ln:
            e, n = ln.split("\t", 1)
            email_names[e.lower()].add(n.strip())
    return email_names


def verified_pairs(gh: GH, org: str, repo: str, since_iso: str) -> dict[str, str]:
    """email -> login, only where GitHub confirmed the link (author non-null)."""
    pairs: dict[str, str] = {}
    rows = gh.paginate(f"/repos/{org}/{repo}/commits", {"since": since_iso}, cap=20000)
    if len(rows) >= 20000:
        gh.warnings.append(f"verified_pairs {org}/{repo}: hit 20000-commit cap")
        print(f"  WARN: verified_pairs {org}/{repo} hit the 20000-commit cap — "
              f"identity resolution may be incomplete", file=sys.stderr)
    for c in rows:
        a = c.get("author")
        ce = ((c.get("commit") or {}).get("author") or {}).get("email")
        if a and a.get("login") and ce:
            pairs[ce.lower()] = a["login"]
    return pairs


def is_bot(login: str, bots) -> bool:
    low = (login or "").lower()
    for b in bots:
        bl = b.lower()
        if bl.startswith("="):          # exact match (avoids e.g. "claude" hitting "claudette")
            if low == bl[1:]:
                return True
        elif bl in low:                 # substring match (default)
            return True
    return False


def gather_pr_bridge(gh: GH, orgs, since_date: str, bots, k: int = 2,
                     skip_prefixes: tuple = ()) -> dict[str, str]:
    """email -> login learned from authenticated PR authorship.

    People PR from their own forks, so a PR's author (a real login) "owns" the
    commits in it — including commits whose email is NOT linked to any account.
    We sample up to k PRs per author and map their commit emails -> that author.
    Bounded by ~(#PR authors * k) calls; cached. Migration-recreated PR stubs
    ('[PR #NNN] …') are skipped — their commits belong to the original author.
    """
    from collections import Counter, defaultdict
    by_author: dict[str, list] = defaultdict(list)
    for org in orgs:
        for pr in gh.search_all(f"org:{org} type:pr", since_date):
            login = (pr.get("user") or {}).get("login")
            if not login or is_bot(login, bots):
                continue
            if skip_prefixes and (pr.get("title") or "").lstrip().startswith(skip_prefixes):
                continue
            repo_full = pr["repository_url"].split("/repos/")[-1]
            by_author[login].append((repo_full, pr["number"]))
    votes: dict[str, Counter] = defaultdict(Counter)
    for login, items in by_author.items():
        for repo_full, num in items[:k]:
            for c in gh.paginate(f"/repos/{repo_full}/pulls/{num}/commits", cap=300):
                e = ((c.get("commit") or {}).get("author") or {}).get("email")
                if e:
                    votes[e.lower()][login] += 1
    return {e: cnt.most_common(1)[0][0] for e, cnt in votes.items()}


# --------------------------------------------------------------------------
def build_identity(email_names: dict[str, set[str]], verified: dict[str, str],
                   overrides: dict, bots: list[str], pr_pairs: dict | None = None):
    """Return (email2login, resolution_reason, suggestions).

    Priority: override > verified > pr-bridge > name-bridge > suggestion.
    pr_pairs: email -> login learned from authenticated PR authorship (a human
    PR'd from their fork, and the PR contains commits with that email).
    """
    ov_email = {k.lower(): v for k, v in overrides.items()}
    pr_pairs = {k.lower(): v for k, v in (pr_pairs or {}).items()}

    # name -> login from verified emails sharing that name
    name_login: dict[str, str] = {}
    for email, login in verified.items():
        for nm in email_names.get(email, []):
            name_login.setdefault(nm.lower(), login)
    # overrides may also key by name
    for k, v in overrides.items():
        name_login.setdefault(k.lower(), v)

    # candidate pool + each login's identity tokens (for suggestions)
    login_tokens: dict[str, set[str]] = defaultdict(set)
    for email, login in verified.items():
        login_tokens[login] |= norm_tokens(login) | norm_tokens(localpart(email))
        for nm in email_names.get(email, []):
            login_tokens[login] |= norm_tokens(nm)

    email2login, reason, suggestions = {}, {}, []
    for email, names in email_names.items():
        nlow = {n.lower() for n in names}
        if email in ov_email:
            email2login[email] = ov_email[email]; reason[email] = "override"
        elif email in verified:
            email2login[email] = verified[email]; reason[email] = "verified"
        elif email in pr_pairs:
            email2login[email] = pr_pairs[email]; reason[email] = "pr-bridge"
        elif any(n in name_login for n in nlow):
            login = next(name_login[n] for n in nlow if n in name_login)
            email2login[email] = login; reason[email] = "name-bridge"
        else:
            # suggest
            cand = norm_tokens(" ".join(names)) | norm_tokens(localpart(email))
            best, best_score = None, 0.0
            for login, toks in login_tokens.items():
                jac = len(cand & toks) / len(cand | toks) if (cand | toks) else 0
                seq = max((SequenceMatcher(None, localpart(email), login).ratio(),
                           *(SequenceMatcher(None, n, login.lower()).ratio() for n in nlow)))
                score = max(jac, seq)
                if score > best_score:
                    best, best_score = login, score
            is_bot = any(b.lower() in email for b in bots) or any(
                b.lower() in n for n in nlow for b in bots)
            suggestions.append({
                "email": email, "names": sorted(names),
                "suggested_login": best, "confidence": round(best_score, 2),
                "likely_bot": is_bot,
            })
            reason[email] = "unresolved"
    return email2login, reason, suggestions


# --------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    org = cfg["org"]
    lookback = int(cfg["lookback_days"])
    bots = cfg.get("bot_logins", [])
    overrides = cfg.get("identity_overrides", {}) or {}
    since_dt = (datetime.now(timezone.utc) - timedelta(days=lookback)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    since_date = since_dt.strftime("%Y-%m-%d")

    repos = sys.argv[1:] or ["insight"]
    orgs = [org] + (cfg.get("extra_orgs", []) or [])
    tok = token()
    gh = GH(tok, cache_ttl_hours=float(cfg.get("cache_ttl_hours", 24)))

    email_names: dict[str, set[str]] = defaultdict(set)
    verified: dict[str, str] = {}
    for repo in repos:
        print(f"[{repo}] clone…", file=sys.stderr)
        dest = clone(org, repo, since_date, tok)
        if dest:
            for e, ns in authors_from_clone(dest, since_iso).items():
                email_names[e] |= ns
        print(f"[{repo}] verified pairs (API, cached)…", file=sys.stderr)
        verified.update(verified_pairs(gh, org, repo, since_iso))

    print(f"PR-bridge across orgs {orgs}…", file=sys.stderr)
    pr_pairs = gather_pr_bridge(gh, orgs, since_date, bots)
    email2login, reason, suggestions = build_identity(
        email_names, verified, overrides, bots, pr_pairs)

    by_reason = defaultdict(int)
    for r in reason.values():
        by_reason[r] += 1
    print(f"\nEmails: {len(email_names)} | " +
          " | ".join(f"{k}: {v}" for k, v in sorted(by_reason.items())))

    # show a few interesting resolutions
    print("\nResolved (sample):")
    for e in sorted(email2login):
        if reason[e] in ("name-bridge", "override"):
            print(f"  [{reason[e]:11}] {e:42} -> {email2login[e]}")

    real_sug = [s for s in suggestions if not s["likely_bot"]]
    if real_sug:
        print(f"\nSUGGESTIONS for {len(real_sug)} unresolved (confirm into config.identity_overrides):")
        for s in sorted(real_sug, key=lambda x: -x["confidence"]):
            print(f"  {s['email']:42} ? {s['suggested_login']}  "
                  f"(conf {s['confidence']}, names={s['names']})")
        with open(paths.data_path("identity_suggestions.yaml"), "w") as fh:
            yaml.safe_dump({"identity_overrides": {
                s["email"]: s["suggested_login"] for s in real_sug}}, fh, sort_keys=False,
                allow_unicode=True)
        print("\nWrote identity_suggestions.yaml (review, then merge confirmed lines into config.yaml).")
    else:
        print("\nNo unresolved human emails. 🎉")


if __name__ == "__main__":
    main()
