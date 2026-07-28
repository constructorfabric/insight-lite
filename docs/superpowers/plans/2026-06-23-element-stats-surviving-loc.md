# Per-Element stats, repo LOC sizing, and surviving-LOC contribution metric — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a product-"Element" grouping with per-element stats, show each repo's size in LOC in the inventory, and replace the headline contribution metric with `git blame`-based surviving LOC (hand-written vs AI-generated, specs vs code).

**Architecture:** `config.yaml` gains an `elements:` taxonomy and `collect.py` gains a `make_element` classifier plus a `blame_tree` pass that blames every code/spec file in each cloned repo's current tree, attributing each surviving line to its last author (split spec/code and human/AI-generated), with a per-repo blob-SHA cache. `data.json` grows per-repo size fields, an `element` per repo, per-person surviving-LOC fields, and an `elements` rollup. `render.py` adds an Elements section/tab, LOC + Element columns in the repo inventory, and re-ranks the People table on hand-written surviving code LOC.

**Tech Stack:** Python 3 (stdlib `subprocess`/`re`/`json`), Jinja2 (inline template in `render.py`), `unittest`. Git CLI for blame. Spec at `docs/superpowers/specs/2026-06-23-element-stats-surviving-loc-design.md`.

---

## File structure

- `config.yaml` — add `elements:` block (modify).
- `collect.py` — add `make_element`, `blame_tree` + helpers, blame cache I/O, new `blank_person` fields, wire blame + per-repo windowed stats + `elements` rollup into `main()` and `parse_git` (modify).
- `render.py` — `build_model`: element rollup passthrough, repo_rows LOC/element, people table re-rank, totals; `TEMPLATE`: Elements section + tab button + JS mode + inventory columns + people columns (modify).
- `tests/test_rules.py` — tests for `make_element`, `blame_tree` (real temp git repo), element rollup, and people re-rank (modify).

Conventions to follow: classifier factories return closures (`make_is_spec`); git invoked via `git_cmd(dest, ...)` + `log_ref(dest)`; blame already used in `blame_marker_authors`; model fields read with `.get(...)` defaults so `build_model` tolerates pre-migration `data.json`.

---

## Task 1: Element taxonomy + `make_element` classifier

**Files:**
- Modify: `config.yaml` (after the `repos:` block, before `labels:`)
- Modify: `collect.py` (add `make_element` after `classify`, ~line 45)
- Test: `tests/test_rules.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_rules.py`, and add `make_element` to the `from collect import ...` line (line 15):

```python
    def test_make_element_maps_repos_to_product_elements(self):
        from collect import make_element
        cfg = {"elements": {
            "Insight": ["insight", "example-web-front", "example-legacy-web"],
            "Studio": ["studio", "example-codegen", "example-studio"],
            "Gears": ["gears-*", "example-crate", "example-legacy-*", "example-template"],
            "default": "Other",
        }}
        element_of = make_element(cfg)
        self.assertEqual(element_of("insight"), "Insight")
        self.assertEqual(element_of("example-web-front"), "Insight")
        self.assertEqual(element_of("example-legacy-web"), "Insight")          # old-org name
        self.assertEqual(element_of("example-core"), "Gears")               # prefix glob
        self.assertEqual(element_of("example-core-web-docs"), "Gears")      # prefix glob
        self.assertEqual(element_of("example-legacy-frontend"), "Gears")         # old-org glob
        self.assertEqual(element_of("example-crate"), "Gears")              # exact beats nothing
        self.assertEqual(element_of("example-codegen"), "Studio")
        self.assertEqual(element_of("totally-unknown"), "Other")          # default

    def test_make_element_exact_match_wins_over_glob(self):
        from collect import make_element
        cfg = {"elements": {
            "Gears": ["gears-*"],
            "Docs": ["example-webdocs"],
            "default": "Other",
        }}
        element_of = make_element(cfg)
        self.assertEqual(element_of("example-webdocs"), "Docs")   # exact wins
        self.assertEqual(element_of("example-core"), "Gears")     # glob fallback
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.CollectRulesTest.test_make_element_maps_repos_to_product_elements -v`
Expected: FAIL — `ImportError: cannot import name 'make_element'`.

- [ ] **Step 3: Implement `make_element` in `collect.py`**

Insert after `classify` (after line 45):

```python
def make_element(cfg: dict):
    """Repo name -> product element. Exact names win; then trailing-'*' prefix
    globs (e.g. 'gears-*'); else `elements.default`. Orthogonal to platform/app.
    Same mapping applies across both orgs because repo names mirror."""
    elems = cfg.get("elements", {}) or {}
    default = elems.get("default", "Other")
    exact: dict[str, str] = {}
    prefixes: list[tuple[str, str]] = []
    for elem, pats in elems.items():
        if elem == "default":
            continue
        for pat in (pats or []):
            if pat.endswith("*"):
                prefixes.append((pat[:-1], elem))
            else:
                exact[pat] = elem

    def element_of(name: str) -> str:
        if name in exact:
            return exact[name]
        for pre, elem in prefixes:
            if name.startswith(pre):
                return elem
        return default

    return element_of
```

- [ ] **Step 4: Add the `elements:` block to `config.yaml`**

Insert immediately after the `repos:` block closes (after the `ignore:` list, before the `labels:` section header comment, around line 76):

```yaml
# ---------------------------------------------------------------------------
# Product "Element" grouping (orthogonal to platform/app). Every repo maps to
# exactly one element: exact names first, then trailing-'*' prefix globs, else
# `default`. Same map applies to both orgs (names mirror after migration).
# LOC/size are only computable for cloned primary-org repos; old-org repos
# still roll their PR/people metadata into their element.
# ---------------------------------------------------------------------------
elements:
  Insight:
    - insight
    - example-web-front
    - example-legacy-web
    - example-legacy-web-front
  Studio:
    - studio
    - example-studio-kit
    - example-slidey
    - example-studio
    - example-studio-app
    - example-studio-kit
    - example-codegen
    - example-pilot
    - example-pilot-kit
  Gears:
    - gears-*
    - example-crate
    - example-template
    - example-legacy-*
    - example-cli
    - example-docs
    - example-arc
  Wiki:
    - example-wiki
    - example-wiki-back
    - example-wiki-front
    - example-wiki-front-old
  Courses:
    - ai-courses
    - your-org-courses
    - your-old-org-courses
    - your-old-org-tutorials
  Website:
    - website
    - website-previews
  DNA:
    - DNA
  Tooling:
    - example-migration-sync
    - example-db-tools
    - example-lounge
  example-app:
    - example-app
  default: Other
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.CollectRulesTest.test_make_element_maps_repos_to_product_elements tests.test_rules.CollectRulesTest.test_make_element_exact_match_wins_over_glob -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Verify `config.yaml` parses and every active repo maps**

Run:
```bash
cd "/path/to/insight" && python -c "
import json, yaml
from collect import make_element
cfg = yaml.safe_load(open('config.yaml'))
eo = make_element(cfg)
d = json.load(open('data.json'))
from collections import Counter
c = Counter(eo(r['name']) for r in d['repos'].values())
print(dict(c))
other = sorted({r['name'] for r in d['repos'].values() if eo(r['name'])=='Other'})
print('Other:', other)
"
```
Expected: a dict of element→count printed; `Other: []` (every current repo maps). If any repo lands in `Other`, add it to `config.yaml` and re-run.

- [ ] **Step 7: Commit**

```bash
cd "/path/to/insight"
git add config.yaml collect.py tests/test_rules.py
printf '%s\n' "feat: element taxonomy config + make_element classifier" > /tmp/commit-msg.txt
git commit -F /tmp/commit-msg.txt
```

---

## Task 2: `blame_tree` — full-tree surviving-LOC blame engine

Blames every spec/code file in a repo's current tree, attributing each surviving line to its last committer, split by `kind` (spec/code) and `origin` (human/ai), with all-time and windowed counts, plus repo size totals. Spec precedence: a `.md` file that is a spec counts as spec, never code.

**Files:**
- Modify: `collect.py` (add module constants near line 33; add helpers + `blame_tree` after `blame_marker_authors`, ~line 320)
- Test: `tests/test_rules.py`

- [ ] **Step 1: Write the failing test (real temp git repo)**

Add a new test class to `tests/test_rules.py`. It builds a real git repo so blame runs for real (deterministic; no network):

```python
class BlameTreeTest(unittest.TestCase):
    def _run(self, *args, cwd):
        env = dict(os.environ,
                   GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="dev@x.com",
                   GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="dev@x.com",
                   GIT_AUTHOR_DATE="2026-06-01T00:00:00", GIT_COMMITTER_DATE="2026-06-01T00:00:00")
        subprocess.run(["git", *args], cwd=cwd, check=True,
                       capture_output=True, text=True, env=env)

    def _make_repo(self, tmp):
        self._run("init", "-q", "-b", "main", cwd=tmp)
        code = "line a\n@cpt-begin\nline gen1\nline gen2\n@cpt-end\nline b\n"  # 4 human + 2 ai (begin/end count ai)
        Path(tmp, "src.py").write_text(code)
        Path(tmp, "doc.md").write_text("# Title\nbody one\nbody two\n")        # spec, all human
        Path(tmp, "gen.md").write_text("---\nstudio: true\n---\nx\ny\n")        # spec, all ai
        Path(tmp, "pkg.lock").write_text("ignored\n")                          # excluded (suffix .lock)
        self._run("add", "-A", cwd=tmp)
        self._run("commit", "-q", "-m", "init", cwd=tmp)

    def test_blame_tree_splits_code_spec_and_human_ai(self):
        from collect import blame_tree, make_is_spec, make_is_meaningful_loc
        is_spec = make_is_spec({})
        is_loc = make_is_meaningful_loc({"exclude_suffixes": [".lock"]})
        with TemporaryDirectory() as tmp:
            self._make_repo(tmp)
            acc, sizes, cache = blame_tree(tmp, "HEAD", is_spec, is_loc,
                                           since_date="2026-01-01", cache={})
            a = acc["dev@x.com"]
            # src.py: 6 lines. @cpt-begin/@cpt-end + the 2 lines between = 4 ai; "line a"/"line b" = 2 human
            self.assertEqual(a["code_human"], 2)
            self.assertEqual(a["code_ai"], 4)
            # doc.md = 3 human spec lines; gen.md (studio:true) = 5 ai spec lines
            self.assertEqual(a["spec_human"], 3)
            self.assertEqual(a["spec_ai"], 5)
            self.assertEqual(sizes["code_loc"], 6)
            self.assertEqual(sizes["spec_loc"], 8)
            self.assertEqual(sizes["total_loc"], 14)

    def test_blame_tree_windows_by_commit_date(self):
        from collect import blame_tree, make_is_spec, make_is_meaningful_loc
        is_spec = make_is_spec({})
        is_loc = make_is_meaningful_loc({})
        with TemporaryDirectory() as tmp:
            self._make_repo(tmp)  # all commits dated 2026-06-01
            acc, _, _ = blame_tree(tmp, "HEAD", is_spec, is_loc,
                                   since_date="2026-07-01", cache={})  # window starts AFTER commit
            a = acc["dev@x.com"]
            self.assertEqual(a["code_human"], 2)        # all-time unaffected
            self.assertEqual(a["win_code_human"], 0)    # nothing inside window
            acc2, _, _ = blame_tree(tmp, "HEAD", is_spec, is_loc,
                                    since_date="2026-05-01", cache={})  # window covers commit
            self.assertEqual(acc2["dev@x.com"]["win_code_human"], 2)

    def test_blame_tree_cache_round_trips(self):
        from collect import blame_tree, make_is_spec, make_is_meaningful_loc
        is_spec, is_loc = make_is_spec({}), make_is_meaningful_loc({})
        with TemporaryDirectory() as tmp:
            self._make_repo(tmp)
            _, _, cache = blame_tree(tmp, "HEAD", is_spec, is_loc, "2026-01-01", {})
            self.assertTrue(cache)  # populated
            # second run with the populated cache yields identical accumulation
            acc2, _, _ = blame_tree(tmp, "HEAD", is_spec, is_loc, "2026-01-01", cache)
            self.assertEqual(acc2["dev@x.com"]["code_human"], 2)
```

Add `os` is already imported in the test file. `Path` and `TemporaryDirectory` are already imported.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.BlameTreeTest -v`
Expected: FAIL — `ImportError: cannot import name 'blame_tree'`.

- [ ] **Step 3: Add module constants in `collect.py`**

After line 33 (`C, F = "\x01", "\x1f" ...`) add:

```python
# Surviving-LOC origin detection (reuses studio_provenance marker semantics).
CPT_BEGIN = re.compile(r'@cpt-begin')
CPT_END = re.compile(r'@cpt-end')
CPT_SINGLE = re.compile(r'@cpt-[a-z]+:')
STUDIO_SPEC = re.compile(r'^\s*studio:\s*true\s*$', re.M)   # spec frontmatter
GEN_STAMP = re.compile(r'Generated by cfs? ')              # cf / cfs stamp
```

- [ ] **Step 4: Add blame helpers + `blame_tree` in `collect.py`**

Insert after `blame_marker_authors` (after line 319):

```python
def _blame_file_records(dest: str, ref: str, path: str, is_spec_file: bool):
    """Blame one file. Returns {f'{email}\\x1f{kind}\\x1f{ai}\\x1f{date}': count}
    where kind in {code,spec}, ai in {0,1}, date = committer-date YYYY-MM-DD.
    Origin: lines inside @cpt-begin/@cpt-end (inclusive) or @cpt-…: are AI; a
    file with `studio: true` frontmatter or a `Generated by cf/cfs` stamp is
    wholly AI. Needs a full (with-blobs) clone."""
    out = subprocess.run(
        git_cmd(dest, "blame", "--line-porcelain", ref, "--", path),
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return {}
    kind = "spec" if is_spec_file else "code"
    # Parse porcelain into ordered per-line records: (email, date, content)
    lines: list[tuple[str, str, str]] = []
    cur_mail, cur_date = None, "?"
    for ln in out.stdout.split("\n"):
        if ln.startswith("author-mail "):
            cur_mail = ln[12:].strip().strip("<>").lower()
        elif ln.startswith("committer-time "):
            try:
                cur_date = datetime.fromtimestamp(
                    int(ln[15:].strip()), tz=timezone.utc).strftime("%Y-%m-%d")
            except ValueError:
                cur_date = "?"
        elif ln.startswith("\t"):
            lines.append((cur_mail or "", cur_date, ln[1:]))
    content = "\n".join(t for _, _, t in lines)
    whole_ai = bool(GEN_STAMP.search(content)) or (is_spec_file and STUDIO_SPEC.search(content))
    counts: Counter = Counter()
    in_cpt = False
    for email, date, text in lines:
        if whole_ai:
            ai = 1
        elif CPT_BEGIN.search(text):
            in_cpt, ai = True, 1
        elif CPT_END.search(text):
            ai, in_cpt = 1, False
        elif CPT_SINGLE.search(text):
            ai = 1
        else:
            ai = 1 if in_cpt else 0
        counts[f"{email}\x1f{kind}\x1f{ai}\x1f{date}"] += 1
    return dict(counts)


def blame_tree(dest: str, ref: str, is_spec, is_meaningful_loc,
               since_date: str, cache: dict):
    """Blame every spec/code file in `ref`'s tree -> surviving-LOC per author.

    Returns (acc, sizes, cache):
      acc[email] = {code_human, code_ai, spec_human, spec_ai,
                    win_code_human, win_code_ai, win_spec_human, win_spec_ai}
        all-time = every surviving line; win_* = lines whose committer-date >= since_date.
      sizes = {code_loc, spec_loc, total_loc}  (totals across all authors)
      cache = {f'{path}\\x00{blob_sha}': records}  reusable across runs;
        unchanged blobs are read from cache instead of re-blamed.
    Spec precedence: a spec .md is counted as spec, never code."""
    tree = subprocess.run(
        git_cmd(dest, "ls-tree", "-r", ref),
        capture_output=True, text=True,
    )
    acc: dict[str, dict] = {}
    sizes = {"code_loc": 0, "spec_loc": 0, "total_loc": 0}

    def slot(email: str) -> dict:
        return acc.setdefault(email, {
            "code_human": 0, "code_ai": 0, "spec_human": 0, "spec_ai": 0,
            "win_code_human": 0, "win_code_ai": 0, "win_spec_human": 0, "win_spec_ai": 0,
        })

    for row in tree.stdout.splitlines():
        # "<mode> <type> <sha>\t<path>"
        meta, _, path = row.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or parts[1] != "blob":
            continue
        blob = parts[2]
        is_spec_file = is_spec(path)
        if not is_spec_file and not is_meaningful_loc(path):
            continue
        key = f"{path}\x00{blob}"
        records = cache.get(key)
        if records is None:
            records = _blame_file_records(dest, ref, path, is_spec_file)
            cache[key] = records
        for ckey, n in records.items():
            email, kind, ai, date = ckey.split("\x1f")
            origin = "ai" if ai == "1" else "human"
            s = slot(email)
            s[f"{kind}_{origin}"] += n
            sizes[f"{kind}_loc"] += n
            sizes["total_loc"] += n
            if date >= since_date:
                s[f"win_{kind}_{origin}"] += n
    return acc, sizes, cache
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.BlameTreeTest -v`
Expected: PASS (3 tests). If `test_blame_tree_splits_code_spec_and_human_ai` shows off-by-one on `@cpt-end`, confirm the fixture: `@cpt-begin`(ai) + `line gen1`(ai, in region) + `line gen2`(ai, in region) + `@cpt-end`(ai) = 4 ai; `line a` + `line b` = 2 human.

- [ ] **Step 6: Commit**

```bash
cd "/path/to/insight"
git add collect.py tests/test_rules.py
printf '%s\n' "feat: blame_tree surviving-LOC engine (spec/code, human/AI, windowed, cached)" > /tmp/commit-msg.txt
git commit -F /tmp/commit-msg.txt
```

---

## Task 3: Wire blame, element, and per-repo windowed stats into `collect.py` `main()`

Adds new person fields; runs `blame_tree` per cloned repo with a persisted cache; stores per-repo `element`, sizes, windowed commits/PRs; folds surviving LOC into people; builds the `elements` rollup in `data.json`.

**Files:**
- Modify: `collect.py` — `blank_person` (line 140), `parse_git` signature + body (lines 342-427), `main()` (repo inventory ~483, git pass ~503-518, fold ~565-600, PR loop ~602-618, review loop ~646-667, finalize/out ~807-821)
- Test: `tests/test_rules.py` (a small `build_elements_rollup` unit test)

- [ ] **Step 1: Write the failing test for the rollup helper**

Add to `tests/test_rules.py` (and add `build_elements_rollup` to the `from collect import ...` line):

```python
    def test_build_elements_rollup_aggregates_repos_and_people(self):
        from collect import build_elements_rollup
        repos = {
            "your-org/insight": {"name": "insight", "element": "Insight",
                "archived": False, "code_loc": 1000, "spec_loc": 200,
                "commits_window": 10, "ai_commits_window": 4,
                "prs_opened_window": 5, "prs_merged_window": 3},
            "your-old-org/example-legacy-web": {"name": "example-legacy-web", "element": "Insight",
                "archived": False, "code_loc": None, "spec_loc": None,
                "commits_window": 0, "ai_commits_window": 0,
                "prs_opened_window": 2, "prs_merged_window": 1},
            "your-org/studio": {"name": "studio", "element": "Studio",
                "archived": False, "code_loc": 500, "spec_loc": 50,
                "commits_window": 7, "ai_commits_window": 0,
                "prs_opened_window": 1, "prs_merged_window": 1},
        }
        people = {
            "alice": {"is_member": True, "repos": ["insight", "example-legacy-web"]},
            "bob": {"is_member": False, "repos": ["insight"]},
            "carol": {"is_member": True, "repos": ["studio"]},
        }
        elements_ttm = {"Insight": [10.0, 20.0, 30.0], "Studio": []}
        roll = build_elements_rollup(repos, people, elements_ttm)
        ins = roll["Insight"]
        self.assertEqual(ins["code_loc"], 1000)          # old-org None ignored
        self.assertEqual(ins["spec_loc"], 200)
        self.assertEqual(ins["repos"], 2)
        self.assertEqual(ins["commits_window"], 10)
        self.assertEqual(ins["prs_opened_window"], 7)    # 5 + 2
        self.assertEqual(ins["prs_merged_window"], 4)    # 3 + 1
        self.assertEqual(ins["ai_pct"], 40.0)            # 4/10
        self.assertEqual(ins["people_members"], 1)       # alice
        self.assertEqual(ins["people_external"], 1)      # bob
        self.assertEqual(ins["median_ttm_h"], 20.0)
        self.assertEqual(roll["Studio"]["people_members"], 1)  # carol
        self.assertIsNone(roll["Studio"]["median_ttm_h"])      # no ttms
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.CollectRulesTest.test_build_elements_rollup_aggregates_repos_and_people -v`
Expected: FAIL — `ImportError: cannot import name 'build_elements_rollup'`.

- [ ] **Step 3: Add `build_elements_rollup` helper to `collect.py`**

Insert after `make_element` (from Task 1):

```python
def build_elements_rollup(repos: dict, people: dict, elements_ttm: dict) -> dict:
    """Aggregate per-element stats from repo meta + people. LOC sums skip
    non-cloned repos (code_loc/spec_loc None). people_* count distinct logins
    whose commit-repos fall in the element."""
    def _med(xs):
        s = sorted(xs)
        return s[len(s) // 2] if s else None

    name2elem: dict[str, str] = {}
    roll: dict[str, dict] = {}
    for meta in repos.values():
        if meta.get("archived"):
            continue
        e = meta.get("element", "Other")
        name2elem[meta["name"]] = e
        r = roll.setdefault(e, {
            "element": e, "code_loc": 0, "spec_loc": 0, "repos": 0,
            "commits_window": 0, "ai_commits_window": 0,
            "prs_opened_window": 0, "prs_merged_window": 0,
            "people_members": 0, "people_external": 0, "median_ttm_h": None,
        })
        r["repos"] += 1
        r["code_loc"] += meta.get("code_loc") or 0
        r["spec_loc"] += meta.get("spec_loc") or 0
        r["commits_window"] += meta.get("commits_window", 0)
        r["ai_commits_window"] += meta.get("ai_commits_window", 0)
        r["prs_opened_window"] += meta.get("prs_opened_window", 0)
        r["prs_merged_window"] += meta.get("prs_merged_window", 0)

    elem_members: dict[str, set] = {}
    elem_external: dict[str, set] = {}
    for login, p in people.items():
        for repo_name in p.get("repos", []):
            e = name2elem.get(repo_name)
            if not e:
                continue
            bucket = elem_members if p.get("is_member") else elem_external
            bucket.setdefault(e, set()).add(login)
    for e, r in roll.items():
        r["people_members"] = len(elem_members.get(e, set()))
        r["people_external"] = len(elem_external.get(e, set()))
        r["ai_pct"] = round(100 * r["ai_commits_window"] / r["commits_window"], 1) if r["commits_window"] else 0.0
        r["median_ttm_h"] = _med(elements_ttm.get(e, []))
    return roll
```

- [ ] **Step 4: Run the rollup test to verify it passes**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.CollectRulesTest.test_build_elements_rollup_aggregates_repos_and_people -v`
Expected: PASS.

- [ ] **Step 5: Extend `blank_person` with surviving-LOC fields**

In `collect.py` `blank_person` (lines 140-153), add these keys to the returned dict (after the `"cpt_lines": 0, "commit_types": {},` line, line 149):

```python
        "surviving_code_human": 0, "surviving_code_ai": 0,
        "surviving_spec_human": 0, "surviving_spec_ai": 0,
        "survwin_code_human": 0, "survwin_code_ai": 0,
        "survwin_spec_human": 0, "survwin_spec_ai": 0,
```

- [ ] **Step 6: Add per-repo windowed counters to `parse_git`**

`parse_git` currently aggregates only per-email. Add an optional `repo_acc` dict it fills with this repo's window totals. Change the signature (line 342-344) to append `repo_acc=None`:

```python
def parse_git(dest: str, since_iso: str, is_spec, classification: str,
              email_agg: dict, email_names: dict, contributors: set,
              weekly: dict, is_meaningful_loc=None, ai_map=None, repo_types=None,
              repo_acc=None) -> None:
```

In the commit-header branch, after `a["commits"] += 1` (line 395) add:

```python
            if repo_acc is not None:
                repo_acc["commits"] = repo_acc.get("commits", 0) + 1
```

In `flush()`, inside `if cur_tools:` (after line 378's `a = agg(cur_email)` block, i.e. after `a["ai_loc"] += cur_meaningful`, line 381) add:

```python
            if repo_acc is not None:
                repo_acc["ai_commits"] = repo_acc.get("ai_commits", 0) + 1
```

Note: `flush()` reads `cur_tools`/`cur_meaningful` from the enclosing scope; `repo_acc` is a parameter and is visible in the closure without `nonlocal` because it is only read/mutated-in-place, never reassigned.

- [ ] **Step 7: Initialize element classifier + blame cache dir in `main()`**

In `main()` after `is_meaningful_loc = make_is_meaningful_loc(...)` (line 443) add:

```python
    element_of = make_element(cfg)
    blame_root = os.path.join(CLONE_ROOT, "..", ".cache", "blame")
    elements_ttm: dict[str, list] = {}
```

Replace that path with a cleaner one anchored at ROOT — use:

```python
    element_of = make_element(cfg)
    blame_cache_dir = os.path.join(ROOT, ".cache", "blame")
    os.makedirs(blame_cache_dir, exist_ok=True)
    elements_ttm: dict[str, list] = {}
```

(`ROOT` is already imported from `ghclient`. `.cache/` is gitignored.)

- [ ] **Step 8: Set `element` + windowed PR/merge defaults on repo meta at inventory time**

In the repo-inventory loop, in the dict assigned to `repos[f"{o}/{r['name']}"]` (lines 483-490), add these keys (after `"contributors": [], "forkers": [],`):

```python
                "element": element_of(r["name"]),
                "code_loc": None, "spec_loc": None, "total_loc": None,
                "commits_window": 0, "ai_commits_window": 0,
                "prs_opened_window": 0, "prs_merged_window": 0,
```

- [ ] **Step 9: Run blame in the git pass + capture per-repo window/size**

In the primary-org git pass loop (lines 503-518), replace the `parse_git(...)` call and the lines around it. Current:

```python
        contributors: set = set()
        ai_map = detect_ai_map(dest, since_iso, ai_detector, log_ref(dest))
        rtypes: dict = {}
        parse_git(dest, since_iso, is_spec, meta["classification"],
                  email_agg, email_names, contributors, weekly, is_meaningful_loc, ai_map, rtypes)
        meta["commit_types"] = rtypes.get(name, {})
```

Replace with:

```python
        contributors: set = set()
        ai_map = detect_ai_map(dest, since_iso, ai_detector, log_ref(dest))
        rtypes: dict = {}
        repo_acc: dict = {}
        parse_git(dest, since_iso, is_spec, meta["classification"],
                  email_agg, email_names, contributors, weekly, is_meaningful_loc,
                  ai_map, rtypes, repo_acc)
        meta["commit_types"] = rtypes.get(name, {})
        meta["commits_window"] = repo_acc.get("commits", 0)
        meta["ai_commits_window"] = repo_acc.get("ai_commits", 0)
        # surviving-LOC blame (full tree) -> repo size + per-author accumulation
        print(f"Blame: {name}", file=sys.stderr)
        cache_path = os.path.join(blame_cache_dir, f"{name}.json")
        bcache = {}
        if os.path.exists(cache_path):
            try:
                with open(cache_path) as cf:
                    bcache = json.load(cf)
            except (ValueError, OSError):
                bcache = {}
        bacc, bsizes, bcache = blame_tree(dest, log_ref(dest), is_spec,
                                          is_meaningful_loc, since_date, bcache)
        with open(cache_path, "w") as cf:
            json.dump(bcache, cf)
        meta["code_loc"] = bsizes["code_loc"]
        meta["spec_loc"] = bsizes["spec_loc"]
        meta["total_loc"] = bsizes["total_loc"]
        for email, s in bacc.items():
            tgt = surv_email_agg.setdefault(email, {})
            for k, v in s.items():
                tgt[k] = tgt.get(k, 0) + v
```

And initialize `surv_email_agg` next to `email_agg` (line 498). Change:

```python
    email_agg: dict[str, dict] = {}
```
to:
```python
    email_agg: dict[str, dict] = {}
    surv_email_agg: dict[str, dict] = {}
```

- [ ] **Step 10: Fold surviving LOC into people**

After the `studio_blame` fold block (after line 600, the `person(login)["cpt_lines"] += n` loop), add:

```python
    # --- surviving-LOC (git blame) folded into people ---------------------
    for email, s in surv_email_agg.items():
        login = email2login.get(email)
        if not login or is_bot(login, bots):
            continue
        p = person(login)
        for k, v in s.items():
            p[k] = p.get(k, 0) + v
```

- [ ] **Step 11: Capture per-repo PR counts in the PR loop**

In the PR loop (lines 602-618), after `p["prs_opened"] += 1` (line 614) add `meta["prs_opened_window"] += 1`, and inside the `if pr.get("pull_request", {}).get("merged_at"):` block (after `p["prs_merged"] += 1`, line 617) add `meta["prs_merged_window"] += 1`. Result:

```python
            p = person(login)
            p["prs_opened"] += 1
            meta["prs_opened_window"] += 1
            p[f"{meta['classification']}_prs"] += 1
            if pr.get("pull_request", {}).get("merged_at"):
                p["prs_merged"] += 1
                meta["prs_merged_window"] += 1
```

- [ ] **Step 12: Accumulate per-element time-to-merge in the review loop**

In the review loop (lines 646-667), after the `if tot:` block that sets `meta["reviews"]` (after line 654), add:

```python
        if ttms:
            elements_ttm.setdefault(meta.get("element", "Other"), []).extend(ttms)
```

- [ ] **Step 13: Build the `elements` rollup and add to `data.json`**

Just before the `out = {` dict (line 807), add:

```python
    elements_rollup = build_elements_rollup(repos, people, elements_ttm)
```

Then inside the `out = {...}` dict, add an `"elements": elements_rollup,` entry (e.g. right after `"fabric_trackers": fabric_trackers,`, line 812).

- [ ] **Step 14: Compile and run the full collect-side test subset**

Run:
```bash
cd "/path/to/insight"
python -m py_compile collect.py
python -m unittest tests.test_rules.CollectRulesTest tests.test_rules.BlameTreeTest -v
```
Expected: all PASS. (The existing `test_parse_git_logs_git_failures` still passes because `repo_acc` defaults to `None`.)

- [ ] **Step 15: Commit**

```bash
cd "/path/to/insight"
git add collect.py tests/test_rules.py
printf '%s\n' "feat: wire blame_tree + element + windowed repo stats + elements rollup into collect" > /tmp/commit-msg.txt
git commit -F /tmp/commit-msg.txt
```

---

## Task 4: `render.py` `build_model` — element rollup, repo LOC/element, people re-rank

**Files:**
- Modify: `render.py` `build_model` — repo_rows (lines 364-389), table (lines 279-305), return dict (lines 395-436)
- Test: `tests/test_rules.py` `RenderModelTest`

- [ ] **Step 1: Write the failing test**

Add to `RenderModelTest` in `tests/test_rules.py`:

```python
    def test_build_model_exposes_elements_repo_loc_and_surviving_rank(self):
        data = {
            "generated_at": "2026-06-22T00:00:00Z",
            "members": ["alice", "carol"],
            "elements": {
                "Insight": {"element": "Insight", "code_loc": 1000, "spec_loc": 200,
                            "repos": 2, "commits_window": 10, "ai_commits_window": 4,
                            "prs_opened_window": 7, "prs_merged_window": 4,
                            "people_members": 1, "people_external": 1,
                            "ai_pct": 40.0, "median_ttm_h": 20.0},
            },
            "repos": {
                "org/insight": {"name": "insight", "classification": "app",
                    "element": "Insight", "unclassified": False, "forks": 1, "stars": 1,
                    "archived": False, "traffic_access": True, "clones_14d": 5,
                    "unique_cloners_14d": 2, "contributor_emails": 2,
                    "code_loc": 1000, "spec_loc": 200, "total_loc": 1200},
            },
            "people": {
                "alice": {"total_activity": 5, "commits": 3, "additions": 50, "deletions": 0,
                    "meaningful_additions": 40, "meaningful_deletions": 0, "prs_opened": 2,
                    "prs_merged": 1, "specs": 1, "bugs": 0, "user_stories": 0,
                    "platform_commits": 0, "app_commits": 3, "platform_prs": 0, "app_prs": 2,
                    "issues_opened": 0, "is_member": True, "company": "Constructor",
                    "name": "Alice", "emails": ["a@x.com"], "identity_confidence": "verified",
                    "identity_evidence": ["verified"], "repos": ["insight"],
                    "surviving_code_human": 800, "surviving_code_ai": 100,
                    "surviving_spec_human": 150, "surviving_spec_ai": 0,
                    "survwin_code_human": 80, "survwin_code_ai": 10,
                    "survwin_spec_human": 5, "survwin_spec_ai": 0},
                "bob": {"total_activity": 4, "commits": 9, "additions": 999, "deletions": 0,
                    "meaningful_additions": 900, "meaningful_deletions": 0, "prs_opened": 0,
                    "prs_merged": 0, "specs": 0, "bugs": 0, "user_stories": 0,
                    "platform_commits": 9, "app_commits": 0, "platform_prs": 0, "app_prs": 0,
                    "issues_opened": 0, "is_member": False, "company": "Other",
                    "name": "Bob", "emails": ["b@x.com"], "identity_confidence": "verified",
                    "identity_evidence": ["verified"], "repos": ["insight"],
                    "surviving_code_human": 50, "surviving_code_ai": 500,
                    "surviving_spec_human": 0, "surviving_spec_ai": 0,
                    "survwin_code_human": 5, "survwin_code_ai": 50,
                    "survwin_spec_human": 0, "survwin_spec_ai": 0},
            },
            "forkers": {}, "weekly": {},
        }
        model = build_model(data)
        # elements passthrough
        self.assertEqual(model["element_rows"][0]["element"], "Insight")
        self.assertEqual(model["element_rows"][0]["code_kloc"], 1.0)
        self.assertEqual(model["element_rows"][0]["spec_kloc"], 0.2)
        # repo inventory LOC + element
        row = next(r for r in model["repo_rows"] if r["name"] == "insight")
        self.assertEqual(row["element"], "Insight")
        self.assertEqual(row["code_loc"], 1000)
        self.assertEqual(row["spec_loc"], 200)
        # People table ranked on hand-written surviving code LOC: alice (800) > bob (50)
        self.assertEqual(model["table"][0]["login"], "alice")
        self.assertEqual(model["table"][0]["surv_code_human"], 800)
        self.assertEqual(model["table"][0]["surv_code_ai"], 100)
        self.assertEqual(model["table"][0]["surv_spec"], 150)
        self.assertEqual(model["table"][0]["surv_win_code"], 90)   # 80 + 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.RenderModelTest.test_build_model_exposes_elements_repo_loc_and_surviving_rank -v`
Expected: FAIL — `KeyError: 'element_rows'` (or `surv_code_human`).

- [ ] **Step 3: Build `element_rows` in `build_model`**

In `render.py`, just before the `return {` (line 395), add:

```python
    # ---- per-element rollup ----------------------------------------------
    element_rows = sorted(
        (
            {
                **e,
                "code_kloc": round((e.get("code_loc") or 0) / 1000, 1),
                "spec_kloc": round((e.get("spec_loc") or 0) / 1000, 1),
                "people": e.get("people_members", 0) + e.get("people_external", 0),
            }
            for e in (d.get("elements", {}) or {}).values()
        ),
        key=lambda x: -(x.get("code_loc") or 0),
    )
```

- [ ] **Step 4: Add LOC + element columns to `repo_rows`**

In the `repo_rows` builder dict (lines 366-378), add after `"contributors": ...,` (line 374):

```python
                "element": r.get("element", "Other"),
                "code_loc": r.get("code_loc"),
                "spec_loc": r.get("spec_loc"),
                "total_loc": r.get("total_loc"),
```

- [ ] **Step 5: Re-rank the People `table` on hand-written surviving code LOC**

In the `table` builder (lines 280-305), add these keys to the per-row dict (after `"cpt_lines": p.get("cpt_lines", 0),`, line 296):

```python
                "surv_code_human": p.get("surviving_code_human", 0),
                "surv_code_ai": p.get("surviving_code_ai", 0),
                "surv_spec": p.get("surviving_spec_human", 0) + p.get("surviving_spec_ai", 0),
                "surv_win_code": p.get("survwin_code_human", 0) + p.get("survwin_code_ai", 0),
```

And change the `key=` of the `table` sort (line 304) from:

```python
        key=lambda x: -(x["commits"] + x["prs"] + x["specs"] + x["bugs"] + x["stories"]),
```
to:
```python
        key=lambda x: (-x["surv_code_human"], -x["surv_code_ai"],
                       -(x["commits"] + x["prs"] + x["specs"] + x["bugs"] + x["stories"])),
```

- [ ] **Step 6: Add `element_rows` to the returned model**

In the `return {` dict (lines 395-436), add after `"repo_rows": repo_rows,` (line 412):

```python
        "element_rows": element_rows,
```

- [ ] **Step 7: Run the model test + full existing RenderModelTest**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.RenderModelTest -v`
Expected: all PASS (existing tests still pass — they use `.get` defaults; surviving fields default to 0).

- [ ] **Step 8: Commit**

```bash
cd "/path/to/insight"
git add render.py tests/test_rules.py
printf '%s\n' "feat: element rollup, repo LOC, surviving-LOC people rank in build_model" > /tmp/commit-msg.txt
git commit -F /tmp/commit-msg.txt
```

---

## Task 5: `render.py` TEMPLATE — Elements tab/section, inventory columns, people columns

**Files:**
- Modify: `render.py` `TEMPLATE` (tab nav line 549-554, JS `modes`/`hashMode` lines 1024-1041, repo inventory table 614-628, people table 984-1000) + a new Elements section.
- Test: `tests/test_rules.py` (assert the rendered HTML contains the new section)

- [ ] **Step 1: Write the failing test**

Add to `RenderModelTest` (reuses the data builder from Task 4 — duplicate the `data` dict inline to keep the test self-contained, or factor a helper; inline is fine):

```python
    def test_template_renders_elements_section_and_loc_columns(self):
        data = {
            "generated_at": "2026-06-22T00:00:00Z",
            "members": ["alice"],
            "elements": {"Insight": {"element": "Insight", "code_loc": 1000, "spec_loc": 200,
                "repos": 1, "commits_window": 10, "ai_commits_window": 4,
                "prs_opened_window": 7, "prs_merged_window": 4, "people_members": 1,
                "people_external": 0, "ai_pct": 40.0, "median_ttm_h": 20.0}},
            "repos": {"org/insight": {"name": "insight", "classification": "app",
                "element": "Insight", "unclassified": False, "forks": 1, "stars": 1,
                "archived": False, "traffic_access": True, "clones_14d": 5,
                "unique_cloners_14d": 2, "contributor_emails": 1,
                "code_loc": 1000, "spec_loc": 200, "total_loc": 1200}},
            "people": {"alice": {"total_activity": 3, "commits": 3, "additions": 50,
                "deletions": 0, "meaningful_additions": 40, "meaningful_deletions": 0,
                "prs_opened": 0, "prs_merged": 0, "specs": 1, "bugs": 0, "user_stories": 0,
                "platform_commits": 0, "app_commits": 3, "platform_prs": 0, "app_prs": 0,
                "issues_opened": 0, "is_member": True, "company": "Constructor",
                "name": "Alice", "emails": ["a@x.com"], "identity_confidence": "verified",
                "identity_evidence": ["verified"], "repos": ["insight"],
                "surviving_code_human": 800, "surviving_code_ai": 100,
                "surviving_spec_human": 150, "surviving_spec_ai": 0,
                "survwin_code_human": 80, "survwin_code_ai": 10,
                "survwin_spec_human": 5, "survwin_spec_ai": 0}},
            "forkers": {}, "weekly": {},
        }
        model = build_model(data)
        html = Environment(autoescape=True).from_string(TEMPLATE).render(**model)
        self.assertIn('data-mode="elements"', html)        # tab button
        self.assertIn('id="elements"', html)               # section anchor
        self.assertIn("Insight", html)                     # element row
        self.assertIn("Code LOC", html)                    # inventory column header
        self.assertIn("Hand-written", html)                # people column header
```

Note: `gh(login)` is a Jinja `{% macro %}` defined inside `TEMPLATE` (around line 540), not a kwarg or env global. So rendering needs only `**model` — exactly like the existing `test_report_links_person_logins_to_github_profiles` test. Do NOT pass a `gh=` kwarg.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.RenderModelTest.test_template_renders_elements_section_and_loc_columns -v`
Expected: FAIL — `data-mode="elements"` not found.

- [ ] **Step 3: Add the Elements tab button**

In the `<nav class="tabs">` (lines 549-554), add a button after the Repos button (line 550):

```html
  <button type="button" data-mode="elements">Elements</button>
```

- [ ] **Step 4: Register `elements` in the tab JS**

In the script (line 1024) change:
```javascript
  var modes = ['overview', 'repos', 'usage', 'people', 'fabric', 'all'];
```
to:
```javascript
  var modes = ['overview', 'repos', 'elements', 'usage', 'people', 'fabric', 'all'];
```
And in `hashMode` (lines 1025-1041) add an entry:
```javascript
    'elements': 'elements',
```

- [ ] **Step 5: Add the Elements section**

Insert a new section immediately after the Repo-coverage section's closing `</div></div>` (after line 632, before the `<div class="mode-section" data-modes="overview all">` at line 634):

```html
<div class="mode-section" data-modes="elements all">
<h2 id="elements">By Element</h2>
<div class="card">
  <p class="hint">Product-line rollup. <b>Code/Spec KLOC</b> = surviving lines in
    today's tree (git blame), specs counted separately from code; computable for
    cloned primary-org repos only. PRs, commits and people are windowed
    (<code>lookback_days</code>). AI% = share of windowed commits carrying an AI-tool marker.</p>
  <div style="overflow-x:auto">
  <table>
    <tr><th>Element</th><th>Code KLOC</th><th>Spec KLOC</th><th>Repos</th>
      <th data-tip="distinct contributors (members) with commits in this element">People</th>
      <th>Commits</th><th>PRs (open / merged)</th>
      <th data-tip="median time-to-merge of PRs in this element">Med TTM</th><th>AI%</th></tr>
    {% for e in element_rows %}
    <tr>
      <td>{{e.element}}</td>
      <td>{{e.code_kloc}}</td>
      <td>{{e.spec_kloc}}</td>
      <td>{{e.repos}}</td>
      <td>{{e.people_members}}{% if e.people_external %} <span class="tag ext">+{{e.people_external}} ext</span>{% endif %}</td>
      <td>{{e.commits_window}}</td>
      <td>{{e.prs_opened_window}} / {{e.prs_merged_window}}</td>
      <td>{% if e.median_ttm_h is not none %}{{e.median_ttm_h}}h{% else %}—{% endif %}</td>
      <td>{{e.ai_pct}}%</td>
    </tr>
    {% endfor %}
  </table>
  </div>
  <p class="conc">Element mapping lives in <code>config.yaml</code> under <code>elements:</code>.
    Old-org (<code>your-old-org</code>) repos contribute PR/people metadata to their
    element but no LOC (not cloned).</p>
</div>
</div>
```

- [ ] **Step 6: Add Element + LOC columns to the repo inventory table**

In the inventory table header (line 614) change:
```html
      <tr><th>Repo</th><th>Org</th><th>Class</th><th>Contributors</th><th>Forks</th><th>Stars</th><th>Traffic</th><th>Clones</th><th>Unique cloners</th></tr>
```
to:
```html
      <tr><th>Repo</th><th>Org</th><th>Class</th><th>Element</th><th data-tip="surviving code lines in today's tree (blame); — = not cloned">Code LOC</th><th data-tip="surviving spec lines in today's tree (blame)">Spec LOC</th><th>Contributors</th><th>Forks</th><th>Stars</th><th>Traffic</th><th>Clones</th><th>Unique cloners</th></tr>
```
And in the row body (lines 616-626) add the three cells after the `<td>{{r.classification}}</td>` line (line 619):
```html
        <td>{{r.element}}</td>
        <td>{% if r.code_loc is not none %}{{ "{:,}".format(r.code_loc) }}{% else %}—{% endif %}</td>
        <td>{% if r.spec_loc is not none %}{{ "{:,}".format(r.spec_loc) }}{% else %}—{% endif %}</td>
```

- [ ] **Step 7: Add surviving-LOC columns to the People table (headline)**

In the People table header (line 984), insert new `<th>`s right after the `<th>Co.</th>` cell and before `<th>Commits</th>`:

```html
<th data-tip="hand-written surviving code lines in today's tree (git blame, exact). Headline contribution metric — final code a person authored that still exists, regeneration-proof.">Hand-written <span class="prec exact">exact</span></th><th data-tip="surviving code lines authored that are AI/Studio-generated (@cpt / studio markers). Heuristic.">AI-gen <span class="prec heuristic">heuristic</span></th><th data-tip="surviving spec (markdown) lines authored in today's tree">Spec LOC</th><th data-tip="surviving code lines whose last commit is inside the window">Δ window</th>
```

And in the row body (line 996) insert matching `<td>`s right after `<td>{{r.company}}</td>` (line 995) and before the `<td>{{r.commits}}</td>` cell:

```html
    <td>{{ "{:,}".format(r.surv_code_human) }}</td><td>{{ "{:,}".format(r.surv_code_ai) }}</td><td>{{ "{:,}".format(r.surv_spec) }}</td><td>{{ "{:,}".format(r.surv_win_code) }}</td>
```

Demotion is implicit: Commits / LOC+ remain but now sit to the right of the new headline columns.

- [ ] **Step 8: Run the template test + full RenderModelTest**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.RenderModelTest -v`
Expected: all PASS.

- [ ] **Step 9: Render against the existing `data.json` to confirm no template break**

The committed `data.json` predates the new fields; `build_model`/template use `.get`/`is not none`, so it must still render (Elements section will be empty, LOC columns show `—`).

Run: `cd "/path/to/insight" && python render.py && python -c "import pathlib; h=pathlib.Path('report.html').read_text(); print('elements tab:', 'data-mode=\"elements\"' in h); print('bytes:', len(h))"`
Expected: prints `elements tab: True` and a byte count; no exception.

- [ ] **Step 10: Commit**

```bash
cd "/path/to/insight"
git add render.py tests/test_rules.py
printf '%s\n' "feat: Elements tab/section, repo LOC + element columns, surviving-LOC people columns" > /tmp/commit-msg.txt
git commit -F /tmp/commit-msg.txt
```

---

## Task 6: GitHub API rate-limit detection & reporting

Make rate-limit exhaustion explicit: a bounded wait, a sticky flag, graceful partial data (no crash, no silent empty), and a clear signal in `data.json` + the report trust surface.

**Files:**
- Modify: `ghclient.py` — `GH.__init__` (line 39), `_throttled` (lines 78-100), `paginate` (lines 128-153), `graphql` (lines 155-171); add `RateLimitError` is **not** needed (flag-based, no exceptions).
- Modify: `config.yaml` — add `api_max_wait_seconds`.
- Modify: `collect.py` — construct `GH` with the config wait; add `api` block to `out`; print warning.
- Modify: `render.py` — `build_model` data_quality (lines 340-354) + return; `TEMPLATE` Overview trust banner (after line 575).
- Test: `tests/test_rules.py` — new `RateLimitTest` (ghclient) + a render assertion.

- [ ] **Step 1: Write the failing ghclient tests**

Add a new test class to `tests/test_rules.py` (add `import time` is unnecessary; use `unittest.mock.patch`). Add `from ghclient import GH` near the other imports (line 16 area):

```python
class RateLimitTest(unittest.TestCase):
    class FakeResp:
        def __init__(self, status, headers=None, body=None):
            self.status_code = status
            self.headers = headers or {}
            self._body = body or {}
        def json(self):
            return self._body

    def _client(self):
        with patch("ghclient.requests.Session"):
            return GH("tok", cache_ttl_hours=0, max_wait_seconds=90)

    def test_non_rate_403_is_not_throttled(self):
        gh = self._client()
        r = self.FakeResp(403, {"X-RateLimit-Remaining": "57"},
                          {"message": "Must have push access to repository"})
        self.assertFalse(gh._throttled(r))
        self.assertFalse(gh.rate_limited)

    def test_primary_limit_near_reset_sleeps_and_retries(self):
        gh = self._client()
        import time as _t
        reset = int(_t.time()) + 5
        r = self.FakeResp(429, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)})
        with patch("ghclient.time.sleep") as slept:
            self.assertTrue(gh._throttled(r))      # retry
            self.assertTrue(slept.called)
        self.assertFalse(gh.rate_limited)          # recoverable
        self.assertEqual(len(gh.rate_events), 1)

    def test_primary_limit_far_reset_gives_up_and_flags(self):
        gh = self._client()
        import time as _t
        reset = int(_t.time()) + 3600          # 1h away, beyond max_wait
        r = self.FakeResp(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)})
        with patch("ghclient.time.sleep") as slept:
            self.assertFalse(gh._throttled(r))     # do NOT retry
            self.assertFalse(slept.called)         # no futile multi-minute sleep
        self.assertTrue(gh.rate_limited)
        self.assertEqual(gh.rate_reset_epoch, reset)

    def test_secondary_limit_backs_off_and_retries(self):
        gh = self._client()
        r = self.FakeResp(403, {"Retry-After": "3"}, {"message": "You have exceeded a secondary rate limit"})
        with patch("ghclient.time.sleep") as slept:
            self.assertTrue(gh._throttled(r))
            slept.assert_called_once_with(3)
        self.assertFalse(gh.rate_limited)
        self.assertEqual(len(gh.rate_events), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.RateLimitTest -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'max_wait_seconds'` (and missing `rate_limited`/`rate_events`/`rate_reset_epoch`).

- [ ] **Step 3: Extend `GH.__init__` with rate-limit state**

In `ghclient.py` `__init__` (line 39), change the signature and add state. Current first line:

```python
    def __init__(self, tok: str, cache_ttl_hours: float = 24.0):
```
becomes:
```python
    def __init__(self, tok: str, cache_ttl_hours: float = 24.0, max_wait_seconds: float = 90.0):
```
And add at the end of `__init__` body:
```python
        self.max_wait_seconds = float(max_wait_seconds)
        self.rate_limited = False           # sticky: a primary limit we couldn't wait out
        self.rate_reset_epoch: int | None = None
        self.rate_events: list[dict] = []   # [{type: primary|secondary, reset?: epoch}]
```

- [ ] **Step 4: Rewrite `_throttled` for bounded wait + flag**

Replace `_throttled` (lines 78-100) entirely with:

```python
    def _throttled(self, r: requests.Response) -> bool:
        """True = handled, retry the request. False = not a rate limit (or we
        gave up). Sets sticky self.rate_limited when a primary quota is exhausted
        and its reset is beyond max_wait_seconds."""
        if r.status_code not in (403, 429):
            return False
        # primary rate limit: quota exhausted
        if r.headers.get("X-RateLimit-Remaining") == "0":
            reset = int(r.headers.get("X-RateLimit-Reset", "0"))
            self.rate_events.append({"type": "primary", "reset": reset})
            self.rate_reset_epoch = reset
            need = reset - int(time.time()) + 2
            if 0 < need <= self.max_wait_seconds:
                print(f"  rate-limited (primary), sleeping {need}s", file=sys.stderr)
                time.sleep(need)
                return True
            # reset too far to wait out -> give up cleanly, flag partial
            self.rate_limited = True
            print(f"  rate limit exhausted; reset in ~{max(need,0)}s — stopping, data will be PARTIAL",
                  file=sys.stderr)
            return False
        # secondary rate limit / abuse detection -> short backoff
        retry_after = r.headers.get("Retry-After")
        msg = ""
        try:
            msg = (r.json() or {}).get("message", "").lower()
        except ValueError:
            pass
        if retry_after or "secondary rate limit" in msg or "abuse" in msg:
            self.rate_events.append({"type": "secondary"})
            time.sleep(int(retry_after) if (retry_after or "").isdigit() else 5)
            return True
        # any other 403 (e.g. "Must have push access" on traffic) is NOT a rate
        # limit — do not retry, let the caller handle the status.
        return False
```

- [ ] **Step 5: Make `paginate` degrade gracefully**

In `paginate` (lines 138-152) replace the loop body's error handling. Current:

```python
        while url and len(out) < cap:
            r = self.get(url, params=params)
            if r.status_code == 404:
                break
            r.raise_for_status()
            batch = r.json()
            ...
            url = r.links.get("next", {}).get("url")
            params = None
        self._cwrite(key, out)
        return out
```
becomes:
```python
        partial = False
        while url and len(out) < cap:
            r = self.get(url, params=params)
            if r.status_code == 404:
                break
            if r.status_code in (403, 429) and self.rate_limited:
                partial = True
                break
            r.raise_for_status()
            batch = r.json()
            if isinstance(batch, dict) and "items" in batch:
                out.extend(batch["items"])
                if len(batch["items"]) < per_page:
                    break
            else:
                out.extend(batch)
            url = r.links.get("next", {}).get("url")
            params = None
        if not partial:
            self._cwrite(key, out)   # never cache a rate-limited partial
        return out
```

- [ ] **Step 6: Make `graphql` degrade gracefully**

In `graphql` (lines 160-170) replace the retry loop body. Current:

```python
        for _ in range(6):
            r = self.s.post(GRAPHQL, json={"query": query, "variables": variables})
            if self._throttled(r):
                continue
            r.raise_for_status()
            data = r.json()
            ...
            self._cwrite(gkey, result)
            return result
        return {}
```
becomes:
```python
        for _ in range(6):
            r = self.s.post(GRAPHQL, json={"query": query, "variables": variables})
            if self._throttled(r):
                continue
            if r.status_code in (403, 429) and self.rate_limited:
                return {}            # partial; flag already set, not cached
            r.raise_for_status()
            data = r.json()
            if "errors" in data:
                print(f"  graphql warn: {data['errors'][0].get('message')}", file=sys.stderr)
            result = data.get("data", {})
            self._cwrite(gkey, result)
            return result
        return {}
```

- [ ] **Step 7: Run ghclient tests to verify they pass**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.RateLimitTest -v`
Expected: PASS (4 tests).

- [ ] **Step 8: Add `api_max_wait_seconds` to `config.yaml`**

After the `cache_ttl_hours:` entry (line 272) add:

```yaml
# Max seconds to wait out a PRIMARY GitHub rate-limit reset before giving up and
# marking the run PARTIAL (rather than hanging or crashing). Secondary-limit
# backoffs are always short and independent of this.
api_max_wait_seconds: 90
```

- [ ] **Step 9: Construct `GH` with the config wait + write the `api` block in `collect.py`**

In `main()` where `GH` is built (line 433):
```python
    gh = GH(tok, cache_ttl_hours=float(cfg.get("cache_ttl_hours", 24)))
```
becomes:
```python
    gh = GH(tok, cache_ttl_hours=float(cfg.get("cache_ttl_hours", 24)),
            max_wait_seconds=float(cfg.get("api_max_wait_seconds", 90)))
```

Then move the rate-limit query above the `out = {` dict so it can be embedded. Just before `out = {` (line 807, after `elements_rollup = build_elements_rollup(...)` from Task 3), add:

```python
    _rl = gh.get("/rate_limit").json().get("resources", {})
    _core, _search = _rl.get("core", {}), _rl.get("search", {})
    api_status = {
        "rate_limited": gh.rate_limited,
        "rate_events": len(gh.rate_events),
        "reset": (datetime.fromtimestamp(gh.rate_reset_epoch, tz=timezone.utc)
                  .strftime("%Y-%m-%dT%H:%M:%SZ") if gh.rate_reset_epoch else None),
        "core_remaining": _core.get("remaining"),
        "core_limit": _core.get("limit"),
        "search_remaining": _search.get("remaining"),
    }
```

Add `"api": api_status,` into the `out = {...}` dict (e.g. right after `"identity": {...},`).

Then in the end-of-run block (lines 825-834), replace the duplicate `/rate_limit` fetch with the already-fetched `_core`/`_search` and add the partial warning. Replace:

```python
    rl = gh.get("/rate_limit").json().get("resources", {})
    core, search = rl.get("core", {}), rl.get("search", {})
    print(
        f"Wrote data.json: {len(people)} people, {len(repos)} repos, {len(forkers)} forkers.\n"
        f"Identity: {out['identity']}\n"
        + (f"Unresolved humans -> identity_suggestions.yaml ({len(real_sug)})\n" if real_sug else "")
        + f"API budget left: core {core.get('remaining','?')}/{core.get('limit','?')}, "
        f"search {search.get('remaining','?')}/{search.get('limit','?')}",
        file=sys.stderr,
    )
```
with:
```python
    if gh.rate_limited:
        print(f"WARNING: GitHub API rate limit hit during collection — data.json is PARTIAL"
              + (f"; resets at {api_status['reset']}." if api_status["reset"] else "."),
              file=sys.stderr)
    print(
        f"Wrote data.json: {len(people)} people, {len(repos)} repos, {len(forkers)} forkers.\n"
        f"Identity: {out['identity']}\n"
        + (f"Unresolved humans -> identity_suggestions.yaml ({len(real_sug)})\n" if real_sug else "")
        + f"API budget left: core {_core.get('remaining','?')}/{_core.get('limit','?')}, "
        f"search {_search.get('remaining','?')}/{_search.get('limit','?')}"
        + (f"  [{len(gh.rate_events)} throttle events]" if gh.rate_events else ""),
        file=sys.stderr,
    )
```

- [ ] **Step 10: Write the failing render test for the banner**

Add to `RenderModelTest` in `tests/test_rules.py`:

```python
    def test_build_model_flags_api_rate_limit_partial(self):
        base = {
            "generated_at": "2026-06-22T00:00:00Z", "members": [], "repos": {},
            "people": {}, "forkers": {}, "weekly": {},
        }
        clean = build_model(base)
        self.assertFalse(clean["data_quality"]["api_rate_limited"])
        limited = build_model({**base, "api": {"rate_limited": True,
                                               "reset": "2026-06-22T01:00:00Z"}})
        self.assertTrue(limited["data_quality"]["api_rate_limited"])
        self.assertEqual(limited["data_quality"]["api_reset"], "2026-06-22T01:00:00Z")
        self.assertGreaterEqual(limited["data_quality"]["risk_count"], 1)
        html = Environment(autoescape=True).from_string(TEMPLATE).render(**limited)
        self.assertIn("API rate limit", html)
```

- [ ] **Step 11: Run it to verify it fails**

Run: `cd "/path/to/insight" && python -m unittest tests.test_rules.RenderModelTest.test_build_model_flags_api_rate_limit_partial -v`
Expected: FAIL — `KeyError: 'api_rate_limited'`.

- [ ] **Step 12: Add the flag to `build_model` data_quality**

In `render.py` `build_model`, the `data_quality` dict (lines 340-354). Add before the dict:

```python
    api = d.get("api", {}) or {}
```
Add two keys inside the `data_quality = {` dict:
```python
        "api_rate_limited": bool(api.get("rate_limited")),
        "api_reset": api.get("reset"),
```
And add the rate-limit to `risk_count` — change the `risk_count` expression to include:
```python
        "risk_count": (
            (1 if identity.get("unresolved_human", 0) else 0)
            + (1 if unclassified else 0)
            + (1 if no_traffic else 0)
            + (1 if api.get("rate_limited") else 0)
        ),
```

- [ ] **Step 13: Add the banner to the Overview trust surface**

In `TEMPLATE`, immediately after the `</nav>` (line 555) and before `<div class="mode-section" data-modes="overview all">` (line 557), insert a banner shown in every mode:

```html
{% if data_quality.api_rate_limited %}
<div class="mode-section" data-modes="overview repos elements usage people fabric all">
  <div class="card" style="border-color:var(--bad);background:#fff5f5">
    <p style="margin:0;color:var(--bad);font-weight:600">⚠ GitHub API rate limit hit during collection — this report is PARTIAL.{% if data_quality.api_reset %} Quota resets at {{data_quality.api_reset}}.{% endif %} Re-run collection after the reset for complete data.</p>
  </div>
</div>
{% endif %}
```

- [ ] **Step 14: Run the render test + full suite**

Run:
```bash
cd "/path/to/insight"
python -m unittest tests.test_rules.RenderModelTest.test_build_model_flags_api_rate_limit_partial -v
python -m unittest discover -s tests -v
python -m py_compile ghclient.py collect.py render.py
```
Expected: target test PASS; full suite PASS; py_compile exit 0.

- [ ] **Step 15: Commit**

```bash
cd "/path/to/insight"
git add ghclient.py collect.py render.py config.yaml tests/test_rules.py
printf '%s\n' "feat: detect GitHub API rate-limit exhaustion and surface PARTIAL-data state" > /tmp/commit-msg.txt
git commit -F /tmp/commit-msg.txt
```

---

## Task 7: Docs + full verification (and optional live collect)

**Files:**
- Modify: `README.md` (Metrics & panels + Tuning sections)

- [ ] **Step 1: Update README**

In `README.md`, under "## Metrics & panels", add a bullet after the **Code / LOC** bullet (line ~52):

```markdown
- **Surviving LOC (contribution headline)** — `git blame` over each cloned
  repo's current tree attributes every still-existing line to its last author,
  split **code vs spec** and **hand-written vs AI-generated** (`@cpt` regions,
  `studio: true` specs, `Generated by cf/cfs` stamps). This is the primary
  per-person ranking: it measures the final code a person authored that still
  exists, so regenerating the same lines doesn't inflate it and commit count
  doesn't distort it. Commits and raw windowed additions are kept as secondary
  columns. A windowed "Δ window" column shows surviving lines whose last commit
  is inside `lookback_days`. Only computable for cloned primary-org repos.
- **By Element** — every repo maps to a product element (`config.elements`);
  the Elements tab rolls up Code/Spec KLOC, people, repos, windowed commits,
  PRs (opened/merged), median time-to-merge, and AI% per element.
- **Repo size** — the repo inventory shows each repo's surviving Code LOC and
  Spec LOC (blame totals; "—" for non-cloned old-org repos).
```

Under "## Tuning", add a bullet:

```markdown
- **Elements**: `elements:` maps repo name → product element (exact names, then
  trailing-`*` prefix globs, else `default`). Orthogonal to platform/app.
- **Blame cache**: surviving-LOC blame is cached per repo under
  `.cache/blame/<repo>.json`, keyed by file blob SHA; unchanged files are not
  re-blamed. Delete `.cache/blame/` to force a full re-blame.
- **API rate limit**: `api_max_wait_seconds` (default 90) caps how long a run
  waits out a primary GitHub limit before giving up. If the limit is hit, the
  run finishes with **partial** data, `data.json` carries an `api` block
  (`rate_limited`, `reset`, remaining budget), and the report shows a red
  "PARTIAL" banner. Re-run after the reset for complete data.
```

- [ ] **Step 2: Run the entire test suite + compile-all**

Run:
```bash
cd "/path/to/insight"
python -m unittest discover -s tests -v
python -m py_compile collect.py render.py ghclient.py identity.py directory.py email_report.py reportctl.py server.py tests/test_rules.py
```
Expected: all tests PASS; py_compile silent (exit 0).

- [ ] **Step 3: (Optional, network) Live collect + render smoke test**

Only if a GitHub token is available and a real refresh is wanted (slow — first blame pass is minutes):
```bash
cd "/path/to/insight"
export GH_TOKEN="$(gh auth token)"
python collect.py && python render.py
python -c "
import json
d=json.load(open('data.json'))
print('elements:', list(d.get('elements',{})))
ins=d.get('elements',{}).get('Insight'); print('Insight:', ins)
r=[x for x in d['repos'].values() if x['name']=='insight']
print('insight size:', r[0].get('code_loc'), r[0].get('spec_loc'))
print('top person surv_code_human:', max((p.get('surviving_code_human',0) for p in d['people'].values()), default=0))
"
```
Expected: `elements` populated; Insight rollup printed with non-zero KLOC; `insight` repo has code/spec LOC; some person has non-zero surviving code. This answers the stakeholder's original questions (KLOC/people/PRs per element). If skipped, note that the report is verified structurally against the stale `data.json` only.

- [ ] **Step 4: Commit**

```bash
cd "/path/to/insight"
git add README.md data.json report.html 2>/dev/null; git add README.md
printf '%s\n' "docs: document surviving-LOC metric, by-Element rollup, repo size + blame cache" > /tmp/commit-msg.txt
git commit -F /tmp/commit-msg.txt
```

(Note: `data.json`/`report.html` are gitignored, so only `README.md` is committed; the `git add` of those is a harmless no-op kept for clarity.)

---

## Self-review

**Spec coverage:**
- "By Element" grouping → Task 1 (config + classifier), Task 3 (rollup in data.json), Task 5 (Elements tab/section). ✓
- KLOC/people/PRs/other per element → Task 3 `build_elements_rollup` (code_loc, spec_loc, people_members/external, prs_opened/merged_window, commits_window, ai_pct, median_ttm_h) + Task 5 render. ✓
- Repo size in LOC in inventory → Task 2 (`blame_tree` sizes), Task 3 (meta.code_loc/spec_loc/total_loc), Task 4/5 (repo_rows + columns). ✓
- Contribution = surviving LOC, all-time snapshot primary + windowed → Task 2 (`blame_tree` all-time + win_*), Task 3 (fold into people), Task 4 (re-rank on `surv_code_human`), Task 5 (headline columns + Δ window). ✓
- Spec vs code separate → spec/code kind throughout (Task 2 spec-precedence; columns split). ✓
- Hand-written vs AI-generated split, rank on hand-written → Task 2 origin split, Task 4 sort key `-surv_code_human`, Task 5 separate columns + precision badges. ✓
- Regeneration not inflating → surviving-blame counts each line once (last author); `meaningful_loc` filter excludes generated/vendored paths (reused in `blame_tree`). ✓
- cloned-org-only scope, old-org repos show "—" → `code_loc=None` default on meta, rollup skips None, template `is not none`. ✓
- Rate-limit detection & reporting (spec "Rate-limit detection & reporting") → Task 6: bounded wait + sticky flag in `_throttled`, graceful partial in `paginate`/`graphql`, `api` block in `data.json`, stderr warning, red PARTIAL banner + `risk_count` in the report; `api_max_wait_seconds` config. ✓
- Tests → Tasks 1-6 each add tests; Task 7 runs the full suite. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output. ✓

**Type consistency:** `make_element`→`element_of` (closure) used in Task 3 inventory loop; `blame_tree(dest, ref, is_spec, is_meaningful_loc, since_date, cache)` signature identical in Task 2 def, Task 2 tests, and Task 3 call; per-person keys (`surviving_code_human`, `surviving_code_ai`, `surviving_spec_human`, `surviving_spec_ai`, `survwin_code_human`, `survwin_code_ai`, `survwin_spec_human`, `survwin_spec_ai`) identical in `blank_person` (Task 3 step 5), `blame_tree` slot (Task 2), and `build_model` table (Task 4 step 5); repo meta keys (`element`, `code_loc`, `spec_loc`, `total_loc`, `commits_window`, `ai_commits_window`, `prs_opened_window`, `prs_merged_window`) identical across Task 3 inventory defaults, the git-pass/PR-loop writes, and `build_elements_rollup` reads; model keys (`element_rows`, `code_kloc`, `spec_kloc`, `surv_code_human`, `surv_code_ai`, `surv_spec`, `surv_win_code`) identical between Task 4 build and Task 5 template; GH rate-limit state (`rate_limited`, `rate_reset_epoch`, `rate_events`, `max_wait_seconds`) identical between `__init__` (Task 6 step 3), `_throttled` (step 4), the `api_status` block (step 9), and the tests (step 1); `data_quality.api_rate_limited`/`api_reset` identical between `build_model` (step 12) and the banner (step 13). ✓

One known caveat documented in the spec, not a bug: the blame cache is keyed by `path\0blob_sha`; a history rewrite that leaves a blob byte-identical but changes its ancestry would serve a stale blame. Acceptable for a weekly batch; delete `.cache/blame/` to force re-blame.
