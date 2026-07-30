import http.client
import json
import os
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from jinja2 import Environment

import directory
from collect import add_forker, build_elements_rollup, classify, make_element, make_is_meaningful_loc, make_is_spec, parse_git
from ghclient import GH
from identity import build_identity, discard_invalid_clone, git_cmd, git_error, is_blobless_clone, redact_token
from render import build_model
import server
from server import snapshot_state, write_people_yaml


class QuietHandler(server.Handler):
    def log_message(self, fmt: str, *args) -> None:
        pass


class CollectRulesTest(unittest.TestCase):
    def test_classify_unknown_repos_as_unclassified(self):
        cfg = {"repos": {"platform": ["core"], "app": ["product"], "ignore": ["meta"]}}

        self.assertEqual(classify("core", cfg), "platform")
        self.assertEqual(classify("product", cfg), "app")
        self.assertEqual(classify("meta", cfg), "ignore")
        self.assertEqual(classify("new-repo", cfg), "unclassified")

    def test_configurable_repo_types(self):
        import collect
        # default set when config predates configurable types
        self.assertEqual([t["id"] for t in collect.repo_types({})], ["platform", "app"])
        self.assertEqual(collect.default_repo_type({}), "app")
        # an arbitrary custom type classifies from its repo list
        cfg = {"repo_types": [{"id": "platform", "name": "P"},
                              {"id": "app", "name": "A", "default": True},
                              {"id": "sdk", "name": "SDK"}],
               "repos": {"platform": ["core"], "sdk": ["client-lib"], "ignore": ["meta"]}}
        self.assertEqual(collect.default_repo_type(cfg), "app")
        self.assertEqual(classify("client-lib", cfg), "sdk")
        self.assertEqual(classify("core", cfg), "platform")
        self.assertEqual(classify("unlisted", cfg), "unclassified")   # -> default (app)

    def test_repo_types_persist_round_trip(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        import store, configstore, collect
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                store.connect().close()
                configstore.save_overlay({
                    "repo_types": [{"id": "platform", "name": "Platform", "color": "#5b5bf0"},
                                   {"id": "app", "name": "App", "color": "#8b5cf6", "default": True},
                                   {"id": "sdk", "name": "SDK", "color": "#f59e0b"}],
                    "repo_class": {"o/lib": "sdk"}})
                ov = configstore.load_overlay()
                self.assertEqual([t["id"] for t in ov["repo_types"]], ["platform", "app", "sdk"])
                self.assertEqual(ov["repo_class"], {"o/lib": "sdk"})
                cfg = configstore.apply_overlay(configstore.base_config(), ov)
                self.assertEqual(collect.classify("o/lib", cfg), "sdk")
                self.assertEqual(collect.default_repo_type(cfg), "app")

    def test_apply_overlay_custom_type(self):
        import configstore
        base = {"repo_types": [{"id": "platform", "name": "P"},
                               {"id": "app", "name": "A", "default": True},
                               {"id": "sdk", "name": "SDK"}], "repos": {}}
        ov = {"repo_class": {"x": "sdk", "y": "platform", "z": "app", "i": "ignore"}}
        cfg = configstore.apply_overlay(base, ov)
        self.assertEqual(classify("x", cfg), "sdk")
        self.assertEqual(classify("y", cfg), "platform")
        self.assertEqual(classify("z", cfg), "unclassified")   # default type stays unlisted
        self.assertEqual(classify("i", cfg), "ignore")

    def test_spec_detection_uses_broad_markdown_with_denylist(self):
        is_spec = make_is_spec({
            "exclude_segments": [".github", "fixtures"],
            "exclude_basenames": ["AGENTS.md", "LICENSE.md"],
            "exclude_name_prefixes": ["hai3-"],
        })

        self.assertTrue(is_spec("README.md"))
        self.assertTrue(is_spec("docs/PRD.md"))
        self.assertFalse(is_spec(".github/PULL_REQUEST_TEMPLATE.md"))
        self.assertFalse(is_spec("tests/fixtures/story.md"))
        self.assertFalse(is_spec("AGENTS.md"))
        self.assertFalse(is_spec("docs/hai3-generated.md"))
        self.assertFalse(is_spec("src/app.py"))

    def test_forkers_keep_org_context_and_deduplicate(self):
        forkers = {}

        add_forker(forkers, "alice", "your-org/example-app")
        add_forker(forkers, "alice", "your-old-org/example-app")
        add_forker(forkers, "alice", "your-org/example-app")

        self.assertEqual(
            forkers["alice"]["forked"],
            ["your-org/example-app", "your-old-org/example-app"],
        )

    def test_meaningful_loc_filter_excludes_generated_dependency_and_binary_noise(self):
        is_meaningful = make_is_meaningful_loc({
            "exclude_segments": ["node_modules", "generated", "fixtures"],
            "exclude_basenames": ["package-lock.json"],
            "exclude_name_prefixes": ["hai3-"],
            "exclude_suffixes": [".min.js", ".png"],
        })

        self.assertTrue(is_meaningful("src/service.py"))
        self.assertFalse(is_meaningful("node_modules/pkg/index.js"))
        self.assertFalse(is_meaningful("src/generated/schema.ts"))
        self.assertFalse(is_meaningful("tests/fixtures/data.json"))
        self.assertFalse(is_meaningful("package-lock.json"))
        self.assertFalse(is_meaningful("docs/hai3-output.md"))
        self.assertFalse(is_meaningful("assets/logo.png"))


    def test_make_element_maps_repos_to_product_elements(self):
        cfg = {"elements": {
            "Insight": ["insight", "example-web-front", "example-legacy-web"],
            "Studio": ["studio", "example-codegen", "example-studio"],
            "Core": ["example-core*", "example-crate", "example-legacy-*", "example-template"],
            "default": "Other",
        }}
        element_of = make_element(cfg)
        self.assertEqual(element_of("insight"), "Insight")
        self.assertEqual(element_of("example-web-front"), "Insight")
        self.assertEqual(element_of("example-legacy-web"), "Insight")          # old-org name
        self.assertEqual(element_of("example-core"), "Core")               # prefix glob
        self.assertEqual(element_of("example-core-web-docs"), "Core")      # prefix glob
        self.assertEqual(element_of("example-legacy-frontend"), "Core")         # old-org glob
        self.assertEqual(element_of("example-crate"), "Core")              # exact beats nothing
        self.assertEqual(element_of("example-codegen"), "Studio")
        self.assertEqual(element_of("totally-unknown"), "Other")          # default

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

    def test_make_element_exact_match_wins_over_glob(self):
        cfg = {"elements": {
            "Core": ["example-core*"],
            "Docs": ["example-webdocs"],
            "default": "Other",
        }}
        element_of = make_element(cfg)
        self.assertEqual(element_of("example-webdocs"), "Docs")   # exact wins
        self.assertEqual(element_of("example-core"), "Core")     # glob fallback


class IdentityRulesTest(unittest.TestCase):
    def test_identity_priority_and_suggestions(self):
        email_names = {
            "manual@example.com": {"Manual User"},
            "verified@example.com": {"Verified User"},
            "pr@example.com": {"PR User"},
            "bridge@example.com": {"Verified User"},
            "unknown@example.com": {"Unknown Human"},
        }
        verified = {"verified@example.com": "verified-login"}
        overrides = {"manual@example.com": "manual-login"}
        pr_pairs = {"pr@example.com": "pr-login"}

        email2login, reason, suggestions = build_identity(
            email_names, verified, overrides, [], pr_pairs
        )

        self.assertEqual(email2login["manual@example.com"], "manual-login")
        self.assertEqual(reason["manual@example.com"], "override")
        self.assertEqual(email2login["verified@example.com"], "verified-login")
        self.assertEqual(reason["verified@example.com"], "verified")
        self.assertEqual(email2login["pr@example.com"], "pr-login")
        self.assertEqual(reason["pr@example.com"], "pr-bridge")
        self.assertEqual(email2login["bridge@example.com"], "verified-login")
        self.assertEqual(reason["bridge@example.com"], "name-bridge")
        self.assertEqual(reason["unknown@example.com"], "unresolved")
        self.assertEqual(len(suggestions), 1)

    def test_git_error_redacts_token_from_clone_output(self):
        result = subprocess.CompletedProcess(
            args=[],
            returncode=128,
            stdout="",
            stderr=(
                "Cloning into '.repos/insight'...\n"
                "remote: Repository not found.\n"
                "fatal: Authentication failed for "
                "'https://x-access-token:secret-token@github.com/your-org/insight.git/'\n"
            ),
        )

        self.assertEqual(
            redact_token("https://x-access-token:secret-token@github.com/x/y.git", "secret-token"),
            "https://x-access-token:<redacted>@github.com/x/y.git",
        )
        self.assertEqual(
            git_error(result, "secret-token"),
            "fatal: Authentication failed for 'https://x-access-token:<redacted>@github.com/your-org/insight.git/'",
        )

    def test_discard_invalid_clone_removes_symlink_without_touching_target(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target.mkdir()
            target_file = target / "keep.txt"
            target_file.write_text("keep")
            link = root / "insight"
            os.symlink(target, link)

            self.assertTrue(discard_invalid_clone(str(link)))
            self.assertFalse(link.exists())
            self.assertEqual(target_file.read_text(), "keep")

    def test_blobless_clone_detection_uses_partialclonefilter_config(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            (repo / ".git").mkdir(parents=True)
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="blob:none\n",
                stderr="",
            )

            with patch("identity.subprocess.run", return_value=completed):
                self.assertTrue(is_blobless_clone(str(repo)))

    def test_git_cmd_scopes_safe_directory_to_clone_path(self):
        cmd = git_cmd("/work/.repos/insight", "log", "HEAD")

        self.assertEqual(cmd[:4], ["git", "-c", "safe.directory=/work/.repos/insight", "-C"])
        self.assertEqual(cmd[4:], ["/work/.repos/insight", "log", "HEAD"])


class GitParsingTest(unittest.TestCase):
    def test_parse_git_logs_git_failures(self):
        failed = subprocess.CompletedProcess(
            args=[],
            returncode=128,
            stdout="",
            stderr="fatal: detected dubious ownership in repository at '/work/.repos/insight'\n",
        )

        with patch("collect.subprocess.run", return_value=failed), \
             patch("collect.log_ref", return_value="HEAD"), \
             patch("collect.sys.stderr") as stderr:
            parse_git(
                "/work/.repos/insight",
                "2026-03-24T00:00:00Z",
                lambda path: False,
                "app",
                {},
                {},
                set(),
                {},
            )

        self.assertTrue(stderr.write.called)


class RenderModelTest(unittest.TestCase):
    def test_build_model_surfaces_unclassified_and_traffic_gaps(self):
        data = {
            "generated_at": "2026-06-22T00:00:00Z",
            "members": ["alice"],
            "repos": {
                "org/core": {
                    "name": "core",
                    "classification": "platform",
                    "unclassified": False,
                    "forks": 2,
                    "stars": 3,
                    "archived": False,
                    "traffic_access": True,
                    "clones_14d": 10,
                    "unique_cloners_14d": 4,
                    "contributor_emails": 2,
                },
                "org/new": {
                    "name": "new",
                    "classification": "app",
                    "unclassified": True,
                    "forks": 0,
                    "stars": 0,
                    "archived": False,
                    "traffic_access": False,
                },
            },
            "people": {
                "alice": {
                    "total_activity": 3,
                    "commits": 1,
                    "additions": 5,
                    "deletions": 0,
                    "meaningful_additions": 3,
                    "meaningful_deletions": 0,
                    "prs_opened": 1,
                    "prs_merged": 1,
                    "specs": 1,
                    "bugs": 0,
                    "features": 0,
                    "platform_commits": 1,
                    "app_commits": 0,
                    "platform_prs": 1,
                    "app_prs": 0,
                    "issues_opened": 0,
                    "is_member": True,
                    "company": "Constructor",
                    "name": "Alice",
                    "emails": ["alice@example.com"],
                    "identity_confidence": "verified",
                    "identity_evidence": ["verified"],
                }
            },
            "forkers": {
                "bob": {
                    "is_member": False,
                    "forked": ["core"],
                    "has_contributed_back": False,
                }
            },
            "weekly": {},
        }

        model = build_model(data)

        self.assertEqual(model["unclassified"], ["new"])
        self.assertEqual(model["data_quality"]["identity_unresolved"], 0)
        self.assertEqual(model["data_quality"]["unclassified_repos"], 1)
        self.assertEqual(model["traffic"]["n_repos"], 1)
        self.assertEqual(model["traffic"]["n_no_access"], 1)
        self.assertEqual(model["repo_rows"][0]["name"], "core")
        self.assertTrue(model["repo_rows"][0]["traffic_access"])
        self.assertEqual(model["non_contributors"][0]["login"], "bob")
        self.assertEqual(model["totals"]["meaningful_additions"], 3)
        self.assertEqual(model["table"][0]["loc"], 3)
        self.assertEqual(model["table"][0]["raw_loc"], 5)

    def test_report_person_logins_open_the_person_tab(self):
        data = {
            "generated_at": "2026-06-22T00:00:00Z",
            "members": ["alice"],
            "repos": {},
            "people": {
                "alice": {
                    "total_activity": 1,
                    "commits": 1,
                    "additions": 1,
                    "deletions": 0,
                    "meaningful_additions": 1,
                    "meaningful_deletions": 0,
                    "prs_opened": 0,
                    "prs_merged": 0,
                    "specs": 0,
                    "bugs": 0,
                    "features": 0,
                    "platform_commits": 1,
                    "app_commits": 0,
                    "platform_prs": 0,
                    "app_prs": 0,
                    "issues_opened": 0,
                    "is_member": True,
                    "company": "Constructor",
                    "name": "Alice",
                    "emails": ["alice@example.com"],
                    "identity_confidence": "verified",
                    "identity_evidence": ["verified"],
                }
            },
            "forkers": {},
            "weekly": {},
        }

        # The rule outlived the monolith: a person's name opens their Person page
        # rather than their GitHub profile. It now lives in the React widget every
        # table renders names through, so it is pinned there — build_model above is
        # still exercised, it just no longer has HTML to inspect.
        from pathlib import Path as _P
        gh = (_P(__file__).resolve().parents[1]
              / "frontend/src/widgets/GhLink.tsx").read_text()
        self.assertIn('data-person={login}', gh)
        self.assertNotIn("github.com", gh)

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
                    "prs_merged": 1, "specs": 1, "bugs": 0, "features": 0,
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
                    "prs_merged": 0, "specs": 0, "bugs": 0, "features": 0,
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
                "prs_opened": 0, "prs_merged": 0, "specs": 1, "bugs": 0, "features": 0,
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
        # What this used to also assert — the Elements section's markup, headers and
        # the Vega hydrator — is the React /elements view's now, covered by
        # tests/test_elements_api.py against render.elements_json.

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


class DirectoryEditorTest(unittest.TestCase):
    def test_identity_editor_links_logins_and_duplicate_suggestions_to_github(self):
        """Every login in the identity editor links to its GitHub profile, and a
        duplicate suggestion links its partner too — this is the one editor where a
        GitHub link IS wanted (you go there to check whether two accounts are the same
        human). Read from the React editor: the Jinja one this used to inspect went
        with the legacy editor layer."""
        from pathlib import Path as _P
        src = (_P(__file__).resolve().parents[1]
               / "frontend/src/pages/IdentityEditor.tsx").read_text()
        self.assertIn("github.com/", src)
        self.assertIn("dupPartner", src)                 # suggestion-driven dedup
        self.assertIn("Possible duplicate", src)         # inline merge card

    def test_snapshot_state_reports_cache_and_clone_visibility(self):
        state = snapshot_state()

        self.assertIn("cache", state)
        self.assertIn("api_files", state["cache"])
        self.assertIn("api_newest", state["cache"])
        self.assertIn("clone_repos", state["cache"])
        self.assertIsInstance(state["cache"]["api_files"], int)
        self.assertIsInstance(state["cache"]["clone_repos"], int)


    def test_write_people_yaml_validates_and_lands_in_the_db_only(self):
        """write_people_yaml parses a YAML roster out of the REQUEST BODY (a pre-JSON tab
        still posts one). It used to also write people.yaml plus a dated copy under
        history/people/; both are gone — the file was a mirror of the override table and
        reading it back imported a test fixture into prod."""
        import store
        with TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            # isolate the DB write to a temp store — like every sibling test — so this
            # neither mutates the real report.db nor contends on its lock under the
            # full-suite run (both were happening before).
            def roster():
                conn = store.connect()
                try:
                    return store.read_overrides(conn, "person")
                finally:
                    conn.close()

            write_people_yaml("people:\n  alice:\n    company: Constructor\n    emails: []\n")
            self.assertEqual(roster()["alice"]["company"], "Constructor")
            with self.assertRaises(ValueError):
                write_people_yaml("not_people: {}\n")
            # an empty roster is refused (guards against accidental clobbers)
            with self.assertRaises(ValueError):
                write_people_yaml("people: {}\n")
            self.assertIn("alice", roster())                  # unchanged
            write_people_yaml("people:\n  alice:\n    company: Constructor\n    emails: []\n"
                              "  bob:\n    company: Example Inc\n    emails: []\n")
            self.assertEqual(sorted(roster()), ["alice", "bob"])
            self.assertFalse((Path(tmp) / "people.yaml").exists())
            self.assertFalse((Path(tmp) / "history" / "people").exists())


class PortalHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def request(self, method: str, path: str, body: bytes | None = None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response.status, response.getheaders(), payload

    def test_status_endpoint_returns_cache_and_file_state(self):
        status, headers, payload = self.request("GET", "/api/status")
        data = json.loads(payload)

        self.assertEqual(status, 200)
        self.assertIn(("Content-Type", "application/json"), headers)
        self.assertIn("files", data)
        self.assertIn("cache", data)
        self.assertIn("job", data)

    def test_every_nav_icon_has_a_server_side_path(self):
        """The React sidebar draws icons from lucide; this module draws them from its
        own copy of the same paths. A name in the model with no path here renders an
        empty rail slot on the server and a real glyph after React mounts — i.e. the
        sidebar visibly changes on load, which is the one thing the two-renderer design
        exists to prevent. Three zone icons were missing exactly this way."""
        import shell
        need = ({z["icon"] for z in shell.NAV_ZONES}
                | {i["icon"] for z in shell.NAV_ZONES for i in z["items"]})
        missing = sorted(need - set(shell._ICONS))
        self.assertEqual(missing, [], f"no server-side path for {missing}")

    def test_report_and_identity_pages_include_sidebar_nav(self):
        # Post-React-cutover the report landing page is /overview (bare /report is
        # now the hash-redirect shim; the monolith lives at /report/legacy). The
        # React shell (render_spa_page) still server-renders the same sidebar.
        report_status, _, report = self.request("GET", "/overview")
        identity_status, _, identity = self.request("GET", "/identity")

        self.assertEqual(report_status, 200)
        self.assertIn(b'class="sidebar"', report)
        # Section tabs lead into the report from every page (jump to AI tools
        # etc.). EVERY report section is now migrated to its own React route
        # (overview/trend/delivery/flow/people/person/repos/elements/usage/fabric),
        # so each section tab links straight to that route via
        # shell.MIGRATED_VIEWS, NOT to /report#<mode>. `fabric` (AI tools) was the
        # last to migrate (Task R-P10) — its tab now points at /ai-tools.
        # The rail links a zone by its FIRST item, and the pane shows the current
        # zone's — so /ai-tools appears on the rail of every page (it is the AI-usage
        # zone's only entry), while /report#… appears nowhere: nothing routes through
        # the monolith's hashes any more.
        self.assertIn(b'href="/ai-tools"', report)
        self.assertNotIn(b'href="/report#', report)
        # "Report" is intentionally NOT a mode link (section tabs cover it); the
        # mode switch offers only Update + Identity.
        self.assertNotIn(b'href="/report"', report)
        self.assertEqual(identity_status, 200)
        self.assertIn(b'class="sidebar"', identity)
        self.assertIn(b'href="/ai-tools"', identity)   # cross-page jump target (migrated section)
        # Manage items live in the pane, so an active one is `class="tab active"`
        # rather than a bare `class="active"` on a .sidenav link. And the pane holds
        # only the CURRENT zone, so /identity is in the sidebar of the Identity page
        # but not of the Overview one — the rail is what is on every page.
        self.assertIn(b'class="tab active" href="/identity"', identity)
        self.assertIn(b'aria-label="Manage"', report)
        self.assertNotIn(b'href="/identity"', report)

    def test_people_yaml_endpoint_saves_valid_payload_and_rejects_invalid(self):
        """REPORT_DB is isolated here, unlike before: this test posts a one-person roster,
        so against the ambient DB's real roster the drop guard answered 400 and the test
        failed on whatever collected data happened to be present."""
        import store
        with TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}), \
                patch("reindex.apply", return_value={}):
            valid = b"people:\n  alice:\n    company: Constructor\n    emails: []\n"
            ok_status, _, ok_payload = self.request(
                "POST",
                "/api/people-yaml",
                body=valid,
                headers={"Content-Type": "text/yaml", "Content-Length": str(len(valid))},
            )
            bad = b"not_people: {}\n"
            bad_status, _, bad_payload = self.request(
                "POST",
                "/api/people-yaml",
                body=bad,
                headers={"Content-Type": "text/yaml", "Content-Length": str(len(bad))},
            )

            self.assertEqual(ok_status, 200)
            self.assertTrue(json.loads(ok_payload)["ok"])
            conn = store.connect()
            try:
                self.assertEqual(store.read_overrides(conn, "person")["alice"]["company"],
                                 "Constructor")
            finally:
                conn.close()
            self.assertEqual(bad_status, 400)
            self.assertFalse(json.loads(bad_payload)["ok"])
            # the save writes no file: that mirror is what the suite used to clobber
            self.assertFalse((Path(tmp) / "people.yaml").exists())

    def test_job_endpoint_rejects_concurrent_run(self):
        original_run_job = server.run_job
        try:
            server.run_job = lambda kind, args: False
            status, _, payload = self.request("POST", "/api/export")

            self.assertEqual(status, 409)
            self.assertEqual(json.loads(payload)["error"], "job already running")
        finally:
            server.run_job = original_run_job


class PortalSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def request(self, method: str, path: str, body: bytes | None = None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response.status, response.getheaders(), payload

    def test_cross_origin_post_is_rejected(self):
        original_run_job = server.run_job
        try:
            server.run_job = lambda kind, args: self.fail("job must not start")
            status, _, payload = self.request(
                "POST", "/api/export", headers={"Origin": "http://evil.example"}
            )

            self.assertEqual(status, 403)
            data = json.loads(payload)
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "cross-origin request rejected")
        finally:
            server.run_job = original_run_job

    def test_same_origin_post_is_accepted(self):
        original_run_job = server.run_job
        try:
            server.run_job = lambda kind, args: True
            status, _, payload = self.request(
                "POST", "/api/export",
                headers={"Origin": f"http://127.0.0.1:{self.port}"},
            )

            self.assertEqual(status, 202)
            self.assertTrue(json.loads(payload)["ok"])
        finally:
            server.run_job = original_run_job

    def test_proxied_origin_accepted_via_x_forwarded_host(self):
        # Behind nginx, the backend Host may lose the port ($host) while the
        # browser's Origin keeps it; X-Forwarded-Host carries the real host.
        original_run_job = server.run_job
        try:
            server.run_job = lambda kind, args: True
            status, _, payload = self.request(
                "POST", "/api/export",
                headers={"Origin": "http://report.example:8081",
                         "X-Forwarded-Host": "report.example:8081"},
            )
            self.assertEqual(status, 202)
            self.assertTrue(json.loads(payload)["ok"])
        finally:
            server.run_job = original_run_job

    def test_non_utf8_body_returns_400(self):
        body = b"\xff\xfe\xfa invalid"
        status, _, payload = self.request(
            "POST",
            "/api/people-yaml",
            body=body,
            headers={"Content-Type": "text/yaml", "Content-Length": str(len(body))},
        )

        self.assertEqual(status, 400)
        self.assertFalse(json.loads(payload)["ok"])

    def test_period_without_db_returns_404(self):
        with TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing.db")
            with patch("store.db_path", return_value=missing):
                # /api/period went with the Jinja fragment layer; the rule it
                # guarded — a data endpoint answers a JSON 404 rather than 500 or an
                # HTML error page when there is no DB — belongs to the JSON views now.
                status, _, payload = self.request("GET", "/api/report/delivery")

        self.assertEqual(status, 404)
        data = json.loads(payload)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "no collected data")


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
        is_loc = make_is_meaningful_loc({"exclude_suffixes": [".lock"]})
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

    def test_blame_tree_whole_file_ai_via_generated_stamp(self):
        from collect import blame_tree, make_is_spec, make_is_meaningful_loc
        is_spec, is_loc = make_is_spec({}), make_is_meaningful_loc({"exclude_suffixes": [".lock"]})
        with TemporaryDirectory() as tmp:
            self._run("init", "-q", "-b", "main", cwd=tmp)
            Path(tmp, "g.py").write_text("# Generated by cfs v1\nalpha\nbeta\n")  # whole file -> ai
            self._run("add", "-A", cwd=tmp)
            self._run("commit", "-q", "-m", "init", cwd=tmp)
            acc, _, _ = blame_tree(tmp, "HEAD", is_spec, is_loc, "2026-01-01", {})
            a = acc["dev@x.com"]
            self.assertEqual(a["code_ai"], 3)
            self.assertEqual(a["code_human"], 0)

    def test_blame_tree_cache_round_trips(self):
        from collect import blame_tree, make_is_spec, make_is_meaningful_loc
        is_spec, is_loc = make_is_spec({}), make_is_meaningful_loc({"exclude_suffixes": [".lock"]})
        with TemporaryDirectory() as tmp:
            self._make_repo(tmp)
            _, _, cache = blame_tree(tmp, "HEAD", is_spec, is_loc, "2026-01-01", {})
            self.assertTrue(cache)  # populated
            # second run with the populated cache yields identical accumulation
            acc2, _, _ = blame_tree(tmp, "HEAD", is_spec, is_loc, "2026-01-01", cache)
            self.assertEqual(acc2["dev@x.com"]["code_human"], 2)

    def test_blame_tree_emits_exactly_the_eight_short_keys(self):
        from collect import blame_tree, make_is_spec, make_is_meaningful_loc, _SURV_KEY
        is_spec, is_loc = make_is_spec({}), make_is_meaningful_loc({"exclude_suffixes": [".lock"]})
        with TemporaryDirectory() as tmp:
            self._make_repo(tmp)
            acc, _, _ = blame_tree(tmp, "HEAD", is_spec, is_loc, "2026-01-01", {})
            slot = acc["dev@x.com"]
            self.assertEqual(set(slot.keys()), set(_SURV_KEY.keys()))   # every blame key has a person-field mapping
            self.assertEqual({v for v in _SURV_KEY.values()},
                             {"surviving_code_human", "surviving_code_ai",
                              "surviving_spec_human", "surviving_spec_ai",
                              "survwin_code_human", "survwin_code_ai",
                              "survwin_spec_human", "survwin_spec_ai"})

    def test_blame_tree_tolerates_non_utf8_file_content(self):
        from collect import blame_tree, make_is_spec, make_is_meaningful_loc
        is_spec, is_loc = make_is_spec({}), make_is_meaningful_loc({})
        with TemporaryDirectory() as tmp:
            self._run("init", "-q", "-b", "main", cwd=tmp)
            # a latin-1 byte (0x80) that is invalid UTF-8 — git blame must not crash collection
            Path(tmp, "bad.py").write_bytes(b"alpha\n\x80beta\ngamma\n")
            self._run("add", "-A", cwd=tmp)
            self._run("commit", "-q", "-m", "init", cwd=tmp)
            acc, sizes, _ = blame_tree(tmp, "HEAD", is_spec, is_loc, "2026-01-01", {})
            # lossy decode counts all 3 lines; no UnicodeDecodeError raised
            self.assertEqual(sizes["code_loc"], 3)
            self.assertEqual(acc["dev@x.com"]["code_human"], 3)


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

    def test_paginate_returns_partial_and_skips_cache_when_rate_limited(self):
        gh = self._client()
        gh.rate_limited = True
        # cache is empty so it will hit the network path; mock get() to return a 403
        resp = self.FakeResp(403, {}, {})
        resp.links = {}
        with patch.object(gh, "get", return_value=resp), \
             patch.object(gh, "_cwrite") as cwrite, \
             patch.object(gh, "_cread", return_value=None):
            out = gh.paginate("/orgs/x/repos")
        self.assertEqual(out, [])              # partial -> empty, no crash
        self.assertFalse(cwrite.called)        # partial result never cached

    def test_graphql_returns_empty_and_skips_cache_when_rate_limited(self):
        gh = self._client()
        gh.rate_limited = True
        resp = self.FakeResp(403, {}, {})
        with patch.object(gh, "_throttled", return_value=False), \
             patch.object(gh.s, "post", return_value=resp), \
             patch.object(gh, "_cwrite") as cwrite, \
             patch.object(gh, "_cread", return_value=None):
            out = gh.graphql("query{}", {})
        self.assertEqual(out, {})              # partial -> empty dict
        self.assertFalse(cwrite.called)        # not cached


class StoreTest(unittest.TestCase):
    def _db(self, tmp):
        import store
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            return store, store.connect()

    def test_traffic_upsert_is_idempotent_latest_wins(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            rows = [{"repo": "o/r", "date": "2026-06-01", "clones": 5,
                     "clone_uniques": 2, "views": 3, "view_uniques": 1}]
            store.upsert_traffic(conn, rows)
            # same (repo,date) re-inserted with a fuller count -> replaced, not duplicated
            rows[0]["clones"] = 9
            n = store.upsert_traffic(conn, rows)
            self.assertEqual(n, 1)
            self.assertEqual(store.read_traffic(conn)[0]["clones"], 9)

    def test_snapshot_upsert_one_row_per_day(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            snap = {"date": "2026-06-23", "generated_at": "2026-06-23T00:00:00Z",
                    "lookback_days": 90, "totals": {"commits": 10},
                    "by_company": {"Example Inc": {"commits": 10}}}
            store.upsert_snapshot(conn, snap)
            snap["totals"]["commits"] = 20
            n = store.upsert_snapshot(conn, snap)
            self.assertEqual(n, 1)
            self.assertEqual(store.read_snapshots(conn)[0]["totals"]["commits"], 20)

    def test_run_blob_plus_normalised_people_repos(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            payload = {
                "generated_at": "2026-06-23T00:00:00Z", "lookback_days": 90, "org": "o",
                "people": {"alice": {"name": "Alice", "company": "Example Inc", "is_member": True,
                                     "commits": 12, "meaningful_additions": 300, "total_activity": 12}},
                "repos": {"o/r": {"org": "o", "name": "r", "classification": "platform",
                                  "commits_window": 5, "legacy_only": False}},
            }
            store.upsert_run(conn, payload)
            # full blob round-trips
            self.assertEqual(store.read_latest_run(conn)["people"]["alice"]["commits"], 12)
            # normalised rows are queryable + booleans coerced to ints
            row = conn.execute(
                "SELECT company, commits, is_member FROM person_runs WHERE login='alice'").fetchone()
            self.assertEqual((row["company"], row["commits"], row["is_member"]), ("Example Inc", 12, 1))
            self.assertEqual(
                conn.execute("SELECT classification FROM repo_runs WHERE repo='o/r'").fetchone()[0],
                "platform")
            # same-day re-run replaces, never duplicates the normalised rows
            payload["people"]["alice"]["commits"] = 99
            store.upsert_run(conn, payload)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM person_runs").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("SELECT commits FROM person_runs WHERE login='alice'").fetchone()[0], 99)

    def _commit(self, **kw):
        base = {"repo": "o/r", "sha": "x", "committed_at": "2026-06-20T00:00:00Z",
                "author_email": "a@x", "author_login": "alice", "classification": "platform",
                "additions": 0, "deletions": 0, "meaningful_additions": 0,
                "meaningful_deletions": 0, "is_spec": 0, "commit_type": "feat",
                "ai_marked": 0, "ai_loc": 0, "is_bot": 0}
        base.update(kw); return base

    def test_aggregate_filters_by_date_and_excludes_bots(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            store.write_people_dim(conn, [{"login": "alice", "company": "Example Inc",
                                           "is_member": True, "emails": []}])
            store.write_commits(conn, [
                self._commit(sha="a", committed_at="2026-06-20T00:00:00Z", meaningful_additions=100),
                self._commit(sha="b", committed_at="2026-01-01T00:00:00Z", meaningful_additions=50, is_spec=1, commit_type="docs"),
                self._commit(sha="c", committed_at="2026-06-21T00:00:00Z", author_login="botx", meaningful_additions=999, is_bot=1),
            ])
            # narrow window: only commit "a" (b out of range, c is a bot)
            a = store.aggregate(conn, "2026-06-01T00:00:00Z", "2026-12-31T00:00:00Z")
            self.assertEqual(a["totals"]["commits"], 1)
            self.assertEqual(a["totals"]["meaningful_additions"], 100)
            # all-time: a + b (2); bot c still excluded; spec counted once
            allt = store.aggregate(conn, "2020-01-01T00:00:00Z", "2026-12-31T00:00:00Z")
            self.assertEqual(allt["totals"]["commits"], 2)
            self.assertEqual(allt["totals"]["specs"], 1)
            self.assertTrue(any(c["company"] == "Example Inc" and c["commits"] == 2
                                for c in allt["company_rows"]))

    def test_aggregate_slices_by_repo(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            store.write_commits(conn, [
                dict(self._commit(sha="a1", committed_at="2026-06-20T00:00:00Z",
                                  meaningful_additions=10), repo="o/gears"),
                dict(self._commit(sha="a2", committed_at="2026-06-21T00:00:00Z",
                                  meaningful_additions=20), repo="o/gears"),
                dict(self._commit(sha="b1", committed_at="2026-06-20T00:00:00Z",
                                  meaningful_additions=5), repo="o/insight"),
            ])
            w = "2026-06-01T00:00:00Z", "2026-12-31T00:00:00Z"
            self.assertEqual(store.aggregate(conn, *w)["totals"]["commits"], 3)      # all
            self.assertEqual(store.aggregate(conn, *w, repos=["o/gears"])["totals"]["commits"], 2)
            self.assertEqual(store.aggregate(conn, *w, repos=["o/gears"])["totals"]
                             ["meaningful_additions"], 30)
            self.assertEqual(store.aggregate(conn, *w, repos=["o/insight"])["totals"]["commits"], 1)
            self.assertEqual(store.aggregate(conn, *w, repos=[])["totals"]["commits"], 0)

    def test_company_trend_granularity_buckets_and_auto(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            store.write_people_dim(conn, [{"login": "alice", "company": "Example Inc",
                                           "is_member": True, "emails": []}])
            # three commits spanning three ISO days, two of them in the same week
            store.write_commits(conn, [
                self._commit(sha="d1", committed_at="2026-06-15T09:00:00Z"),   # Mon
                self._commit(sha="d2", committed_at="2026-06-16T09:00:00Z"),   # Tue (same week)
                self._commit(sha="d3", committed_at="2026-07-06T09:00:00Z"),   # later month/week
            ])
            w = ("2026-06-01T00:00:00Z", "2026-07-31T23:59:59Z")
            day = store.company_trend(conn, *w, gran="day")
            self.assertEqual((day["gran"], day["points"]), ("day", 3))    # 3 distinct days
            week = store.company_trend(conn, *w, gran="week")
            self.assertEqual((week["gran"], week["points"]), ("week", 2))  # d1+d2 share a week
            month = store.company_trend(conn, *w, gran="month")
            self.assertEqual((month["gran"], month["points"]), ("month", 2))  # Jun, Jul
            # auto scales to the span: ~60 days → weekly
            auto = store.company_trend(conn, *w, gran="auto")
            self.assertEqual((auto["gran_req"], auto["gran"]), ("auto", "week"))
            # a 5-day window auto-resolves to daily; all-time to quarterly
            self.assertEqual(store.company_trend(
                conn, "2026-07-02T00:00:00Z", "2026-07-07T00:00:00Z", gran="auto")["gran"], "day")
            self.assertEqual(store.company_trend(
                conn, "2008-01-01T00:00:00Z", "2026-07-31T00:00:00Z", gran="auto")["gran"], "quarter")

    def test_developer_score_weights_are_configurable(self):
        import store, configstore
        # keep REPORT_DB pointed at the temp DB for the whole test, so the internal
        # store.connect() inside _score_weights()/load_overlay() hits the same file.
        with TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            conn = store.connect()
            # weights are owned by the Calibrate page, NOT the Config overlay: a Config
            # POST never carries them, so a Config save can't touch or reset them.
            self.assertNotIn("dev_score_weights", configstore.overlay_from_post(
                {"dev_score_weights": {"engagement": 40, "delivery": 25, "craft": 30, "flow": 25}}))
            # a stored setting override feeds through to the effective weights…
            store.write_override(conn, "setting", "dev_score_weights",
                                 {"value": {"engagement": 40, "delivery": 25, "craft": 30, "flow": 25}})
            self.assertEqual(store._score_weights()["engagement"], 40.0)
            # …and clearing it (reset to default) restores the built-in weights
            store.delete_override(conn, "setting", "dev_score_weights")
            self.assertEqual(store._score_weights(),
                             {"engagement": 20.0, "delivery": 25.0, "craft": 25.0, "flow": 35.0})

    def test_person_flow_from_timeline_events(self):
        import store, semantic_metrics
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            conn.executemany("INSERT INTO pull_request (repo,number,author_login) VALUES (?,?,?)",
                             [("o/r", 1, "alice"), ("o/r", 2, "alice"), ("o/r", 3, "alice"),
                              ("o/r", 9, "bob")])
            conn.executemany(
                "INSERT INTO timeline_event (repo,item_type,number,event,actor_login,created_at) "
                "VALUES (?,?,?,?,?,?)",
                [("o/r", "pull_request", 1, "ready_for_review", "x", "2026-06-01T00:00:00Z"),
                 ("o/r", "pull_request", 1, "merged", "x", "2026-06-02T00:00:00Z"),        # clean → 0
                 ("o/r", "pull_request", 2, "convert_to_draft", "x", "2026-06-03T00:00:00Z"),  # bounce → 2·1
                 ("o/r", "pull_request", 3, "review_requested", "x", "2026-06-04T00:00:00Z"),
                 ("o/r", "pull_request", 3, "review_requested", "x", "2026-06-05T00:00:00Z"),   # extra re-request → 1
                 ("o/r", "pull_request", 9, "merged", "x", "2026-06-06T00:00:00Z")])
            conn.commit()
            pf = semantic_metrics.person_flow(conn)
            # alice: friction = (0 + 2·1 + 1) / 3 items = 1.0 per item (lower = smoother)
            self.assertAlmostEqual(pf["alice"], 1.0, places=3)
            # bob: only 1 item (< min) → not scored
            self.assertNotIn("bob", pf)

    def test_flow_report_rates_and_cycle_times(self):
        import store, semantic_metrics
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            # 3 PRs by alice, all created in-window. #1 clean+merged, #2 bounced, #3 re-reviewed.
            conn.executemany(
                "INSERT INTO pull_request (repo,number,author_login,created_at,"
                "review_requested_at,merged_at) VALUES (?,?,?,?,?,?)",
                [("o/r", 1, "alice", "2026-06-01T00:00:00Z", "2026-06-01T02:00:00Z", "2026-06-01T10:00:00Z"),
                 ("o/r", 2, "alice", "2026-06-02T00:00:00Z", None, None),
                 ("o/r", 3, "alice", "2026-06-03T00:00:00Z", "2026-06-03T01:00:00Z", None)])
            # one issue reopened, closed 1 day after open
            conn.execute("INSERT INTO issue (repo,number,author_login,created_at,closed_at,assignees) "
                         "VALUES (?,?,?,?,?,?)",
                         ("o/r", 5, "alice", "2026-06-04T00:00:00Z", "2026-06-05T00:00:00Z", '["alice"]'))
            conn.executemany(
                "INSERT INTO timeline_event (repo,item_type,number,event,actor_login,created_at) "
                "VALUES (?,?,?,?,?,?)",
                [("o/r", "pull_request", 1, "merged", "x", "2026-06-01T10:00:00Z"),
                 ("o/r", "pull_request", 2, "convert_to_draft", "x", "2026-06-02T05:00:00Z"),
                 ("o/r", "pull_request", 3, "review_requested", "x", "2026-06-03T01:00:00Z"),
                 ("o/r", "pull_request", 3, "review_requested", "x", "2026-06-03T09:00:00Z"),
                 ("o/r", "issue", 5, "reopened", "x", "2026-06-04T12:00:00Z")])
            # PR #1 sent back for changes twice, PR #2 once (both alice's) — COMMENTED/APPROVED ignored
            conn.executemany(
                "INSERT INTO review (repo,pr_number,reviewer_login,state,submitted_at) VALUES (?,?,?,?,?)",
                [("o/r", 1, "bob", "CHANGES_REQUESTED", "2026-06-01T05:00:00Z"),
                 ("o/r", 1, "bob", "CHANGES_REQUESTED", "2026-06-01T07:00:00Z"),
                 ("o/r", 1, "bob", "APPROVED", "2026-06-01T09:00:00Z"),
                 ("o/r", 2, "bob", "CHANGES_REQUESTED", "2026-06-02T06:00:00Z"),
                 ("o/r", 3, "bob", "COMMENTED", "2026-06-03T02:00:00Z")])
            conn.commit()
            w = "2026-05-01T00:00:00Z", "2026-07-01T00:00:00Z"
            f = semantic_metrics.flow_report(conn, None, *w)
            self.assertTrue(f["has_data"])
            self.assertEqual(f["n_items"], 4)          # 3 PRs + 1 issue
            # changes-requested: PRs #1 and #2 got sent back; 3 CR reviews total (2 on #1, 1 on #2)
            self.assertEqual(f["cr_prs"], 2)
            self.assertEqual(f["cr_rounds"], 3)
            alice = [p for p in f["people"] if p["login"] == "alice"][0]
            self.assertEqual(alice["cr_rounds"], 3)
            self.assertEqual(alice["cr_prs"], 2)
            # rates over 4 cohort items
            self.assertEqual(f["bounced_n"], 1)        # PR #2
            self.assertEqual(f["reopened_n"], 1)       # issue #5
            self.assertEqual(f["rereq_n"], 1)          # PR #3 (2 review_requested → 1 extra)
            self.assertEqual(f["bounce_rate"], 25.0)
            # cycle-time medians (hours): PR#1 open→merge = 10h; open→first-review = 2h
            self.assertAlmostEqual(f["cycle"]["ttm"]["h"], 10.0, places=1)
            self.assertAlmostEqual(f["cycle"]["ttc"]["h"], 24.0, places=1)  # issue open→close
            # alice appears with ≥3 items
            self.assertTrue(any(p["login"] == "alice" for p in f["people"]))

    def test_flow_report_empty_when_no_items(self):
        import store, semantic_metrics
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            f = semantic_metrics.flow_report(conn, None, "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
            self.assertFalse(f["has_data"])

    def test_board_rewinds_qa_to_dev(self):
        import store, semantic, semantic_metrics
        from unittest.mock import patch
        # map the raw board values to stages directly (default taxonomy has no mapping)
        stage = {"QA": "qa", "In Progress": "in_progress"}
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            conn.execute("INSERT INTO pull_request (repo,number,author_login) VALUES ('o/r',7,'alice')")
            # item 7 sits in QA on day 1, moves back to In Progress on day 2 (a rewind),
            # then to QA again on day 3 → exactly one qa->dev transition detected
            for date, raw in [("2026-06-01", "QA"), ("2026-06-02", "In Progress"),
                              ("2026-06-03", "QA")]:
                store.write_work_item_status(conn, date, [
                    {"item_id": "IT7", "project": "P", "item_type": "PullRequest",
                     "repo": "o/r", "number": 7, "status_raw": raw, "title": "widget"}])
            conn.commit()
            with patch.object(semantic, "stage_for", lambda cfg, raw: stage.get(raw, "other")):
                rw = semantic_metrics.board_rewinds(conn, None, "2026-05-01T00:00:00Z", "2026-07-01T00:00:00Z")
                self.assertTrue(rw["has_history"])
                self.assertEqual(rw["qa_to_dev"], 1)
                self.assertEqual(rw["events"][0]["owner"], "alice")
                self.assertEqual(rw["events"][0]["to"], "In progress")
                # a single snapshot day can't show movement
                rw1 = semantic_metrics.board_rewinds(conn, None, "2026-06-01T00:00:00Z", "2026-06-01T23:59:59Z")
                self.assertEqual(rw1["qa_to_dev"], 0)
                # the drill returns the same events as capped, GitHub-linked rows
                dr = semantic_metrics.drill_board_rewinds(conn, None, "2026-05-01T00:00:00Z", "2026-07-01T00:00:00Z")
                self.assertEqual(dr["entity"], "rewinds")
                self.assertEqual(dr["total"], 1)
                self.assertEqual(dr["rows"][0]["owner"], "alice")
                self.assertEqual(dr["rows"][0]["move"], "QA / Test → In progress")
                self.assertTrue(dr["rows"][0]["url"].startswith("https://github.com/o/r/pull/7"))

    def test_board_cfd_series_over_snapshots(self):
        import store, semantic, semantic_metrics
        from unittest.mock import patch
        stage = {"In Progress": "in_progress", "QA": "qa", "Done": "done"}
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            snaps = {
                "2026-06-01": [("A", "In Progress"), ("B", "QA")],
                "2026-06-02": [("A", "QA"), ("B", "Done")],
            }
            for date, items in snaps.items():
                store.write_work_item_status(conn, date, [
                    {"item_id": it, "project": "P", "item_type": "Issue",
                     "repo": "o/r", "number": i + 1, "status_raw": raw, "title": it}
                    for i, (it, raw) in enumerate(items)])
            conn.commit()
            with patch.object(semantic, "stage_for", lambda cfg, raw: stage.get(raw, "other")):
                c = semantic_metrics.board_cfd(conn, None, "2026-05-01T00:00:00Z", "2026-07-01T00:00:00Z")
            self.assertTrue(c["has_data"])
            self.assertEqual(c["dates"], ["2026-06-01", "2026-06-02"])
            by = {s["key"]: s["vals"] for s in c["series"]}
            self.assertEqual(by["qa"], [1, 1])           # B then A
            self.assertEqual(by["in_progress"], [1, 0])  # A moves out on day 2
            self.assertEqual(by["done"], [0, 1])         # B lands on day 2
            # one snapshot is not enough for a chart
            c1 = semantic_metrics.board_cfd(conn, None, "2026-06-01T00:00:00Z", "2026-06-01T23:59:59Z")
            self.assertFalse(c1["has_data"])

    def test_stage_dwell_between_statuses(self):
        import store, semantic, semantic_metrics
        from unittest.mock import patch
        stage = {"In Progress": "in_progress", "QA": "qa", "Done": "done"}
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            # item walks In Progress (day1-2) -> QA (day3) -> Done (day4).
            # The In Progress run is the FIRST observed stage (entry unseen) -> skipped.
            # The QA run: entered day3, exited day4 -> ~24h dwell, counted.
            snaps = {"2026-06-01": "In Progress", "2026-06-02": "In Progress",
                     "2026-06-03": "QA", "2026-06-04": "Done"}
            for date, raw in snaps.items():
                store.write_work_item_status(conn, date + "T00:00:00Z", [
                    {"item_id": "IT1", "item_type": "Issue", "repo": "o/r", "number": 1,
                     "status_raw": raw, "title": "x"}])
            conn.commit()
            with patch.object(semantic, "stage_for", lambda cfg, raw: stage.get(raw, "other")):
                d = semantic_metrics.stage_dwell(conn, None, "2026-05-01T00:00:00Z", "2026-07-01T00:00:00Z")
            self.assertTrue(d["has_data"])
            by = {s["key"]: s for s in d["stages"]}
            self.assertIn("qa", by)                       # QA dwell observed (entry+exit seen)
            self.assertAlmostEqual(by["qa"]["median_h"], 24.0, places=1)
            self.assertNotIn("in_progress", by)           # first observed stage — entry unseen, excluded

    def test_intraday_snapshots_cfd_uses_last_and_rewinds_catch_moves(self):
        import store, semantic, semantic_metrics
        from unittest.mock import patch
        stage = {"QA": "qa", "In Progress": "in_progress"}
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            conn.execute("INSERT INTO issue (repo,number,assignees) VALUES ('o/r',9,'[\"al\"]')")
            # two snapshots on the SAME day: item 9 goes QA -> In Progress intra-day
            store.write_work_item_status(conn, "2026-06-01T06:00:00Z", [
                {"item_id": "IT9", "item_type": "Issue", "repo": "o/r", "number": 9,
                 "status_raw": "QA", "title": "x"}])
            store.write_work_item_status(conn, "2026-06-01T18:00:00Z", [
                {"item_id": "IT9", "item_type": "Issue", "repo": "o/r", "number": 9,
                 "status_raw": "In Progress", "title": "x"}])
            store.write_work_item_status(conn, "2026-06-02T06:00:00Z", [
                {"item_id": "IT9", "item_type": "Issue", "repo": "o/r", "number": 9,
                 "status_raw": "In Progress", "title": "x"}])
            conn.commit()
            with patch.object(semantic, "stage_for", lambda cfg, raw: stage.get(raw, "other")):
                # rewinds catch the intra-day QA->In Progress move
                rw = semantic_metrics.board_rewinds(conn, None, "2026-05-01T00:00:00Z", "2026-07-01T00:00:00Z")
                self.assertEqual(rw["qa_to_dev"], 1)
                self.assertEqual(rw["events"][0]["date"], "2026-06-01")
                # CFD counts only the LAST snapshot of 2026-06-01 (In Progress, not QA)
                c = semantic_metrics.board_cfd(conn, None, "2026-05-01T00:00:00Z", "2026-07-01T00:00:00Z")
                by = {s["key"]: s["vals"] for s in c["series"]}
                self.assertEqual(c["dates"], ["2026-06-01", "2026-06-02"])
                self.assertEqual(by["in_progress"], [1, 1])   # last snap of day 1 is In Progress
                self.assertNotIn("qa", by)                    # QA never the day's final state

    def test_score_labels_roundtrip_and_summary(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            store.write_score_label(conn, "alice", "boss", 4, "solid")
            store.write_score_label(conn, "alice", "peer", 2)
            store.write_score_label(conn, "alice", "boss", 5)   # same rater → updates
            s = store.label_summary(conn)
            self.assertEqual(s["alice"]["n"], 2)                # not duplicated
            self.assertAlmostEqual(s["alice"]["mean"], 3.5)     # (5 + 2) / 2

    def test_drill_returns_rows_with_github_urls(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            store.write_people_dim(conn, [
                {"login": "alice", "company": "Example Inc", "is_member": True, "emails": []},
                {"login": "bob", "company": "Other", "is_member": True, "emails": []}])
            store.write_commits(conn, [
                dict(self._commit(sha="deadbeef1234", committed_at="2026-06-20T00:00:00Z",
                                  meaningful_additions=10), repo="o/gears"),
                dict(self._commit(sha="cafef00d5678", committed_at="2026-06-21T00:00:00Z",
                                  author_login="bob", is_spec=1,
                                  title="docs: write the spec"), repo="o/insight")])
            w = "2026-06-01T00:00:00Z", "2026-12-31T00:00:00Z"
            d = store.drill(conn, "commit", *w)
            self.assertEqual(d["total"], 2)
            self.assertEqual(d["rows"][0]["url"],
                             "https://github.com/o/insight/commit/cafef00d5678")  # newest first
            self.assertEqual(d["rows"][0]["short"], "cafef00d")
            self.assertEqual(d["rows"][0]["title"], "docs: write the spec")  # subject round-trips
            # slice + flag narrow it
            self.assertEqual(store.drill(conn, "commit", *w, repos=["o/gears"])["total"], 1)
            self.assertEqual(store.drill(conn, "commit", *w, flag="is_spec")["total"], 1)
            # author scope (per-person drill) and company scope narrow it
            self.assertEqual(store.drill(conn, "commit", *w, author="alice")["total"], 1)
            self.assertEqual(store.drill(conn, "commit", *w, company="Example Inc")["total"], 1)
            self.assertEqual(store.drill(conn, "bad-entity", *w).get("error") is not None, True)

    def test_delivery_spark_points_per_metric(self):
        import store, semantic_metrics
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            conn.executemany(
                "INSERT INTO pull_request (repo,number,author_login,created_at,merged_at,state) VALUES (?,?,?,?,?,?)",
                [("o/r", i, "alice", f"2026-06-{i:02d}T00:00:00Z",
                  f"2026-06-{i:02d}T05:00:00Z", "MERGED") for i in range(1, 20)])
            conn.commit()
            sp = semantic_metrics.delivery_spark(conn, "2026-06-01T00:00:00Z", "2026-06-20T00:00:00Z")
            # a point-string per KPI, incl. rates and medians (not just counts)
            for k in ("prs_total", "pr_merge_rate", "pr_median_additions"):
                self.assertIn(k + "_pts", sp)
            self.assertTrue(sp["prs_total_pts"])          # non-empty polyline points
            self.assertIn(",", sp["prs_total_pts"])

    def test_pr_drill_shows_time_to_merge(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            conn.execute(
                "INSERT INTO pull_request (repo,number,author_login,created_at,merged_at,state,additions,changed_files) "
                "VALUES ('o/r',7,'alice','2026-06-01T00:00:00Z','2026-06-01T06:00:00Z','MERGED',12,2)")
            conn.commit()
            d = store.drill(conn, "pr", "2026-05-01T00:00:00Z", "2026-07-01T00:00:00Z", pr_state="merged")
            self.assertEqual(d["total"], 1)
            self.assertAlmostEqual(d["rows"][0]["ttm_h"], 6.0, places=1)  # per-PR time-to-merge
            self.assertIn("6.0h to merge", d["rows"][0]["meta"])

    def test_drill_extra_filters(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            store.write_commits(conn, [
                dict(self._commit(sha="c1", committed_at="2026-06-10T00:00:00Z",
                                  commit_type="feat", is_spec=0), repo="o/r"),
                dict(self._commit(sha="c2", committed_at="2026-06-11T00:00:00Z",
                                  commit_type="fix", is_spec=1), repo="o/r"),
                dict(self._commit(sha="c3", committed_at="2026-06-12T00:00:00Z",
                                  commit_type="feat", ai_marked=1, ai_tools="assistant,studio"),
                     repo="o/r")])
            store.write_prs(conn, [
                {"repo": "o/r", "number": 1, "author_login": "alice",
                 "created_at": "2026-06-10T00:00:00Z", "merged_at": "2026-06-11T00:00:00Z",
                 "state": "MERGED", "is_migration": 0, "is_bot": 0, "is_revert": 0},
                {"repo": "o/r", "number": 2, "author_login": "alice",
                 "created_at": "2026-06-10T00:00:00Z", "merged_at": None, "state": "CLOSED",
                 "is_migration": 0, "is_bot": 0, "is_revert": 1}])
            w = "2026-06-01T00:00:00Z", "2026-06-30T00:00:00Z"
            # pagination: total stays full, pages are disjoint slices (newest first)
            p0 = store.drill(conn, "commit", *w, limit=2, offset=0)
            p1 = store.drill(conn, "commit", *w, limit=2, offset=2)
            self.assertEqual(p0["total"], 3)
            self.assertEqual(len(p0["rows"]), 2)
            self.assertEqual(len(p1["rows"]), 1)         # 3 commits, 2 + 1
            self.assertEqual(p0["rows"][0]["ref"], "c3")  # newest first (2026-06-12)
            self.assertEqual(p1["rows"][0]["ref"], "c1")  # oldest on page 2
            self.assertEqual(store.drill(conn, "commit", *w, commit_type="feat")["total"], 2)
            self.assertEqual(store.drill(conn, "commit", *w, spec="1")["total"], 1)
            self.assertEqual(store.drill(conn, "commit", *w, spec="0")["total"], 2)
            self.assertEqual(store.drill(conn, "commit", *w, ai_tool="assistant")["total"], 1)
            self.assertEqual(store.drill(conn, "commit", *w, ai_tool="copilot")["total"], 0)  # no substring false-match
            self.assertEqual(store.drill(conn, "pr", *w, pr_state="merged")["total"], 1)
            self.assertEqual(store.drill(conn, "pr", *w, pr_state="abandoned")["total"], 1)
            self.assertEqual(store.drill(conn, "pr", *w, flag="is_revert")["total"], 1)
            # reviewed = PR with a row in the review table (matches the coverage tile)
            conn.execute("INSERT INTO review (repo, pr_number, reviewer_login, state, "
                         "submitted_at) VALUES ('o/r', 1, 'bob', 'APPROVED', "
                         "'2026-06-11T00:00:00Z')")
            conn.commit()
            self.assertEqual(store.drill(conn, "pr", *w, reviewed="1")["total"], 1)
            self.assertEqual(store.drill(conn, "pr", *w, reviewed="1")["rows"][0]["ref"], "1")

    def test_weekly_activity_windows_and_slices(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            store.write_commits(conn, [
                dict(self._commit(sha="w1", committed_at="2026-06-01T00:00:00Z"), repo="o/gears"),
                dict(self._commit(sha="w2", committed_at="2026-06-08T00:00:00Z", is_spec=1), repo="o/gears"),
                dict(self._commit(sha="w3", committed_at="2026-06-08T12:00:00Z"), repo="o/insight"),
                dict(self._commit(sha="wb", committed_at="2026-06-08T00:00:00Z", author_login="botx", is_bot=1), repo="o/gears")])
            w = "2026-05-01T00:00:00Z", "2026-07-01T00:00:00Z"
            wk = store.weekly_activity(conn, *w)
            # two distinct ISO weeks (Jun 1 = W23, Jun 8 = W24); bot excluded
            self.assertEqual(len(wk["weeks"]), 2)
            commits_row = next(r for r in wk["rows"] if r["key"] == "commits")
            self.assertEqual(commits_row["vals"], [1, 2])           # w1 / (w2+w3)
            specs_row = next(r for r in wk["rows"] if r["key"] == "specs")
            self.assertEqual(sum(specs_row["vals"]), 1)             # only w2
            # slice to one repo drops the insight commit in week 2
            wk_g = store.weekly_activity(conn, *w, repos=["o/gears"])
            self.assertEqual(next(r for r in wk_g["rows"] if r["key"] == "commits")["vals"], [1, 1])

    def test_company_trend_buckets_by_month_and_slices(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            store.write_people_dim(conn, [
                {"login": "alice", "company": "Example Inc", "is_member": True, "emails": []},
                {"login": "bob", "company": "Constructor", "is_member": True, "emails": []}])
            store.write_commits(conn, [
                dict(self._commit(sha="m1", committed_at="2026-05-10T00:00:00Z", meaningful_additions=5), repo="o/gears"),
                dict(self._commit(sha="m2", committed_at="2026-06-10T00:00:00Z", meaningful_additions=7), repo="o/gears"),
                dict(self._commit(sha="m3", committed_at="2026-06-20T00:00:00Z", author_login="bob"), repo="o/insight")])
            w = "2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z"
            ct = store.company_trend(conn, *w)
            self.assertEqual(ct["dates"], ["May 26", "Jun 26"])
            acr = next(r for r in ct["commit_rows"] if r["company"] == "Example Inc")
            self.assertEqual(acr["vals"], [1, 1])                 # May + Jun, alice in o/gears
            con = next(r for r in ct["commit_rows"] if r["company"] == "Constructor")
            self.assertEqual(con["vals"], [0, 1])                 # bob only in Jun
            # slice to gears drops bob's insight commit → Constructor disappears
            ct_g = store.company_trend(conn, *w, repos=["o/gears"])
            self.assertNotIn("Constructor", [r["company"] for r in ct_g["commit_rows"]])

    def test_aggregate_excludes_migration_prs(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            store.write_people_dim(conn, [{"login": "alice", "company": "Example Inc",
                                           "is_member": True, "emails": []}])
            store.write_prs(conn, [
                {"repo": "o/r", "number": 1, "org": "o", "author_login": "alice",
                 "created_at": "2026-06-20T00:00:00Z", "merged_at": "2026-06-21T00:00:00Z",
                 "review_requested_at": None, "classification": "platform",
                 "is_migration": 0, "is_bot": 0},
                {"repo": "o/r", "number": 2, "org": "o", "author_login": "alice",
                 "created_at": "2026-06-20T00:00:00Z", "merged_at": None,
                 "review_requested_at": None, "classification": "platform",
                 "is_migration": 1, "is_bot": 0},   # migration stub -> excluded
            ])
            a = store.aggregate(conn, "2026-06-01T00:00:00Z", "2026-12-31T00:00:00Z")
            self.assertEqual(a["totals"]["prs"], 1)
            self.assertEqual(a["totals"]["prs_merged"], 1)

    def test_aggregate_per_tool_split_windowed_and_stale(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            store.write_people_dim(conn, [{"login": "alice", "company": "Example Inc",
                                           "is_member": True, "emails": []}])
            store.write_commits(conn, [
                self._commit(sha="a", committed_at="2026-06-20T00:00:00Z",
                             ai_marked=1, ai_loc=10, ai_tools="the in-house assistant"),
                self._commit(sha="b", committed_at="2026-06-21T00:00:00Z",
                             ai_marked=1, ai_loc=5, ai_tools="the in-house assistant,Devin"),
                self._commit(sha="c", committed_at="2026-06-22T00:00:00Z"),
                # pre-migration row: ai_marked but tool names were not recorded
                self._commit(sha="old", committed_at="2026-01-05T00:00:00Z",
                             ai_marked=1, ai_loc=3, ai_tools=""),
            ])
            a = store.aggregate(conn, "2026-06-01T00:00:00Z", "2026-12-31T00:00:00Z")
            tools = {t["tool"]: t for t in a["ai_usage"]["tools"]}
            self.assertEqual(tools["the in-house assistant"]["commits"], 2)
            self.assertEqual(tools["the in-house assistant"]["loc"], 15)
            self.assertEqual(tools["Devin"]["commits"], 1)
            # window with only the pre-migration marked commit -> split unknowable
            stale = store.aggregate(conn, "2026-01-01T00:00:00Z", "2026-01-31T00:00:00Z")
            self.assertIsNone(stale["ai_usage"]["tools"])
            # window with no marked commits at all -> empty list, not None
            none_marked = store.aggregate(conn, "2026-06-22T00:00:00Z", "2026-06-23T00:00:00Z")
            self.assertEqual(none_marked["ai_usage"]["tools"], [])

    def test_aggregate_returns_windowed_people_mix_split_elements_traffic(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            store.write_people_dim(conn, [{"login": "alice", "name": "Alice",
                                           "company": "Example Inc", "is_member": True,
                                           "emails": [], "surviving_code_human": 4200,
                                           "reviews_given": 7, "median_ttm_h": 5.0}])
            store.write_repos_dim(conn, [
                {"key": "o/plat", "org": "o", "name": "plat", "classification": "platform",
                 "element": "Core", "code_loc": 2000, "spec_loc": 1000},
                {"key": "o/app", "org": "o", "name": "app", "classification": "app",
                 "element": "Insight", "code_loc": 5000, "spec_loc": 0}])
            store.write_commits(conn, [
                self._commit(sha="a", repo="o/plat", committed_at="2026-06-20T00:00:00Z",
                             classification="platform", meaningful_additions=100, additions=140, ai_marked=1),
                self._commit(sha="b", repo="o/app", committed_at="2026-06-21T00:00:00Z",
                             classification="app", meaningful_additions=50, is_spec=1),
                self._commit(sha="old", repo="o/app", committed_at="2026-01-01T00:00:00Z",
                             classification="app", meaningful_additions=999),  # out of window
            ])
            store.upsert_traffic(conn, [
                {"repo": "o/app", "date": "2026-06-20", "clones": 10, "clone_uniques": 4,
                 "views": 30, "view_uniques": 9},
                {"repo": "o/app", "date": "2026-06-21", "clones": 5, "clone_uniques": 2,
                 "views": 12, "view_uniques": 3},
                {"repo": "o/app", "date": "2026-01-01", "clones": 999, "clone_uniques": 1,
                 "views": 1, "view_uniques": 1},  # out of window
            ])
            a = store.aggregate(conn, "2026-06-01T00:00:00Z", "2026-06-30T23:59:59Z")
            # per-person: window activity + all-time sticky columns from the dim
            alice = next(p for p in a["people"] if p["login"] == "alice")
            self.assertEqual((alice["commits"], alice["specs"]), (2, 1))
            self.assertEqual(alice["by_type"].get("platform"), 1)   # 1 platform commit in window
            self.assertEqual(alice["by_type"].get("app"), 1)        # 1 app commit in window
            self.assertEqual(alice["surv_code_human"], 4200)  # all-time, from dim
            self.assertEqual(alice["reviews"], 7)
            self.assertIsNone(alice["surv_win_code"])       # not derivable per arbitrary period
            self.assertEqual(sum(p["commits"] for p in a["people"]), a["totals"]["commits"])
            # commit mix
            self.assertEqual((a["commit_mix"]["total"], a["commit_mix"]["specs"]), (2, 1))
            # platform/app split (commits): 1 each
            self.assertEqual((a["split"]["commits"]["platform"], a["split"]["commits"]["app"]), (1, 1))
            # by-element: window commits + all-time KLOC from repo dim
            gears = next(e for e in a["element_rows"] if e["element"] == "Core")
            self.assertEqual(gears["commits_window"], 1)
            self.assertEqual(gears["code_kloc"], 2.0)       # 2000 LOC / 1000
            # traffic summed over the window, out-of-window day excluded
            self.assertEqual(a["traffic"]["total_clones"], 15)
            self.assertEqual(a["traffic"]["total_views"], 42)
            self.assertEqual(a["traffic"]["n_repos"], 1)

    def test_aggregate_windows_reviews(self):
        import store
        with TemporaryDirectory() as tmp:
            _, conn = self._db(tmp)
            store.write_people_dim(conn, [{"login": "alice", "company": "Example Inc",
                                           "is_member": True, "emails": []}])
            store.write_prs(conn, [
                {"repo": "o/r", "number": 1, "org": "o", "author_login": "bob",
                 "created_at": "2026-06-10T00:00:00Z", "merged_at": "2026-06-12T00:00:00Z",
                 "review_requested_at": None, "classification": "platform",
                 "is_migration": 0, "is_bot": 0}])
            store.write_reviews(conn, [
                {"repo": "o/r", "pr_number": 1, "reviewer_login": "alice",
                 "state": "APPROVED", "submitted_at": "2026-06-11T00:00:00Z"},
                {"repo": "o/r", "pr_number": 1, "reviewer_login": "alice",
                 "state": "COMMENTED", "submitted_at": "2026-06-11T01:00:00Z"},
                {"repo": "o/r", "pr_number": 2, "reviewer_login": "alice",
                 "state": "APPROVED", "submitted_at": "2026-01-01T00:00:00Z"}])  # out of window
            a = store.aggregate(conn, "2026-06-01T00:00:00Z", "2026-06-30T23:59:59Z")
            rv = next(r for r in a["reviews"]["reviewers"] if r["login"] == "alice")
            self.assertEqual((rv["reviews"], rv["approvals"]), (2, 1))
            self.assertEqual(a["reviews"]["reviewed_prs"], 1)   # only PR#1 reviewed in window
            self.assertEqual(a["reviews"]["merged"], 1)         # PR#1 merged in window
            self.assertTrue(any(c["company"] == "Example Inc" and c["reviews"] == 2
                                for c in a["reviews_by_company"]))
            # re-writing the same repo replaces (idempotent), never duplicates
            store.write_reviews(conn, [
                {"repo": "o/r", "pr_number": 1, "reviewer_login": "alice",
                 "state": "APPROVED", "submitted_at": "2026-06-11T00:00:00Z"}])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM review").fetchone()[0], 1)


class ContributorsTimeseriesTest(unittest.TestCase):
    def _db(self, tmp):
        import store
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            return store.connect()

    def test_cumulative_counts_and_exclusions(self):
        import store
        with TemporaryDirectory() as tmp:
            conn = self._db(tmp)
            store.write_people_dim(conn, [
                {"login": "alice", "company": "Example Inc", "is_member": True, "emails": []},
                {"login": "bob", "company": "Constructor", "is_member": True, "emails": []}])
            base = {"repo": "o/r", "author_email": "x", "classification": "app",
                    "additions": 1, "deletions": 0, "meaningful_additions": 1,
                    "meaningful_deletions": 0, "is_spec": 0, "commit_type": "feat",
                    "ai_marked": 0, "ai_loc": 0, "is_bot": 0}
            store.write_commits(conn, [
                {**base, "sha": "a", "author_login": "alice", "committed_at": "2026-01-10T00:00:00Z"},
                {**base, "sha": "b", "author_login": "bob", "committed_at": "2026-03-10T00:00:00Z"},
                {**base, "sha": "c", "author_login": "botx", "committed_at": "2026-01-10T00:00:00Z", "is_bot": 1},
            ])
            ts = store.contributors_timeseries(
                conn, ["2026-02-01T00:00:00Z", "2026-04-01T00:00:00Z"])
            # by Feb only alice contributed; by Apr alice+bob; bot never counts
            self.assertEqual(ts[0]["total"], 1)
            self.assertEqual(ts[1]["total"], 2)
            self.assertEqual(ts[1]["by_company"].get("Example Inc"), 1)
            self.assertEqual(ts[1]["by_company"].get("Constructor"), 1)


class AliasMergeTest(unittest.TestCase):
    def test_build_roster_folds_alias_and_keeps_it(self):
        import directory
        existing = {"alice": {"company": "Example Inc", "emails": ["a@x.com"],
                              "aliases": ["alice-alt"]}}
        people = {"alice": {"emails": ["a@x.com"], "company": "Example Inc", "commits": 10},
                  "alice-alt": {"emails": [], "company": "Other", "commits": 3}}
        roster = directory.build_roster(people, existing)
        # the aliased login must NOT reappear as its own row...
        self.assertNotIn("alice-alt", roster)
        # ...and the alias is carried on the primary so it survives the next rebuild
        self.assertEqual(roster["alice"]["aliases"], ["alice-alt"])

    def test_aliases_round_trip_through_the_override_table(self):
        """Used to go out through directory.write_yaml and back in through the seed that
        read people.yaml. Both are gone; the round trip is save -> override row -> read."""
        import directory
        import server
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                server.save_people({"alice": {"name": "Alice", "company": "Example Inc",
                                              "emails": ["a@x.com"],
                                              "aliases": ["alice-alt"]}})
                back = directory.load_existing()
        self.assertEqual(back["alice"]["aliases"], ["alice-alt"])


class BootstrapXssEscapingTest(unittest.TestCase):
    """A server→client payload is embedded as a `<script type="application/json">`
    island now (render_spa_page's `bootstrap=`), not interpolated into an inline JS
    literal in a Jinja editor. The breakout rule is unchanged and still load-bearing:
    a display name containing `</script>` must not be able to close the tag.

    The U+2028/U+2029 half of the old rule is genuinely gone rather than relocated.
    Those characters were a hazard only inside a JavaScript string literal (they were
    illegal there before ES2019). Inside a JSON island read via `textContent` and
    `JSON.parse` they are ordinary characters, so escaping them would be cargo cult —
    what matters is that the payload still parses, which is asserted below."""

    def _boot(self, payload):
        import render
        return render.render_spa_page("identity", "identity", "Identity",
                                      bootstrap=payload)

    def test_script_breakout_in_display_name_is_escaped(self):
        evil = {"people": [{"login": "mallory",
                            "name": "</script><script>alert(1)</script>"}]}
        benign = {"people": [{"login": "mallory", "name": "Mallory"}]}
        html = self._boot(evil)
        baseline = self._boot(benign)
        # the payload cannot terminate the JSON island...
        self.assertNotIn("</script><script>", html)
        # ...because every "</" in it is emitted as "<\/"
        self.assertIn("<\\/script><script>alert(1)<\\/script>", html)
        # and it adds no real </script> tags: same count as a benign render
        self.assertEqual(html.count("</script>"), baseline.count("</script>"))

    def test_the_island_still_parses_with_exotic_separators_in_it(self):
        import json, re
        html = self._boot({"header": "a\u2028b\u2029c"})
        m = re.search(r'<script id="spa-bootstrap"[^>]*>(.*?)</script>', html, re.S)
        self.assertTrue(m, "no bootstrap island in the page")
        got = json.loads(m.group(1).replace("<\\/", "</"))
        self.assertEqual(got["header"], "a\u2028b\u2029c")

class PartialDataDetectionTest(unittest.TestCase):
    """Every degradation path (GraphQL, permission 403s, failed searches) must
    surface via gh.partial instead of producing silent zeros or crashes."""

    class FakeResp:
        def __init__(self, status, headers=None, body=None):
            self.status_code = status
            self.headers = headers or {}
            self._body = body if body is not None else {}
            self.links = {}
        def json(self):
            return self._body
        def raise_for_status(self):
            raise AssertionError(f"raise_for_status called on {self.status_code}")

    def _client(self):
        with patch("ghclient.requests.Session"):
            gh = GH("tok", cache_ttl_hours=0, max_wait_seconds=90)
        gh.cache_on = False
        return gh

    def test_graphql_rate_limited_error_sets_flag_and_returns_empty(self):
        gh = self._client()
        body = {"data": None,
                "errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]}
        resp = self.FakeResp(200, {"X-RateLimit-Reset": "0"}, body)
        resp.raise_for_status = lambda: None
        with patch.object(gh, "_throttled", return_value=False), \
             patch.object(gh.s, "post", return_value=resp), \
             patch.object(gh, "_cwrite") as cwrite:
            out = gh.graphql("query{}", {})
        self.assertEqual(out, {})
        self.assertTrue(gh.rate_limited)
        self.assertFalse(cwrite.called)

    def test_graphql_partial_errors_returned_but_never_cached(self):
        gh = self._client()
        body = {"data": {"repository": {"x": 1}},
                "errors": [{"type": "SOME_ERROR", "message": "partial"}]}
        resp = self.FakeResp(200, {}, body)
        resp.raise_for_status = lambda: None
        with patch.object(gh, "_throttled", return_value=False), \
             patch.object(gh.s, "post", return_value=resp), \
             patch.object(gh, "_cwrite") as cwrite:
            out = gh.graphql("query{}", {})
        self.assertEqual(out, {"repository": {"x": 1}})
        self.assertTrue(gh.partial)
        self.assertFalse(cwrite.called)

    def test_graphql_null_data_does_not_crash(self):
        gh = self._client()
        resp = self.FakeResp(200, {}, {"data": None})
        resp.raise_for_status = lambda: None
        with patch.object(gh, "_throttled", return_value=False), \
             patch.object(gh.s, "post", return_value=resp), \
             patch.object(gh, "_cwrite"):
            self.assertEqual(gh.graphql("query{}", {}), {})

    def test_get_json_does_not_cache_403(self):
        gh = self._client()
        resp = self.FakeResp(403, {}, {"message": "SAML enforcement"})
        with patch.object(gh, "get", return_value=resp), \
             patch.object(gh, "_cread", return_value=None), \
             patch.object(gh, "_cwrite") as cwrite:
            st, _ = gh.get_json("/x")
        self.assertEqual(st, 403)
        self.assertFalse(cwrite.called)

    def test_paginate_handles_empty_repo_409_and_permission_403(self):
        gh = self._client()
        with patch.object(gh, "get", return_value=self.FakeResp(409)), \
             patch.object(gh, "_cread", return_value=None), \
             patch.object(gh, "_cwrite"):
            self.assertEqual(gh.paginate("/repos/o/empty/commits"), [])
        self.assertFalse(gh.partial)      # empty repo is not data loss
        with patch.object(gh, "get", return_value=self.FakeResp(403)), \
             patch.object(gh, "_cread", return_value=None), \
             patch.object(gh, "_cwrite") as cwrite:
            self.assertEqual(gh.paginate("/orgs/o/members"), [])
        self.assertTrue(gh.partial)       # permission 403 = incomplete data
        self.assertFalse(cwrite.called)   # never cached

    def test_search_all_failed_count_marks_partial_instead_of_zero(self):
        gh = self._client()
        with patch.object(gh, "get_json", return_value=(403, None)):
            out = gh.search_all("org:o type:pr", "2026-01-01")
        self.assertEqual(out, [])
        self.assertTrue(gh.partial)


class ConfigOverlayTest(unittest.TestCase):
    def test_apply_overlay_reclass_and_element(self):
        import collect, configstore, copy
        base = {"repos": {"platform": ["a"], "app": ["b"], "ignore": []},
                "elements": {"Core": ["example-core*"], "default": "Other"},
                "extra_orgs": ["your-old-org"], "extra_repos": []}
        ov = {"repo_class": {"b": "platform", "a": "app"},
              "repo_element": {"insight": "Studio", "example-core-x": "Tooling"},
              "elements_extra": ["Brand"], "extra_orgs": ["neworg"], "extra_repos": ["o/r"]}
        cfg = configstore.apply_overlay(copy.deepcopy(base), ov)
        self.assertEqual(collect.classify("b", cfg), "platform")
        self.assertEqual(collect.classify("a", cfg), "unclassified")   # dropped -> app default
        eo = collect.make_element(cfg)
        self.assertEqual(eo("insight"), "Studio")
        self.assertEqual(eo("example-core-x"), "Tooling")                     # exact beats glob
        self.assertEqual(eo("example-core-y"), "Core")                       # glob still applies
        self.assertIn("Brand", cfg["elements"])
        self.assertEqual(cfg["extra_orgs"], ["your-old-org", "neworg"])
        # idempotent
        cfg2 = configstore.apply_overlay(configstore.apply_overlay(copy.deepcopy(base), ov), ov)
        self.assertEqual(cfg2["repos"]["platform"], cfg["repos"]["platform"])

    def test_overlay_from_post_keeps_only_diffs(self):
        import configstore
        base = {"repos": {"platform": ["p"], "app": [], "ignore": []},
                "elements": {"E1": ["p"], "default": "Other"}, "extra_orgs": [], "extra_repos": []}
        with patch.object(configstore, "base_config", lambda: base):
            ov = configstore.overlay_from_post({
                "repo_class": {"p": "platform", "q": "app"},   # p unchanged, q is default -> both no-op
                "repo_element": {"p": "E1", "q": "E2"},        # p unchanged, q changes
            })
        self.assertNotIn("repo_class", ov)                     # nothing differs from base
        self.assertEqual(ov["repo_element"], {"q": "E2"})

    def test_overlay_from_post_company_domains_diff(self):
        import configstore
        base = {"repos": {"platform": [], "app": [], "ignore": []}, "elements": {"default": "Other"},
                "companies": {"domains": {"acme.com": "Acme"}}, "extra_orgs": [], "extra_repos": []}
        with patch.object(configstore, "base_config", lambda: base):
            ov = configstore.overlay_from_post({
                "company_domains": {"acme.com": "Acme", "NewCo.io ": " Beta"}})  # base kept + new (normalized)
        self.assertEqual(ov["company_domains"], {"newco.io": "Beta"})   # base dropped, new lower-cased/trimmed


class ReconfigTest(unittest.TestCase):
    def test_apply_reclassifies_repo_without_github(self):
        import store, reconfig, configstore, directory
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}), \
                 patch.object(reconfig, "render"), \
                 patch.object(reconfig, "ROOT", tmp):
                conn = store.connect()
                # 'zrepo' is in no base list -> base class app; overlay flips to platform
                store.write_repos_dim(conn, [{"key": "o/zrepo", "org": "o", "name": "zrepo",
                    "classification": "app", "element": "Other", "code_loc": 10, "spec_loc": 0,
                    "total_loc": 10}])
                store.write_commits(conn, [
                    {"repo": "o/zrepo", "sha": s, "committed_at": "2026-06-01T00:00:00Z",
                     "author_email": "a@x", "author_login": "alice", "classification": "app",
                     "additions": 4, "deletions": 0, "meaningful_additions": 4,
                     "meaningful_deletions": 0, "is_spec": 0, "commit_type": "feat",
                     "ai_marked": 0, "ai_loc": 0, "is_bot": 0} for s in ("1", "2")])
                store.upsert_run(conn, {"generated_at": "2026-07-01T00:00:00Z",
                    "org": "o", "lookback_days": "all",
                    "people": {"alice": {"platform_commits": 0, "app_commits": 2,
                                         "platform_meaningful": 0, "app_meaningful": 8}},
                    "repos": {"o/zrepo": {"name": "zrepo", "classification": "app",
                                          "element": "Other"}}})
                conn.close()

                configstore.save_overlay({"repo_class": {"zrepo": "platform"},
                                          "repo_element": {"zrepo": "Core"}})
                reconfig.apply(do_render=False)

                conn = store.connect()
                dim = conn.execute("SELECT classification, element FROM repo WHERE name='zrepo'").fetchone()
                self.assertEqual((dim["classification"], dim["element"]), ("platform", "Core"))
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM commits WHERE repo='o/zrepo' AND classification='platform'"
                ).fetchone()[0], 2)
                blob = store.read_latest_run(conn)
                self.assertEqual(blob["people"]["alice"]["platform_commits"], 2)
                self.assertEqual(blob["people"]["alice"]["app_commits"], 0)
                conn.close()


class OverrideStoreTest(unittest.TestCase):
    def test_read_write_replace(self):
        """The seed half of this test (store.seed_overrides_from_yaml imports people.yaml
        into an empty scope) went with the function — that import is how a fixture became
        curated data in prod. tests/test_identity_save_transport.py pins its absence."""
        import store
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_override(conn, "person", "alice",
                                     {"company": "Example Inc", "name": "Alice",
                                      "emails": ["a@x"], "aliases": ["alice2"]})
                self.assertEqual(store.read_overrides(conn, "person")["alice"],
                                 {"company": "Example Inc", "name": "Alice",
                                  "emails": ["a@x"], "aliases": ["alice2"]})
                store.write_override(conn, "person", "bob", {"company": "Other", "is_bot": True})
                self.assertTrue(store.read_overrides(conn, "person")["bob"]["is_bot"])
                store.replace_overrides(conn, "person", {"carol": {"company": "X"}})
                self.assertEqual(set(store.read_overrides(conn, "person")), {"carol"})


class GhProfileTest(unittest.TestCase):
    def test_dim_roundtrip_and_empty_filtered(self):
        import store
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_people_dim(conn, [
                    {"login": "alice", "gh_name": "Alice R", "gh_company": "@acme",
                     "gh_bio": "infra", "gh_location": "Belgrade"},
                    {"login": "bob"},   # no profile fields
                ])
                self.assertEqual(store.gh_profile(conn, "alice"),
                                 {"name": "Alice R", "company": "@acme",
                                  "bio": "infra", "location": "Belgrade"})
                self.assertEqual(store.gh_profile(conn, "bob"), {})   # all-empty omitted
                self.assertEqual(sorted(store.gh_profiles(conn)), ["alice"])


class OverridesVersionTest(unittest.TestCase):
    def test_version_changes_on_any_content_edit(self):
        import store
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                self.assertEqual(store.overrides_version(conn, ("person",)),
                                 store.overrides_version(conn, ("person",)))   # stable
                store.write_override(conn, "person", "alice", {"company": "X"})
                v1 = store.overrides_version(conn, ("person",))
                store.write_override(conn, "person", "alice", {"company": "Y"})  # same second
                self.assertNotEqual(v1, store.overrides_version(conn, ("person",)))


class BuildRosterEmailTest(unittest.TestCase):
    def test_override_emails_are_authoritative_deletion_sticks(self):
        import directory
        # collect discovered two emails; the override curated only one (other deleted)
        people = {"alice": {"emails": ["a@x", "old@x"], "company": "Other", "commits": 5}}
        existing = {"alice": {"emails": ["a@x"], "company": "Example Inc"}}
        roster = directory.build_roster(people, existing)
        self.assertEqual(roster["alice"]["emails"], ["a@x"])   # old@x stays deleted


class EmailDuplicateValidationTest(unittest.TestCase):
    def test_same_email_on_two_people_is_rejected(self):
        import server
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                yaml_text = ("people:\n"
                             "  alice:\n    emails: [shared@x.com]\n"
                             "  bob:\n    emails: [Shared@X.com]\n")   # case-insensitive dup
                with self.assertRaises(ValueError) as ctx:
                    server.write_people_yaml(yaml_text)
                self.assertIn("shared@x.com", str(ctx.exception).lower())

    def test_distinct_emails_pass(self):
        import server
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                server.write_people_yaml("people:\n"
                                         "  alice:\n    emails: [a@x.com]\n"
                                         "  bob:\n    emails: [b@x.com]\n")   # no raise


class BotOverrideTest(unittest.TestCase):
    def test_force_bot_folds_granular_and_drops_from_metrics(self):
        import store, reindex, directory
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}), \
                 patch.object(reindex, "render"):
                conn = store.connect()
                store.write_people_dim(conn, [{"login": "noisy", "company": "Other",
                                               "is_member": False, "emails": []}])
                store.write_commits(conn, [{"repo": "o/r", "sha": "1",
                    "committed_at": "2026-06-01T00:00:00Z", "author_email": "n@x",
                    "author_login": "noisy", "classification": "app", "additions": 5,
                    "deletions": 0, "meaningful_additions": 5, "meaningful_deletions": 0,
                    "is_spec": 0, "commit_type": "feat", "ai_marked": 0, "ai_loc": 0, "is_bot": 0}])
                store.upsert_run(conn, {"generated_at": "2026-07-01T00:00:00Z", "org": "o",
                    "people": {"noisy": {"commits": 1}}, "repos": {}})
                store.write_override(conn, "person", "noisy", {"is_bot": True})
                conn.close()

                res = reindex.apply(do_render=False)
                self.assertEqual(res["bots_forced"], 1)
                conn = store.connect()
                self.assertEqual(conn.execute(
                    "SELECT is_bot FROM commits WHERE author_login='noisy'").fetchone()[0], 1)
                self.assertIsNone(conn.execute(
                    "SELECT 1 FROM person WHERE login='noisy'").fetchone())
                self.assertNotIn("noisy", store.read_latest_run(conn)["people"])
                conn.close()


class MetricsRegistryTest(unittest.TestCase):
    def test_number_formatting_goes_through_filters(self):
        """All number formatting is centralized in the num/loc/pct/dur Jinja filters,
        so digits group and magnitudes read consistently everywhere. Guard: no inline
        '{:,}'.format(...) left in any report template."""
        import render
        # PANELS is all that is left of the Jinja layer (the dashboard panel macros)
        for name in ("PANELS",):
            tpl = getattr(render, name)
            self.assertNotIn("{:,}", tpl, f"{name} still formats a number inline — use |num/|loc")
        for f in ("num", "loc", "pct", "dur", "compact"):
            self.assertIn(f, render._env().filters, f"missing '{f}' filter")

    def test_number_filters(self):
        import render
        self.assertEqual(render._num(5848), "5,848")
        self.assertEqual(render._num("1,282"), "1,282")
        self.assertEqual(render._loc(3_493_179), "3.49M")
        self.assertEqual(render._loc(25_727), "25.7K")
        self.assertEqual(render._loc(812), "812")
        self.assertEqual(render._pct(50.0), "50")
        self.assertEqual(render._pct(72.1), "72.1")
        self.assertEqual(render._dur(148), "2m28s")
        self.assertEqual(render._dur(45), "45s")

    def test_every_kpi_tile_is_drillable_or_explicitly_exempt(self):
        """Coverage guard (mirrors the metrics-registry completeness test): every KPI
        tile — now the kpi_tile() component — must either carry a real drill target
        (a 'drill': key in its drill dict) or be listed here as one that genuinely
        can't drill to a commit/PR/issue list. A new tile added without a drill — or a
        drill silently removed — fails this until it's classified."""
        import re
        # The tiles are built in render.py now (_kpi_tiles_json / delivery_json /
        # person tiles), not in Jinja — same rule, new home. Each `tile(...)` call is
        # one KPI tile; a drillable one names a drill dict.
        src = (Path(__file__).resolve().parent.parent / "backend" / "render.py").read_text()
        # paren-matched, not regex-sliced: a tile call spans lines and nests parens
        # (drill={"drill": "commit"}), so a lazy `.*?` stops at the first inner one.
        calls = []
        for m in re.finditer(r'(?<!def )\btile\(', src):   # calls, not the helper's own def
            i, depth = m.end(), 1
            while i < len(src) and depth:
                depth += (src[i] == "(") - (src[i] == ")")
                i += 1
            calls.append(src[m.end():i - 1])
        self.assertGreater(len(calls), 20, "tile() calls not found — did the component change?")
        # numbers that are a derived rate / median — no underlying list to open.
        EXEMPT = {"close rate", "median time-to-close", "time to first review",
                  # team developer-score medians (Overview score panel) — derived
                  # medians over the whole team, no single list to open
                  "median commits", "median time-to-merge", "median review rounds",
                  "median friction"}
        missing = []
        for call in calls:
            if '"drill"' in call or "drill=" in call:   # carries a real drill target
                continue
            if not any('"' + ex + '"' in call for ex in EXEMPT):
                missing.append(" ".join(call.split())[:90])
        self.assertEqual(missing, [],
                         f"kpi_tile tiles neither drillable nor exempt: {missing}")

    def test_every_metric_points_at_a_real_function(self):
        import importlib, metrics_registry as mreg
        metrics = mreg.all_metrics()
        self.assertGreater(len(metrics), 20)
        group_ids = {g for g, _ in mreg.GROUPS}
        for m in metrics:
            self.assertIn(m["group"], group_ids, m["name"])
            mod_name, _, qual = m["fn"].rpartition(".")
            obj = importlib.import_module(mod_name)
            for part in qual.split("."):
                obj = getattr(obj, part)                       # resolves or AttributeError
            self.assertTrue(callable(obj), f"{m['name']} → {m['fn']} not callable")

    def test_no_drift_emitted_totals_are_all_documented(self):
        import store, metrics_registry as mreg
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_commits(conn, [{"repo": "o/r", "sha": "1",
                    "committed_at": "2026-06-01T00:00:00Z", "author_email": "a@x",
                    "author_login": "alice", "classification": "app", "additions": 3,
                    "deletions": 0, "meaningful_additions": 3, "meaningful_deletions": 0,
                    "is_spec": 0, "commit_type": "feat", "ai_marked": 0, "ai_loc": 0,
                    "is_bot": 0}])
                totals = store.aggregate(conn, "2026-01-01T00:00:00Z",
                                         "2026-12-31T00:00:00Z")["totals"]
                conn.close()
            documented = mreg.names()
            missing = set(totals) - documented
            self.assertEqual(missing, set(),
                             f"headline totals not in the metrics catalog: {missing}")


class SetupWizardTest(unittest.TestCase):
    def test_secret_store_and_token_precedence(self):
        import store, ghclient
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}, clear=False):
                os.environ.pop("GH_TOKEN", None); os.environ.pop("GITHUB_TOKEN", None)
                conn = store.connect()
                self.assertFalse(store.has_secret(conn, "gh_token"))
                self.assertEqual(ghclient.token(required=False), "")   # none yet
                with patch.dict(os.environ, {"GH_TOKEN": "tok_env"}):
                    self.assertEqual(ghclient.token(), "tok_env")      # env as fallback
                    store.set_secret(conn, "gh_token", "tok_db")
                    self.assertEqual(ghclient.token(), "tok_db")       # UI token wins
                conn.close()

    def test_setting_org_overrides_config(self):
        import store, ghclient
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.write_override(conn, "setting", "org", {"value": "acme-org"})
                conn.close()
                self.assertEqual(ghclient.load_config()["org"], "acme-org")

    def test_setup_page_never_contains_the_token(self):
        import store, server
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}, clear=False):
                os.environ.pop("GH_TOKEN", None); os.environ.pop("GITHUB_TOKEN", None)
                conn = store.connect()
                store.set_secret(conn, "gh_token", "SUPERSECRET_TOKEN_VALUE")
                conn.close()
                # The wizard is a React page now, bootstrapped from server.setup_boot();
                # the rule is unchanged and is asserted on that payload directly rather
                # than by scraping rendered HTML for a JS assignment.
                import json as _json
                boot = server.setup_boot()
                self.assertNotIn("SUPERSECRET_TOKEN_VALUE", _json.dumps(boot))
                self.assertEqual(boot["token_status"], "db")            # only presence


class ReindexTest(unittest.TestCase):
    def test_apply_folds_aliases_and_sets_company_without_github(self):
        import importlib, store, directory, reindex
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}), \
                 patch.object(reindex, "render") as _r:
                conn = store.connect()
                # the curated roster: rows in the override table, which is where reindex
                # reads it from (it used to be seeded in from a people.yaml fixture)
                store.replace_overrides(conn, "person", {
                    "canon": {"name": "Canon Dev", "company": "Example Inc", "aliases": ["alt"]},
                    "bob": {"name": "Bob", "company": "Constructor"}})
                store.write_people_dim(conn, [
                    {"login": "canon", "company": "Other", "is_member": True, "emails": []},
                    {"login": "alt", "company": "Other", "is_member": True, "emails": []},
                    {"login": "bob", "company": "Other", "is_member": True, "emails": []}])
                store.write_commits(conn, [
                    {"repo": "o/r", "sha": "1", "committed_at": "2026-06-01T00:00:00Z",
                     "author_email": "a@x", "author_login": "alt", "classification": "app",
                     "additions": 5, "deletions": 0, "meaningful_additions": 5,
                     "meaningful_deletions": 0, "is_spec": 0, "commit_type": "feat",
                     "ai_marked": 0, "ai_loc": 0, "is_bot": 0},
                    {"repo": "o/r", "sha": "2", "committed_at": "2026-06-02T00:00:00Z",
                     "author_email": "c@x", "author_login": "canon", "classification": "app",
                     "additions": 3, "deletions": 0, "meaningful_additions": 3,
                     "meaningful_deletions": 0, "is_spec": 0, "commit_type": "feat",
                     "ai_marked": 0, "ai_loc": 0, "is_bot": 0}])
                res = reindex.apply(do_render=False)   # render patched out
                self.assertEqual(res["aliases"], 1)
                self.assertEqual(res["folded_rows"], 1)          # alt's 1 commit -> canon
                # alias commit now attributed to canonical
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM commits WHERE author_login='canon'").fetchone()[0], 2)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM commits WHERE author_login='alt'").fetchone()[0], 0)
                # company applied from people.yaml; alias dim row dropped
                self.assertEqual(conn.execute(
                    "SELECT company FROM person WHERE login='canon'").fetchone()[0], "Example Inc")
                self.assertIsNone(conn.execute(
                    "SELECT 1 FROM person WHERE login='alt'").fetchone())
                # aggregate reflects the merge under one company
                agg = store.aggregate(conn, "2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z")
                acr = next(c for c in agg["company_rows"] if c["company"] == "Example Inc")
                self.assertEqual(acr["commits"], 2)


class TrafficPreservationTest(unittest.TestCase):
    def test_failed_views_fetch_does_not_zero_stored_history(self):
        import store
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                conn = store.connect()
                store.upsert_traffic(conn, [{"repo": "o/r", "date": "2026-06-01",
                    "clones": 10, "clone_uniques": 5, "views": 100, "view_uniques": 40}])
                # a later run where the views request failed → NULL, clones refreshed
                store.upsert_traffic(conn, [{"repo": "o/r", "date": "2026-06-01",
                    "clones": 12, "clone_uniques": 6, "views": None, "view_uniques": None}])
                row = conn.execute("SELECT clones, views, view_uniques FROM traffic "
                                   "WHERE repo='o/r' AND date='2026-06-01'").fetchone()
                self.assertEqual((row["clones"], row["views"], row["view_uniques"]), (12, 100, 40))


class StoreWindowScopeTest(unittest.TestCase):
    def _db(self, tmp):
        import store
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            return store, store.connect()

    @staticmethod
    def _commit(sha, at, login="alice"):
        return {"repo": "o/r", "sha": sha, "committed_at": at, "author_email": "a@x",
                "author_login": login, "classification": "app", "additions": 1,
                "deletions": 0, "meaningful_additions": 1, "meaningful_deletions": 0,
                "is_spec": 0, "commit_type": "feat", "ai_marked": 0, "ai_loc": 0,
                "is_bot": 0}

    def test_narrow_lookback_run_keeps_older_rows(self):
        with TemporaryDirectory() as tmp:
            store, conn = self._db(tmp)
            store.write_commits(conn, [self._commit("old", "2024-01-01T00:00:00Z"),
                                       self._commit("new", "2026-06-01T00:00:00Z")],
                                since="2008-01-01T00:00:00Z")
            # a later run with a NARROW window must not destroy 2024 history
            store.write_commits(conn, [self._commit("new2", "2026-06-02T00:00:00Z")],
                                since="2026-05-01T00:00:00Z")
            shas = {r["sha"] for r in conn.execute("SELECT sha FROM commits")}
            self.assertEqual(shas, {"old", "new2"})

    def test_commit_type_panel_matches_kpi_population(self):
        with TemporaryDirectory() as tmp:
            store, conn = self._db(tmp)
            # one resolved commit + one with an unresolved (blank) login
            store.write_commits(conn, [self._commit("a", "2026-06-01T00:00:00Z"),
                                       self._commit("b", "2026-06-01T01:00:00Z", login="")])
            agg = store.aggregate(conn, "2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z")
            self.assertEqual(agg["totals"]["commits"], 1)
            self.assertEqual(sum(ct["count"] for ct in agg["commit_types"]), 1)

    def test_aggregate_emits_window_sparkline(self):
        with TemporaryDirectory() as tmp:
            store, conn = self._db(tmp)
            # two commits at opposite ends of a 12-day window → first & last bucket
            store.write_commits(conn, [self._commit("a", "2026-06-01T00:00:00Z"),
                                       self._commit("b", "2026-06-12T00:00:00Z")])
            agg = store.aggregate(conn, "2026-06-01T00:00:00Z", "2026-06-12T23:59:59Z")
            spark = agg["spark"]
            self.assertEqual(len(spark["commits"]), 12)
            self.assertEqual(sum(spark["commits"]), 2)          # every commit counted
            self.assertEqual(spark["commits"][0], 1)            # earliest bucket
            self.assertEqual(spark["commits"][-1], 1)           # latest bucket
            self.assertTrue(spark["commits_pts"])               # drawable polyline


class KpiDeltaTest(unittest.TestCase):
    def test_delta_map_direction_pct_and_new(self):
        import render
        dm = render.delta_map(
            {"commits": 120, "meaningful_additions": 0, "prs": 5, "prs_merged": 3,
             "specs": 2, "bugs": 1, "features": 4, "people": 10},
            {"commits": 100, "meaningful_additions": 0, "prs": 0, "prs_merged": 0,
             "specs": 2, "bugs": 0, "features": 4, "people": 12})
        self.assertEqual((dm["commits"]["dir"], dm["commits"]["pct"]), ("up", 20))
        self.assertEqual((dm["people"]["dir"], dm["people"]["pct"]), ("down", -17))
        self.assertEqual(dm["specs"]["dir"], "flat")            # unchanged
        self.assertIsNone(dm["prs"]["pct"])                     # prev 0 → 'new', no pct
        self.assertEqual(dm["prs"]["diff"], 5)


class PersonProfileTest(unittest.TestCase):
    def _db(self, tmp):
        import store
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            return store, store.connect()

    @staticmethod
    def _c(sha, login, at="2026-06-10T00:00:00Z", repo="o/r", spec=0, ctype="feat", loc=10):
        return {"repo": repo, "sha": sha, "committed_at": at, "author_email": f"{login}@x",
                "author_login": login, "classification": "platform", "additions": loc,
                "deletions": 1, "meaningful_additions": loc, "meaningful_deletions": 0,
                "is_spec": spec, "commit_type": ctype, "ai_marked": 0, "ai_loc": 0, "is_bot": 0}

    def test_profile_rank_share_and_composition(self):
        with TemporaryDirectory() as tmp:
            store, conn = self._db(tmp)
            store.write_repos_dim(conn, [{"key": "o/r", "org": "o", "name": "r",
                "classification": "platform", "element": "Insight", "code_loc": 100,
                "spec_loc": 0, "total_loc": 100}])
            # alice: 3 commits (1 spec, feat/fix), bob: 1 commit — alice ranks #1, 75% share
            store.write_commits(conn, [
                self._c("a1", "alice", ctype="feat"),
                self._c("a2", "alice", ctype="fix"),
                self._c("a3", "alice", spec=1, ctype="docs"),
                self._c("b1", "bob", ctype="feat")])
            p = store.person_profile(conn, "alice", "2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z")
            self.assertEqual(p["totals"]["commits"], 3)
            self.assertEqual((p["rank"], p["n_people"]), (1, 2))
            self.assertEqual(p["shares"]["commits"], 75.0)      # 3 of 4 org commits
            self.assertEqual(p["mix"], {"code": 2, "specs": 1, "pct_code": 66.7, "pct_specs": 33.3})
            self.assertEqual(p["split"]["total"], 3)
            _byid = {t["id"]: t["commits"] for t in p["split"]["types"]}
            self.assertEqual((_byid.get("platform"), _byid.get("app")), (3, 0))
            self.assertEqual([e["element"] for e in p["elements"]], ["Insight"])
            self.assertEqual({w["type"]: w["count"] for w in p["work_type"]},
                             {"feat": 1, "fix": 1, "docs": 1})

    # test_profile_renders_dashboard_fragment lived here until the Jinja person
    # fragment was removed with the monolith. The same payload is now asserted
    # against render.person_json in tests/test_person_api.py.

    @staticmethod
    def _data(periods):
        return {
            "generated_at": "2026-07-06T00:00:00Z", "members": ["alice"],
            "repos": {}, "forkers": {}, "weekly": {},
            "people": {"alice": {
                "total_activity": 3, "commits": 2, "additions": 5, "deletions": 0,
                "meaningful_additions": 3, "meaningful_deletions": 0,
                "prs_opened": 1, "prs_merged": 1, "specs": 1, "bugs": 0,
                "features": 0, "platform_commits": 1, "app_commits": 1,
                "platform_prs": 1, "app_prs": 0, "issues_opened": 0,
                "is_member": True, "company": "Constructor", "name": "Alice",
                "emails": ["alice@example.com"],
                "identity_confidence": "verified", "identity_evidence": ["verified"],
            }},
            "_periods": periods,
        }

    def test_missing_periods_synthesize_all_time_block(self):
        model = build_model(self._data([]))
        self.assertEqual(len(model["periods"]), 1)
        pr = model["periods"][0]
        self.assertEqual(pr["label"], "all")
        self.assertEqual(pr["totals"]["commits"], model["totals"]["commits"])
        # (it used to also render the monolith and look for the KPI row; the
        # synthesized block itself is the claim, and /api/report/overview reads it)

    def test_all_zero_store_periods_are_replaced(self):
        zero = {"label": "all", "totals": {"commits": 0}, "categories": [],
                "company_rows": [], "commit_types": [], "loc_added_h": "0"}
        model = build_model(self._data([zero]))
        self.assertEqual(model["periods"][0]["totals"]["commits"], 2)

    def test_real_store_periods_are_kept(self):
        real = {"label": "30d", "totals": {"commits": 7}, "categories": [],
                "company_rows": [], "commit_types": [], "loc_added_h": "9"}
        model = build_model(self._data([real]))
        self.assertEqual(model["periods"][0]["totals"]["commits"], 7)

    def test_all_block_collapses_onto_the_store_all_window(self):
        # Guards the aggregator collapse: the build-time all-time block must be the
        # store.aggregate('all') window (what /api/period 'all' returns), NOT the blob
        # — so the two paths cannot diverge. Blob would show alice's 2 commits; the
        # store 'all' window here says 99 with a distinct commit_mix and people list.
        allblk = {"label": "all",
                  "totals": {"commits": 99, "meaningful_additions": 0, "prs": 0,
                             "prs_merged": 0, "specs": 0, "bugs": 0, "epics": 0,
                             "features": 0, "people": 1},
                  "categories": [], "company_rows": [], "commit_types": [],
                  "commit_mix": {"total": 42}, "split": {}, "element_rows": [],
                  "people": [{"login": "zzz"}], "traffic": {}, "loc_added_h": "0"}
        ab = build_model(self._data([allblk]))["all_block"]
        self.assertEqual(ab["totals"]["commits"], 99)          # store window, not blob(2)
        self.assertEqual(ab["commit_mix"], {"total": 42})
        self.assertEqual([p["login"] for p in ab["people"]], ["zzz"])
        self.assertIn("delivery", ab)                          # taxonomy panels grafted on
        self.assertIn("flow", ab)


if __name__ == "__main__":
    unittest.main()
